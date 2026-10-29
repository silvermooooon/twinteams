"""Approved Duet routing ignores owner presence and keeps shared causal intent."""
from uuid import uuid4

from sqlalchemy import select
from test_credentials import product as product  # noqa: PLC0414
from test_duet_conversations import active

from app.models import ReplyRequest


def test_published_message_is_accepted_before_read_and_retry_preserves_chain(product):
    row, context, _ = active(product)
    chat_id = row['chat_id']
    opening = product.chat_detail('owner-maya', chat_id)['messages'][0]
    product.read('owner-maya', chat_id, [opening['id']])
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
