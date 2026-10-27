"""Approved Duet routing ignores owner presence and keeps shared causal intent."""
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_credentials import product as product  # noqa: PLC0414
from test_duet_conversations import active

from app.models import ReplyRequest


@pytest.mark.parametrize('presence', ['online', 'offline', 'unknown'])
def test_duet_admission_has_no_human_presence_or_read_delay(product, presence):
    row, context, _ = active(product)
    chat_id = row['chat_id']
    opening = product.chat_detail('owner-maya', chat_id)['messages'][0]
    product.read('owner-maya', chat_id, [opening['id']])
    if presence == 'unknown':
        product._presence_cache.pop('owner-maya', None)
    else:
        product._presence_cache['owner-maya'] = {'expires_at': 9999999999 if presence == 'online' else 0,
                                               'focused_chats': [chat_id]}
    decision_id = product.pending_routes({chat_id})[0]
    product._settle_route(decision_id, ['owner-maya'], True, 'An answer helps', None)
    product.process_candidates({chat_id})
    with product.transaction() as db:
        request = db.scalar(select(ReplyRequest).where(ReplyRequest.chat_id == chat_id))
        assert request and request.target_twin_id == 'twin-maya'
        request_id = request.id
    actual = product.get_runtime_context(request_id)
    assert 'automatic_reply' not in actual
    assert actual['causal_chain_id'] == context['causal_chain_id']
    assert actual['chat']['kind'] == 'duet'
    assert len(actual['messages']) == 1
    with product.transaction() as db:
        db.get(ReplyRequest, request_id).status = 'failed'
    retry = product.retry_request('owner-maya', request_id, uuid4().hex)
    retried = product.get_runtime_context(retry['id'])
    assert retried['causal_chain_id'] == context['causal_chain_id']
    assert 'automatic_reply' not in retried
