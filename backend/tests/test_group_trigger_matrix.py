"""Approved Duet recipient routing matrix; real PostgreSQL, no provider network."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from duet_test_support import append_duet, seed_collaborations
from sqlalchemy import select
from test_twin_followup_acceptance import product as product  # noqa: PLC0414

from app import service as service_module
from app.models import Actor, CausalChain, Message, ReplyRequest, RoutingDecision, TriggerCandidate
from app.service import aware

OWNERS = ('owner-alex', 'owner-maya', 'owner-jordan')
GROUP = 'duet-team'


def key():
    return uuid4().hex


def decision_for(product, message_id):
    with product.transaction() as db:
        row = db.scalar(select(RoutingDecision).where(RoutingDecision.message_id == message_id))
        return None if row is None else {column.name: getattr(row, column.name) for column in RoutingDecision.__table__.columns}


def route(product, message_id, targets=(), needs_response=True, *, error=None, reason='Deterministic matrix decision'):
    decision = decision_for(product, message_id)
    assert decision and decision['status'] == 'pending'
    classify = AsyncMock(side_effect=error, return_value={'needs_response': needs_response,
        'target_owner_ids': list(targets), 'reason': reason})
    product.bind_runtime(SimpleNamespace(configured=True, startup_error=None, route_targets=classify))
    product.worker_mode = True
    asyncio.run(product.route_pending([decision['id']]))
    return classify


def candidates(product, message_id):
    with product.transaction() as db:
        return {db.get(Actor, row.target_twin_id).owner_id: {
            'id': row.id, 'state': row.state, 'request_id': row.accepted_request_id,
            'due_at': aware(row.due_at), 'chain_id': row.causal_chain_id, 'evidence': row.evidence}
            for row in db.scalars(select(TriggerCandidate).where(TriggerCandidate.message_id == message_id))}


def publish(product, request_id, text):
    product.get_runtime_context(request_id)
    product.complete_request(request_id, body=text, session_id=product.get_request(request_id)['session_id'],
        run_id='matrix-run-' + request_id, event_id='matrix-final-' + request_id, event_seq=1)
    with product.transaction() as db:
        request = db.get(ReplyRequest, request_id)
        message = db.scalar(select(Message).where(Message.request_id == request_id))
        assert request.status == 'completed' and message.state == 'published'
        assert message.author_id == request.target_twin_id
        assert message.causal_chain_id == request.causal_chain_id
        return message.id


def clock_after(product, monkeypatch, message_id, seconds):
    with product.transaction() as db:
        timestamp = aware(db.get(Message, message_id).created_at) + timedelta(seconds=seconds)
    monkeypatch.setattr(service_module, 'now', lambda: timestamp)


@pytest.fixture(autouse=True)
def approved_collaboration_fixtures(product):
    seed_collaborations(product)


@pytest.mark.parametrize('source', OWNERS)
def test_three_owner_target_union_and_response_gate(product, source):
    first, second = [owner for owner in OWNERS if owner != source]
    cases = [([first], True, 'Question', {first}),
             ([*OWNERS, first, 'foreign-owner'], True, 'Question', {first, second}),
             ([], True, 'Question', set()), ([source], True, 'Question', set()),
             ([first], True, '@' + second.replace('owner-', 'twin-') + ' question', {first, second}),
             ([first, source], True, '@' + first.replace('owner-', 'twin-') + ' question', {first}),
             ([*OWNERS], False, '@' + second.replace('owner-', 'twin-') + ' thanks, complete', set())]
    for inferred, needed, body, expected in cases:
        message = append_duet(product, GROUP, source, body)
        classifier = route(product, message['id'], inferred, needed)
        assert classifier.call_count == 1
        assert set(candidates(product, message['id'])) == expected
        product.process_candidates({GROUP})
        assert all(value['state'] == 'accepted' for value in candidates(product, message['id']).values())


@pytest.mark.parametrize('source', OWNERS)
def test_human_group_messages_never_route_or_accept_and_private_accepts_own_duet(product, source):
    human = product.send_message(source, 'group-team', 'Human discussion', key())
    assert human['request'] is None and decision_for(product, human['message']['id']) is None
    assert candidates(product, human['message']['id']) == {}
    private = product.send_message(source, source.replace('owner-', 'private-'), 'Owner task', key())
    assert private['request']['target_twin_id'] == source.replace('owner-', 'twin-')
    assert decision_for(product, private['message']['id']) is None


@pytest.mark.parametrize('source', OWNERS)
def test_published_duets_branch_share_six_and_continue(product, source):
    message = append_duet(product, GROUP, source, 'Initial question')
    route(product, message['id'], [owner for owner in OWNERS if owner != source])
    product.process_candidates({GROUP})
    queue = [entry['request_id'] for entry in candidates(product, message['id']).values()]
    accepted = list(queue)
    while queue:
        request_id = queue.pop(0)
        reply_id = publish(product, request_id, 'Next useful question ' + request_id)
        with product.transaction() as db:
            owner = product.message_owner(db, db.get(Message, reply_id))
        route(product, reply_id, [peer for peer in OWNERS if peer != owner])
        product.process_candidates({GROUP})
        fresh = [entry['request_id'] for entry in candidates(product, reply_id).values()
                 if entry['state'] == 'accepted' and entry['request_id'] not in accepted]
        accepted.extend(fresh)
        queue.extend(fresh)
    assert len(accepted) == 6
    paused = product.chat_detail(source, GROUP)['automatic_conversations'][0]
    assert paused['spent'] == paused['allowance'] == 6 and paused['can_continue']
    grant_key = key()
    grant = product.continue_automatic_conversation(source, GROUP, paused['id'], grant_key, 6, 6)
    assert grant['allowance'] == 12
    assert product.continue_automatic_conversation(source, GROUP, paused['id'], grant_key, 6, 6)['already_applied']
    for decision_id in product.pending_routes({GROUP}):
        product._settle_route(decision_id, list(OWNERS), True, "Owner continued", None)
    product.process_candidates({GROUP})
    with product.transaction() as db:
        chain = db.get(CausalChain, paused['id'])
        assert chain.spent > 6 and chain.spent <= chain.allowance


@pytest.mark.parametrize('presence', ['online', 'offline', 'unknown'])
@pytest.mark.parametrize('read_first', [False, True])
def test_duet_admission_is_immediate_independent_of_owner_presence_receipts(product, presence, read_first):
    message = append_duet(product, GROUP, 'owner-alex', 'Review the plan')
    if presence != 'unknown':
        product._presence_cache['owner-maya'] = {'expires_at': 9999999999 if presence == 'online' else 0, 'focused_chats': [GROUP]}
    else:
        product._presence_cache.pop('owner-maya', None)
    if read_first:
        product.read('owner-maya', GROUP, [message['id']])
    route(product, message['id'], ['owner-maya'])
    product.process_candidates({GROUP})
    assert candidates(product, message['id'])['owner-maya']['state'] == 'accepted'


def test_failed_router_never_admits_explicit_mentions(product):
    message = append_duet(product, GROUP, 'owner-alex', '@twin-maya please review')
    route(product, message['id'], error=RuntimeError('Synthetic router failure'))
    product.process_candidates({GROUP})
    assert decision_for(product, message['id'])['status'] == 'failed'
    assert candidates(product, message['id']) == {}


@pytest.mark.parametrize('body,expected', [('@Anna please review', {'owner-maya'}),
    ('(@aNnA), please review', {'owner-maya'}), ('@Ann question', {'owner-jordan'}),
    ('@Anna-extra is not a mention', set()), ('name@Anna is not a mention', set())])
def test_duet_names_and_ids_resolve_with_mention_boundaries(product, body, expected):
    with product.transaction() as db:
        db.get(Actor, 'twin-maya').name = 'Anna'
        db.get(Actor, 'twin-jordan').name = 'Ann'
    message = append_duet(product, GROUP, 'owner-alex', body)
    classifier = route(product, message['id'], [])
    assert set(candidates(product, message['id'])) == expected
    roster = classifier.call_args.args[4]
    assert {actor['kind'] for actor in roster} == {'twin'}
