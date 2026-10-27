"""Explicit approved-conversation fixtures; production admission guards stay enabled."""
from uuid import uuid4

from sqlalchemy import select

from app.models import Approval, Chat, DuetConversation, Membership, uid


def approved_chat(product, chat_id, owner_ids=None, *, origin_chat_id=None):
    """Seed completed consent facts for unit fixtures, without a model call."""
    owner_ids = owner_ids or (['owner-alex', 'owner-maya', 'owner-jordan'] if chat_id == 'duet-team'
                             else ['owner-alex', 'owner-maya'])
    with product.transaction() as db:
        existing = db.get(Chat, chat_id)
        if existing:
            assert existing.kind == 'duet'
            return chat_id
        owner = owner_ids[0]
        private = db.scalar(select(Chat).where(Chat.kind == 'private', Chat.private_owner_id == owner))
        request = product.accept_request(db, private, product.twin(db, owner).id, owner,
            'manual', uid(), input_body='Explicit fixture task with completed owner consent')
        request.status = 'completed'
        chat = Chat(id=chat_id, kind='duet', title='Approved Duet fixture')
        db.add(chat)
        db.flush()
        db.add_all([Membership(chat_id=chat_id, owner_id=peer, history_policy='all') for peer in owner_ids])
        db.add(DuetConversation(chat_id=chat_id, origin_chat_id=origin_chat_id or private.id,
            initiating_request_id=request.id, initiating_owner_id=owner,
            operation_id='fixture:' + uid(), purpose='Approved synthetic coordination',
            opening_message='Approved fixture opening', causal_chain_id=request.causal_chain_id,
            status='active'))
        for peer in owner_ids:
            db.add(Approval(id=uid(), chat_id=chat_id, approver_id=peer,
                title='Approved fixture conversation', description='Synthetic consent',
                target='Fixed fixture participants', impact='Conversation only', status='approved',
                sdk_reference={'type': 'duet_conversation', 'scope': 'once',
                    'peer_owner_ids': [other for other in owner_ids if other != peer]}))
    return chat_id


def seed_collaborations(product):
    approved_chat(product, 'duet-alex-maya')
    approved_chat(product, 'duet-team')


def append_duet(product, chat_id, owner_id, body, *, reply_to=None, attachment_ids=(), causal_chain_id=None):
    with product.transaction() as db:
        chat, member = product.membership(db, owner_id, chat_id, True)
        assert chat.kind == 'duet'
        if reply_to and not causal_chain_id:
            causal_chain_id = product.message_chain(db, product.visible_message(db, member, reply_to)).id
        chain_id = causal_chain_id or product.new_causal_chain(db, owner_id).id
        message = product.append_message(db, chat, author_id=product.twin(db, owner_id).id,
            kind='twin', body=body, reply_to_message_id=reply_to,
            attachment_ids=list(attachment_ids), causal_chain_id=chain_id)
        product.route_committed_message(db, chat, message)
        return product.message_json(db, message, member)


def request_duet_reply(product, requester, chat_id, message_id, twin_id, key=None):
    with product.transaction() as db:
        chat, _ = product.membership(db, requester, chat_id, True)
        assert chat.kind == 'duet'
        request = product.accept_request(db, chat, twin_id, requester, 'auto', key or uuid4().hex,
                                         source_message_id=message_id)
        assert request, 'Fixture exhausted the real shared automatic allowance'
        return product.request_json(db, request, requester)


def send_message(product, owner_id, chat_id, body, key=None, *, reply_to=None, **kwargs):
    """Explicitly publish as an approved peer Duet; retain real human/private APIs."""
    if chat_id.startswith('duet-'):
        assert not kwargs, f'Unsupported Duet fixture publication options: {kwargs}'
        message = append_duet(product, chat_id, owner_id, body, reply_to=reply_to)
        return {'message': message, 'request': None}
    return product.send_message(owner_id, chat_id, body, key or uuid4().hex, reply_to=reply_to, **kwargs)
