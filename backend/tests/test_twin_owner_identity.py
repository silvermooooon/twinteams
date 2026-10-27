"""Target Twin identity wins over the participant asking it to work; offline only."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agent_runtime import AssistantMessageEventStream, assistant_message
from agent_runtime.memory import MemorySource
from agent_runtime.sessions import DatabaseSession, SessionInfo
from duet_test_support import request_duet_reply as legacy_request
from duet_test_support import send_message
from test_credentials import product as product  # noqa: PLC0414

from app import credentials, personal, twin_execution
from app import service as service_module
from app.runtime_bridge import RuntimeBridge


@pytest.mark.parametrize('chat_id', ['private', 'duet-alex-maya', 'duet-team'])
@pytest.mark.parametrize(('target_owner', 'requester'), [
    ('owner-alex', 'owner-maya'), ('owner-maya', 'owner-alex'),
])
def test_other_participant_uses_target_twin_profile_skills_and_sdk_identity(
        product, tmp_path, monkeypatch, target_owner, requester, chat_id):
    from duet_test_support import seed_collaborations
    seed_collaborations(product)
    monkeypatch.setenv('AGENT_MEMORY_DIR', str(tmp_path / 'memory'))
    backend_class = twin_execution.TwinExecutionBackend
    monkeypatch.setattr(twin_execution, 'TwinExecutionBackend',
        lambda identity, workspace: backend_class(identity, workspace, directory=tmp_path / 'execution'))
    workspace_root = tmp_path / 'workspaces'
    monkeypatch.setattr(personal, 'settings', replace(personal.settings, workspace_root=workspace_root))
    monkeypatch.setattr(service_module, 'settings', replace(service_module.settings, workspace_root=workspace_root))
    markers = {}
    for owner_id in (target_owner, requester):
        marker = f'SYNTHETIC_PROFILE_{owner_id}'
        markers[owner_id] = marker
        initial = personal.get_soul(owner_id)
        personal.write_soul(owner_id, marker, initial['version'])
        skill_name = f'{owner_id}-skill'
        skill_path = personal.resolve_twin_workspace(owner_id) / 'skills' / skill_name
        skill_path.mkdir(parents=True)
        (skill_path / 'SKILL.md').write_text(
            f'---\nname: {skill_name}\ndescription: {marker} skill\n---\n{marker} instructions.\n')
        with product.transaction() as db:
            product.twin(db, owner_id).enabled_skill_names = [skill_name]

    is_private = chat_id == 'private'
    if is_private:
        chat_id = 'private-' + target_owner.removeprefix('owner-')
    source_owner = target_owner if is_private else requester
    sent = send_message(product, source_owner, chat_id, 'Synthetic participant request', uuid4().hex)
    source = sent['message']
    with product.transaction() as db:
        target_twin = product.twin(db, target_owner).id
    request = sent['request'] if is_private else legacy_request(product, requester, chat_id, source['id'], target_twin, uuid4().hex)
    context = product.get_runtime_context(request['id'])
    assert context['owner_id'] == target_owner and context['requester_id'] == source_owner
    assert context['source_owner_id'] == source_owner

    async def run():
        for owner in (target_owner, requester):
            identity = SessionInfo(f'synthetic-{owner}', 'local', owner)
            await credentials.make_secret_store(identity).put('test-owner', {'FAKE_OWNER_VALUE': owner})
            await personal.make_memory_store(identity).write('MEMORY', f'OWNER_MEMORY_{owner}',
                sources=[MemorySource(identity.session_id, 'synthetic-event')], expected_version=None)
        session = SimpleNamespace(info=SessionInfo('owner-assembly', 'local', target_owner),
                                  subagent_creation=None, resumable=False)
        tools, assembled, memory = await twin_execution.assemble(product, session, context)
        frozen = assembled['execution_config']
        assert frozen['workspace'] == str(personal.resolve_twin_workspace(target_owner))
        assert frozen['profile']['soul'] == markers[target_owner]
        assert [item['name'] for item in frozen['skills']] == [f'{target_owner}-skill']
        assert assembled['execution_enabled'] and [row['ref'] for row in frozen['credentials']] == ['test-owner']
        assert memory['content'] == f'OWNER_MEMORY_{target_owner}'
        names = {tool.name for tool in tools}
        assert {'load_skill', 'bash', 'read', 'write', 'edit', 'memory_read', 'memory_write', 'memory_delete'} <= names
        read_memory = next(tool for tool in tools if tool.name == 'memory_read')
        read_result = await read_memory.execute('read-memory', {'key': 'MEMORY'})
        assert read_result.details['memory']['entry']['content'] == f'OWNER_MEMORY_{target_owner}'
        backend = backend_class(session.info, frozen['workspace'], directory=tmp_path / 'execution')
        bash = next(tool for tool in tools if tool.name == 'bash')
        result = await bash.execute('owner-cwd', {'command': 'pwd; printf "\\n%s\\n" "$HOME"; test "$FAKE_OWNER_VALUE" = ' + target_owner + ' && printf owner-binding-ok', 'credentials': ['test-owner']})
        output = str(result.content)
        assert frozen['workspace'] in output and str(backend.home) in output and 'owner-binding-ok' in output
        assert str(personal.resolve_twin_workspace(requester)) not in output
        child = SimpleNamespace(info=SessionInfo('owner-child', 'local', target_owner),
                                subagent_creation={'parent_session_id': 'owner-assembly'}, resumable=False)
        child_tools, child_context, child_memory = await twin_execution.assemble(
            product, child, context)
        assert child_context['execution_config'] == frozen and child_memory == memory
        assert {'bash', 'read', 'write', 'load_skill'} <= {tool.name for tool in child_tools}
        assert {'memory_read', 'memory_write', 'memory_search', 'memory_delete'} <= {tool.name for tool in child_tools}

        runtime = RuntimeBridge(product)
        await runtime.start()
        captured = []

        def offline(model, model_context, options):
            captured.append({
                'system': model_context.messages[0]['content'],
            })
            stream = AssistantMessageEventStream()
            stream.push({'type': 'done', 'message': assistant_message(model,
                content=[{'type': 'text', 'text': 'Synthetic owner identity verified.'}], stopReason='stop')})
            return stream

        runtime.models.register_api('offline-regression', offline)
        try:
            result = await runtime.execute(product.get_request(request['id']), context)
            stored = await DatabaseSession.open(runtime.store, tenant_id='local', session_id=result['session_id'])
            assert stored.info.user_id == target_owner
            assert stored.session_id == runtime.session_id(chat_id, target_twin)
            system = captured[0]['system']
            assert markers[target_owner] in system and markers[requester] not in system
            assert f'OWNER_MEMORY_{target_owner}' in system
            assert f'OWNER_MEMORY_{requester}' not in system
            names = {tool['name'] for tool in stored.snapshot['tools']}
            assert {'load_skill', 'bash', 'request_credential', 'memory_read', 'memory_write'} <= names
            assert {row['owner_id'] for row in context['chat']['audience']} == ({target_owner} if is_private else {target_owner, requester} if chat_id == 'duet-alex-maya' else {target_owner, requester, 'owner-jordan'})
            assert source_owner in system and 'Destination audience' in system
        finally:
            await runtime.close()

    asyncio.run(run())
