"""Sender correlation over HTTP/WS; disposable PostgreSQL, no worker or model."""
import json
import time
from contextlib import contextmanager
from uuid import uuid4

import pytest
from duet_test_support import append_duet, request_duet_reply, seed_collaborations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_memory_dream import product as product  # noqa: PLC0414

from app import api, websocket
from app.models import Message, ReplyRequest


@pytest.fixture
def send_clients(product, monkeypatch):
    monkeypatch.setattr(api, "service", product)
    monkeypatch.setattr(websocket, "service", product)
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as sender, TestClient(app) as peer:
        assert sender.post('/api/auth/login', json={'username': 'DEV-AAA'}).status_code == 200
        assert peer.post('/api/auth/login', json={'username': 'DEV-BBB'}).status_code == 200
        yield product, sender, peer


def command(key, *, chat='private-alex', body='Synthetic optimistic send', reply_for_me=False):
    return {'v': 1, 'type': 'command', 'command_id': key, 'chat_id': chat, 'command': 'send',
            'payload': {'body': body, 'reply_for_me': reply_for_me}}


@contextmanager
def sender_socket(sender):
    with sender.websocket_connect('/api/ws', headers={'origin': 'http://testserver'}) as socket:
        try:
            yield socket
        finally:
            # Let the ASGI endpoint settle its send/receive tasks before the
            # TestClient cancellation scope exits. Unread ACKs may be drained here.
            socket.close()
            while socket.receive()['type'] != 'websocket.close':
                pass


def test_send_ack_is_bounded_metadata_not_content():
    full = {'message': {'id': 'm', 'chat_id': 'c', 'seq': 3, 'state': 'published',
                       'created_at': '2026-10-19T00:00:00Z', 'client_request_key': 'key',
                       'body': 'x' * 30000, 'raw_owner_input': 'private raw input',
                       'attachments': [{'name': 'private filename'}], 'read_by': ['another owner']},
            'request': {'id': 'r', 'chat_id': 'c', 'status': 'queued', 'source': 'private',
                        'source_message_id': 'm', 'accepted_at': '2026-10-19T00:00:00Z',
                        'client_request_key': 'auto:m', 'error': 'private diagnostic',
                        'background': True, 'quiet_background': True, 'can_cancel': False,
                        'session_id': 'runtime-session', 'run_id': 'runtime-run'}}
    reduced = websocket.send_ack_result(full)
    assert set(reduced['message']) == {'id', 'chat_id', 'seq', 'state', 'created_at', 'client_request_key'}
    assert set(reduced['request']) == {'id', 'chat_id', 'status', 'source', 'source_message_id',
                                      'accepted_at', 'client_request_key', 'background', 'quiet_background', 'can_cancel'}
    assert {key: reduced['request'][key] for key in ('background', 'quiet_background', 'can_cancel')} == {
        'background': True, 'quiet_background': True, 'can_cancel': False}
    assert len(json.dumps(reduced).encode()) < 1000
    assert websocket.send_ack_result({'message': None, 'request': None}) == {'message': None, 'request': None}


@pytest.mark.parametrize('origin', ['private', 'duet', 'scheduled'])
def test_question_answer_ack_retains_real_execution_scope(send_clients, origin):
    from test_questions import pending
    from test_scheduled_tasks import create, due

    from app import personal, questions, scheduled_tasks

    product, sender, _ = send_clients
    if origin == 'scheduled':
        task = create(product)
        due(product, task['id'])
        request_id = scheduled_tasks.process_due(product, [task['id']])[0]
        session_id = product.get_request(request_id)['session_id']
        product.bind_runtime_run(request_id, session_id, 'run-' + request_id)
        personal.freeze_execution_config(request_id, {
            'version': 1, 'skills': [], 'credentials': [], 'execution_enabled': False})
        context = product.get_runtime_context(request_id)
        context.update(operation_id=uuid4().hex, runtime_session_id=session_id,
            runtime_run_id='run-' + request_id, runtime_command_id=request_id)
        question = questions.create(product, {'question': 'Which update should I review?', 'options': []}, context)
    else:
        request, _, question, _ = pending(product, 'duet-alex-maya' if origin == 'duet' else 'private-alex')
        request_id = request['id']
    product.complete_request(request_id)
    frame = command(uuid4().hex, body='Review the latest update.')
    frame['payload']['reply_to_message_id'] = question['message_id']
    with sender_socket(sender) as socket:
        socket.send_json(frame)
        ack = socket.receive_json()
    assert ack['type'] == 'ack'
    accepted = ack['result']['request']
    with product.transaction() as db:
        row = db.get(ReplyRequest, accepted['id'])
        current = product.request_json(db, row, 'owner-alex')
    assert accepted['source'] == 'question_result'
    assert accepted['chat_id'] == ('duet-alex-maya' if origin == 'duet' else 'private-alex')
    assert accepted['background'] is (origin != 'private')
    assert accepted['quiet_background'] is (origin == 'scheduled')
    assert accepted['can_cancel'] is current['can_cancel'] is True


def test_http_and_get_expose_key_only_to_sender(send_clients):
    product, sender, peer = send_clients
    key = uuid4().hex
    response = sender.post('/api/chats/group-alex-maya/messages', json={
        'body': 'Synthetic sender-owned message', 'client_request_key': key})
    assert response.status_code == 200
    message = response.json()['message']
    assert message['client_request_key'] == key and message['body'] == 'Synthetic sender-owned message'
    own = sender.get('/api/chats/group-alex-maya').json()
    other = peer.get('/api/chats/group-alex-maya').json()
    assert next(row for row in own['messages'] if row['id'] == message['id'])['client_request_key'] == key
    assert 'client_request_key' not in next(row for row in other['messages'] if row['id'] == message['id'])
    # An older accepted cross-owner task still keeps its original requester's key private.
    seed_collaborations(product)
    duet_message = append_duet(product, 'duet-alex-maya', 'owner-alex', 'Approved peer message')
    requested = request_duet_reply(product, 'owner-alex', 'duet-alex-maya', duet_message['id'], 'twin-maya', uuid4().hex)
    with product.transaction() as db:
        row = db.get(ReplyRequest, requested['id'])
        assert product.request_json(db, row, 'owner-alex')['client_request_key'] == row.client_request_key
        assert 'client_request_key' not in product.request_json(db, row, 'owner-maya')


def test_websocket_lost_ack_retry_and_http_fallback_share_one_message(send_clients):
    product, sender, _ = send_clients
    key = uuid4().hex
    frame = command(key)
    with sender_socket(sender) as socket:
        socket.send_json(frame)
        # Observe committed state while deliberately leaving the ACK unread.
        saved = None
        for _ in range(50):
            detail = sender.get('/api/chats/private-alex').json()
            saved = next((row for row in detail['messages'] if row.get('client_request_key') == key), None)
            if saved:
                break
            time.sleep(.01)
        assert saved is not None
    with sender_socket(sender) as socket:
        socket.send_json(frame)
        ack = socket.receive_json()
        assert ack['type'] == 'ack' and ack['accepted'] == 'business_persisted'
        assert ack['result']['message']['id'] == saved['id']
        assert ack['result']['message']['client_request_key'] == key
        assert 'body' not in ack['result']['message']
        # Other command ACKs keep their original shape.
        socket.send_json({'v': 1, 'type': 'command', 'command_id': uuid4().hex,
                          'chat_id': 'private-alex', 'command': 'reply',
                          'payload': {'message_id': saved['id'], 'target_twin_id': 'twin-alex'}})
        other_ack = socket.receive_json()
        assert other_ack['type'] == 'ack' and 'result' not in other_ack
    fallback = sender.post('/api/chats/private-alex/messages', json={
        **frame['payload'], 'client_request_key': key})
    assert fallback.status_code == 200 and fallback.json()['message']['id'] == saved['id']
    with product.transaction() as db:
        assert db.scalar(select(func.count()).select_from(Message).where(
            Message.command_owner_id == 'owner-alex', Message.client_request_key == key)) == 1
        assert db.scalar(select(func.count()).select_from(ReplyRequest).where(
            ReplyRequest.source_message_id == saved['id'], ReplyRequest.source == 'private')) == 1


def test_removed_reply_for_me_websocket_returns_error_and_persists_nothing(send_clients):
    _product, sender, _peer = send_clients
    key = uuid4().hex
    with sender_socket(sender) as socket:
        socket.send_json(command(key, chat='group-alex-maya', reply_for_me=True))
        ack = socket.receive_json()
    assert ack['type'] == 'error'
    assert not any(row.get('client_request_key') == key for row in sender.get('/api/chats/group-alex-maya').json()['messages'])
    assert sender.get('/api/chats/group-alex-maya').json()['requests'] == []
