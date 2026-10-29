"""Topology, consent, and automation use disposable PG and no model/provider."""
import asyncio
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from test_credentials import product as product  # noqa: PLC0414

from app import duet_conversations as duet
from app.models import (
    Approval,
    Chat,
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
    args = {'participant_owner_ids': peers or ['owner-maya'], 'title': 'Plan a time',
            'purpose': 'Compare the owners explicitly supplied availability',
            'opening_message': 'Could we find an agreed time?'}
    if origin:
        args['origin_chat_id'] = origin
    return product._runtime_tool_transaction('request_duet_conversation', args, context), context, args


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
    assert product._runtime_tool_transaction('request_duet_conversation', args, context) == row
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
        assert len(rows) == 1 and rows[0].target_twin_id == 'twin-maya'
        assert rows[0].source == 'duet_message'
    with pytest.raises(HTTPException):
        duet.detail(product, 'owner-jordan', chat_id)


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
    second, _, _ = proposal(product)
    assert second['status'] == 'active'
    assert {a['scope'] for a in second['conversation']['approvals']} == {'standing'}
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
    with pytest.raises(HTTPException):
        product.publish_runtime_output(request_id, 'Late output', product.get_request(request_id)['session_id'], 'synthetic-run')
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
    context = {**context, 'operation_id': key()}
    def revoke():
        revoking.set()
        return duet.revoke(product, 'owner-alex', 'owner-maya')
    with ThreadPoolExecutor(max_workers=2) as pool:
        proposed = pool.submit(product._runtime_tool_transaction, 'request_duet_conversation', args, context)
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
