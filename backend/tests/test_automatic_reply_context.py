"""Forum notifications ignore owner presence and keep independent task lineage."""
import json
from uuid import uuid4

from duet_test_support import request_duet_reply
from sqlalchemy import select
from test_credentials import product as product  # noqa: PLC0414
from test_duet_conversations import active

from app.models import ReplyRequest


def test_mentioned_update_is_accepted_before_read_and_retry_preserves_its_chain(product):
    row, context, _ = active(product)
    chat_id = row['chat_id']
    posted = product._runtime_tool_transaction('post_duet_reply', {
        'chat_id': chat_id, 'body': 'Please review this specific requirement.',
        'mention_owner_ids': ['owner-maya'],
    }, {**context, 'operation_id': uuid4().hex})
    receiver = request_duet_reply(product, 'owner-alex', chat_id, posted['message_id'], 'twin-maya')
    request_id = receiver['id']
    product.read('owner-maya', chat_id, [posted['message_id']])
    with product.transaction() as db:
        request = db.get(ReplyRequest, request_id)
        assert request.target_twin_id == 'twin-maya' and request.source == 'duet_topic_mention'
        assert request.source_message_id is None and request.status == 'queued'
        chain_id = request.causal_chain_id
        assert chain_id != context['causal_chain_id']
        assert len(list(db.scalars(select(ReplyRequest).where(
            ReplyRequest.intent_key == request.intent_key)))) == 1
    actual = product.get_runtime_context(request_id)
    assert 'automatic_reply' not in actual
    assert actual['causal_chain_id'] == chain_id and actual['chat']['kind'] == 'duet'
    assert actual['messages'] == [] and actual['source_message'] is None and actual['summary'] is None
    assert json.loads(actual['input_body'])['update_id'] == posted['message_id']
    assert 'specific requirement' not in actual['input_body']
    product.fail_request(request_id, 'Synthetic failed attempt')
    retry = product.retry_request('owner-maya', request_id, uuid4().hex)
    retried = product.get_runtime_context(retry['id'])
    assert retried['causal_chain_id'] == chain_id
    assert retried['messages'] == [] and retried['input_body'] == actual['input_body']
    assert 'automatic_reply' not in retried
