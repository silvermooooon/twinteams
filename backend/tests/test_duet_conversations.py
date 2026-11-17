"""Topology, consent, and automation use disposable PG and no model/provider."""
import asyncio
from uuid import uuid4

import pytest
from duet_test_support import approved_action_context
from fastapi import HTTPException
from sqlalchemy import func, select
from test_credentials import product as product  # noqa: PLC0414

from app import duet_conversations as duet
from app.models import (
    Approval,
    CausalChain,
    Chat,
    ContextManifest,
    DuetContactGrant,
    Message,
    ReplyRequest,
)


def key():
    return uuid4().hex


def proposal(product, peers=None, origin=None, context=None):
    if context is None:
        request = product.send_message('owner-alex', 'private-alex', 'Prepare a coordination proposal', key())['request']
        context = product.get_runtime_context(request['id'])
    context = {**context, 'operation_id': key()}
    args = {'participant_owner_ids': ['owner-maya'] if peers is None else peers, 'title': 'Plan a time',
            'purpose': 'Compare the owners explicitly supplied availability',
            'opening_message': 'Could we find an agreed time?'}
    if origin:
        args['origin_chat_id'] = origin
    approved = approved_action_context(product, context, 'create_duet_topic', args)
    return product._runtime_tool_transaction('create_duet_topic', args, approved), context, args


def consent(product, row, owner='owner-alex', scope='once', decision='approved'):
    approval = next(a for a in row['conversation']['approvals'] if a['approver_id'] == owner)
    return asyncio.run(product.decide_approval(owner, approval['id'], decision, scope))


def active(product, **kwargs):
    row, context, args = proposal(product, **kwargs)
    for approval in row['conversation']['approvals']:
        if approval['status'] == 'pending':
            consent(product, row, approval['approver_id'])
    return row, context, args


def test_pending_unanimous_consent_then_atomic_opening_and_receiver(product):
    row, context, args = proposal(product, origin='group-alex-maya')
    chat_id = row['chat_id']
    assert row['status'] == 'pending'
    approved = approved_action_context(product, context, 'create_duet_topic', args)
    assert product._runtime_tool_transaction('create_duet_topic', args, approved) == row
    with product.transaction() as db:
        assert db.scalar(select(func.count()).select_from(Message).where(Message.chat_id == chat_id)) == 0
        assert db.scalar(select(func.count()).select_from(ReplyRequest).where(ReplyRequest.chat_id == chat_id)) == 0
        assert len(duet.approvals(db, chat_id)) == 2
    assert chat_id not in {chat['id'] for chat in product.bootstrap('owner-alex')['chats']}
    assert duet.list_conversations(product, 'owner-maya', 'group-alex-maya')['conversations'][0]['chat_id'] == chat_id
    assert duet.list_conversations(product, 'owner-maya', 'private-maya')['conversations'][0]['chat_id'] == chat_id
    consent(product, row)
    assert duet.detail(product, 'owner-maya', chat_id)['messages'] == []
    consent(product, row, 'owner-maya')
    detail = duet.detail(product, 'owner-maya', chat_id)
    assert detail['conversation']['status'] == 'active'
    assert [message['body'] for message in detail['messages']] == [args['opening_message']]
    assert detail['messages'][0]['author_id'] == 'twin-alex'
    assert detail['messages'][0]['causal_chain_id'] == context['causal_chain_id']
    with product.transaction() as db:
        rows = list(db.scalars(select(ReplyRequest).where(ReplyRequest.chat_id == chat_id)))
        assert len(rows) == 1 and rows[0].target_twin_id == 'twin-alex'
        assert rows[0].source == 'duet_topic_update' and rows[0].source_message_id is None
    with pytest.raises(HTTPException):
        duet.detail(product, 'owner-jordan', chat_id)


@pytest.mark.parametrize('exhausted', [False, True])
def test_consent_resumes_original_private_task_once_with_actual_opening_state(product, exhausted):
    row, context, _ = proposal(product)
    original_id = context['request_id']
    with product.transaction() as db:
        original = db.get(ReplyRequest, original_id)
        original.execution_config = {'version': 0, 'skills': [], 'credentials': [], 'execution_enabled': False}
        if exhausted:
            db.get(CausalChain, original.causal_chain_id).allowance = 6
            db.get(CausalChain, original.causal_chain_id).spent = 6
        original_sources = product.manifest_provenance(db, original_id)
    consent(product, row)
    with product.transaction() as db:
        assert not db.scalar(select(ReplyRequest.id).where(ReplyRequest.continuation_of_request_id == original_id))
    consent(product, row, 'owner-maya')
    consent(product, row, 'owner-maya')
    with product.transaction() as db:
        continuations = list(db.scalars(select(ReplyRequest).where(ReplyRequest.continuation_of_request_id == original_id)))
        assert len(continuations) == 1
        resumed, original = continuations[0], db.get(ReplyRequest, original_id)
        assert resumed.source == 'approval_result' and resumed.chat_id == original.chat_id == 'private-alex'
        assert resumed.session_id == original.session_id and resumed.causal_chain_id == original.causal_chain_id
        assert resumed.target_twin_id == original.target_twin_id and resumed.requester_id == original.requester_id
        assert resumed.execution_config == original.execution_config
        assert product.manifest_provenance(db, resumed.id) == original_sources
        assert db.scalar(select(ContextManifest.id).where(ContextManifest.request_id == resumed.id))
        assert 'connection only' in resumed.input_body and 'Do not resend' in resumed.input_body
        assert 'opening post was published' in resumed.input_body
        messages = list(db.scalars(select(Message).where(Message.chat_id == row['chat_id'])))
        assert len(messages) == 1 and messages[0].state == 'published'
        assert db.get(CausalChain, original.causal_chain_id).spent == (6 if exhausted else 0)
        assert resumed.id not in product.claim_dispatches()
        resumed_id = resumed.id
    product.finish_request(original_id, 'completed')
    assert resumed_id in product.claim_dispatches()


@pytest.mark.parametrize('restriction', ['rejected', 'cancelled', 'reset', 'source_invalid'])
def test_no_continuation_after_rejection_or_invalid_original_task(product, restriction):
    row, context, _ = proposal(product)
    original_id = context['request_id']
    if restriction == 'rejected':
        consent(product, row, decision='rejected')
    else:
        consent(product, row)
        with product.transaction() as db:
            origin = db.get(ReplyRequest, original_id)
            if restriction == 'cancelled':
                origin.cancel_requested = True
            elif restriction == 'reset':
                db.get(Chat, origin.chat_id).reset_generation += 1
            else:
                source_id = next(source['id'] for source in product.manifest_provenance(db, origin.id)
                                 if source['kind'] == 'message')
                db.get(Message, source_id).state = 'withdrawn'
        with pytest.raises(HTTPException):
            consent(product, row, 'owner-maya')
    with product.transaction() as db:
        assert not db.scalar(select(ReplyRequest.id).where(ReplyRequest.continuation_of_request_id == original_id))
        assert not db.scalar(select(Message.id).where(Message.chat_id == row['chat_id']))


def test_human_only_group_and_owner_read_only_duet(product):
    sent = product.send_message('owner-alex', 'group-alex-maya', 'Human message', key())
    assert sent['request'] is None
    with product.transaction() as db:
        assert not db.scalar(select(ReplyRequest).where(ReplyRequest.source_message_id == sent['message']['id']))
    detail = product.chat_detail('owner-alex', 'group-alex-maya')
    assert all(message['kind'] == 'human' for message in detail['messages'])
    assert all(actor['kind'] == 'human' for actor in detail['members'])
    assert detail['chat']['last_message'] == 'Human message'
    with product.transaction() as db, pytest.raises(HTTPException):
        product.accept_request(db, db.get(Chat, 'group-alex-maya'), 'twin-alex', 'owner-alex', 'manual', key())
    with pytest.raises(HTTPException):
        product.send_message('owner-alex', 'group-alex-maya', 'Rewrite', key(), reply_for_me=True)
    row, _, _ = active(product)
    chat_id = row['chat_id']
    message_id = duet.detail(product, 'owner-alex', chat_id)['messages'][0]['id']
    for action in [
        lambda: product.send_message('owner-alex', chat_id, 'Human attempt', key()),
        lambda: product.manual_request('owner-alex', chat_id, message_id, 'twin-alex', key()),
        lambda: product.set_message_reaction('owner-alex', chat_id, message_id, '👍', True, key()),
        lambda: product.add_members('owner-alex', chat_id, ['owner-jordan'], 'all'),
    ]:
        with pytest.raises(HTTPException):
            action()


def test_always_grants_are_directed_new_peers_prompt_revoke_closes(product):
    row, _, _ = proposal(product)
    consent(product, row, scope='always')
    consent(product, row, 'owner-maya', scope='always')
    assert duet.authorizations(product, 'owner-alex')['authorizations'][0]['peer_owner_id'] == 'owner-maya'
    second, second_context, _ = proposal(product)
    assert second['status'] == 'active'
    assert {a['scope'] for a in second['conversation']['approvals']} == {'standing'}
    with product.transaction() as db:
        assert not db.scalar(select(ReplyRequest.id).where(ReplyRequest.continuation_of_request_id == second_context['request_id']))
    third, _, _ = proposal(product, ['owner-maya', 'owner-jordan'])
    assert third['status'] == 'pending'
    assert all(a['status'] == 'pending' for a in third['conversation']['approvals'])
    revoked = duet.revoke(product, 'owner-alex', 'owner-maya')
    assert set(revoked['closed_chat_ids']) == {row['chat_id'], second['chat_id']}
    assert duet.detail(product, 'owner-maya', second['chat_id'])['conversation']['close_reason'] == 'authorization_revoked'
    with product.transaction() as db:
        assert not db.get(DuetContactGrant, ('owner-alex', 'owner-maya'))
        assert db.get(DuetContactGrant, ('owner-maya', 'owner-alex'))
        with pytest.raises(HTTPException):
            duet.require_active(db, db.get(Chat, second['chat_id']))
    fourth, _, _ = proposal(product)
    assert next(a for a in fourth['conversation']['approvals'] if a['approver_id'] == 'owner-alex')['status'] == 'pending'


def test_once_consent_survives_unrelated_grant_revocation_rejection_terminal(product):
    row, _, _ = active(product)
    duet.revoke(product, 'owner-alex', 'owner-maya')
    assert duet.detail(product, 'owner-alex', row['chat_id'])['conversation']['status'] == 'active'
    rejected, _, _ = proposal(product)
    consent(product, rejected, 'owner-maya', decision='rejected')
    assert duet.detail(product, 'owner-alex', rejected['chat_id'])['conversation']['status'] == 'rejected'
    with pytest.raises(HTTPException):
        consent(product, rejected, 'owner-alex')
    assert duet.detail(product, 'owner-alex', rejected['chat_id'])['messages'] == []




def test_conversation_consent_is_independent_of_action_approvals(product):
    row, _, _ = active(product)
    with product.transaction() as db:
        db.add(Approval(id=key(), chat_id=row['chat_id'], approver_id='owner-maya',
            title='Sensitive owner action', description='Owner-only operation', target='owner workspace',
            impact='A distinct effect', sdk_reference={'action': {'tool': 'bash', 'arguments': {'command': 'synthetic'}}}))
    detail = duet.detail(product, 'owner-alex', row['chat_id'])
    assert len(detail['approvals']) == 2
    assert all(a['type'] == 'duet_conversation' for a in detail['approvals'])
    with product.transaction() as db:
        duet.require_active(db, db.get(Chat, row['chat_id']))


def test_close_signals_running_sdk_stop_and_blocks_late_output(product):
    from app.models import DispatchCommand
    row, _, _ = active(product)
    with product.transaction() as db:
        request = db.scalar(select(ReplyRequest).where(ReplyRequest.chat_id == row['chat_id']))
        request.status = 'running'
        request_id = request.id
    duet.close_conversation(product, 'owner-alex', row['chat_id'])
    assert request_id in {request['id'] for request in product.pending_stops({row['chat_id']})}
    with product.transaction() as db:
        assert db.get(DispatchCommand, request_id).kind == 'cancel'
    assert product.publish_runtime_output(request_id, 'Late output', product.get_request(request_id)['session_id'], 'synthetic-run') is None
    assert len(duet.detail(product, 'owner-alex', row['chat_id'])['messages']) == 1


def test_concurrent_proposal_and_revoke_never_admit_revoked_standing_consent(product, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    row, context, args = proposal(product)
    consent(product, row, scope='always')
    consent(product, row, 'owner-maya', scope='always')
    entered, release, revoking = Event(), Event(), Event()
    original = duet.propose
    def held(*values):
        entered.set()
        assert release.wait(5)
        return original(*values)
    monkeypatch.setattr(duet, 'propose', held)
    context = approved_action_context(product, {**context, 'operation_id': key()}, 'create_duet_topic', args)
    def revoke():
        revoking.set()
        return duet.revoke(product, 'owner-alex', 'owner-maya')
    with ThreadPoolExecutor(max_workers=2) as pool:
        proposed = pool.submit(product._runtime_tool_transaction, 'create_duet_topic', args, context)
        assert entered.wait(5)
        revoked = pool.submit(revoke)
        assert revoking.wait(5)
        release.set()
        created = proposed.result(timeout=10)
        result = revoked.result(timeout=10)
    assert created['chat_id'] in result['closed_chat_ids']
    assert duet.detail(product, 'owner-alex', created['chat_id'])['conversation']['status'] == 'closed'
    next_row, _, _ = proposal(product)
    assert next(a for a in next_row['conversation']['approvals'] if a['approver_id'] == 'owner-alex')['status'] == 'pending'


def test_consent_lock_allows_running_topic_context_foreign_keys(product):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    row, _, _ = active(product)
    with product.transaction() as db:
        request_id = db.scalar(select(ReplyRequest.id).where(ReplyRequest.chat_id == row['chat_id']))
    locked, release = Event(), Event()
    def hold_consent():
        with product.transaction() as db:
            duet.lock_consent(db, ['owner-alex', 'owner-maya'])
            locked.set()
            assert release.wait(10)
    with ThreadPoolExecutor(max_workers=2) as pool:
        consent_task = pool.submit(hold_consent)
        assert locked.wait(5)
        try:
            context = pool.submit(product.get_runtime_context, request_id).result(timeout=5)
        finally:
            release.set()
        consent_task.result(timeout=5)
    assert context['chat']['id'] == row['chat_id']






def test_api_scoped_approval_authorizations_and_sidebar_detail(product):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import identity, router
    row, _, _ = proposal(product, origin='group-alex-maya')
    application = FastAPI()
    application.include_router(router)
    owner = ['owner-alex']
    application.dependency_overrides[identity] = lambda: owner[0]
    with TestClient(application) as client:
        response = client.get('/api/duet-conversations', params={'origin_chat_id': 'group-alex-maya'})
        assert response.status_code == 200 and response.json()['conversations'][0]['chat_id'] == row['chat_id']
        assert client.get(f"/api/duet-conversations/{row['chat_id']}").json()['messages'] == []
        for approval in row['conversation']['approvals']:
            owner[0] = approval['approver_id']
            response = client.post(f"/api/approvals/{approval['id']}/decision",
                                   json={'decision': 'approved', 'scope': 'always'})
            assert response.status_code == 200 and response.json()['scope'] == 'always'
        assert client.get(f"/api/duet-conversations/{row['chat_id']}").json()['conversation']['status'] == 'active'
        grants = client.get('/api/duet-authorizations').json()['authorizations']
        assert grants[0]['peer_owner_id'] == 'owner-alex'
        revoked = client.delete('/api/duet-authorizations/owner-alex')
        assert revoked.status_code == 200 and revoked.json()['closed_chat_ids'] == [row['chat_id']]
        assert client.get(f"/api/duet-conversations/{row['chat_id']}").json()['conversation']['status'] == 'closed'
        assert client.get('/api/duet-authorizations').json()['authorizations'] == []


def test_sidebar_listing_does_not_query_transcript_or_unread_counts(product):
    from sqlalchemy import event

    from app import service as service_module
    active(product)
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())
    event.listen(service_module.engine, 'before_cursor_execute', capture)
    try:
        rows = duet.list_conversations(product, 'owner-alex', 'private-alex')['conversations']
    finally:
        event.remove(service_module.engine, 'before_cursor_execute', capture)
    assert rows and rows[0]['kind'] == 'duet'
    assert not any('from tt_messages' in statement or 'from tt_read_receipts' in statement for statement in statements)






def test_human_group_send_and_detail_do_not_query_automation_candidates_or_requests(product):
    from sqlalchemy import event

    from app import service as service_module
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(' '.join(statement.lower().split()))
    event.listen(service_module.engine, 'before_cursor_execute', capture)
    try:
        sent = product.send_message('owner-alex', 'group-alex-maya', 'Only a human discussion', key())
        assert sent['request'] is None and sent['message']['causal_chain_id'] is None
        assert not any(statement.startswith('insert into tt_causal_chains') or 'from tt_trigger_candidates' in statement for statement in statements)
        statements.clear()
        assert product.chat_detail('owner-alex', 'group-alex-maya')['requests'] == []
        assert not any('from tt_reply_requests' in statement for statement in statements)
    finally:
        event.remove(service_module.engine, 'before_cursor_execute', capture)


def test_solo_topic_is_immediately_active_and_creator_is_administrator(product):
    row, _, _ = proposal(product, peers=[])
    assert row['status'] == 'active'
    assert row['conversation']['admin_owner_id'] == 'owner-alex'
    assert row['conversation']['participant_owner_ids'] == ['owner-alex']
    detail = duet.detail(product, 'owner-alex', row['chat_id'])
    assert len(detail['messages']) == 1
    with product.transaction() as db:
        requests = list(db.scalars(select(ReplyRequest).where(ReplyRequest.chat_id == row['chat_id'])))
        assert len(requests) == 1 and requests[0].target_twin_id == 'twin-alex'


def invitation(product, row, context, mentions=None):
    args = {'chat_id': row['chat_id'], 'participant_owner_ids': ['owner-jordan'],
        'requirements': 'Check the release requirements and append findings',
        'mention_owner_ids': mentions or []}
    approved = approved_action_context(product, context, 'invite_duet_participants', args)
    return product._runtime_tool_transaction('invite_duet_participants', args,
        {**approved, 'operation_id': key()})


def invitation_approvals(row):
    return [approval for approval in row['conversation']['approvals'] if approval.get('invitation')]


def test_only_administrator_invites_and_all_consents_precede_access(product):
    row, context, _ = active(product)
    request = product.send_message('owner-maya', 'private-maya', 'Coordinate within my authorization', key())['request']
    peer_context = product.get_runtime_context(request['id'])
    with pytest.raises(HTTPException, match='Only the topic administrator'):
        invitation(product, row, peer_context)
    invited = invitation(product, row, context, ['owner-jordan'])
    assert invited['status'] == 'pending'
    assert len(invitation_approvals(invited)) == 3
    assert duet.detail(product, 'owner-alex', row['chat_id'])['conversation']['participant_owner_ids'] == ['owner-alex', 'owner-maya']
    with pytest.raises(HTTPException):
        duet.detail(product, 'owner-jordan', row['chat_id'])
    inbox = product.chat_detail('owner-jordan', 'private-jordan')['approvals']
    invitation_ids = {approval['id'] for approval in invitation_approvals(invited)}
    assert any(approval['id'] in invitation_ids for approval in inbox)
    for approval in invitation_approvals(invited):
        asyncio.run(product.decide_approval(approval['approver_id'], approval['id'], 'approved', 'once'))
    detail = duet.detail(product, 'owner-jordan', row['chat_id'])
    assert set(detail['conversation']['participant_owner_ids']) == {'owner-alex', 'owner-maya', 'owner-jordan'}
    assert detail['messages'][-1]['body'] == 'Check the release requirements and append findings'
    with product.transaction() as db:
        requests = list(db.scalars(select(ReplyRequest).where(ReplyRequest.chat_id == row['chat_id'],
            ReplyRequest.target_twin_id == 'twin-jordan')))
        assert len(requests) == 1 and requests[0].source == 'duet_topic_mention'
        assert requests[0].source_message_id is None


def test_rejected_invitation_does_not_close_existing_topic_or_grant_access(product):
    row, context, _ = active(product)
    invited = invitation(product, row, context)
    approval = next(approval for approval in invitation_approvals(invited) if approval['approver_id'] == 'owner-jordan')
    asyncio.run(product.decide_approval('owner-jordan', approval['id'], 'rejected', 'once'))
    assert duet.detail(product, 'owner-alex', row['chat_id'])['conversation']['status'] == 'active'
    with pytest.raises(HTTPException):
        duet.detail(product, 'owner-jordan', row['chat_id'])
    assert invitation(product, row, context)['status'] == 'pending'


@pytest.mark.parametrize('restriction', ['withdrawn', 'cancelled', 'reset'])
def test_delayed_invitation_rechecks_origin_before_sharing_requirements_and_history(product, restriction):
    row, context, _ = active(product)
    invited = invitation(product, row, context)
    approvals = invitation_approvals(invited)
    for approval in approvals[:-1]:
        asyncio.run(product.decide_approval(approval['approver_id'], approval['id'], 'approved', 'once'))
    with product.transaction() as db:
        request = db.get(ReplyRequest, context['request_id'])
        if restriction == 'withdrawn':
            db.get(Message, request.source_message_id).state = 'withdrawn'
        elif restriction == 'cancelled':
            request.cancel_requested = True
        else:
            db.get(Chat, request.chat_id).reset_generation += 1
    final = approvals[-1]
    with pytest.raises(HTTPException):
        asyncio.run(product.decide_approval(final['approver_id'], final['id'], 'approved', 'once'))
    with pytest.raises(HTTPException):
        duet.detail(product, 'owner-jordan', row['chat_id'])
    assert len(duet.detail(product, 'owner-alex', row['chat_id'])['messages']) == 1


def test_topic_metadata_changes_wake_admin_only_and_use_revision_check(product):
    row, context, _ = active(product)
    result = product._runtime_tool_transaction('update_duet_topic', {'chat_id': row['chat_id'],
        'expected_revision': 1, 'title': 'Updated topic'}, {**context, 'operation_id': key()})
    assert result['revision'] == 2 and result['title'] == 'Updated topic'
    with product.transaction() as db:
        updates = list(db.scalars(select(ReplyRequest).where(ReplyRequest.chat_id == row['chat_id'],
            ReplyRequest.input_body.contains('topic_updated'))))
        assert len(updates) == 1 and updates[0].target_twin_id == 'twin-alex'
        assert 'Updated topic' not in updates[0].input_body
    with pytest.raises(HTTPException, match='Topic changed'):
        product._runtime_tool_transaction('update_duet_topic', {'chat_id': row['chat_id'],
            'expected_revision': 1, 'title': 'Stale edit'}, {**context, 'operation_id': key()})


def test_pending_invitee_private_duet_reads_own_approval_without_topic_access(product):
    from app.models import ChangeOutbox

    row, admin_context, _ = active(product)
    with product.transaction() as db:
        before_revision = db.get(Chat, 'private-jordan').revision
        before_requests = list(db.scalars(select(ReplyRequest.id).where(ReplyRequest.chat_id == 'private-jordan')))
    invited = invitation(product, row, admin_context)
    with product.transaction() as db:
        private = db.get(Chat, 'private-jordan')
        assert private.revision > before_revision
        assert db.scalar(select(ChangeOutbox.id).where(ChangeOutbox.chat_id == private.id,
            ChangeOutbox.revision == private.revision, ChangeOutbox.kind == 'changed'))
        assert list(db.scalars(select(ReplyRequest.id).where(ReplyRequest.chat_id == 'private-jordan'))) == before_requests
    approval = next(item for item in invitation_approvals(invited) if item['approver_id'] == 'owner-jordan')
    request = product.send_message('owner-jordan', 'private-jordan',
        'Inspect my invitation and create a reminder to review it', key())['request']
    context = product.get_runtime_context(request['id'])
    result = product._runtime_tool_transaction('get_approval', {'id': approval['id']},
        {**context, 'operation_id': key()})
    assert result['id'] == approval['id'] and result['status'] == 'pending' and result['invitation']
    assert result['impact'] == 'Check the release requirements and append findings'
    with pytest.raises(HTTPException):
        product._runtime_tool_transaction('read_duet_topic', {'chat_id': row['chat_id']},
            {**context, 'operation_id': key()})
    reminder = product._runtime_tool_transaction('create_reminder',
        {'title': 'Review the topic invitation'}, {**context, 'operation_id': key()})
    assert reminder['title'] == 'Review the topic invitation'
    with product.transaction() as db:
        member = product.membership(db, 'owner-jordan', 'private-jordan')[1]
        sources = product.manifest_provenance(db, request['id'])
        assert product.provenance_allowed(db, member, sources)
        assert all(source.get('chat_id') != row['chat_id'] for source in sources)
