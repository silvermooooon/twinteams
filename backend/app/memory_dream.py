"""Incremental owner memory maintenance on the bounded task admission and deadlines."""
import json
import os
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select

from .config import settings
from .models import (
    Actor,
    Chat,
    ChatAttachment,
    MemoryDreamState,
    MemoryEditAudit,
    Message,
    ReplyRequest,
    now,
)

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


def message_namespace(chat):
    return f"twin-chat:{chat.id}:generation:{chat.reset_generation}"


def _message_evidence(service, db, chat, member, message):
    if (message.chat_id != chat.id or message.state != "published"
            or message.kind not in {"human", "twin", "reply_for_me"}
            or not service.visible_message(db, member, message.id, False)):
        return None
    origin = db.get(ReplyRequest, message.request_id) if message.request_id else None
    if message.kind == "reply_for_me" and (not origin or origin.status != "completed"):
        return None
    author = db.get(Actor, message.author_id)
    attachments = [db.get(ChatAttachment, aid) for aid in message.attachment_ids or []]
    value = {"source": {"session_id": message_namespace(chat), "event_id": message.id},
        "role": "user" if message.kind == "human" else "assistant",
        "id": message.id, "seq": message.seq, "kind": message.kind,
        "author_id": message.author_id, "author_owner_id": author.owner_id if author else None,
        "author_name": author.name if author else "Unknown", "created_at": message.created_at.isoformat(),
        "body": message.body, "reply_to_message_id": message.reply_to_message_id,
        "attachments": [{"id": a.id, "name": a.name, "mime_type": a.mime_type} for a in attachments
                        if a and (not a.private_owner_input or a.owner_id == member.owner_id)]}
    if message.raw_owner_input is not None:
        value["raw_owner_input"] = message.raw_owner_input
    value["full_content_hash"] = service_hash(value)
    fields = [field for field in ("body", "raw_owner_input") if value.get(field)]
    remaining = 90 * 1024
    for index, field in enumerate(sorted(fields, key=lambda name: len(value[name].encode("utf-8")))):
        original = value[field]
        encoded_size = len(json.dumps(original, ensure_ascii=False).encode("utf-8")) - 2
        allowance = remaining // (len(fields) - index)
        remaining -= min(encoded_size, allowance)
        if encoded_size > allowance or len(original) > 48000:
            # Stay below safe_text's per-string display cap; truncation must be explicit.
            lower, upper = 0, min(len(original), 48000)
            while lower < upper:
                middle = (lower + upper + 1) // 2
                if len(json.dumps(original[:middle], ensure_ascii=False).encode("utf-8")) - 2 <= allowance:
                    lower = middle
                else:
                    upper = middle - 1
            value[field] = original[:lower]
            value.setdefault("truncated_fields", {})[field] = {"original_bytes": len(original.encode("utf-8")),
                "included_bytes": len(value[field].encode("utf-8"))}
    return value


def _message_batch(service, db, state):
    """One indexed scan page; never step over an unconsumed mutable/oversized row."""
    chat, member = service.membership(db, state.owner_id, state.chat_id)
    cursor = state.message_cursor_seq or 0
    rows = list(db.scalars(select(Message).where(Message.chat_id == chat.id, Message.seq > cursor)
        .order_by(Message.seq).limit(100)))
    batch, through, size = [], cursor, 0
    for row in rows:
        origin = db.get(ReplyRequest, row.request_id) if row.kind == "reply_for_me" and row.request_id else None
        if row.state == "published" and origin and origin.status in {"queued", "running"}:
            break  # The same published bubble can still change body without changing seq.
        value = _message_evidence(service, db, chat, member, row)
        if value is not None:
            from .prompt_safety import safe_text
            amount = len(safe_text(value).encode("utf-8"))
            if batch and (len(batch) >= BATCH_SIZE or size + amount > 96 * 1024):
                break
            batch.append((row, value))
            size += amount
        through = row.seq
    return batch, through


def message_published(service, db, chat, message):
    if (chat.kind != "private" or message.state != "published"
            or message.kind not in {"human", "twin", "reply_for_me"}
            or not (os.getenv("AGENT_MEMORY_DIR") or settings.local_identity_enabled)):
        return
    state = db.get(MemoryDreamState, chat.private_owner_id)
    if state is None:
        state = MemoryDreamState(owner_id=chat.private_owner_id, chat_id=chat.id,
            cursor_seq=0, message_cursor_seq=0, last_status="scheduled",
            due_at=now() + timedelta(seconds=settings.memory_dream_interval_seconds))
        db.add(state)
    elif (not state.queued_request_id and state.last_status != "recovery_required"
            and state.due_at is None and message.seq > (state.message_cursor_seq or 0)):
        state.due_at, state.last_status = now() + timedelta(seconds=settings.memory_dream_interval_seconds), "scheduled"


def _message_payload(batch, through, state):
    return {"scope": "private_messages", "owner_id": state.owner_id,
        "cursor_before": state.message_cursor_seq or 0, "through_seq": through,
        "messages": [{"id": row.id, "seq": row.seq, "hash": service_hash(value)} for row, value in batch]}


def _manual_edits(db, owner_id, since):
    rows = list(db.scalars(select(MemoryEditAudit).where(MemoryEditAudit.owner_id == owner_id,
        MemoryEditAudit.created_at >= since, MemoryEditAudit.action.in_(["write", "delete"]), MemoryEditAudit.state.in_(["published", "requested", "recovery_required"]))
        .order_by(MemoryEditAudit.created_at, MemoryEditAudit.id).limit(101)))
    if len(rows) > 100:
        raise HTTPException(409, "Too many manual memory edits in this batch; reduce the experience batch")
    return [{"session_id": f"twin-config:{owner_id}", "event_id": row.id, "key": row.key,
        "action": row.action, "content": row.content, "created_at": row.created_at.isoformat(), "state": row.state} for row in rows]


def experience_completed(service, db, request):
    # Completion stabilizes mutable reply-for-me projections. Other private messages
    # already schedule at publication, including proactive reports without a user turn.
    chat = db.get(Chat, request.chat_id)
    if chat.kind == "private":
        message = db.scalar(select(Message).where(Message.chat_id == chat.id, Message.request_id == request.id)
                            .order_by(Message.seq.desc()).limit(1))
        if message:
            message_published(service, db, chat, message)


def context(service, db, request):
    owner_id = db.get(Actor, request.target_twin_id).owner_id
    chat, member = service.membership(db, owner_id, request.chat_id)
    state = db.get(MemoryDreamState, owner_id)
    if (chat.kind != "private" or chat.private_owner_id != owner_id or request.requester_id != owner_id
            or not state or state.queued_request_id != request.id):
        raise HTTPException(403, "Memory dream has no matching owner scheduling intent")
    payload = json.loads(request.input_body)
    message_scope = payload.get("scope") == "private_messages"
    cursor = (state.message_cursor_seq or 0) if message_scope else state.cursor_seq
    if payload["owner_id"] != owner_id or payload["cursor_before"] != cursor:
        raise HTTPException(409, "Memory dream cursor changed")
    evidence = []
    if message_scope:
        for reference in payload["messages"]:
            message = db.get(Message, reference["id"])
            value = _message_evidence(service, db, chat, member, message) if message else None
            if (not value or message.seq != reference["seq"] or service_hash(value) != reference["hash"]
                    or not cursor < message.seq <= payload["through_seq"]):
                raise HTTPException(409, "Private message evidence is no longer authorized or changed")
            evidence.append(value)
    for reference in payload.get("experiences", []):
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
    times = ([db.get(Message, reference["id"]).created_at for reference in payload["messages"]] if message_scope else
             [db.get(ReplyRequest, reference["request_id"]).accepted_at for reference in payload["experiences"]])
    if not times and not audit:
        raise HTTPException(409, "Memory dream has no authorized sources")
    since = min(times) if times else audit.created_at
    if _manual_edits(db, owner_id, since) != payload.get("manual_edits", []):
        raise HTTPException(409, "Manual memory edits changed; start a fresh dream attempt")
    return {"owner_id": owner_id, "tenant_id": settings.tenant_id, "chat": {"id": chat.id, "kind": "private"},
        "chat_id": chat.id, "request_id": request.id, "origin_request_id": request.id,
        "target_twin_id": request.target_twin_id, "requester_id": owner_id,
        "private_generation": request.private_generation, "memory_dream": payload,
        "memory_dream_evidence": evidence, "messages": [], "summary": None}


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
            batch, through = _message_batch(service, db, state)
            if not batch:
                # Skipping only unavailable/non-evidence rows needs no model or Git publication.
                state.message_cursor_seq = through
                more = through < chat.next_message_seq - 1
                state.due_at = now() + timedelta(seconds=settings.memory_dream_interval_seconds) if more else None
                state.last_status = "scheduled" if more else "idle"
                continue
            try:
                edits = _manual_edits(db, owner_id, min(row.created_at for row, _ in batch))
            except HTTPException as exc:
                state.last_status, state.last_error = "failed", str(exc.detail)[:1000]
                state.due_at = now() + timedelta(seconds=settings.memory_dream_interval_seconds)
                continue
            payload = {**_message_payload(batch, through, state), "manual_edits": edits}
            key = "memory-dream:" + service_hash({**payload, "attempt": now().isoformat()})
            request = service.accept_request(db, chat, service.twin(db, owner_id).id, owner_id,
                "memory_dream", key, intent_key=key, input_body=json.dumps(payload),
                causal_chain_id=batch[-1][0].causal_chain_id, source_owner_id=owner_id)
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
        message_scope = payload.get("scope") == "private_messages"
        cursor = (state.message_cursor_seq or 0) if message_scope else state.cursor_seq
        if (not result or result.get("published") is not True or not result.get("version")
                or result.get("cursor_before") != cursor
                or result.get("through_seq") != payload["through_seq"]):
            raise HTTPException(409, "Memory dream has no confirmed publication")
        if message_scope:
            state.message_cursor_seq = payload["through_seq"]
        else:
            state.cursor_seq = payload["through_seq"]
        state.last_version = result["version"]
        state.last_success_at, state.last_status, state.last_error = now(), "published", None
        state.queued_request_id = None
        state.due_at = now() + timedelta(seconds=settings.memory_dream_interval_seconds) if (
            not message_scope or (state.message_cursor_seq or 0) < db.get(Chat, state.chat_id).next_message_seq - 1) else None
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
    """An explicit owner action; deduplicate against the existing task intent."""
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
            state = MemoryDreamState(owner_id=owner_id, chat_id=chat.id,
                cursor_seq=0, message_cursor_seq=0, last_status="idle")
            db.add(state)
            db.flush()
        batch, through = _message_batch(service, db, state)
        if not batch and not entries:
            state.message_cursor_seq = through
            more = through < chat.next_message_seq - 1
            state.due_at = now() if more else None
            state.last_status = "scheduled" if more else "idle"
            return {"status": "scheduled" if more else "nothing_to_process", "request_id": None}
        audit = MemoryEditAudit(owner_id=owner_id, private_generation=chat.reset_generation,
            action="dream", key="[dream]", content="Owner requested immediate consolidation of existing Memory and any new authorized experiences.", state="published")
        db.add(audit)
        db.flush()
        since = min(row.created_at for row, _ in batch) if batch else audit.created_at
        payload = {**_message_payload(batch, through, state),
            "memory_only": not bool(batch),
            "manual_request": {"session_id": f"twin-config:{owner_id}", "event_id": audit.id},
            "current_entries_sources": list({(source["session_id"], source["event_id"]): source
                for entry in entries for source in entry["sources"]}.values()),
            "manual_edits": _manual_edits(db, owner_id, since)}
        key = "manual-memory-dream:" + audit.id
        request = service.accept_request(db, chat, service.twin(db, owner_id).id, owner_id,
            "memory_dream", key, intent_key=key, input_body=json.dumps(payload), source_owner_id=owner_id,
            causal_chain_id=batch[-1][0].causal_chain_id if batch else None)
        request.session_id = "dream:" + request.id
        state.queued_request_id, state.due_at, state.last_status = request.id, None, "queued"
        return {"status": "queued", "request_id": request.id}
