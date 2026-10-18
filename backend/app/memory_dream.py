"""Incremental owner memory maintenance on the existing private FIFO and deadlines."""
import json
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import aliased

from .config import settings
from .models import Actor, Chat, MemoryDreamState, MemoryEditAudit, Message, ReplyRequest, now

BATCH_SIZE = 20


def _experience(service, db, request):
    source = request.source
    if source == "retry":
        initial = db.scalar(select(ReplyRequest).where(ReplyRequest.intent_key == request.intent_key,
            ReplyRequest.generation == 0))
        source = initial.source if initial else ""
    if source not in {"private", "reply_for_me", "manual"}:
        return False
    twin = db.get(Actor, request.target_twin_id)
    if not twin or request.requester_id != twin.owner_id:
        return False
    if request.raw_owner_input is not None:
        return True
    message = db.get(Message, request.source_message_id) if request.source_message_id else None
    return bool(message and message.kind == "human" and service.message_owner(db, message) == twin.owner_id)


def _eligible_requests(service, db, state):
    root = aliased(ReplyRequest)
    human = exists(select(Message.id).join(Actor, Actor.id == Message.author_id).where(
        Message.id == ReplyRequest.source_message_id, Message.kind == "human", Actor.owner_id == state.owner_id))
    eligible_source = or_(ReplyRequest.source.in_(["private", "reply_for_me", "manual"]),
        and_(ReplyRequest.source == "retry", exists(select(root.id).where(root.intent_key == ReplyRequest.intent_key,
            root.generation == 0, root.source.in_(["private", "reply_for_me", "manual"])))))
    return select(ReplyRequest).where(ReplyRequest.chat_id == state.chat_id,
        ReplyRequest.target_twin_id == service.twin(db, state.owner_id).id, ReplyRequest.requester_id == state.owner_id,
        eligible_source, or_(ReplyRequest.raw_owner_input.is_not(None), human),
        ReplyRequest.private_generation == db.get(Chat, state.chat_id).reset_generation)


def _batch(service, db, state):
    candidates = db.scalars(_eligible_requests(service, db, state).where(
        ReplyRequest.accepted_seq > state.cursor_seq, ReplyRequest.status == "completed",
        ReplyRequest.cancel_requested.is_(False), ReplyRequest.session_id.is_not(None),
        ReplyRequest.run_id.is_not(None)).order_by(ReplyRequest.accepted_seq).limit(BATCH_SIZE))
    return list(candidates)


def _manual_edits(db, owner_id, since):
    rows = list(db.scalars(select(MemoryEditAudit).where(MemoryEditAudit.owner_id == owner_id,
        MemoryEditAudit.created_at >= since, MemoryEditAudit.action.in_(["write", "delete"]), MemoryEditAudit.state.in_(["published", "requested", "recovery_required"]))
        .order_by(MemoryEditAudit.created_at, MemoryEditAudit.id).limit(101)))
    if len(rows) > 100:
        raise HTTPException(409, "Too many manual memory edits in this batch; reduce the experience batch")
    return [{"session_id": f"twin-config:{owner_id}", "event_id": row.id, "key": row.key,
        "action": row.action, "content": row.content, "created_at": row.created_at.isoformat(), "state": row.state} for row in rows]


def experience_completed(service, db, request):
    chat = db.get(Chat, request.chat_id)
    if chat.kind != "private" or not _experience(service, db, request):
        return
    owner_id = db.get(Actor, request.target_twin_id).owner_id
    state = db.get(MemoryDreamState, owner_id)
    if state is None:
        # Activation starts at this newly completed experience, not all prior history.
        state = MemoryDreamState(owner_id=owner_id, chat_id=chat.id,
            cursor_seq=request.accepted_seq - 1, last_status="scheduled",
            due_at=now() + timedelta(seconds=settings.memory_dream_interval_seconds))
        db.add(state)
    elif request.accepted_seq > state.cursor_seq and not state.queued_request_id and state.due_at is None:
        state.due_at, state.last_status = now() + timedelta(seconds=settings.memory_dream_interval_seconds), "scheduled"


def context(service, db, request):
    owner_id = db.get(Actor, request.target_twin_id).owner_id
    chat, member = service.membership(db, owner_id, request.chat_id)
    state = db.get(MemoryDreamState, owner_id)
    if (chat.kind != "private" or chat.private_owner_id != owner_id or request.requester_id != owner_id
            or not state or state.queued_request_id != request.id):
        raise HTTPException(403, "Memory dream has no matching owner scheduling intent")
    payload = json.loads(request.input_body)
    if payload["owner_id"] != owner_id or payload["cursor_before"] != state.cursor_seq:
        raise HTTPException(409, "Memory dream cursor changed")
    for reference in payload["experiences"]:
        origin = db.get(ReplyRequest, reference["request_id"])
        if (not origin or origin.chat_id != chat.id or origin.status != "completed" or origin.cancel_requested
                or origin.session_id != reference["session_id"] or origin.run_id != reference["run_id"]
                or not _experience(service, db, origin)
                or not service.provenance_allowed(db, member, service.manifest_provenance(db, origin.id))):
            raise HTTPException(409, "Memory experience is no longer authorized")
        if origin.source_message_id:
            service.visible_message(db, member, origin.source_message_id)
    manual = payload.get("manual_request")
    audit = db.get(MemoryEditAudit, manual.get("event_id")) if manual else None
    if manual and (not audit or audit.owner_id != owner_id or audit.action != "dream" or audit.state != "published"
            or audit.private_generation != chat.reset_generation or manual.get("session_id") != f"twin-config:{owner_id}"):
        raise HTTPException(409, "Manual memory dream has no matching owner request")
    times = [db.get(ReplyRequest, reference["request_id"]).accepted_at for reference in payload["experiences"]]
    if not times and not audit:
        raise HTTPException(409, "Memory dream has no authorized sources")
    since = min(times) if times else audit.created_at
    if _manual_edits(db, owner_id, since) != payload.get("manual_edits", []):
        raise HTTPException(409, "Manual memory edits changed; start a fresh dream attempt")
    return {"owner_id": owner_id, "tenant_id": settings.tenant_id, "chat": {"id": chat.id, "kind": "private"},
        "chat_id": chat.id, "request_id": request.id, "origin_request_id": request.id,
        "target_twin_id": request.target_twin_id, "requester_id": owner_id,
        "private_generation": request.private_generation, "memory_dream": payload, "messages": [], "summary": None}


def process(service, scopes=None):
    from agent_runtime.sessions import SessionInfo

    from .personal import make_memory_store
    with service.transaction() as db:
        query = select(MemoryDreamState.owner_id).where(MemoryDreamState.due_at <= now(),
            MemoryDreamState.queued_request_id.is_(None))
        if scopes is not None:
            query = query.where(MemoryDreamState.chat_id.in_(scopes))
        owners = list(db.scalars(query.order_by(MemoryDreamState.due_at).limit(100)))
    for owner_id in owners:
        with service.transaction() as db:
            state = db.get(MemoryDreamState, owner_id)
            chat = db.scalar(select(Chat).where(Chat.id == state.chat_id).with_for_update())
            db.refresh(state)
            if state.queued_request_id or state.due_at is None or state.due_at > now():
                continue
            if make_memory_store(SessionInfo("dream-check", settings.tenant_id, owner_id)) is None:
                state.due_at, state.last_status = None, "disabled"
                continue
            batch = _batch(service, db, state)
            if not batch:
                state.due_at, state.last_status = None, "idle"
                continue
            try:
                edits = _manual_edits(db, owner_id, min(row.accepted_at for row in batch))
            except HTTPException as exc:
                state.last_status, state.last_error = "failed", str(exc.detail)[:1000]
                state.due_at = now() + timedelta(seconds=settings.memory_dream_interval_seconds)
                continue
            payload = {"owner_id": owner_id, "cursor_before": state.cursor_seq,
                "through_seq": batch[-1].accepted_seq,
                "manual_edits": edits,
                "experiences": [{"request_id": row.id, "session_id": row.session_id, "run_id": row.run_id} for row in batch]}
            key = "memory-dream:" + service_hash({**payload, "attempt": now().isoformat()})
            request = service.accept_request(db, chat, service.twin(db, owner_id).id, owner_id,
                "memory_dream", key, intent_key=key, input_body=json.dumps(payload),
                causal_chain_id=batch[-1].causal_chain_id, source_owner_id=owner_id)
            request.session_id = "dream:" + request.id
            state.queued_request_id, state.due_at, state.last_status = request.id, None, "queued"


def service_hash(value):
    from .service import digest
    return digest(value)


def finish(service, db, request, status, result=None, error=None):
    state = db.get(MemoryDreamState, request.requester_id)
    if not state or state.queued_request_id != request.id:
        return
    payload = json.loads(request.input_body)
    if status == "completed":
        if (not result or result.get("published") is not True or not result.get("version")
                or result.get("cursor_before") != state.cursor_seq
                or result.get("through_seq") != payload["through_seq"]):
            raise HTTPException(409, "Memory dream has no confirmed publication")
        state.cursor_seq, state.last_version = payload["through_seq"], result["version"]
        state.last_success_at, state.last_status, state.last_error = now(), "published", None
        state.queued_request_id = None
        state.due_at = now() + timedelta(seconds=settings.memory_dream_interval_seconds) if _batch(service, db, state) else None
    elif state.last_status != "recovery_required":
        state.queued_request_id, state.last_status = None, status
        state.last_error = (error or "Memory dream did not publish")[:1000]
        state.due_at = now() + timedelta(seconds=settings.memory_dream_interval_seconds)


def recovery(service, request_id, error):
    with service.transaction() as db:
        request = db.get(ReplyRequest, request_id)
        if not request or request.source != "memory_dream":
            raise HTTPException(404, "Memory dream not found")
        db.scalar(select(Chat).where(Chat.id == request.chat_id).with_for_update())
        state = db.get(MemoryDreamState, request.requester_id)
        if state and state.queued_request_id == request.id:
            state.last_status, state.last_error, state.due_at = "recovery_required", error[:1000], None


def confirm(service, request_id, version):
    with service.transaction() as db:
        request = db.get(ReplyRequest, request_id)
        if not request or request.source != "memory_dream":
            raise HTTPException(404, "Memory dream not found")
        db.scalar(select(Chat).where(Chat.id == request.chat_id).with_for_update())
        service._check_private_request(db, request)
        payload = json.loads(request.input_body)
        finish(service, db, request, "completed", {"published": True, "version": version,
            "cursor_before": payload["cursor_before"], "through_seq": payload["through_seq"]})


def manual_request(service, owner_id, entries, generation):
    """An explicit owner action; deduplicate against the existing private FIFO intent."""
    from .models import ReplyLane
    with service.transaction() as db:
        chat = db.scalar(select(Chat).where(Chat.private_owner_id == owner_id).with_for_update())
        if chat.reset_pending or chat.reset_generation != generation:
            raise HTTPException(409, "Private reset changed while preparing Memory Dream")
        state = db.get(MemoryDreamState, owner_id)
        if state and state.last_status == "recovery_required":
            raise HTTPException(409, "Memory publication requires reconciliation before another Dream")
        if state and state.queued_request_id:
            return {"status": "already_queued", "request_id": state.queued_request_id}
        if state is None:
            lane = db.get(ReplyLane, (chat.id, service.twin(db, owner_id).id))
            state = MemoryDreamState(owner_id=owner_id, chat_id=chat.id,
                cursor_seq=lane.next_accept_seq - 1 if lane else 0, last_status="idle")
            pending = db.scalar(_eligible_requests(service, db, state).where(
                ReplyRequest.status.in_(["queued", "running"]), ReplyRequest.cancel_requested.is_(False))
                .order_by(ReplyRequest.accepted_seq).limit(1))
            if pending:
                state.cursor_seq = min(state.cursor_seq, pending.accepted_seq - 1)
            db.add(state)
            db.flush()
        batch = _batch(service, db, state)
        if not batch and not entries:
            return {"status": "nothing_to_process", "request_id": None}
        audit = MemoryEditAudit(owner_id=owner_id, private_generation=chat.reset_generation,
            action="dream", key="[dream]", content="Owner requested immediate consolidation of existing Memory and any new authorized experiences.", state="published")
        db.add(audit)
        db.flush()
        since = min(row.accepted_at for row in batch) if batch else audit.created_at
        payload = {"owner_id": owner_id, "cursor_before": state.cursor_seq,
            "through_seq": batch[-1].accepted_seq if batch else state.cursor_seq,
            "experiences": [{"request_id": row.id, "session_id": row.session_id, "run_id": row.run_id} for row in batch],
            "memory_only": not bool(batch),
            "manual_request": {"session_id": f"twin-config:{owner_id}", "event_id": audit.id},
            "current_entries_sources": list({(source["session_id"], source["event_id"]): source
                for entry in entries for source in entry["sources"]}.values()),
            "manual_edits": _manual_edits(db, owner_id, since)}
        key = "manual-memory-dream:" + audit.id
        request = service.accept_request(db, chat, service.twin(db, owner_id).id, owner_id,
            "memory_dream", key, intent_key=key, input_body=json.dumps(payload), source_owner_id=owner_id,
            causal_chain_id=batch[-1].causal_chain_id if batch else None)
        request.session_id = "dream:" + request.id
        state.queued_request_id, state.due_at, state.last_status = request.id, None, "queued"
        return {"status": "queued", "request_id": request.id}
