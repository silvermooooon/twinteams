"""Disposable integrated Duet acceptance. Offline by default; no production state.

Launch with --serve, then use the printed loopback URL for real CUA journeys.
The isolated provider executes real SDK tool calls and scripted model decisions;
it is not evidence of natural-language model quality. --live requires a separate
explicit --live-approved flag, uses the existing bounded Responses guard (30 HTTP
attempts maximum), and never silently falls back or retries provider errors.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import runpy
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / 'checks'))


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        value = sock.getsockname()[1]
    return port() if value in {8000, 8001} else value


def install_offline(root):
    from agent_runtime import AssistantMessageEventStream, assistant_message
    from agent_runtime.transcript import current_tools

    from app.runtime_bridge import RuntimeBridge
    from checks.browser_e2e_fixture import text_content
    original = RuntimeBridge.__init__

    async def offline(model, context, options):
        users = [i for i, item in enumerate(context.messages) if item.get('role') == 'user']
        position = users[-1] if users else 0
        user = context.messages[position]
        text, system = text_content(user), text_content(context.messages[0])
        results = [item for item in context.messages[position + 1:] if item.get('role') == 'toolResult']
        tools = {item['name'] for item in current_tools(context.messages)}
        tool_names = [item.get('toolName') for item in results]
        name, args, info = None, None, {}
        if system.startswith("Choose which participant owners'"):
            payload = json.loads(text)
            message = payload['message']; body = message.get('body', '')
            allowed = json.loads(system.rsplit('Allowed owners: ', 1)[1])
            author = message.get('author_owner_id') or message.get('source_owner_id') or message.get('author_id')
            peers = [owner for owner in allowed if owner != author]
            if body.startswith('ACCEPTANCE ROUTE_FAIL'):
                marker = root / ('route-fail-once-' + message['id'])
                if not marker.exists():
                    marker.write_text('intentional isolated route failure')
                    raise RuntimeError('Acceptance intentional classifier failure')
            needs = body.startswith(('ACCEPTANCE QUESTION', 'ACCEPTANCE CHAIN', 'ACCEPTANCE REPORTBACK', 'ACCEPTANCE SLOW', 'ACCEPTANCE ASKOWNER'))
            final = json.dumps({'target_owner_ids': peers[:1] if needs else [], 'needs_response': needs, 'reason': 'Deterministic approved-conversation acceptance scenario'})
            phase = 'routing'
        else:
            def details():
                from app.models import Actor, Message, ReplyRequest
                from app.service import service
                with service.transaction() as db:
                    request = db.get(ReplyRequest, user.get('queueId', ''))
                    if not request:
                        return {}
                    source = db.get(Message, request.source_message_id) if request.source_message_id else None
                    return {'request_id': request.id, 'source': request.source, 'chat_id': request.chat_id,
                            'owner_id': db.get(Actor, request.target_twin_id).owner_id,
                            'session_id': request.session_id, 'continuation_of_request_id': getattr(request, 'continuation_of_request_id', None),
                            'background': getattr(request, 'background', False),
                            'body': request.input_body if request.source == 'followup_result' else source.body if source else request.input_body or ''}
            info = await asyncio.to_thread(details)
            body = info.get('body', text)
            if info.get('source') in {'scheduled_task', 'system_review'}:
                try: body = json.loads(body).get('instructions', body)
                except (ValueError, TypeError): pass
            if (root / ('hold-provider-source-' + info.get('source', 'unknown'))).exists():
                body = 'CONCURRENCY HOLD ' + info['source']
            phase = 'reply'
            if info.get('request_id'):
                marker = root / ('provider-start-' + info['request_id'] + '.json')
                if not marker.exists(): marker.write_text(json.dumps(info))
            final = 'Acceptance private reply.'
            if info.get('source') == 'question_result' and body.startswith('CONCURRENCY QUESTION'):
                final = 'Completed question continuation: Blue'
            elif body.startswith('CONCURRENCY HOLD'):
                release = root / ('release-slow-provider-' + info['request_id'])
                (root / ('slow-provider-' + info['request_id'])).write_text('explicit offline concurrency barrier')
                deadline = asyncio.get_running_loop().time() + 300
                while not release.exists():
                    if asyncio.get_running_loop().time() > deadline: raise RuntimeError('Controlled concurrency barrier timed out')
                    await asyncio.sleep(.1)
                final = 'Completed held task: ' + body.removeprefix('CONCURRENCY HOLD').strip()
            elif body.startswith('CONCURRENCY QUESTION'):
                if 'ask_question' not in tool_names:
                    name, args = 'ask_question', {'question': 'Choose the fixture color for this task.', 'options': ['Blue', 'Green']}
                else:
                    (root / ('slow-provider-' + info['request_id'])).write_text('question published; origin still running')
                    release = root / ('release-slow-provider-' + info['request_id'])
                    deadline = asyncio.get_running_loop().time() + 300
                    while not release.exists():
                        if asyncio.get_running_loop().time() > deadline: raise RuntimeError('Question origin barrier timed out')
                        await asyncio.sleep(.1)
                final = ''
            elif body.startswith('CONCURRENCY FAIL_ONCE'):
                marker = root / ('failed-once-' + (info.get('continuation_of_request_id') or info['request_id']))
                if not marker.exists():
                    marker.write_text('isolated intentional failure')
                    raise RuntimeError('Controlled concurrency task failure')
                final = 'Recovered fixture task.'
            elif body.startswith('CONCURRENCY COMPLETE'):
                final = 'Completed independent task: ' + body.removeprefix('CONCURRENCY COMPLETE').strip()
            elif 'ACCEPTANCE PROPOSE ' in body:
                payload = json.loads(body.split('ACCEPTANCE PROPOSE ', 1)[1])
                if 'request_duet_conversation' not in tool_names:
                    name, args = 'request_duet_conversation', payload
                final = 'The Duet conversation is requested. Each owner can approve it in their private inbox or Duets sidebar.'
            elif info.get('source') == 'followup_result':
                final = 'Coordination summary for the initiating owner: the supplied meeting time works.'
            elif body.startswith('ACCEPTANCE ASKOWNER'):
                if 'ask_question' not in tool_names:
                    name, args = 'ask_question', {'question': 'Which supplied meeting time should I use?', 'options': ['Tuesday at 14:30', 'Wednesday at 15:00']}
                final = ''
            elif body.startswith('ACCEPTANCE REPORTBACK'):
                final = 'ACCEPTANCE QUESTION: The supplied time works; please confirm this to your owner.'
            elif body.startswith('ACCEPTANCE SLOW'):
                (root / ('slow-provider-' + info['request_id'])).write_text('isolated in-flight provider marker')
                release = root / ('release-slow-provider-' + info['request_id'])
                deadline = asyncio.get_running_loop().time() + 120
                while not release.exists():
                    if asyncio.get_running_loop().time() > deadline: raise RuntimeError('Controlled provider release timed out')
                    await asyncio.sleep(.1)
                final = 'The slow coordination response is complete.'
            elif body.startswith('ACCEPTANCE CHAIN'):
                number = int(body.split()[-1]) + 1
                final = f'ACCEPTANCE CHAIN {number}' if number < 12 else 'The coordination is complete. Thank you.'
            elif body.startswith('ACCEPTANCE QUESTION'):
                if 'send_chat_message' not in tool_names:
                    name, args = 'send_chat_message', {'chat_id': 'private-' + info['owner_id'].removeprefix('owner-'), 'body': 'Coordination report: the supplied meeting time works.', 'source_message_ids': []}
                final = 'The supplied meeting time works. Thank you.'
            elif 'Approval' in body:
                final = ''
            if name and name not in tools:
                raise RuntimeError('Acceptance provider requested an unavailable tool: ' + name)
            if results and results[-1].get('isError'):
                raise RuntimeError('Acceptance tool failed: ' + str(results[-1].get('toolName')))
        content = [{'type': 'toolCall', 'id': f'acceptance-{len(results)}-{name}', 'name': name, 'arguments': args}] if name else [{'type': 'text', 'text': final}]
        with (root / 'provider-calls.jsonl').open('a') as target:
            target.write(json.dumps({'phase': phase, 'tool': name, **{key: info.get(key) for key in ('request_id', 'source', 'chat_id', 'owner_id', 'session_id', 'continuation_of_request_id', 'background')}}) + '\n')
        stream = AssistantMessageEventStream()
        stream.push({'type': 'done', 'message': assistant_message(model, content=content, stopReason='toolUse' if name else 'stop')})
        return stream

    def initialize(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.models.register_api('browser-e2e-offline', offline)
    RuntimeBridge.__init__ = initialize


def snapshot(root):
    from sqlalchemy import select

    from app.models import (
        Approval,
        CausalChain,
        Chat,
        DuetContactGrant,
        DuetConversation,
        Membership,
        Message,
        ReplyRequest,
        RoutingDecision,
        TwinFollowup,
    )
    from app.service import service
    with service.transaction() as db:
        fields = lambda item, names: {name: (getattr(item, name).isoformat() if hasattr(getattr(item, name), 'isoformat') else getattr(item, name)) for name in names.split()}
        return {
            'chats': [fields(item, 'id kind title') for item in db.scalars(select(Chat))],
            'conversations': [fields(item, 'chat_id origin_chat_id status close_reason') for item in db.scalars(select(DuetConversation))],
            'memberships': [fields(item, 'chat_id owner_id') for item in db.scalars(select(Membership))],
            'messages': [fields(item, 'id chat_id seq author_id kind body request_id reply_to_message_id') for item in db.scalars(select(Message).order_by(Message.created_at))],
            'requests': [fields(item, 'id chat_id target_twin_id source status cancel_requested session_id run_id source_message_id accepted_at error background continuation_of_request_id') for item in db.scalars(select(ReplyRequest))],
            'routes': [fields(item, 'id message_id status needs_response') for item in db.scalars(select(RoutingDecision))],
            'approvals': [fields(item, 'id chat_id approver_id status sdk_reference') for item in db.scalars(select(Approval))],
            'followups': [fields(item, 'id owner_id chat_id target_chat_id status notification_state continuation_request_id due_at') for item in db.scalars(select(TwinFollowup))],
            'grants': [fields(item, 'owner_id peer_owner_id') for item in db.scalars(select(DuetContactGrant))],
            'chains': [fields(item, 'id spent allowance') for item in db.scalars(select(CausalChain))],
            'provider_starts': [json.loads(path.read_text()) for path in root.glob('provider-start-*.json')],
            'provider_calls': [json.loads(line) for line in (root / 'provider-calls.jsonl').read_text().splitlines()] if (root / 'provider-calls.jsonl').exists() else [],
        }


def verify_concurrency_stage(value, stage):
    requests = {row['id']: row for row in value['requests']}
    messages = {row['id']: row for row in value['messages']}
    def by_body(body):
        source = next(row for row in messages.values() if row['kind'] == 'human' and row['body'] == body)
        return next(row for row in requests.values() if row['source_message_id'] == source['id'] and not row['continuation_of_request_id'])
    def output(request):
        return [row for row in messages.values() if row.get('request_id') == request['id'] and row['kind'] == 'twin']
    if stage == 'concurrent':
        first, fast = by_body('CONCURRENCY HOLD first'), by_body('CONCURRENCY COMPLETE fast')
        assert first['status'] == 'running' and fast['status'] == 'completed'
        assert first['session_id'] and fast['session_id'] and first['session_id'] != fast['session_id']
        assert first['chat_id'] == fast['chat_id'] and not output(first) and len(output(fast)) == 1
    elif stage == 'cancel-isolation':
        survivor, stopped = by_body('CONCURRENCY HOLD survivor'), by_body('CONCURRENCY HOLD cancel-me')
        assert survivor['status'] == 'running' and not survivor['cancel_requested']
        assert stopped['status'] == 'cancelled' or stopped['cancel_requested']
        assert not output(stopped) and survivor['session_id'] != stopped['session_id']
    elif stage == 'cancel-finished':
        survivor, stopped = by_body('CONCURRENCY HOLD survivor'), by_body('CONCURRENCY HOLD cancel-me')
        assert survivor['status'] == 'completed' and len(output(survivor)) == 1
        assert stopped['status'] == 'cancelled' and not output(stopped)
    elif stage == 'background-overlap':
        background = next(row for row in reversed(list(requests.values())) if row['background'] and row['status'] == 'running')
        fast = by_body('CONCURRENCY COMPLETE during-background')
        assert fast['status'] == 'completed' and len(output(fast)) == 1
        assert background['chat_id'] == fast['chat_id'] and background['session_id'] != fast['session_id']
        assert not fast['background'] and not output(background)
    elif stage == 'retry-isolation':
        failed = by_body('CONCURRENCY FAIL_ONCE retry-task')
        retry = next(row for row in requests.values() if row['continuation_of_request_id'] == failed['id'])
        held = by_body('CONCURRENCY HOLD beside-retry')
        assert failed['status'] == 'failed' and retry['status'] == 'completed'
        assert retry['session_id'] == failed['session_id'] and len(output(retry)) == 1
        assert held['status'] == 'running' and not held['cancel_requested']
        assert held['session_id'] != retry['session_id']
    elif stage == 'parallel-results':
        private = by_body('CONCURRENCY HOLD simultaneous-private')
        background = next(row for row in requests.values() if row['source'] == 'system_review' and row['status'] == 'completed')
        assert private['status'] == 'completed' and len(output(private)) == len(output(background)) == 1
        assert output(private)[0]['body'] == 'Completed held task: simultaneous-private'
        assert output(background)[0]['body'] == 'Completed held task: system_review'
        assert output(private)[0]['seq'] != output(background)[0]['seq']
    elif stage == 'question-wait':
        root = by_body('CONCURRENCY QUESTION color-task')
        children = [row for row in requests.values() if row['continuation_of_request_id'] == root['id']]
        fast = by_body('CONCURRENCY COMPLETE beside-question-fast')
        assert root['status'] == 'running' and fast['status'] == 'completed'
        assert children and all(row['status'] == 'queued' and row['session_id'] == root['session_id'] for row in children)
        assert not any(row['request_id'] in {child['id'] for child in children} for row in value['provider_starts'])
    elif stage == 'continuation':
        root = by_body('CONCURRENCY QUESTION color-task')
        children = [row for row in requests.values() if row['continuation_of_request_id'] == root['id']]
        assert children and any(row['source'] == 'question_result' and row['status'] == 'completed' for row in children)
        assert all(row['session_id'] == root['session_id'] for row in children)
        unrelated = by_body('CONCURRENCY HOLD beside-question')
        assert unrelated['status'] == 'running' and unrelated['session_id'] != root['session_id']
    return {'stage': stage, 'verified': True, 'snapshot': value}


def verify_stage(value, stage):
    if stage in {'concurrent', 'cancel-isolation', 'background-overlap', 'continuation', 'cancel-finished', 'question-wait', 'parallel-results', 'retry-isolation'}:
        return verify_concurrency_stage(value, stage)
    groups = {chat['id'] for chat in value['chats'] if chat['kind'] == 'group'}
    group_messages = [message for message in value['messages'] if message['chat_id'] in groups]
    assert len(group_messages) >= 2 and all(message['kind'] == 'human' for message in group_messages)
    assert not any(request['chat_id'] in groups for request in value['requests'])
    group_message_ids = {message['id'] for message in group_messages}
    assert not any(route['message_id'] in group_message_ids for route in value['routes'])
    titles = {chat['id']: chat['title'] for chat in value['chats']}
    conversations = {titles[row['chat_id']]: row for row in value['conversations']}
    if stage == 'human':
        assert not value['provider_calls'] and not value['requests'] and not value['routes']
    elif stage == 'pending':
        pending = [row for row in value['conversations'] if row['status'] == 'pending']
        assert pending
        for row in pending:
            assert not any(message['chat_id'] == row['chat_id'] for message in value['messages'])
            assert not any(request['chat_id'] == row['chat_id'] for request in value['requests'])
        assert any(call['tool'] == 'request_duet_conversation' for call in value['provider_calls'])
    elif stage == 'directional':
        assert value['grants'] == [{'owner_id': 'owner-maya', 'peer_owner_id': 'owner-alex'}]
        assert any(row['status'] == 'pending' for row in value['conversations'])
    elif stage == 'expanded':
        row = conversations['Team new recipient consent']
        assert row['status'] == 'pending'
        approvals = [approval for approval in value['approvals'] if approval['chat_id'] == row['chat_id']]
        assert len(approvals) == 3 and all(approval['status'] == 'pending' for approval in approvals)
        assert any(chain['spent'] == 12 and chain['allowance'] == 12 for chain in value['chains'])
    elif stage == 'final':
        assert conversations['Pair meeting once']['status'] == 'active'
        assert conversations['Team new recipient consent']['status'] == 'active'
        assert conversations['Pair trusted access']['close_reason'] == 'authorization_revoked'
        assert conversations['Trusted chain six replies']['status'] == 'closed'
        assert conversations['Private renewed consent']['status'] == 'rejected'
        assert conversations['Private renewed consent']['origin_chat_id'] == 'private-alex'
        assert value['grants'] == [{'owner_id': 'owner-maya', 'peer_owner_id': 'owner-alex'}]
        assert not any(request['status'] in {'running', 'queued'} for request in value['requests'])
    return {'stage': stage, 'passed': True, 'model_provider': 'scripted offline' if value['provider_calls'] else 'not called',
            'provider_decisions': len(value['provider_calls']), 'human_chat_requests': 0, 'human_chat_routes': 0}


def child(args):
    from checks.browser_e2e_fixture import api_app, guard, install_fixture, seed
    if args.state:
        if args.state.is_symlink() or args.state.stat().st_mode & 0o077:
            raise RuntimeError('Acceptance state must be a private fixture file')
        saved = json.loads(args.state.read_text()); os.environ.clear(); os.environ.update(saved['env'])
    root = guard()
    if args.role == 'seed-background':
        from sqlalchemy import select

        from app.models import ScheduledTask, now
        from app.scheduled_tasks import enqueue, ensure_system, mutate
        from app.service import service
        (root / ('hold-provider-source-' + args.task_source)).write_text('explicit offline fixture barrier')
        with service.transaction() as db:
            if args.task_source == 'system_review':
                ensure_system(service, db, args.owner_id)
                row = db.scalar(select(ScheduledTask).where(ScheduledTask.owner_id == args.owner_id, ScheduledTask.kind == 'system_review'))
            else:
                result = mutate(service, db, args.owner_id, values={'title': 'Concurrency fixture schedule', 'instructions': 'CONCURRENCY HOLD scheduled_task', 'timezone': 'UTC', 'schedule': {'kind': 'interval', 'seconds': 86400}, 'enabled': False})
                row = db.get(ScheduledTask, result['id'])
            request = enqueue(service, db, row, now(), now(), manual_key='offline-concurrency-' + uuid4().hex)
            identifier = request.id
        print(json.dumps({'request_id': identifier, 'source': args.task_source, 'fixture_only': True})); return
    if args.role == 'release-provider':
        if os.environ.get('TWINTEAMS_E2E_MODEL_MODE') == 'live-gpt-6-luna': raise RuntimeError('Release controls are offline-only')
        from sqlalchemy import select

        from app.models import ReplyRequest
        from app.service import service
        with service.transaction() as db:
            ids = list(db.scalars(select(ReplyRequest.id).where(ReplyRequest.id.in_(args.request_id.split(',')) if args.request_id else ReplyRequest.chat_id == args.chat_id)))
        released = 0
        for identifier in ids:
            if (root / ('slow-provider-' + identifier)).exists():
                (root / ('release-slow-provider-' + identifier)).write_text('operator released after UI revocation'); released += 1
        if not released: raise RuntimeError('Selected disposable conversation has no started provider marker')
        print(json.dumps({'selected_chat_id': args.chat_id, 'released_provider_turns': released})); return
    if args.role == 'expire-followup':
        from datetime import timedelta

        from sqlalchemy import select

        from app.models import TwinFollowup, now
        from app.service import service
        if not args.chat_id: raise RuntimeError('A selected fixture Duet chat ID is required')
        with service.transaction() as db:
            rows = list(db.scalars(select(TwinFollowup).where(TwinFollowup.target_chat_id == args.chat_id, TwinFollowup.status == 'waiting')))
            if not rows: raise RuntimeError('No waiting followup in the selected disposable conversation')
            for row in rows: row.due_at = now() - timedelta(seconds=1)
        service.process_followups()
        print(json.dumps({'selected_chat_id': args.chat_id, 'followup_deadlines_advanced': len(rows), 'test_only_time_seed': True})); return
    if args.role == 'inspect':
        value = snapshot(root)
        print(json.dumps(verify_stage(value, args.verify_stage) if args.verify_stage else value)); return
    install_fixture(root)
    if os.environ.get('TWINTEAMS_E2E_MODEL_MODE') != 'live-gpt-6-luna':
        install_offline(root)
    if args.role == 'init':
        seed(root)
    elif args.role == 'api':
        if not args.port or args.port in {8000, 8001}:
            raise RuntimeError('Acceptance API requires an isolated nonproduction port')
        import uvicorn
        uvicorn.run(api_app(), host='127.0.0.1', port=args.port, log_level='warning')
    elif args.role == 'worker':
        # Explicit live guard is installed by install_fixture; no smoke request is
        # sent automatically. Every actual SDK attempt consumes the shared budget.
        runpy.run_module('app.worker', run_name='__main__')


async def launch(args):
    import httpx
    from cryptography.fernet import Fernet
    from redis.asyncio import Redis

    from checks.local_stack import environment
    from checks.postgres import TestDatabase
    if args.live and not args.live_approved:
        raise RuntimeError('Live model work requires explicit root GO and --live-approved')
    if args.live and args.max_attempts > 30:
        raise RuntimeError('Acceptance campaign permits at most 30 HTTP attempts')
    private = environment('api')
    prior = os.environ.get('TWINTEAMS_TEST_DB_URL'); os.environ['TWINTEAMS_TEST_DB_URL'] = private['DATABASE_URL']
    try:
        database = TestDatabase()
    finally:
        if prior is None: os.environ.pop('TWINTEAMS_TEST_DB_URL', None)
        else: os.environ['TWINTEAMS_TEST_DB_URL'] = prior
    nonce = uuid4().hex; prefix = 'browser-e2e-' + nonce
    root = Path(tempfile.mkdtemp(prefix='twinteams-browser-e2e-')); root.chmod(0o700)
    artifacts = args.artifact_dir.resolve(); artifacts.mkdir(parents=True, exist_ok=True); artifacts.chmod(0o700)
    sources = [*BACKEND.joinpath('app').rglob('*.py'), *BACKEND.parent.joinpath('frontend/src').rglob('*.ts*'), *BACKEND.parent.joinpath('frontend/src').rglob('*.css')]
    (artifacts / 'start-source-hashes.json').write_text(json.dumps({str(path.relative_to(BACKEND.parent)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}, indent=2))
    (root / '.fixture').write_text(nonce); (root / 'gitconfig').write_text('')
    for folder in ('tmp', 'workspaces', 'memory', 'secrets'): (root / folder).mkdir(mode=0o700)
    # Preserve the reviewed candidate independently from production frontend/dist.
    shutil.copytree(args.dist.resolve(), root / 'dist')
    env = {name: os.environ[name] for name in ('PATH', 'HOME', 'LANG', 'LC_ALL', 'SYSTEMROOT') if name in os.environ}
    env.update(PYTHONPATH=str(BACKEND), PYTHONUNBUFFERED='1', TMPDIR=str(root / 'tmp'), GIT_CONFIG_GLOBAL=str(root / 'gitconfig'), GIT_CONFIG_NOSYSTEM='1', NO_PROXY='127.0.0.1,localhost,::1',
        TWINTEAMS_ENV='local', TWINTEAMS_DEVELOPMENT_MODE='true', TWINTEAMS_TENANT_ID='browser-e2e', TWINTEAMS_BROWSER_E2E='true', TWINTEAMS_E2E_INSTANCE_ID=nonce,
        TWINTEAMS_E2E_ROOT=str(root), TWINTEAMS_E2E_DIST=str(root / 'dist'), TWINTEAMS_E2E_ROLE='init', TWINTEAMS_E2E_SCENARIO='duet-collaboration',
        TWINTEAMS_E2E_MODEL_MODE='live-gpt-6-luna' if args.live else 'deterministic-offline', DATABASE_URL=database.url, AGENT_DB_URL=database.url,
        TWINTEAMS_REDIS_URL=private['TWINTEAMS_REDIS_URL'], REDIS_URL=private['TWINTEAMS_REDIS_URL'], TWINTEAMS_REDIS_PREFIX=prefix, TWINTEAMS_COMPENSATION_SECONDS='2',
        TWINTEAMS_WORKSPACE_ROOT=str(root / 'workspaces'), AGENT_MEMORY_DIR=str(root / 'memory'), AGENT_SECRETS_DIR=str(root / 'secrets'), AGENT_SECRETS_KEY=Fernet.generate_key().decode(),
        TWINTEAMS_ONBOARDING_ENABLED='false', AGENT_PROVIDER='openai', AGENT_API='openai-responses' if args.live else 'browser-e2e-offline', AGENT_MODEL='gpt-6-luna', AGENT_MAX_TOKENS='4096',
        OPENAI_API_KEY='explicit-offline-not-a-provider-key')
    live = {}
    if args.live:
        from checks.browser_e2e_live import configuration, initialize_budget
        live = configuration(); initialize_budget(root, max_attempts=args.max_attempts); env.pop('OPENAI_API_KEY')
    state = root / 'state.json'; state.write_text(json.dumps({'env': env, 'root': str(root)})); state.chmod(0o600)
    processes, handles = [], []
    def spawn(role, port_number=None):
        log = (artifacts / f'{role}-{port_number or 0}.log').open('w'); os.chmod(log.name, 0o600); handles.append(log)
        command = [sys.executable, '-u', str(Path(__file__).resolve()), '--role', role]
        if port_number: command += ['--port', str(port_number)]
        child_env = {**env, 'TWINTEAMS_E2E_ROLE': role, **(live if role == 'worker' else {})}
        process = subprocess.Popen(command, cwd=BACKEND, env=child_env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        processes.append(process); return process
    try:
        init = spawn('init')
        if await asyncio.to_thread(init.wait, 45): raise RuntimeError('Acceptance init failed; inspect private init log')
        p = port(); url = f'http://127.0.0.1:{p}'; env['TWINTEAMS_CORS_ORIGINS'] = url
        state.write_text(json.dumps({'env': env, 'root': str(root)})); state.chmod(0o600)
        spawn('worker'); spawn('api', p)
        async with httpx.AsyncClient(trust_env=False) as client:
            for _ in range(200):
                if any(process.poll() not in {None, 0} for process in processes): raise RuntimeError('Acceptance child exited; inspect isolated logs')
                try:
                    response = await client.get(url + '/health'); value = response.json()
                    if response.status_code == 200 and value.get('e2e_instance_id') == nonce: break
                except (httpx.HTTPError, ValueError): pass
                await asyncio.sleep(.15)
            else: raise RuntimeError('Acceptance readiness timed out')
        public = {'url': url, 'instance_id': nonce, 'namespace': prefix, 'state': str(state), 'artifacts': str(artifacts), 'mode': env['TWINTEAMS_E2E_MODEL_MODE'], 'max_http_attempts': args.max_attempts if args.live else 0, 'launcher_pid': os.getpid(), 'child_pids': [process.pid for process in processes if process.poll() is None]}
        (artifacts / 'instance.json').write_text(json.dumps(public, indent=2)); print(json.dumps({'status': 'ready', **public}), flush=True)
        if args.serve:
            task = asyncio.current_task(); loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM): loop.add_signal_handler(sig, task.cancel)
            await asyncio.Event().wait()
    finally:
        for process in processes:
            if process.poll() is None:
                try: os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError: pass
        for process in processes:
            try: await asyncio.to_thread(process.wait, 8)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL); await asyncio.to_thread(process.wait, 5)
        for handle in handles: handle.close()
        for name in ('provider-calls.jsonl', 'live-budget.json'):
            if (root / name).exists(): shutil.copyfile(root / name, artifacts / name); (artifacts / name).chmod(0o600)
        redis = Redis.from_url(private['TWINTEAMS_REDIS_URL'])
        keys = [key async for key in redis.scan_iter(match=prefix + ':*')]
        if keys: await redis.unlink(*keys)
        await redis.aclose(); database.close(); shutil.rmtree(root)
        print(json.dumps({'cleanup': 'complete', 'owned_schema_removed': True, 'owned_redis_keys_removed': len(keys)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role', choices=['launch', 'init', 'api', 'worker', 'inspect', 'expire-followup', 'release-provider', 'seed-background'], default='launch')
    parser.add_argument('--verify-stage', choices=['human', 'pending', 'directional', 'expanded', 'final', 'concurrent', 'cancel-isolation', 'background-overlap', 'continuation', 'cancel-finished', 'question-wait', 'parallel-results', 'retry-isolation'])
    parser.add_argument('--task-source', choices=['scheduled_task', 'system_review'], default='scheduled_task'); parser.add_argument('--owner-id', default='owner-alex'); parser.add_argument('--request-id')
    parser.add_argument('--chat-id'); parser.add_argument('--port', type=int); parser.add_argument('--state', type=Path)
    parser.add_argument('--serve', action='store_true'); parser.add_argument('--live', action='store_true'); parser.add_argument('--live-approved', action='store_true')
    parser.add_argument('--max-attempts', type=int, default=30); parser.add_argument('--dist', type=Path, default=Path('/tmp/duet-collab-dist'))
    parser.add_argument('--artifact-dir', type=Path, default=Path('/tmp/duet-collaboration-acceptance-offline'))
    args = parser.parse_args()
    if args.role != 'launch': child(args)
    else:
        try: asyncio.run(launch(args))
        except asyncio.CancelledError: pass

if __name__ == '__main__': main()
