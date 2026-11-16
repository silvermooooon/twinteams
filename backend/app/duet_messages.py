"""Forum publication and explicit, metadata-only topic notifications."""
import json

from fastapi import HTTPException
from sqlalchemy import select

from .duet_conversations import require_active
from .models import Actor, DuetConversation, DuetPostRevision, Message, ReplyRequest, now


def validate_mentions(service, db, chat, values):
    if (not isinstance(values, list) or len(values) > 100
            or any(not isinstance(value, str) for value in values)
            or len(set(values)) != len(values)):
        raise HTTPException(400, "mention_owner_ids must contain distinct participant owner IDs")
    owners = {member.owner_id for member in service.members(db, chat.id)}
    if not set(values).issubset(owners):
        raise HTTPException(403, "Only current topic participants can be mentioned; ask the administrator to invite others")
    return values


def notify_update(service, db, chat, owner_id, event, update_id, revision, mentions=()):
    require_active(db, chat)
    conversation = db.get(DuetConversation, chat.id)
    recipients = set(mentions) - {owner_id}
    recipients.add(conversation.initiating_owner_id)
    result = []
    for recipient in sorted(recipients):
        source = "duet_topic_update" if recipient == conversation.initiating_owner_id else "duet_topic_mention"
        key = f"duet-topic:{event}:{update_id}:{revision}:{recipient}"
        payload = {"topic_id": chat.id, "update_id": update_id, "revision": revision,
            "event": event, "mentioned": recipient in mentions}
        request = service.accept_request(db, chat, service.twin(db, recipient).id,
            owner_id, source, key, intent_key=key, source_owner_id=owner_id,
            input_body=json.dumps(payload, separators=(",", ":")))
        result.append(request.id)
    return result


def deliver(service, db, chat, message):
    require_active(db, chat)
    if chat.kind != "duet" or message.state != "published":
        return []
    return notify_update(service, db, chat, service.message_owner(db, message),
        "post_created" if message.revision == 1 else "post_edited", message.id,
        message.revision, message.mention_owner_ids)


def publish(service, db, chat, message):
    require_active(db, chat)
    if message.state == "withdrawn":
        return False
    if message.state == "published":
        deliver(service, db, chat, message)
        return True
    if message.request_id:
        request = db.get(ReplyRequest, message.request_id)
        if not request:
            raise HTTPException(409, "The originating request no longer exists")
        actor = db.get(Actor, request.target_twin_id)
        service._authorize_generation(db, {"request_id": request.id,
            "owner_id": actor.owner_id, "private_generation": request.private_generation})
    sender = db.get(Actor, message.author_id)
    if not sender or sender.kind != "twin":
        raise HTTPException(403, "Only a participating Duet can publish in this topic")
    member = service.membership(db, sender.owner_id, chat.id)[1]
    if (not service.sources_allowed(db, member, message.source_message_ids, message.source_floor_seq)
            or not service.provenance_allowed(db, member, message.source_provenance)):
        raise HTTPException(409, "Pending post sources are no longer authorized")
    if message.reply_to_message_id:
        service.visible_message(db, member, message.reply_to_message_id)
    message.seq, message.state = chat.next_message_seq, "published"
    chat.next_message_seq += 1
    db.flush()
    from .memory_dream import message_published
    message_published(service, db, chat, message)
    deliver(service, db, chat, message)
    return True


def edit_post(service, db, args, context):
    _, twin, _, _, _ = service._message_tool_origin(db, context)
    chat, member = service.membership(db, twin.owner_id, args.get("chat_id"), True)
    require_active(db, chat)
    message = service.visible_message(db, member, args.get("message_id"))
    if chat.kind != "duet" or message.chat_id != chat.id or message.author_id != twin.id:
        raise HTTPException(403, "A Duet can edit only its own posts in this topic")
    body = args.get("body")
    expected = args.get("expected_revision")
    if not isinstance(body, str) or not body.strip():
        raise HTTPException(400, "Post body must contain text")
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
        raise HTTPException(400, "expected_revision must be a positive integer")
    mentions = validate_mentions(service, db, chat, args.get("mention_owner_ids", []))
    if message.revision != expected:
        raise HTTPException(409, "Post changed; read it before editing again")
    if message.body == body.strip() and message.mention_owner_ids == mentions:
        return {"chat_id": chat.id, "message_id": message.id, "revision": message.revision, "changed": False}
    db.add(DuetPostRevision(message_id=message.id, revision=message.revision,
        body=message.body, mention_owner_ids=message.mention_owner_ids,
        created_at=message.edited_at or message.created_at))
    message.body = body.strip()
    message.mention_owner_ids = mentions
    message.revision += 1
    message.edited_at = now()
    db.flush()
    deliver(service, db, chat, message)
    return {"chat_id": chat.id, "message_id": message.id, "revision": message.revision,
        "edited_at": message.edited_at.isoformat(), "changed": True}


def post_history(service, db, args, context):
    owner_id = context["owner_id"]
    chat, member = service.membership(db, owner_id, args.get("chat_id"))
    message = service.visible_message(db, member, args.get("message_id"))
    if chat.kind != "duet" or message.chat_id != chat.id:
        raise HTTPException(404, "Topic post not found")
    limit, before = args.get("limit", 30), args.get("before_revision")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise HTTPException(400, "limit must be between 1 and 100")
    if before is not None and (isinstance(before, bool) or not isinstance(before, int) or before < 1):
        raise HTTPException(400, "before_revision must be a positive integer")
    query = select(DuetPostRevision).where(DuetPostRevision.message_id == message.id)
    if before is not None:
        query = query.where(DuetPostRevision.revision < before)
    rows = list(db.scalars(query.order_by(DuetPostRevision.revision.desc()).limit(limit + 1)))
    revisions = [{"revision": row.revision, "body": row.body,
        "mention_owner_ids": row.mention_owner_ids, "created_at": row.created_at.isoformat()} for row in rows]
    if before is None or message.revision < before:
        revisions.insert(0, {"revision": message.revision, "body": message.body,
            "mention_owner_ids": message.mention_owner_ids,
            "created_at": (message.edited_at or message.created_at).isoformat()})
    has_more = len(revisions) > limit
    revisions = revisions[:limit]
    return {"chat_id": chat.id, "message_id": message.id, "revisions": revisions,
        "has_more": has_more, "before_revision": revisions[-1]["revision"] if revisions else None}


def revision_source(db, message, revision):
    from .service import digest
    row = message if message.revision == revision else db.get(DuetPostRevision, (message.id, revision))
    if row is None:
        raise HTTPException(404, "Post revision not found")
    return {"kind": "duet_post_revision", "id": message.id, "revision": revision,
        "seq": message.seq, "hash": digest([row.body, row.mention_owner_ids])}


def revision_allowed(service, db, member, source):
    message = db.get(Message, source.get("id"))
    if not message or not service.message_visible(db, member, message):
        return False
    row = message if message.revision == source.get("revision") else db.get(
        DuetPostRevision, (message.id, source.get("revision")))
    if row is None:
        return False
    from .service import digest
    return source.get("hash") == digest([row.body, row.mention_owner_ids])


def pending_dto(message):
    return {"id": message.id, "chat_id": message.chat_id, "causal_chain_id": message.causal_chain_id,
        "author_id": message.author_id, "body": message.body, "state": "pending",
        "created_at": message.created_at.isoformat(), "can_cancel": True}


def view(service, db, chat, member):
    own_twin = service.twin(db, member.owner_id)
    pending = db.scalars(select(Message).where(Message.chat_id == chat.id,
        Message.state == "pending", Message.author_id == own_twin.id).order_by(Message.created_at, Message.id))
    return {"pending_messages": [pending_dto(message) for message in pending]}


def cancel_pending(service, owner_id, chat_id, message_id):
    with service.transaction() as db:
        service.membership(db, owner_id, chat_id, True)
        message = db.get(Message, message_id)
        if not message or message.chat_id != chat_id or message.author_id != service.twin(db, owner_id).id:
            raise HTTPException(404, "Pending post not found")
        if message.state == "published":
            raise HTTPException(409, "This post has already been published")
        message.state = "withdrawn"
        return {"id": message.id, "state": "withdrawn"}
