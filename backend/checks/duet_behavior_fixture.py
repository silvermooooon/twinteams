"""Acceptance-only observation and temporary-file fencing; no scripted model choices."""
import hashlib
import json
import os
import time
from pathlib import Path


def record(root, name, value):
    encoded = json.dumps(value, ensure_ascii=False, default=str)
    for variable in ('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'HTTPS_PROXY'):
        if os.environ.get(variable):
            encoded = encoded.replace(os.environ[variable], '[private-provider-config]')
    with (root / name).open('a') as stream:
        stream.write(encoded + '\n')


def install(root):
    from agent_runtime.types import maybe_await

    from app.runtime_bridge import RuntimeBridge
    from checks.browser_e2e_fixture import text_content
    from checks.browser_e2e_live import PHASE
    original = RuntimeBridge._attach_subagents
    # Native web search executes remotely, outside the local executor boundary.
    # Its omission is an explicit acceptance limitation, not a production change.
    RuntimeBridge._add_web_search = staticmethod(lambda agent: None)

    async def attach(self, agent, context):
        manager = await original(self, agent, context)
        sid = agent.session.session_id
        child = bool(agent.session.subagent_creation)
        identity = {'session_id': sid, 'request_id': context.get('request_id'), 'worker': child}
        stream = agent.stream_function

        async def observed(model, messages, options):
            system = text_content(messages.messages[0])
            user = next((text_content(row) for row in reversed(messages.messages) if row.get('role') == 'user'), '')
            record(root, 'behavior-model-inputs.jsonl', {**identity, 'time': time.time(),
                'system_sha256': hashlib.sha256(system.encode()).hexdigest(), 'system_prompt': system,
                'current_input': user, 'model': model.id})
            marker = PHASE.set(('worker:' if child else 'main:') + sid)
            try:
                return await maybe_await(stream(model, messages, options))
            finally:
                PHASE.reset(marker)
        agent.stream_function = observed
        for tool in agent.state.tools:
            execute, name = tool.execute, tool.name
            declaration = {'name': name, 'description': tool.description, 'parameters': tool.parameters}
            record(root, 'behavior-tool-declarations.jsonl', {**identity, **declaration})

            async def observed_tool(call_id, args, signal=None, on_update=None, *, execute=execute, name=name):
                event = {**identity, 'time': time.time(), 'call_id': call_id, 'tool': name, 'arguments': args}
                record(root, 'behavior-tools.jsonl', {**event, 'phase': 'started'})
                try:
                    if name == 'bash':
                        raise PermissionError('Acceptance boundary denies shell execution; no command ran')
                    if name in {'read', 'write', 'edit'}:
                        target = Path(args['path']).expanduser()
                        if not target.is_absolute():
                            target = Path(context['execution_config']['workspace']) / target
                        if not target.resolve().is_relative_to((root / 'workspaces').resolve()):
                            raise PermissionError('Acceptance file tools are confined to disposable workspaces')
                    result = await execute(call_id, args, signal, on_update)
                    record(root, 'behavior-tools.jsonl', {**event, 'phase': 'completed', 'is_error': result.is_error})
                    return result
                except BaseException as error:
                    record(root, 'behavior-tools.jsonl', {**event, 'phase': 'failed',
                        'error_type': type(error).__name__, 'error': str(error)})
                    raise
            tool.execute = observed_tool
        return manager
    RuntimeBridge._attach_subagents = attach


def seed_materials(root):
    folder = root / 'workspaces' / 'private-alex'
    (folder / 'orders.csv').write_text(
        'id,customer,month,amount,status\n'
        'A01,North,2026-09,120,paid\nA02,South,2026-09,80,paid\n'
        'A02,South,2026-09,80,paid\nA03,North,2026-09,50,cancelled\n'
        'A04,South,2026-10,200,paid\nA05,North,2026-10,150,paid\n'
        'A06,West,2026-10,70,paid\nA07,North,2026-10,30,refunded\n'
        'A08,South,2026-09,20,paid\nA09,West,2026-09,40,paid\n'
        'A10,North,2026-09,110,paid\n')
    (folder / 'rules.md').write_text(
        '# Reconciliation rules\nOnly paid orders count toward receipts. '
        'Exact duplicate IDs count once. Cancelled and refunded rows are excluded, '
        'not counted as negative receipts. Keep the order IDs for traceability.\n')
    (folder / 'draft.md').write_text(
        '# Unverified draft\nSeptember receipts: 450. October receipts: 450. '
        'Total receipts: 900. North total: 400. These claims need verification.\n')


def snapshot(root):
    from sqlalchemy import select, text

    from app.models import MessageReaction, PendingChildResult, ProductOperation
    from app.service import service
    with service.transaction() as db:
        def fields(row, names):
            return {name: getattr(row, name) for name in names.split()}
        value = {
            'reactions': [fields(row, 'message_id actor_id emoji') for row in db.scalars(select(MessageReaction))],
            'pending_children': [fields(row, 'id parent_request_id state accepted_request_id') for row in db.scalars(select(PendingChildResult))],
            'operations': [fields(row, 'operation_id name chat_id') for row in db.scalars(select(ProductOperation))],
            'sdk_commands': [dict(row) for row in db.execute(text('SELECT command_id,session_id,run_id,state FROM agent_commands')).mappings()],
            'sdk_runs': [dict(row) for row in db.execute(text('SELECT session_id,run_id,status FROM agent_runs')).mappings()],
        }
    value['tool_events'] = [json.loads(line) for line in (root / 'behavior-tools.jsonl').read_text().splitlines()] if (root / 'behavior-tools.jsonl').exists() else []
    value['files'] = {str(path.relative_to(root / 'workspaces')): path.read_text()
        for path in (root / 'workspaces' / 'private-alex').iterdir()
        if path.is_file() and path.name != 'SOUL.md' and path.suffix in {'.md', '.csv'}}
    value['live_budget'] = json.loads((root / 'live-budget.json').read_text()) if (root / 'live-budget.json').exists() else None
    return value
