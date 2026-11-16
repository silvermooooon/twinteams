"""External human chat commands cannot enter Duet sessions or bypass owner consent."""
import asyncio
from uuid import uuid4

import pytest
from duet_test_support import append_duet, request_duet_reply, seed_collaborations
from sqlalchemy import func, select
from test_memory_dream import product as product  # noqa: PLC0414
from test_send_ack import send_clients as send_clients  # noqa: PLC0414
from test_send_ack import sender_socket

from app.models import ReplyRequest


@pytest.mark.parametrize('chat_id', ['group-alex-maya', 'group-team'])
def test_human_http_messages_do_not_schedule_model_work(send_clients, chat_id):
    product, sender, _ = send_clients
    response = sender.post(f'/api/chats/{chat_id}/messages', json={'body': '@twin-maya can you help?', 'client_request_key': uuid4().hex})
    assert response.status_code == 200 and response.json()['request'] is None
    with product.transaction() as db:
        assert db.scalar(select(func.count()).select_from(ReplyRequest).where(ReplyRequest.source_message_id == response.json()['message']['id'])) == 0


@pytest.mark.parametrize('command', ['messages', 'reply-requests'])
def test_owner_cannot_write_or_manually_request_inside_duet_thread(send_clients, command):
    product, sender, _ = send_clients
    seed_collaborations(product)
    message = append_duet(product, 'duet-alex-maya', 'owner-maya', 'Approved peer statement')
    data = {'body': 'Owner injected message', 'client_request_key': uuid4().hex} if command == 'messages' else {
        'message_id': message['id'], 'target_twin_id': 'twin-alex', 'client_request_key': uuid4().hex}
    response = sender.post(f'/api/chats/duet-alex-maya/{command}', json=data)
    assert response.status_code == 403


@pytest.mark.parametrize('chat_id', ['group-alex-maya', 'group-team'])
def test_http_and_websocket_cannot_request_another_owners_duet_in_human_chat(send_clients, chat_id):
    product, sender, _ = send_clients
    message = sender.post(f'/api/chats/{chat_id}/messages', json={'body': 'Human question', 'client_request_key': uuid4().hex}).json()['message']
    denied = sender.post(f'/api/chats/{chat_id}/reply-requests', json={'message_id': message['id'],
        'target_twin_id': 'twin-maya', 'client_request_key': uuid4().hex})
    assert denied.status_code == 403
    draft = sender.post(f'/api/chats/{chat_id}/messages', json={'body': 'Human text',
        'reply_for_me': True, 'client_request_key': uuid4().hex})
    assert draft.status_code == 403
    with product.transaction() as db:
        before = db.scalar(select(func.count()).select_from(ReplyRequest))
    command_id = uuid4().hex
    with sender_socket(sender) as socket:
        socket.send_json({'v': 1, 'type': 'command', 'command_id': command_id,
            'chat_id': chat_id, 'command': 'reply',
            'payload': {'message_id': message['id'], 'target_twin_id': 'twin-maya'}})
        rejected = socket.receive_json()
    assert rejected['type'] == 'error' and rejected['status'] == 403
    assert rejected['command_id'] == command_id
    with product.transaction() as db:
        assert db.scalar(select(func.count()).select_from(ReplyRequest)) == before


def test_owner_reads_do_not_consume_or_cancel_approved_duet_work(send_clients):
    product, sender, peer = send_clients
    seed_collaborations(product)
    message = append_duet(product, 'duet-alex-maya', 'owner-alex', 'Question for peer')
    assert peer.post('/api/chats/duet-alex-maya/read', json={'message_ids': [message['id']]}).status_code == 200
    receiver = request_duet_reply(product, 'owner-alex', 'duet-alex-maya', message['id'], 'twin-maya')
    with product.transaction() as db:
        row = db.get(ReplyRequest, receiver['id'])
        assert row.status == 'queued' and row.source_message_id is None
    assert sender.get('/api/duet-conversations/duet-alex-maya').status_code == 200


@pytest.mark.parametrize('target', ['twin-alex', 'twin-maya'])
def test_websocket_manual_duet_thread_request_is_rejected_without_acceptance(send_clients, target):
    product, sender, _ = send_clients
    seed_collaborations(product)
    message = append_duet(product, 'duet-alex-maya', 'owner-maya', 'Approved peer statement')
    with product.transaction() as db:
        before = db.scalar(select(func.count()).select_from(ReplyRequest))
    command_id = uuid4().hex
    with sender_socket(sender) as socket:
        socket.send_json({'v': 1, 'type': 'command', 'command_id': command_id,
            'chat_id': 'duet-alex-maya', 'command': 'reply',
            'payload': {'message_id': message['id'], 'target_twin_id': target}})
        rejected = socket.receive_json()
    assert rejected['type'] == 'error' and rejected['status'] == 403
    assert rejected['command_id'] == command_id
    with product.transaction() as db:
        assert db.scalar(select(func.count()).select_from(ReplyRequest)) == before


@pytest.mark.parametrize('via_tools', [False, True])
def test_notification_recipient_can_inspect_retry_and_cancel_own_work(send_clients, via_tools):
    product, _, peer = send_clients
    seed_collaborations(product)
    message = append_duet(product, 'duet-alex-maya', 'owner-alex',
        'Peer requirements stay in the post', mention_owner_ids=['owner-maya'])
    receiver = request_duet_reply(product, 'owner-alex', 'duet-alex-maya', message['id'], 'twin-maya')
    request_id = receiver['id']
    with product.transaction() as db:
        row = db.get(ReplyRequest, request_id)
        assert row.requester_id == 'owner-alex' and row.target_twin_id == 'twin-maya'
        assert row.source_message_id is None
    queued = peer.get('/api/duet-conversations/duet-alex-maya')
    assert queued.status_code == 200
    visible = next(row for row in queued.json()['requests'] if row['id'] == request_id)
    assert visible['status'] == 'queued' and visible['can_cancel']
    assert 'Peer requirements' not in str(visible)
    private = product.send_message('owner-maya', 'private-maya', 'Inspect my own topic work', uuid4().hex)['request']
    context = product.get_runtime_context(private['id'])

    def tool(name, arguments):
        return asyncio.run(product.runtime_tool(name, arguments, {**context, 'operation_id': uuid4().hex}))

    assert tool('get_reply_request', {'id': request_id})['status'] == 'queued'
    product.fail_request(request_id, 'Synthetic notification failure')
    failed = next(row for row in peer.get('/api/duet-conversations/duet-alex-maya').json()['requests'] if row['id'] == request_id)
    assert failed['status'] == 'failed' and failed['can_retry']
    assert tool('get_reply_request', {'id': request_id})['status'] == 'failed'
    if via_tools:
        retried = tool('retry_reply_request', {'id': request_id})
    else:
        response = peer.post(f'/api/reply-requests/{request_id}/retry', json={'client_request_key': uuid4().hex})
        assert response.status_code == 200
        retried = response.json()
    rows = peer.get('/api/duet-conversations/duet-alex-maya').json()['requests']
    assert request_id not in {row['id'] for row in rows}
    assert any(row['id'] == retried['id'] and row['status'] == 'queued' and row['can_cancel'] for row in rows)
    if via_tools:
        stopped = tool('cancel_reply_request', {'id': retried['id']})
    else:
        response = peer.post(f"/api/reply-requests/{retried['id']}/cancel")
        assert response.status_code == 200
        stopped = response.json()
    assert stopped['status'] == 'cancelled'
    assert product.get_request(retried['id'])['status'] == 'cancelled'
    assert retried['id'] not in {row['id'] for row in peer.get('/api/duet-conversations/duet-alex-maya').json()['requests']}
    assert any(row['id'] == message['id'] for row in peer.get('/api/duet-conversations/duet-alex-maya').json()['messages'])
