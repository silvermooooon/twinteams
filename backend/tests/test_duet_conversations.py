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
    CausalChain,
    Chat,
    DuetContactGrant,
    Message,
    ReplyRequest,
    RoutingDecision,
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


def test_pending_unanimous_consent_no_message_request_or_router(product):
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
    assert detail['requests'] == []
    assert len(product.pending_routes({chat_id})) == 1
    with pytest.raises(HTTPException):
        duet.detail(product, 'owner-jordan', chat_id)


def test_human_only_group_and_owner_read_only_duet(product):
    sent = product.send_message('owner-alex', 'group-alex-maya', 'Human message', key())
    assert sent['request'] is None
    with product.transaction() as db:
        assert not db.scalar(select(RoutingDecision).where(RoutingDecision.message_id == sent['message']['id']))
        # Retain historical agent data, but exclude every public projection.
        chat = db.get(Chat, 'group-alex-maya')
        legacy = product.append_message(db, chat, author_id='twin-maya', kind='twin', body='Historical hidden')
        legacy_id = legacy.id
    detail = product.chat_detail('owner-alex', 'group-alex-maya')
    assert all(message['kind'] == 'human' for message in detail['messages'])
    assert all(actor['kind'] == 'human' for actor in detail['members'])
    assert detail['chat']['last_message'] == 'Human message'
    with product.transaction() as db:
        member = product.membership(db, 'owner-alex', 'group-alex-maya')[1]
        assert product.visible_message(db, member, legacy_id, False) is None
        assert product._tool_dispatch(db, 'history', {'query': 'Historical'}, {}, db.get(Chat, member.chat_id), member)['messages'] == []
        with pytest.raises(HTTPException):
            product.accept_request(db, db.get(Chat, member.chat_id), 'twin-alex', 'owner-alex', 'manual', key())
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


def test_immediate_recipient_gate_presence_read_independent_shared_six_budget(product):
    row, context, _ = active(product)
    chat_id = row['chat_id']
    detail = duet.detail(product, 'owner-maya', chat_id)
    opening = detail['messages'][0]
    decision_id = product.pending_routes({chat_id})[0]
    prepared = product._prepare_route(decision_id)
    assert prepared
    assert {actor['kind'] for actor in prepared[4]} == {'twin'}
    product._settle_route(decision_id, ['owner-maya', 'owner-jordan'], True, 'Answer useful', None, prepared[-1])
    product.read('owner-maya', chat_id, [opening['id']])
    product._presence_cache['owner-maya'] = {'expires_at': 9999999999, 'focused_chat_id': chat_id, 'visible': True}
    product.process_candidates({chat_id})
    with product.transaction() as db:
        requests = list(db.scalars(select(ReplyRequest).where(ReplyRequest.chat_id == chat_id)))
        assert len(requests) == 1 and requests[0].target_twin_id == 'twin-maya'
        assert requests[0].causal_chain_id == context['causal_chain_id']
        chain = db.get(CausalChain, context['causal_chain_id'])
        assert chain.allowance == 6 and chain.spent == 2
        chain.spent = chain.allowance
        chat = db.get(Chat, chat_id)
        extra = product.append_message(db, chat, author_id='twin-maya', kind='twin', body='Another useful question?', causal_chain_id=chain.id)
        product.route_committed_message(db, chat, extra)
        extra_id = extra.id
    paused_id = product.pending_routes({chat_id})[0]
    assert product._prepare_route(paused_id) is None
    paused = duet.detail(product, 'owner-alex', chat_id)['automatic_conversations'][0]
    assert paused['can_continue'] and paused['spent'] == 6
    grant = product.continue_automatic_conversation('owner-alex', chat_id, chain.id, key(), 6, 6)
    assert grant['remaining'] == 6 and grant['allowance'] == 12
    with product.transaction() as db:
        assert db.get(Message, extra_id).causal_chain_id == chain.id
    duet.close_conversation(product, 'owner-maya', chat_id)
    assert duet.detail(product, 'owner-alex', chat_id)['conversation']['status'] == 'closed'
    assert product._prepare_route(paused_id) is None


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
    decision = product.pending_routes({row['chat_id']})[0]
    product._settle_route(decision, ['owner-maya'], True, 'Useful', None)
    product.process_candidates({row['chat_id']})
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


def test_six_shared_replies_new_conversation_inherits_budget_fresh_human_intent_has_new_chain(product):
    outer, initial, _ = active(product)
    outer_decision = product.pending_routes({outer['chat_id']})[0]
    product._settle_route(outer_decision, ['owner-maya'], True, 'Useful', None)
    product.process_candidates({outer['chat_id']})
    with product.transaction() as db:
        parent = db.scalar(select(ReplyRequest).where(ReplyRequest.chat_id == outer['chat_id']))
        parent_context = product.get_runtime_context(parent.id)
    row, _, _ = active(product, peers=['owner-alex'], context=parent_context)
    chat_id = row['chat_id']
    last_context = None
    for turn in range(4):
        decision = product.pending_routes({chat_id})[0]
        prepared = product._prepare_route(decision)
        assert prepared
        source_owner = prepared[1]['author_owner_id']
        target_owner = 'owner-maya' if source_owner == 'owner-alex' else 'owner-alex'
        product._settle_route(decision, [target_owner], True, 'Useful question', None, prepared[-1])
        product.process_candidates({chat_id})
        with product.transaction() as db:
            request = db.scalar(select(ReplyRequest).where(ReplyRequest.chat_id == chat_id,
                ReplyRequest.status == 'queued').order_by(ReplyRequest.accepted_at.desc()))
            request_id = request.id
        last_context = product.get_runtime_context(request_id)
        output = product.publish_runtime_output(request_id, f'Useful next question {turn}?',
            product.get_request(request_id)['session_id'], f'offline-run-{turn}')
        product.finish_request(request_id, 'completed', route_message_id=output)
    with product.transaction() as db:
        chain = db.get(CausalChain, initial['causal_chain_id'])
        assert chain.spent == chain.allowance == 6
    assert product._prepare_route(product.pending_routes({chat_id})[0]) is None
    assert len(duet.detail(product, 'owner-alex', chat_id)['messages']) == 5
    other_owner = 'owner-maya' if last_context['owner_id'] == 'owner-alex' else 'owner-alex'
    nested, _, _ = active(product, peers=[other_owner], context=last_context)
    assert nested['conversation']['causal_chain_id'] == initial['causal_chain_id']
    assert product._prepare_route(product.pending_routes({nested['chat_id']})[0]) is None
    with product.transaction() as db:
        assert not db.scalar(select(ReplyRequest.id).where(ReplyRequest.chat_id == nested['chat_id']))
    fresh = product.send_message('owner-alex', 'private-alex', 'New owner-authorized request', key())['request']
    fresh_context = {**product.get_runtime_context(fresh['id']), 'operation_id': key()}
    sent = product._runtime_tool_transaction('send_chat_message', {'chat_id': chat_id, 'body': 'New owner-authorized question'}, fresh_context)
    assert sent['causal_chain_id'] != initial['causal_chain_id']
    with product.transaction() as db:
        decision = db.scalar(select(RoutingDecision).where(RoutingDecision.message_id == sent['message_id']))
        decision_id = decision.id
    product._settle_route(decision_id, ['owner-maya'], True, 'New request', None)
    product.process_candidates({chat_id})
    with product.transaction() as db:
        assert db.get(CausalChain, fresh['causal_chain_id']).spent == 2
        assert db.get(CausalChain, initial['causal_chain_id']).spent == 6


def test_explicit_upgrade_preserves_history_and_suppresses_legacy_group_work(product):
    from app import service as service_module
    from app.duet_conversation_schema import upgrade_duet_conversation_schema
    from app.models import DispatchCommand, TriggerCandidate
    origin = product.send_message('owner-alex', 'private-alex', 'Synthetic legacy request', key())['request']
    with product.transaction() as db:
        request = db.get(ReplyRequest, origin['id'])
        request.chat_id = 'group-alex-maya'
        message = product.append_message(db, db.get(Chat, request.chat_id), author_id='twin-alex',
            kind='twin', body='Preserved legacy record', causal_chain_id=origin['causal_chain_id'])
        message_id = message.id
        db.add(RoutingDecision(message_id=message.id, strategy='pair'))
        db.add(TriggerCandidate(chat_id=request.chat_id, message_id=message.id,
            target_twin_id='twin-maya', due_at=message.created_at, evidence={}))
    upgrade_duet_conversation_schema(service_module.engine)
    upgrade_duet_conversation_schema(service_module.engine)
    with product.transaction() as db:
        request = db.get(ReplyRequest, origin['id'])
        assert request.status == 'cancelled' and request.cancel_requested
        assert db.get(DispatchCommand, request.id).kind == 'cancel'
        assert db.get(Message, message_id).body == 'Preserved legacy record'
        assert db.scalar(select(TriggerCandidate).where(TriggerCandidate.message_id == message_id)).state == 'suppressed'
        assert db.scalar(select(RoutingDecision).where(RoutingDecision.message_id == message_id)).status == 'completed'
    with pytest.raises(HTTPException):
        product.get_runtime_context(origin['id'], include_completed=True)
    assert product.chat_detail('owner-maya', 'group-alex-maya')['messages'] == []


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


def test_private_proposal_starts_followup_only_after_consent_and_peer_answer_reports_privately(product):
    from datetime import timedelta

    from app.models import TwinFollowup, now
    row, context, _ = proposal(product)
    with product.transaction() as db:
        assert not db.scalar(select(TwinFollowup.id).where(TwinFollowup.target_chat_id == row['chat_id']))
    consent(product, row)
    consent_time = now()
    consent(product, row, 'owner-maya')
    with product.transaction() as db:
        followup = db.scalar(select(TwinFollowup).where(TwinFollowup.target_chat_id == row['chat_id']))
        assert followup.status == 'waiting'
        assert followup.due_at >= consent_time + timedelta(minutes=29)
        opening_id = followup.sent_message_id
        assert db.get(Message, opening_id).request_id == context['request_id']
    decision_id = product.pending_routes({row['chat_id']})[0]
    product._settle_route(decision_id, ['owner-maya'], True, 'Peer can answer', None)
    product.process_candidates({row['chat_id']})
    with product.transaction() as db:
        peer = db.scalar(select(ReplyRequest).where(ReplyRequest.chat_id == row['chat_id']))
        peer_id = peer.id
    product.get_runtime_context(peer_id)
    answer_id = product.publish_runtime_output(peer_id, 'Friday at 15:00 is confirmed.', product.get_request(peer_id)['session_id'], 'peer-run')
    product.finish_request(peer_id, 'completed', route_message_id=answer_id)
    with product.transaction() as db:
        followup = db.scalar(select(TwinFollowup).where(TwinFollowup.target_chat_id == row['chat_id']))
        assert followup.status == 'replied' and followup.reply_message_id == answer_id
        report_id = followup.continuation_request_id
        report = db.get(ReplyRequest, report_id)
        assert report.chat_id == 'private-alex' and report.source == 'followup_result'
        assert report.causal_chain_id == context['causal_chain_id']
        assert db.scalar(select(func.count()).select_from(ReplyRequest).where(ReplyRequest.chat_id == row['chat_id'])) == 1
    for decision in product.pending_routes({row['chat_id']}):
        assert product._prepare_route(decision) is None
    report_context = product.get_runtime_context(report_id)
    assert report_context['followup_result']['reply_body'] == 'Friday at 15:00 is confirmed.'
    product.publish_runtime_output(report_id, 'Your Duet confirmed Friday at 15:00.', product.get_request(report_id)['session_id'], 'report-run')
    assert any(message['body'] == 'Your Duet confirmed Friday at 15:00.' for message in product.chat_detail('owner-alex', 'private-alex')['messages'])
    duet.close_conversation(product, 'owner-maya', row['chat_id'])
    assert product.publish_runtime_output(report_id, 'Late report after closed authorization', product.get_request(report_id)['session_id'], 'report-run', 'late') is None
    with pytest.raises(HTTPException):
        product.authorize_runtime_generation(report_context)


def test_private_proposal_followup_timeout_uses_existing_private_continuation(product):
    from datetime import timedelta

    from app.models import TwinFollowup, now
    row, context, _ = active(product)
    with product.transaction() as db:
        followup = db.scalar(select(TwinFollowup).where(TwinFollowup.target_chat_id == row['chat_id']))
        followup.due_at = now() - timedelta(seconds=1)
    product.process_followups({row['chat_id']})
    with product.transaction() as db:
        followup = db.scalar(select(TwinFollowup).where(TwinFollowup.target_chat_id == row['chat_id']))
        assert followup.status == 'timed_out'
        continuation = db.get(ReplyRequest, followup.continuation_request_id)
        assert continuation.chat_id == 'private-alex' and continuation.causal_chain_id == context['causal_chain_id']
        continuation_id = continuation.id
    assert product.get_runtime_context(continuation_id)['followup_result']['status'] == 'timeout'


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


def test_legacy_human_chat_router_failure_is_preserved_but_not_projected(product):
    sent = product.send_message('owner-alex', 'group-alex-maya', 'Historical human question', key())['message']
    with product.transaction() as db:
        row = RoutingDecision(message_id=sent['id'], strategy='pair', status='failed', error='Retired route failure')
        db.add(row)
        db.flush()
        decision_id = row.id
    detail = product.chat_detail('owner-alex', 'group-alex-maya')
    assert all('routing_failure' not in message for message in detail['messages'])
    with product.transaction() as db:
        assert db.get(RoutingDecision, decision_id).status == 'failed'
    with pytest.raises(HTTPException):
        product.retry_route('owner-alex', 'group-alex-maya', decision_id, key(), sent['id'])
