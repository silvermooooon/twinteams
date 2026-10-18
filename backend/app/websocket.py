"""A bounded socket carrying commands and scope invalidations, never streamed content."""
import asyncio
import json
import time
from contextlib import suppress
from urllib.parse import urlsplit

import jwt
from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from .config import settings
from .notifications import notifications
from .service import service

MAX_FRAME = 65536


async def authenticate(socket, bearer=None):
    from .api import identity
    authorization = bearer or socket.headers.get('authorization')
    token = socket.cookies.get('tt_dev_session')
    owner = await service.db_call(identity, authorization, None, token)
    if settings.local_identity_enabled:
        def expires():
            import hashlib
            from .models import DevelopmentSession
            from .service import aware
            with service.transaction() as db:
                row = db.get(DevelopmentSession, hashlib.sha256(token.encode()).hexdigest())
                return aware(row.expires_at).timestamp() if row else 0
        expiry = await service.db_call(expires)
    else:
        expiry = jwt.decode(authorization[7:], options={'verify_signature': False})['exp']
    return owner, expiry


class SocketHub:
    def __init__(self):
        self.clients = set()
        self.task = None

    async def start(self):
        notifications.listeners.add(self.notice)
        self.task = asyncio.create_task(self.compensate())

    def notice(self, event):
        chat_id = event.get('chat_id')
        for client in tuple(self.clients):
            if event.get('type') == 'personal_changed':
                if event.get('owner_id') == client.owner:
                    client.put({'v': 1, 'type': 'personal_changed', 'section': event.get('section')})
            elif event.get('type') == 'actor_changed':
                client.put({'v': 1, 'type': 'actor_changed', 'actor_id': event['actor_id'],
                            'profile_revision': event['profile_revision']})
            elif event.get('worker_status'):
                client.put({'v': 1, 'type': 'worker_status'})
            elif chat_id in client.scopes:
                client.mark(chat_id)

    async def compensate(self):
        while True:
            await asyncio.sleep(settings.compensation_seconds)
            scopes = {scope for client in self.clients for scope in client.scopes}
            if scopes:
                def versions():
                    from sqlalchemy import select
                    from .models import Chat
                    with service.transaction() as db:
                        return dict(db.execute(select(Chat.id, Chat.revision).where(Chat.id.in_(scopes))).all())
                revisions = await service.db_call(versions)
                for client in tuple(self.clients):
                    for chat_id, prior in tuple(client.scopes.items()):
                        if revisions.get(chat_id) != prior:
                            client.mark(chat_id)

    async def close(self):
        notifications.listeners.discard(self.notice)
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        for client in tuple(self.clients):
            with suppress(Exception):
                await client.socket.close(code=1001)


hub = SocketHub()


class Client:
    def __init__(self, socket, owner, expiry):
        self.socket, self.owner, self.expiry = socket, owner, expiry
        self.scopes = {}
        self.queue = asyncio.Queue(maxsize=64)
        self.dirty = set()
        self.overflow = False

    def mark(self, chat_id):
        if chat_id in self.dirty:
            return
        self.dirty.add(chat_id)
        self.put({'_scope': chat_id})

    def put(self, value):
        try:
            self.queue.put_nowait(value)
        except asyncio.QueueFull:
            self.overflow = True

    async def send(self):
        while True:
            if self.overflow:
                await self.socket.close(code=1013, reason='Reconnect and reload canonical state')
                return
            remaining = self.expiry - time.time()
            if remaining <= 0:
                await self.socket.close(code=4401, reason='Authentication expired')
                return
            try:
                value = await asyncio.wait_for(self.queue.get(), min(remaining, 20))
            except TimeoutError:
                # Session logout and JWT expiry apply even to an otherwise idle socket.
                await self.socket.send_json({'v': 1, 'type': 'ping'})
                continue
            chat_id = value.pop('_scope', None)
            if chat_id:
                self.dirty.discard(chat_id)
                try:
                    await authenticate(self.socket)
                    revision = await service.db_call('scope_revision', self.owner, chat_id)
                except HTTPException:
                    self.scopes.pop(chat_id, None)
                    await self.socket.close(code=4403, reason='Subscription access changed')
                    return
                if self.scopes.get(chat_id) == revision:
                    continue
                self.scopes[chat_id] = revision
                value = {'v': 1, 'type': 'changed', 'chat_id': chat_id, 'revision': revision}
            if value.get('type') in {'personal_changed', 'actor_changed', 'worker_status'}:
                try:
                    await authenticate(self.socket)
                except HTTPException:
                    await self.socket.close(code=4401, reason='Authentication expired')
                    return
            encoded = json.dumps(value, ensure_ascii=False, default=str)
            if len(encoded.encode()) > MAX_FRAME:
                value = {'v': 1, 'type': 'error', 'command_id': value.get('command_id'), 'detail': 'Result exceeds socket limit; reload HTTP state'}
            await asyncio.wait_for(self.socket.send_json(value), 10)

    async def receive(self):
        from .api import SendMessage, RequestReply, PresenceInput
        while True:
            raw = await self.socket.receive_text()
            if len(raw.encode()) > MAX_FRAME:
                await self.socket.close(code=1009)
                return
            command_id = None
            try:
                frame = json.loads(raw)
                if not isinstance(frame, dict) or frame.get('v') != 1:
                    raise ValueError('Unsupported protocol version')
                command_id = frame.get('command_id')
                kind, chat_id = frame.get('type'), frame.get('chat_id')
                if kind == 'pong':
                    continue
                if kind == 'subscribe':
                    if not isinstance(chat_id, str) or len(self.scopes) >= 100:
                        raise ValueError('Invalid subscription')
                    await authenticate(self.socket)
                    await service.db_call('scope_revision', self.owner, chat_id)
                    self.scopes[chat_id] = None
                    self.mark(chat_id)
                    continue
                if kind == 'unsubscribe':
                    self.scopes.pop(chat_id, None)
                    continue
                if kind != 'command' or not isinstance(command_id, str) or not 1 <= len(command_id) <= 160:
                    raise ValueError('A bounded command_id is required')
                payload = frame.get('payload', {})
                command = frame.get('command')
                if command != 'presence':
                    await authenticate(self.socket)
                if command == 'send':
                    data = SendMessage.model_validate({**payload, 'client_request_key': command_id})
                    await service.db_call('send_message', self.owner, chat_id, data.body, command_id, data.reply_to_message_id, data.reply_for_me, data.attachment_ids)
                elif command == 'reply':
                    data = RequestReply.model_validate({**payload, 'client_request_key': command_id})
                    await service.db_call('manual_request', self.owner, chat_id, data.message_id, data.target_twin_id, command_id)
                elif command == 'stop':
                    await service.cancel_request(self.owner, payload['request_id'])
                elif command == 'presence':
                    data = PresenceInput.model_validate(payload)
                    await service.update_presence(self.owner, **data.model_dump())
                else:
                    raise ValueError('Unknown command')
                self.put({'v': 1, 'type': 'ack', 'command_id': command_id, 'accepted': 'presence_renewed' if command == 'presence' else 'business_persisted'})
            except (ValueError, KeyError, ValidationError, HTTPException) as exc:
                self.put({'v': 1, 'type': 'error', 'command_id': command_id, 'detail': str(getattr(exc, 'detail', exc)), 'status': getattr(exc, 'status_code', 400)})


async def websocket_endpoint(socket: WebSocket):
    origin = socket.headers.get('origin')
    own_origin = ('https' if socket.url.scheme == 'wss' else 'http') + '://' + socket.headers.get('host', '')
    if origin not in {*settings.cors_origins, own_origin}:
        await socket.close(code=4403, reason='Origin is not allowed')
        return
    try:
        owner, expiry = await authenticate(socket)
    except Exception:
        await socket.close(code=4401)
        return
    await socket.accept()
    client = Client(socket, owner, expiry)
    hub.clients.add(client)
    tasks = [asyncio.create_task(client.send()), asyncio.create_task(client.receive())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except (WebSocketDisconnect, HTTPException, RuntimeError, TimeoutError, OSError):
        pass
    finally:
        hub.clients.discard(client)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        with suppress(Exception):
            await socket.close()
