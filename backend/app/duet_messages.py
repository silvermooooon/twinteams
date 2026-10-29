"""Atomic channel publication and bounded automatic sends, independent of task completion."""
from fastapi import HTTPException
from sqlalchemy import func, select

from .duet_conversations import require_active
from .models import Actor, AllowanceGrant, CausalChain, Chat, Message, ReplyRequest

SEND_ALLOWANCE = 6


def deliver(service, db, chat, message):
    require_active(db, chat)
    if chat.kind != "duet" or message.state != "published":
        return []
    sender = service.message_owner(db, message)
    result = []
    for member in service.members(db, chat.id):
        if member.owner_id == sender:
            continue
        key = f"duet-message:{message.id}:{member.owner_id}"
        request = service.accept_request(db, chat, service.twin(db, member.owner_id).id,
            sender, "duet_message", key, source_message_id=message.id, intent_key=key,
            causal_chain_id=message.causal_chain_id, source_owner_id=sender)
        result.append(request.id)
    return result


def publish(service, db, chat, message):
    require_active(db, chat)
    if message.state == "withdrawn":
        return False
    if message.state == "published":
        deliver(service, db, chat, message)
        return True
    chain = db.scalar(select(CausalChain).where(CausalChain.id == message.causal_chain_id).with_for_update().execution_options(populate_existing=True))
    if not chain:
        raise HTTPException(409, "A Duet send requires its original causal chain")
    if chain.spent >= chain.allowance:
        return False
    if message.request_id:
        request = db.get(ReplyRequest, message.request_id)
        if not request:
            raise HTTPException(409, "The originating request no longer exists")
        actor = db.get(Actor, request.target_twin_id)
        service._authorize_generation(db, {"request_id": request.id,
            "owner_id": actor.owner_id, "private_generation": request.private_generation})
    sender = db.get(Actor, message.author_id)
    if not sender or sender.kind != "twin":
        raise HTTPException(403, "Only a participating Duet can publish in this channel")
    member = service.membership(db, sender.owner_id, chat.id)[1]
    if not service.message_sources_visible(db, member, message):
        raise HTTPException(409, "Pending send sources are no longer authorized")
    if message.reply_to_message_id:
        service.visible_message(db, member, message.reply_to_message_id)
    message.seq, message.state = chat.next_message_seq, "published"
    chat.next_message_seq += 1
    chain.spent += 1
    db.flush()
    from .memory_dream import message_published
    message_published(service, db, chat, message)
    deliver(service, db, chat, message)
    return True


def pending_dto(message):
    return {"id": message.id, "chat_id": message.chat_id, "causal_chain_id": message.causal_chain_id,
        "author_id": message.author_id, "body": message.body, "state": "pending",
        "created_at": message.created_at.isoformat(), "can_cancel": True}


def view(service, db, chat, member):
    if chat.kind != "duet":
        return {"send_budgets": [], "pending_messages": []}
    counts = dict(db.execute(select(Message.causal_chain_id, func.count()).where(
        Message.chat_id == chat.id, Message.state == "pending").group_by(Message.causal_chain_id)).all())
    from .models import DuetConversation
    conversation = db.get(DuetConversation, chat.id)
    active = bool(conversation and conversation.status == "active")
    budgets = [{"causal_chain_id": chain.id, "spent": chain.spent, "allowance": chain.allowance,
        "remaining": max(0, chain.allowance - chain.spent), "pending_count": counts[chain.id],
        "can_continue": active, "continuation_allowance": SEND_ALLOWANCE}
        for chain in db.scalars(select(CausalChain).where(CausalChain.id.in_(counts)))] if counts else []
    own_twin = service.twin(db, member.owner_id)
    pending = db.scalars(select(Message).where(Message.chat_id == chat.id,
        Message.state == "pending", Message.author_id == own_twin.id).order_by(Message.created_at, Message.id))
    return {"send_budgets": budgets, "pending_messages": [pending_dto(message) for message in pending]}


def continue_sends(service, owner_id, chat_id, chain_id, key, expected_allowance):
    with service.transaction() as db:
        pending_query = select(Message).where(Message.causal_chain_id == chain_id, Message.state == "pending")
        drafts = list(db.scalars(pending_query))
        # Use the same source-chat locks as an ordinary prepared send. A reset,
        # source withdrawal or task stop cannot race the delayed publication.
        scopes = {chat_id}
        for message in drafts:
            request = db.get(ReplyRequest, message.request_id) if message.request_id else None
            scopes.update(service._message_source_chat_ids(db, request, message.chat_id,
                [*(message.source_message_ids or []), *([message.reply_to_message_id] if message.reply_to_message_id else [])]))
        for scope in sorted(scopes):
            db.scalar(select(Chat).where(Chat.id == scope).with_for_update())
        db.expire_all()
        chat, _ = service.membership(db, owner_id, chat_id)
        require_active(db, chat)
        if chat.kind != "duet" or not db.scalar(select(Message.id).where(
                Message.chat_id == chat_id, Message.causal_chain_id == chain_id).limit(1)):
            raise HTTPException(404, "Send chain is not part of this channel")
        chain = db.scalar(select(CausalChain).where(CausalChain.id == chain_id).with_for_update().execution_options(populate_existing=True))
        if not chain:
            raise HTTPException(404, "Send chain not found")
        prior = db.scalar(select(AllowanceGrant).where(AllowanceGrant.owner_id == owner_id,
            AllowanceGrant.client_request_key == key))
        replay = prior is not None
        if prior:
            if (prior.chat_id, prior.causal_chain_id, prior.base_allowance) != (chat_id, chain_id, expected_allowance):
                raise HTTPException(409, "Continue key belongs to another authorization")
            grant = prior
        else:
            if chain.allowance != expected_allowance:
                raise HTTPException(409, "Send allowance changed; refresh before continuing")
            pending = list(db.scalars(select(Message).where(Message.causal_chain_id == chain_id,
                Message.state == "pending").order_by(Message.created_at, Message.id)))
            if not pending:
                raise HTTPException(409, "There are no pending sends")
            for message in pending:
                request = db.get(ReplyRequest, message.request_id) if message.request_id else None
                current = service._message_source_chat_ids(db, request, message.chat_id,
                    [*(message.source_message_ids or []), *([message.reply_to_message_id] if message.reply_to_message_id else [])])
                if not current.issubset(scopes):
                    raise HTTPException(409, "Pending sends changed; retry Continue")
            chain.allowance += SEND_ALLOWANCE
            grant = AllowanceGrant(causal_chain_id=chain_id, chat_id=chat_id, owner_id=owner_id,
                client_request_key=key, amount=SEND_ALLOWANCE, base_allowance=expected_allowance)
            db.add(grant)
        published = []
        if not replay:
            for message in pending:
                if chain.spent >= chain.allowance:
                    break
                target = db.get(Chat, message.chat_id)
                if publish(service, db, target, message) and message.chat_id == chat_id:
                    published.append(message.id)
        db.flush()
        count = db.scalar(select(func.count()).select_from(Message).where(Message.chat_id == chat_id,
            Message.causal_chain_id == chain_id, Message.state == "pending"))
        return {"causal_chain_id": chain.id, "spent": chain.spent, "allowance": chain.allowance,
            "remaining": chain.allowance - chain.spent, "grant_id": grant.id,
            "published_message_ids": published, "pending_count": count, "already_applied": replay}


def cancel_pending(service, owner_id, chat_id, message_id):
    with service.transaction() as db:
        service.membership(db, owner_id, chat_id, True)
        message = db.get(Message, message_id)
        if not message or message.chat_id != chat_id or message.author_id != service.twin(db, owner_id).id:
            raise HTTPException(404, "Pending send not found")
        if message.state == "published":
            raise HTTPException(409, "This message has already been published")
        message.state = "withdrawn"
        return {"id": message.id, "state": "withdrawn"}
