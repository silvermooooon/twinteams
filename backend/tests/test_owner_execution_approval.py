"""Exact owner consent reuses real SDK approval facts and disposable product storage."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agent_runtime import AgentTool, AgentToolResult
from agent_runtime.execution import ApprovalRequired
from agent_runtime.sessions import SessionInfo
from agent_runtime.types import AbortSignal
from duet_test_support import request_duet_reply as legacy_request
from duet_test_support import seed_collaborations, send_message
from fastapi import HTTPException
from test_credentials import product as product  # noqa: PLC0414

from app import personal
from app.models import Approval, Message, ReplyRequest
from app.runtime_bridge import RuntimeBridge


def test_group_local_approval_exact_owner_origin_single_use_and_frozen_recovery(product):
    seed_collaborations(product)
    source = send_message(product, 'owner-maya', 'duet-alex-maya', 'Synthetic work', uuid4().hex)['message']
    request = legacy_request(product, 'owner-maya', 'duet-alex-maya', source['id'], 'twin-alex', uuid4().hex)
    context = product.get_runtime_context(request['id'])
    config = {'version': 1, 'workspace': '/synthetic/owner-alex', 'skills': [],
              'credentials': [], 'execution_enabled': True, 'profile': {'soul': 'Synthetic owner'}}
    personal.freeze_execution_config(request['id'], config)
    context.update(execution_config=config, execution_enabled=True)
    calls = []

    async def local(call_id, args, signal, on_update):
        calls.append((call_id, args))
        return AgentToolResult([{'type': 'text', 'text': 'Verified synthetic action'}])

    execution = AgentTool('bash', 'Synthetic installed tool', {
        'type': 'object', 'properties': {'command': {'type': 'string'}},
        'required': ['command'], 'additionalProperties': False}, local, replay='never')
    session_id = product.get_request(request['id'])['session_id']
    session = SimpleNamespace(info=SessionInfo(session_id, 'local', 'owner-alex'),
        session_id=session_id, snapshot={'run_id': 'synthetic-run'}, resumable=False,
        tool_operation_id=lambda call_id: f'synthetic-run:{call_id}')
    runtime = RuntimeBridge(product)

    async def run():
        await runtime.start()
        try:
            tools = {tool.name: tool for tool in runtime._tools(context, session, [execution])}
            create, execute = tools['create_approval'], tools['execute_approved_action']
            assert execute.replay == 'never'
            base = {'title': 'Owner operation', 'description': 'Exact action', 'target': 'Owner workspace', 'impact': 'One action'}
            action = {'tool': 'bash', 'arguments': {'command': 'synthetic exact command'}}
            for invalid in [
                {'tool': 'bash', 'arguments': {'command': 42}},
                {'tool': 'bash', 'arguments': {'command': 'x', 'extra': True}},
                {'tool': 'uninstalled', 'arguments': {}},
            ]:
                with pytest.raises(HTTPException):
                    await create.execute(uuid4().hex, {**base, 'action': invalid}, AbortSignal(), None)
            result = await create.execute('create', {**base, 'action': action}, AbortSignal(), None)
            approval_id = result.details['id']
            stored = product.approval_for_execution('owner-alex', 'duet-alex-maya', approval_id)
            assert stored['action'] == action and calls == []
            with pytest.raises(ApprovalRequired):
                await execute.execute('before-decision', {'id': approval_id}, AbortSignal(), None)
            with pytest.raises(HTTPException):
                product.approval_for_execution('owner-maya', 'duet-alex-maya', approval_id)
            decision = await runtime.decide_approval(stored, 'approved')
            product.accept_approval_decision(approval_id, decision)
            with product.transaction() as db:
                continuation_id = db.get(Approval, approval_id).continuation_request_id
                continued = db.get(ReplyRequest, continuation_id)
                assert continued.requester_id == continued.source_owner_id == 'owner-maya'
                assert continued.source_message_id == source['id']
                assert continued.execution_config == config
                assert continued.causal_chain_id == request['causal_chain_id']
            continuation = {**product.get_runtime_context(continuation_id), 'execution_config': config}
            continuation_tools = {tool.name: tool for tool in runtime._tools(continuation, session, [execution])}
            execute = continuation_tools['execute_approved_action']
            with pytest.raises(HTTPException):
                await execute.execute('override', {'id': approval_id, 'command': 'different'}, AbortSignal(), None)
            mismatched = {**continuation, 'execution_config': {**config, 'workspace': '/different'}}
            bad = next(tool for tool in runtime._tools(mismatched, session, [execution]) if tool.name == 'execute_approved_action')
            with pytest.raises(HTTPException):
                await bad.execute('wrong-config', {'id': approval_id}, AbortSignal(), None)
            other_source = send_message(product, 'owner-maya', 'duet-alex-maya', 'Different synthetic goal', uuid4().hex)['message']
            other = legacy_request(product, 'owner-maya', 'duet-alex-maya', other_source['id'], 'twin-alex', uuid4().hex)
            personal.freeze_execution_config(other['id'], config)
            unrelated = {**product.get_runtime_context(other['id']), 'execution_config': config}
            bad = next(tool for tool in runtime._tools(unrelated, session, [execution]) if tool.name == 'execute_approved_action')
            with pytest.raises(HTTPException):
                await bad.execute('wrong-task', {'id': approval_id}, AbortSignal(), None)
            # New model context must stay authorized even if the original approval sources still are.
            product.get_runtime_context(continuation_id)
            with product.transaction() as db:
                db.get(Message, other_source['id']).state = 'withdrawn'
            with pytest.raises(HTTPException):
                await execute.execute('revoked-current-context', {'id': approval_id}, AbortSignal(), None)
            assert calls == []
            with product.transaction() as db:
                db.get(Message, other_source['id']).state = 'published'
            await execute.execute('once', {'id': approval_id}, AbortSignal(), None)
            assert calls == [('once', action['arguments'])]
            with pytest.raises(ApprovalRequired):
                await execute.execute('twice', {'id': approval_id}, AbortSignal(), None)
            assert len(calls) == 1
            # An old accepted model request cannot acquire newly added action variants.
            old = runtime._tools(context, session)
            session.resumable = True
            session.snapshot['tools'] = [{'name': tool.name, 'description': tool.description,
                'parameters': tool.parameters, 'replay': tool.replay} for tool in old]
            recovered = {tool.name: tool for tool in runtime._tools(context, session, [execution])}
            assert recovered['execute_approved_action'].replay == 'safe'
            with pytest.raises(HTTPException):
                await recovered['create_approval'].execute('old-contract', {**base, 'action': action}, AbortSignal(), None)
            with pytest.raises(HTTPException):
                await recovered['execute_approved_action'].execute('old-execute', {'id': approval_id}, AbortSignal(), None)
            assert len(calls) == 1
        finally:
            await runtime.close()

    asyncio.run(run())
