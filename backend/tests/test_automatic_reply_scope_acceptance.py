"""External human chat commands cannot enter Duet sessions or bypass owner consent."""
from uuid import uuid4

import pytest
from duet_test_support import append_duet, seed_collaborations
from sqlalchemy import func, select
from test_group_trigger_matrix import candidates, route
from test_memory_dream import product as product  # noqa: PLC0414
from test_send_ack import send_clients as send_clients  # noqa: PLC0414
from test_send_ack import sender_socket

from app.models import ReplyRequest


@pytest.mark.parametrize('chat_id', ['group-alex-maya', 'group-team'])
def test_human_http_messages_do_not_schedule_model_work(send_clients, chat_id):
    product, sender, _ = send_clients
    response = sender.post(f'/api/chats/{chat_id}/messages', json={'body': '@twin-maya can you help?', 'client_request_key': uuid4().hex})
    assert response.status_code == 200 and response.json()['request'] is None
    assert product.pending_routes({chat_id}) == []
    assert candidates(product, response.json()['message']['id']) == {}


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
    route(product, message['id'], ['owner-maya'])
    product.process_candidates({'duet-alex-maya'})
    assert candidates(product, message['id'])['owner-maya']['state'] == 'accepted'
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
