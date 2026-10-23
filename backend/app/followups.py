"""Thirty-minute explicit-reply followups; no waiting Agent or extra worker queue."""
import json
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import and_, or_, select

from .models import (
    Actor,
    CausalChain,
    Chat,
    ContextManifest,
    DispatchCommand,
    ReplyRequest,
    TwinFollowup,
    now,
)


def create(service, db, origin, owner_id, destination, message, provenance):
    row = TwinFollowup(owner_id=owner_id, chat_id=origin.chat_id,
        origin_request_id=origin.id, target_chat_id=destination.id,
        sent_message_id=message.id, causal_chain_id=origin.causal_chain_id,
        due_at=now() + timedelta(minutes=30), source_provenance=provenance)
    db.add(row)
    db.flush()
    return {"id": row.id, "status": row.status, "due_at": row.due_at.isoformat()}


def reply_scope_ids(service, db, chat_id, reply_to):
    ids = {chat_id}
    if reply_to:
        from .question_models import QuestionRequest
        question = db.scalar(select(QuestionRequest).where(QuestionRequest.message_id == reply_to))
        if question:
            ids.update({question.chat_id, question.origin_chat_id})
        for row in db.scalars(select(TwinFollowup).where(
                TwinFollowup.sent_message_id == reply_to, TwinFollowup.status == "waiting")):
            origin = db.get(ReplyRequest, row.origin_request_id)
            if origin:
                ids.update(service._message_source_chat_ids(db, origin, row.target_chat_id, []))
    return ids


def lock_reply_scopes(service, db, chat_id, reply_to):
    locked = reply_scope_ids(service, db, chat_id, reply_to)
    for scope in sorted(locked):
        db.scalar(select(Chat).where(Chat.id == scope).with_for_update())
    # A competing deadline, cancellation or grant change may have committed
    # while the caller waited for a scope. Never settle from pre-lock ORM data.
    db.expire_all()
    if not reply_scope_ids(service, db, chat_id, reply_to).issubset(locked):
        raise HTTPException(409, "Followup context changed while waiting; retry the same message")


def _authorize(service, db, row):
    origin = db.get(ReplyRequest, row.origin_request_id)
    if not origin or origin.cancel_requested or origin.status in {"cancelled", "failed"}:
        raise HTTPException(409, "Followup origin was cancelled or is unavailable")
    twin = db.get(Actor, origin.target_twin_id)
    chat, member = service.membership(db, row.owner_id, row.chat_id)
    if (not twin or twin.owner_id != row.owner_id or origin.requester_id != row.owner_id
            or origin.chat_id != chat.id or chat.kind != "private" or chat.private_owner_id != row.owner_id
            or origin.causal_chain_id != row.causal_chain_id):
        raise HTTPException(403, "Followup has no matching owner private origin")
    _, recipient = service.membership(db, row.owner_id, row.target_chat_id)
    sent = service.visible_message(db, recipient, row.sent_message_id)
    if sent.author_id != twin.id:
        raise HTTPException(403, "Followup message has another author")
    sources = service.manifest_provenance(db, origin.id)
    if not service.provenance_allowed(db, member, [*row.source_provenance, *sources]):
        raise HTTPException(409, "Followup source context is no longer authorized")
    reply = service.visible_message(db, recipient, row.reply_message_id) if row.reply_message_id else None
    return origin, twin, chat, member, sent, reply, sources


def data(service, db, row):
    origin, _, _, _, sent, reply, _ = _authorize(service, db, row)
    return {"id": row.id, "status": "reply" if row.status == "replied" else "timeout",
        "origin_request_id": origin.id, "target_chat_id": row.target_chat_id,
        "outbound_message_id": sent.id, "outbound_body": sent.body,
        "reply_message_id": reply.id if reply else None, "reply_body": reply.body if reply else None,
        "reply_author_id": reply.author_id if reply else None,
        "reply_author_owner_id": service.message_owner(db, reply) if reply else None}


def notify(service, db, row):
    if row.notification_state not in {"pending", "paused"} or row.continuation_request_id:
        return
    try:
        origin, twin, chat, member, _, reply, sources = _authorize(service, db, row)
    except HTTPException:
        row.notification_state, row.pause_reason = "blocked", "source_unavailable"
        return
    payload = data(service, db, row)
    body = json.dumps({"followup_result": payload}, ensure_ascii=False)
    key = f"followup:{row.id}"
    source_owner = service.message_owner(db, reply) if reply else row.owner_id
    reservation, reason = service.reserve_automatic(db, row.causal_chain_id, key, source_owner, row.owner_id, body)
    if reason:
        row.notification_state, row.pause_reason = "paused", reason
        return
    continuation = service.accept_request(db, chat, twin.id, row.owner_id, "followup_result", key,
        source_message_id=origin.source_message_id, intent_key=key, input_body=body,
        causal_chain_id=row.causal_chain_id, automatic_reservation_id=reservation.id,
        source_owner_id=source_owner)
    continuation.execution_config = json.loads(json.dumps(origin.execution_config)) if origin.execution_config is not None else None
    if reply:
        sources = [*sources, {"kind": "chat_context", "chat_id": row.target_chat_id,
            "sources": [{"kind": "message", "id": reply.id, "seq": reply.seq}]}]
    sources = list({service_hash(source): source for source in [*row.source_provenance, *sources]}.values())
    db.add(ContextManifest(request_id=continuation.id, owner_id=row.owner_id,
        grant_version=member.grant_version, high_water_seq=chat.next_message_seq - 1,
        sources=sources, source_digest=service_hash(sources)))
    row.notification_state, row.pause_reason = "accepted", None
    row.continuation_request_id = continuation.id


def service_hash(value):
    from .service import digest
    return digest(value)


def consume_reply(service, db, message):
    if not message.reply_to_message_id or message.state != "published":
        return
    row = db.scalar(select(TwinFollowup).where(TwinFollowup.sent_message_id == message.reply_to_message_id,
        TwinFollowup.target_chat_id == message.chat_id).with_for_update().execution_options(populate_existing=True))
    if not row or row.status != "waiting" or service.message_owner(db, message) == row.owner_id:
        return
    # The reply and deadline compete on the same scope/row locks. The first
    # committed terminal fact wins, including a reply arriving near the deadline.
    row.status, row.reply_message_id, row.settled_at = "replied", message.id, now()
    row.notification_state = "pending"
    notify(service, db, row)


def consumed_owners(db, message):
    if not message.reply_to_message_id:
        return set()
    return set(db.scalars(select(TwinFollowup.owner_id).where(
        TwinFollowup.sent_message_id == message.reply_to_message_id,
        TwinFollowup.target_chat_id == message.chat_id,
        or_(and_(TwinFollowup.status == "replied", TwinFollowup.reply_message_id == message.id),
            TwinFollowup.status == "timed_out"))))


def continuation(db, request, owner_id):
    return db.scalar(select(TwinFollowup).join(ReplyRequest,
        TwinFollowup.continuation_request_id == ReplyRequest.id).where(
        ReplyRequest.intent_key == request.intent_key,
        ReplyRequest.chat_id == request.chat_id, ReplyRequest.target_twin_id == request.target_twin_id,
        TwinFollowup.owner_id == owner_id, TwinFollowup.notification_state == "accepted"))


def context(service, db, request, owner_id):
    row = continuation(db, request, owner_id)
    if not row or row.status not in {"replied", "timed_out"}:
        raise HTTPException(403, "Followup continuation has no matching completed followup")
    return data(service, db, row)


def process(service, scopes=None):
    with service.transaction() as db:
        query = select(TwinFollowup.id).outerjoin(CausalChain, CausalChain.id == TwinFollowup.causal_chain_id).where(or_(
            and_(TwinFollowup.status == "waiting", TwinFollowup.due_at <= now()),
            TwinFollowup.notification_state == "pending",
            and_(TwinFollowup.notification_state == "paused", TwinFollowup.pause_reason == "budget_exhausted",
                 CausalChain.spent < CausalChain.allowance)))
        if scopes is not None:
            query = query.where(or_(TwinFollowup.chat_id.in_(scopes), TwinFollowup.target_chat_id.in_(scopes)))
        rows = list(db.scalars(query.order_by(TwinFollowup.due_at).limit(100)))
    for followup_id in rows:
        with service.transaction() as db:
            row = db.get(TwinFollowup, followup_id)
            origin = db.get(ReplyRequest, row.origin_request_id)
            ids = service._message_source_chat_ids(db, origin, row.target_chat_id, []) if origin else {row.chat_id, row.target_chat_id}
            for chat_id in sorted(ids):
                db.scalar(select(Chat).where(Chat.id == chat_id).with_for_update())
            db.expire_all()
            db.refresh(row, with_for_update=True)
            if row.status == "waiting" and row.due_at <= now():
                row.status, row.settled_at, row.notification_state = "timed_out", now(), "pending"
            if row.status in {"replied", "timed_out"}:
                notify(service, db, row)


def cancel(db, request):
    for row in db.scalars(select(TwinFollowup).where(TwinFollowup.origin_request_id == request.id)):
        if row.status == "waiting":
            row.status, row.settled_at = "cancelled", now()
            row.notification_state, row.pause_reason = "blocked", "origin_cancelled"
        if row.continuation_request_id:
            pending = db.get(ReplyRequest, row.continuation_request_id)
            if pending and pending.status in {"queued", "running"}:
                pending.cancel_requested = True
                if pending.status == "queued":
                    pending.status = "cancelled"
                command = db.get(DispatchCommand, pending.id)
                if command:
                    command.kind, command.state = "cancel", "pending"
