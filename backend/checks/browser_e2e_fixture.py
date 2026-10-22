"""Explicit disposable browser E2E entrypoint, never a production app module."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import runpy
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def guard():
    root = Path(os.environ.get('TWINTEAMS_E2E_ROOT', '/nonexistent')).resolve()
    nonce = os.environ.get('TWINTEAMS_E2E_INSTANCE_ID', '')
    parsed = urlsplit(os.environ.get('DATABASE_URL', ''))
    options = parse_qs(parsed.query).get('options', [])
    if (os.environ.get('TWINTEAMS_BROWSER_E2E') != 'true'
            or not re.fullmatch(r'[0-9a-f]{32}', nonce)
            or parsed.hostname not in {'localhost', '127.0.0.1', '::1'}
            or len(options) != 1
            or not re.fullmatch(r'-c search_path=tt_test_[0-9a-f]{32}', options[0])
            or not root.name.startswith('twinteams-browser-e2e-')
            or not (root / '.fixture').is_file()
            or (root / '.fixture').is_symlink()
            or (root / '.fixture').read_text() != nonce
            or os.environ.get('AGENT_DB_URL') != os.environ.get('DATABASE_URL')
            or os.environ.get('TWINTEAMS_REDIS_PREFIX') != 'browser-e2e-' + nonce):
        raise RuntimeError('Browser E2E requires its disposable schema, nonce and private test root')
    for name in ('TWINTEAMS_WORKSPACE_ROOT', 'AGENT_MEMORY_DIR', 'AGENT_SECRETS_DIR'):
        path = Path(os.environ[name]).resolve()
        if not path.is_relative_to(root) or path == root:
            raise RuntimeError('Browser E2E data directories must be inside the disposable root')
    return root


def text_content(message):
    content = message.get('content', '')
    return content if isinstance(content, str) else '\n'.join(str(row.get('text', '')) for row in content if row.get('type') == 'text')


def install_fixture(root):
    import httpx

    async def deny_async(*args, **kwargs):
        raise AssertionError('Browser E2E forbids outbound HTTP, including model requests')

    def deny_sync(*args, **kwargs):
        raise AssertionError('Browser E2E forbids outbound HTTP, including model requests')

    live = os.environ.get('TWINTEAMS_E2E_MODEL_MODE') == 'live-gpt-6-luna'
    if live:
        from checks.browser_e2e_live import install
        install(root, os.environ.get('TWINTEAMS_E2E_ROLE'))
    else:
        httpx.AsyncClient.send, httpx.Client.send = deny_async, deny_sync
    from app import twin_execution
    original_backend = twin_execution.TwinExecutionBackend

    class FixtureExecutionBackend(original_backend):
        def __init__(self, identity, workspace, *, directory=None):
            super().__init__(identity, workspace, directory=root / 'execution')

    twin_execution.TwinExecutionBackend = FixtureExecutionBackend
    if live:
        return  # Use the actual SDK provider, never a scripted response.
    from agent_runtime import AssistantMessageEventStream, assistant_message

    from app.runtime_bridge import RuntimeBridge
    original = RuntimeBridge.__init__

    async def offline(model, context, options):
        users = [i for i, row in enumerate(context.messages) if row.get('role') == 'user']
        position = users[-1] if users else 0
        user = context.messages[position]
        text = text_content(user)
        system = text_content(context.messages[0])
        from agent_runtime.transcript import current_tools
        catalog = current_tools(context.messages)
        tools = {tool['name'] for tool in catalog}
        results = [row for row in context.messages[position + 1:] if row.get('role') == 'toolResult']
        names = [row.get('toolName') for row in results]

        def details():
            from app.models import Actor, Message, ReplyRequest
            from app.service import service
            with service.transaction() as db:
                request = db.get(ReplyRequest, user.get('queueId', ''))
                if not request:
                    return {}
                source = db.get(Message, request.source_message_id) if request.source_message_id else None
                return {'id': request.id, 'source': request.source, 'generation': request.generation,
                        'chat_id': request.chat_id, 'owner_id': db.get(Actor, request.target_twin_id).owner_id,
                        'reply_for_me': request.raw_owner_input is not None,
                        'body': request.raw_owner_input if request.raw_owner_input is not None else source.body if source else request.input_body or '',
                        'workspace': str(root / 'workspaces' / request.chat_id)}

        info = await asyncio.to_thread(details)
        if 'memory_publish' in tools and not info:
            from app.models import MemoryDreamState
            from app.service import service
            def dream_details():
                with service.transaction() as db:
                    owner = re.search(r'"session_id":\s*"twin-(?:chat:private-|config:owner-)(alex|maya|jordan)', text)
                    state = db.get(MemoryDreamState, 'owner-' + owner.group(1)) if owner else None
                    if state is None:
                        from sqlalchemy import select
                        states = list(db.scalars(select(MemoryDreamState).where(MemoryDreamState.queued_request_id.is_not(None))))
                        state = states[0] if len(states) == 1 else None
                    if state and state.queued_request_id:
                        return {'id': state.queued_request_id, 'source': 'memory_dream', 'owner_id': state.owner_id}
                    return {}
            info = await asyncio.to_thread(dream_details)
        if system.startswith('Choose which human owners'):
            payload = json.loads(text)
            message = payload['message']
            body = message.get('body', '')
            needs = bool(re.search(r'\bE2E (?:ECHO|MARKDOWN|SLOW|FAIL_ONCE|FILE|IDENTITY|OWNER_DM|CHAIN)\b', body))
            if message.get('kind') in {'twin', 'reply_for_me'} and not body.startswith('E2E CHAIN '):
                needs = False
            allowed = json.loads(system.rsplit('Allowed owners: ', 1)[1])
            author = message.get('author_owner_id') or message.get('source_owner_id') or message.get('author_id')
            allowed = [owner for owner in allowed if owner != author]
            if len(allowed) > 1:
                allowed = [owner for owner in allowed if owner != 'owner-alex'] or allowed
            content = [{'type': 'text', 'text': json.dumps({'target_owner_ids': allowed[:1], 'needs_response': needs, 'reason': 'Explicit deterministic browser E2E routing'})}]
            stop = 'stop'
        else:
            match = re.search(r'\bE2E ([A-Z_]+)(?: ([A-Za-z0-9_-]+))?', info.get('body') or text)
            command, label = match.groups() if match else ('ECHO', 'message')
            label = label or 'fixture'
            name, arguments = None, None
            final = 'E2E echo: ' + label
            if 'memory_publish' in tools:
                command, label = 'DREAM', 'private-evidence'
                if 'memory_publish' in names:
                    final = 'E2E Dream published private evidence.'
                elif not results:
                    refs = re.findall(r'"session_id":\s*"([^"]+)"\s*,\s*"event_id":\s*"([^"]+)"', text)
                    if refs:
                        name, arguments = 'read_source', {'session_id': refs[0][0], 'event_id': refs[0][1]}
                    else:
                        name, arguments = 'memory_read', {'key': 'e2e-dream-' + info.get('id', 'source')[:12]}
                elif 'memory_read' not in names:
                    name, arguments = 'memory_read', {'key': 'e2e-dream-' + info.get('id', 'source')[:12]}
                elif 'memory_write' not in names:
                    read = next(row for row in results if row.get('toolName') == 'memory_read')
                    value = json.loads(text_content(read))
                    entry = value.get('entry') or {}
                    content = entry.get('content', '')
                    name, arguments = 'memory_write', {'key': 'e2e-dream-' + info.get('id', 'source')[:12], 'content': content + '\nE2E Dream consolidated authorized private evidence.', 'expected_version': entry.get('version')}
                else:
                    name, arguments = 'memory_publish', {}
            elif info.get('reply_for_me'):
                final = 'E2E echo: ' + label
            elif info.get('source') == 'credential_result':
                if 'bash' not in names:
                    credential_tool = next(tool for tool in catalog if tool['name'] == 'bash')
                    refs = credential_tool['parameters']['properties']['credentials']['items']['enum']
                    name, arguments = 'bash', {'command': '[ -n "$E2E_TOKEN" ] && printf E2E_CREDENTIAL_AVAILABLE', 'credentials': [next(ref for ref in refs if ref.endswith('e2e-' + label))]}
                final = 'E2E credential resumed: ' + label
            elif info.get('source') == 'approval_result':
                if 'execute_approved_action' not in names:
                    from sqlalchemy import select

                    from app.models import Approval
                    from app.service import service
                    def approval_id():
                        with service.transaction() as db:
                            row = db.scalar(select(Approval).where(Approval.continuation_request_id == info['id']))
                            return (row.id, row.status) if row else (None, None)
                    identifier, status = await asyncio.to_thread(approval_id)
                    if status == 'approved':
                        name, arguments = 'execute_approved_action', {'id': identifier}
                    else:
                        final = 'E2E approval rejected: ' + label
                if name or 'execute_approved_action' in names:
                    final = 'E2E approval executed: ' + label
            elif info.get('source') == 'subagent_result':
                final = 'E2E delegation reviewed: ' + label
            elif command == 'SLOW':
                await options['signal'].run(asyncio.sleep(8))
                final = 'E2E slow completed: ' + label
            elif command == 'FAIL_ONCE':
                if info.get('generation', 0) == 0:
                    raise RuntimeError('E2E intentional first-attempt failure')
                final = 'E2E retry succeeded: ' + label
            elif command == 'MARKDOWN':
                final = '# E2E Markdown ' + label + '\n\n**Bold** and `inline code`.\n\n- First item\n- Second item\n\n```python\nprint("E2E")\n```\n\n| Column | Value |\n| --- | --- |\n| E2E | passed |'
            elif command == 'FILE':
                if 'write' not in names:
                    name, arguments = 'write', {'path': info['workspace'] + '/e2e-' + label + '.txt', 'content': 'E2E generated workspace artifact: ' + label + '\n'}
                final = 'E2E file ready: e2e-' + label + '.txt'
            elif command == 'IDENTITY':
                if 'bash' not in names:
                    name, arguments = 'bash', {'command': 'printf "owner-cwd=%s\\n" "$PWD"; printf "owner-home=%s\\n" "$HOME"'}
                elif 'memory_read' not in names:
                    name, arguments = 'memory_read', {'key': 'e2e-' + label}
                final = 'E2E owner identity: ' + info.get('owner_id', 'worker')
                bash_result = next((row for row in results if row.get('toolName') == 'bash'), None)
                if bash_result:
                    final += '\n' + text_content(bash_result)
                memory_result = next((row for row in results if row.get('toolName') == 'memory_read'), None)
                if memory_result:
                    final += '\nactual-memory-present=' + str(json.loads(text_content(memory_result)).get('status') == 'found').lower()
            elif command == 'CREDENTIAL':
                if 'request_credential' not in names:
                    name, arguments = 'request_credential', {'name': 'e2e-' + label, 'description': 'Synthetic browser E2E credential', 'keynames': ['E2E_TOKEN'], 'reason': 'Continue the isolated E2E request'}
                final = 'E2E credential pending: ' + label
            elif command == 'APPROVAL':
                if 'create_approval' not in names:
                    name, arguments = 'create_approval', {'title': 'E2E approval ' + label, 'description': 'Create exactly one isolated test reminder', 'target': 'Your reminders', 'impact': 'One in-app reminder, no external effect', 'action': {'tool': 'create_reminder', 'arguments': {'title': 'E2E approved ' + label}}}
                final = 'E2E approval pending: ' + label
            elif command == 'SKILL':
                if 'load_skill' not in names:
                    name, arguments = 'load_skill', {'name': 'e2e-verification'}
                final = 'E2E skill loaded: ' + label
            elif command == 'MEMORY':
                if 'memory_write' not in names:
                    name, arguments = 'memory_write', {'key': 'e2e-' + label, 'content': 'E2E memory ' + label, 'expected_version': None}
                final = 'E2E memory saved: ' + label
            elif command == 'DELEGATE':
                if 'spawn_agent' not in names:
                    name, arguments = 'spawn_agent', {'name': 'worker', 'task': 'E2E CHILD ' + label}
                final = 'E2E delegated: ' + label
            elif command == 'CHILD':
                await options['signal'].run(asyncio.sleep(2))
                final = 'E2E child completed: ' + label
            elif command == 'OWNER_DM':
                if 'list_authorized_chats' not in names:
                    name, arguments = 'list_authorized_chats', {}
                elif 'send_chat_message' not in names:
                    owner = info['owner_id'].removeprefix('owner-')
                    name, arguments = 'send_chat_message', {'chat_id': 'private-' + owner, 'body': 'E2E owner report: ' + label, 'source_message_ids': []}
                final = 'E2E owner report sent: ' + label
            elif command == 'CHAIN':
                number = re.search(r'E2E CHAIN [A-Za-z0-9_-]+ (\d+)', info.get('body') or text)
                step = int(number.group(1)) + 1 if number else 1
                final = f'E2E CHAIN {label} {step}' if step <= 12 else 'E2E chain complete: ' + label
            if results and results[-1].get('isError'):
                name, arguments, final = None, None, 'E2E tool failed: ' + str(results[-1].get('toolName'))
            if name:
                if name not in tools:
                    raise RuntimeError('E2E command requires installed tool: ' + name)
                content = [{'type': 'toolCall', 'id': f'e2e-{len(results)}-{name}', 'name': name, 'arguments': arguments}]
                stop = 'toolUse'
            else:
                content, stop = [{'type': 'text', 'text': final}], 'stop'
            with (root / 'provider-calls.jsonl').open('a') as target:
                target.write(json.dumps({'command': command, 'tool': name, 'request_id': info.get('id'), 'source': info.get('source'), 'owner_id': info.get('owner_id')}) + '\n')
        stream = AssistantMessageEventStream()
        stream.push({'type': 'done', 'message': assistant_message(model, content=content, stopReason=stop)})
        return stream

    def initialize(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.models.register_api('browser-e2e-offline', offline)
    RuntimeBridge.__init__ = initialize


def seed(root):
    from app.init_db import main as provision
    from app.models import Actor
    from app.service import service
    asyncio.run(provision())
    for owner in ('alex', 'maya', 'jordan'):
        folder = root / 'workspaces' / ('private-' + owner) / 'skills' / 'e2e-verification'
        folder.mkdir(parents=True)
        (folder / 'SKILL.md').write_text('---\nname: e2e-verification\ndescription: Synthetic isolated E2E skill\n---\nReturn the E2E skill verification marker after loading this skill.\n')
        (folder.parent.parent / 'e2e-owner-sentinel.txt').write_text('E2E owner ' + owner + '\n')
    with service.transaction() as db:
        assert db.get(Actor, 'owner-alex') is not None


def api_app():
    from fastapi import FastAPI
    from fastapi.responses import FileResponse, JSONResponse
    from starlette.staticfiles import StaticFiles

    from app import api, main
    from app.credential_api import router as credentials
    from app.personal_api import router as personal
    from app.provider_config import router as provider
    app = FastAPI(title='Twinteams isolated browser E2E', lifespan=main.lifespan)
    app.exception_handlers = main.app.exception_handlers.copy()

    @app.get('/api/config')
    def fixture_config():
        return {**api.config(), 'acceptance_fixture': os.environ.get('TWINTEAMS_E2E_MODEL_MODE') != 'live-gpt-6-luna'}

    for router in (api.router, credentials, personal, provider):
        app.include_router(router)

    @app.get('/health')
    def fixture_health():
        return {**main.health(),
                'browser_e2e': True, 'e2e_instance_id': os.environ['TWINTEAMS_E2E_INSTANCE_ID'],
                'model_mode': os.environ.get('TWINTEAMS_E2E_MODEL_MODE', 'deterministic-offline'),
                'scenario': os.environ.get('TWINTEAMS_E2E_SCENARIO')}

    dist = Path(os.environ['TWINTEAMS_E2E_DIST'])
    app.mount('/assets', StaticFiles(directory=dist / 'assets'), name='fixture-assets')

    @app.get('/{path:path}')
    def fixture_frontend(path: str):
        if path.startswith(('api/', 'api', 'health')):
            return JSONResponse(status_code=404, content={'detail': 'Endpoint not found'})
        file = (dist / path).resolve()
        if file.is_relative_to(dist) and file.is_file():
            return FileResponse(file)
        return FileResponse(dist / 'index.html')
    return app



def inspect_fixture(root):
    """Read allowlisted metadata from this disposable instance, never raw values."""
    from collections import Counter

    from sqlalchemy import select, text

    from app.credential_models import CredentialRequest
    from app.models import (
        Approval,
        Chat,
        ChatAttachment,
        ContextManifest,
        Message,
        ReplyRequest,
        TrackingRevision,
        TrackingTopic,
        TwinFollowup,
    )
    from app.service import service
    with service.transaction() as db:
        requests = [{key: getattr(row, key) for key in ('id', 'chat_id', 'requester_id', 'target_twin_id', 'source', 'status', 'generation', 'private_generation', 'source_message_id', 'causal_chain_id', 'session_id', 'run_id')} for row in db.scalars(select(ReplyRequest))]
        dreams = []
        for request in db.scalars(select(ReplyRequest).where(ReplyRequest.source == 'memory_dream')):
            if not request.input_body:
                continue  # A private reset legitimately erases old Dream input.
            payload = json.loads(request.input_body)
            refs = []
            for ref in payload.get('messages', []):
                message = db.get(Message, ref['id'])
                refs.append({'id': ref['id'], 'chat_id': message.chat_id if message else None, 'kind': message.kind if message else None, 'seq': ref['seq']})
            dreams.append({'request_id': request.id, 'owner_id': payload.get('owner_id'), 'scope': payload.get('scope'), 'messages': refs})
        def tracking_refs(sources, chat_id, via_context=False):
            found = []
            for source in sources or []:
                if source.get('kind') == 'tracking' and isinstance(source.get('id'), str):
                    found.append({'tracking_id': source['id'], 'chat_id': chat_id, 'via_chat_context': via_context})
                elif source.get('kind') == 'chat_context':
                    found.extend(tracking_refs(source.get('sources'), source.get('chat_id'), True))
            return found
        request_chats = {row['id']: row['chat_id'] for row in requests}
        manifests = []
        for row in db.scalars(select(ContextManifest)):
            refs = tracking_refs(row.sources, request_chats.get(row.request_id))
            manifests.append({'request_id': row.request_id, 'owner_id': row.owner_id,
                              'tracking_ids': list(dict.fromkeys(ref['tracking_id'] for ref in refs)),
                              'tracking_refs': refs})
        tracking = [{'id': row.id, 'chat_id': row.chat_id, 'source_message_ids': row.source_message_ids, 'revision': row.revision, 'status': row.status, 'deleted': row.deleted_at is not None} for row in db.scalars(select(TrackingTopic))]
        revisions = [{key: getattr(row, key) for key in ('topic_id', 'revision', 'status', 'source_message_ids', 'changed_by', 'deleted')} for row in db.scalars(select(TrackingRevision))]
        followups = [{**{key: getattr(row, key) for key in ('id', 'owner_id', 'chat_id', 'origin_request_id', 'target_chat_id', 'sent_message_id', 'causal_chain_id', 'status', 'reply_message_id', 'notification_state', 'continuation_request_id')}, 'created_at': row.created_at.isoformat(), 'due_at': row.due_at.isoformat(), 'settled_at': row.settled_at.isoformat() if row.settled_at else None} for row in db.scalars(select(TwinFollowup))]
        runs = [dict(row._mapping) for row in db.execute(text('SELECT session_id,run_id,user_id,root_run_id,source,status FROM agent_runs'))]
        approvals = [{'id': row.id, 'status': row.status, 'owner_id': row.approver_id, 'continuation_request_id': row.continuation_request_id} for row in db.scalars(select(Approval))]
        credentials = [{'id': row.id, 'status': row.status, 'owner_id': row.owner_id, 'continuation_request_id': row.continuation_request_id} for row in db.scalars(select(CredentialRequest))]
        private_chats = {row.private_owner_id: row.id for row in db.scalars(select(Chat).where(Chat.kind == "private"))}
        attachments = [{key: getattr(row, key) for key in ('id', 'chat_id', 'relative_path')} for row in db.scalars(select(ChatAttachment))]
        records = list(db.execute(text('SELECT session_id, record FROM agent_session_events ORDER BY session_id, seq')))
    current, arguments, returned, tools, files, model_calls = {}, {}, {}, [], [], []
    request_ids = {(row['session_id'], row['run_id']): row['id'] for row in requests}
    run_owners = {(row['session_id'], row['run_id']): row['user_id'] for row in runs}
    for session_id, record in records:
        data, kind, run_id = record.get('data', {}), record.get('type'), record.get('run_id')
        if kind == 'model_completed':
            usage = data.get('message', {}).get('usage', {})
            model_calls.append({'session_id': session_id, 'run_id': run_id,
                'request_id': request_ids.get((session_id, run_id)),
                'usage': {name: value for name, value in usage.items() if type(value) in {int, float}}})
            for call in data.get('message', {}).get('content', []):
                if call.get('type') == 'toolCall':
                    current[(session_id, run_id, call['id'])] = call['name']
                    arguments[(session_id, run_id, call['id'])] = call.get('arguments', {})
        key = session_id, run_id, data.get('call_id')
        if kind == 'tool_returned':
            returned[key] = data.get('result', {})
        elif kind == 'tool_completed':
            result = returned.get(key, {}) if data.get('use_returned') else data.get('result', {})
            output = text_content(result)
            name, args = current.get(key), arguments.get(key, {})
            try:
                result_value = json.loads(output)
            except (ValueError, TypeError):
                result_value = {}
            tools.append({'name': name, 'session_id': session_id, 'run_id': run_id,
                'call_id': data.get('call_id'), 'event_id': record.get('id'), 'seq': record.get('seq'),
                'request_id': request_ids.get((session_id, run_id)), 'owner_id': run_owners.get((session_id, run_id)),
                'arguments': {field: args[field] for field in ('id', 'chat_id', 'owner_ids', 'source_message_ids', 'name', 'task_id', 'path', 'offset', 'limit', 'key') if field in args},
                'result_ids': {field: result_value[field] for field in ('id', 'chat_id', 'message_id', 'request_id', 'continuation_request_id') if isinstance(result_value, dict) and isinstance(result_value.get(field), str)},
                'success': not result.get('isError', False),
                'credential_available': 'E2E_CREDENTIAL_AVAILABLE' in output,
                'owner_cwd_verified': 'owner-cwd=' in output and '/workspaces/private-' in output})
            from app.config import settings
            tools[-1]['memory_key'] = args.get('key') if name and name.startswith('memory_') else None
            tools[-1]['result_memory_keys'] = [match['key'] for match in result_value.get('matches', [])
                if isinstance(match, dict) and isinstance(match.get('key'), str)] if name == 'memory_search' and isinstance(result_value, dict) else []
            tools[-1]['tracking_id'] = args.get('id') if name == 'get_tracking' else None
            tools[-1]['result_tracking_markers'] = sorted(set(re.findall(r'\bGROUP-[0-9a-f]{8}\b', output)))
            tools[-1]['attachment_ids_read'] = []
            tools[-1]['attachment_paths_mentioned'] = []
            for attachment in attachments:
                expected = (settings.workspace_root / attachment['chat_id'] / attachment['relative_path']).resolve()
                read_path = Path(args.get('path', ''))
                if not read_path.is_absolute():
                    owner = run_owners.get((session_id, run_id))
                    read_path = settings.workspace_root / private_chats[owner] / read_path if owner in private_chats else read_path
                if name == 'read' and not result.get('isError') and read_path.resolve() == expected:
                    tools[-1]['attachment_ids_read'].append(attachment['id'])
                elif name == 'bash':
                    owner = run_owners.get((session_id, run_id))
                    owner_root = settings.workspace_root / private_chats[owner] if owner in private_chats else None
                    paths = [str(expected)]
                    if owner_root and expected.is_relative_to(owner_root):
                        paths.append(expected.relative_to(owner_root).as_posix())
                    if any(path in args.get('command', '') for path in paths):
                        tools[-1]['attachment_paths_mentioned'].append(attachment['id'])
            if name == 'write' and not result.get('isError') and isinstance(args.get('path'), str):
                import hashlib

                from app import personal
                from app.config import settings
                owner = run_owners.get((session_id, run_id))
                path = Path(args['path'])
                if not path.is_absolute() and owner:
                    path = personal.resolve_twin_workspace(owner) / path
                path = path.resolve()
                if path.is_relative_to(settings.workspace_root) and path.is_file():
                    with path.open('rb') as source:
                        checksum = hashlib.file_digest(source, 'sha256').hexdigest()
                    files.append({'path': str(path), 'chat_id': path.relative_to(settings.workspace_root).parts[0], 'size': path.stat().st_size, 'sha256': checksum, 'request_id': request_ids.get((session_id, run_id)), 'run_id': run_id})
    return {'browser_e2e': True, 'e2e_instance_id': os.environ['TWINTEAMS_E2E_INSTANCE_ID'],
            'requests': requests, 'runs': runs, 'context_manifests': manifests, 'model_calls': model_calls, 'dreams': dreams, 'tracking': tracking, 'tracking_revisions': revisions, 'followups': followups, 'files': files, 'approvals': approvals, 'credentials': credentials,
            'tool_counts': dict(Counter(row['name'] for row in tools)), 'tools': tools}



async def presence_fixture():
    """A one-shot read of this test namespace, never a presence override."""
    from datetime import UTC, datetime

    from redis.asyncio import Redis
    client = Redis.from_url(os.environ['TWINTEAMS_REDIS_URL'], decode_responses=True)
    owners = {}
    try:
        prefix = os.environ['TWINTEAMS_REDIS_PREFIX']
        for owner in ('owner-alex', 'owner-maya', 'owner-jordan'):
            devices = await client.smembers(f'{prefix}:devices:{owner}')
            live = []
            for device in devices:
                ttl = await client.ttl(f'{prefix}:presence:{owner}:{device}')
                if ttl > 0:
                    live.append(ttl)
            owners[owner] = {'online': bool(live), 'live_devices': len(live), 'max_ttl_seconds': max(live, default=0)}
    finally:
        await client.aclose()
    return {'browser_e2e': True, 'e2e_instance_id': os.environ['TWINTEAMS_E2E_INSTANCE_ID'],
            'observed_at': datetime.now(UTC).isoformat(), 'owners': owners}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', choices=['init', 'api', 'worker', 'inspect', 'restart-worker', 'presence'])
    parser.add_argument('--port', type=int)
    parser.add_argument('--state', type=Path)
    args = parser.parse_args()
    if args.role in {'inspect', 'restart-worker', 'presence'}:
        if not args.state or args.state.is_symlink() or args.state.stat().st_mode & 0o077:
            raise RuntimeError('Inspect requires its private fixture state file')
        saved = json.loads(args.state.read_text())
        os.environ.clear()
        os.environ.update(saved['env'])
    root = guard()
    if args.state and args.state.resolve().parent != root:
        raise RuntimeError('State file must belong to its disposable root')
    if args.role == 'presence':
        print(json.dumps(asyncio.run(presence_fixture())))
        return
    if args.role == 'restart-worker':
        identifier = uuid4().hex
        control = root / 'restart-worker.json'
        with control.open('x') as target:
            json.dump({'id': identifier, 'instance_id': os.environ['TWINTEAMS_E2E_INSTANCE_ID']}, target)
        result = root / ('restart-' + identifier + '.json')
        deadline = time.monotonic() + 20
        while not result.exists() and time.monotonic() < deadline:
            time.sleep(.1)
        if not result.exists():
            raise RuntimeError('Owned worker restart did not complete')
        print(result.read_text())
        return
    if args.role == 'inspect':
        print(json.dumps(inspect_fixture(root)))
        return
    install_fixture(root)
    if args.role == 'init':
        seed(root)
    elif args.role == 'api':
        if not args.port or not 1 <= args.port <= 65535 or args.port in {8000, 8001}:
            raise RuntimeError('Fixture API must use a non-production loopback port')
        import uvicorn
        uvicorn.run(api_app(), host='127.0.0.1', port=args.port, log_level='warning', ws_max_size=65536, ws_max_queue=64)
    else:
        if os.environ.get('TWINTEAMS_E2E_MODEL_MODE') == 'live-gpt-6-luna':
            from checks.browser_e2e_live import smoke
            asyncio.run(smoke(root))
        runpy.run_module('app.worker', run_name='__main__')


if __name__ == '__main__':
    main()
