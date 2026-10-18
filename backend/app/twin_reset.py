"""Owner-only private reset: durable fence, idempotent local cleanup, no group deletion."""
import shutil
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import delete, select, update

from .config import settings
from .credential_models import CredentialRequest
from .models import (
    ActivityProjection,
    Approval,
    Chat,
    ChatAttachment,
    ContextManifest,
    DispatchCommand,
    MemoryDreamState,
    MemoryEditAudit,
    Message,
    MessageReaction,
    PendingChildResult,
    ProductOperation,
    Reminder,
    ReplyLane,
    ReplyProjection,
    ReplyRequest,
    Summary,
    TrackingRevision,
    TrackingTopic,
    TrackingUpdate,
    TriggerCandidate,
    TwinFollowup,
    TwinReset,
    WorkspaceNode,
)
from .notifications import notifications


def check_request(db, request, *, pending=False):
    chat = db.scalar(select(Chat).where(Chat.id == request.chat_id).execution_options(populate_existing=True))
    if chat.kind == "private" and (request.private_generation != chat.reset_generation or (chat.reset_pending and not pending)):
        raise HTTPException(409, "This private request belongs to a reset generation")
    return chat


def payload(row):
    return {"reset_id": row.id, "status": row.status, "generation": row.generation,
        "private_chat_id": row.chat_id, "idempotency_key": row.idempotency_key,
        "expected_generation": row.expected_generation}


def status(service, owner_id):
    with service.transaction() as db:
        chat = db.scalar(select(Chat).where(Chat.private_owner_id == owner_id))
        if not chat:
            raise HTTPException(404, "Private Twin chat not found")
        row = db.scalar(select(TwinReset).where(TwinReset.owner_id == owner_id).order_by(TwinReset.generation.desc()).limit(1))
        return payload(row) if row else {"reset_id": None, "status": "ready", "generation": chat.reset_generation,
            "private_chat_id": chat.id, "idempotency_key": None, "expected_generation": None}


def fence(service, owner_id, key, expected_generation):
    with service.transaction() as db:
        chat = db.scalar(select(Chat).where(Chat.private_owner_id == owner_id).with_for_update())
        if not chat:
            raise HTTPException(404, "Private Twin chat not found")
        prior = db.scalar(select(TwinReset).where(TwinReset.owner_id == owner_id, TwinReset.idempotency_key == key))
        if prior:
            if prior.expected_generation != expected_generation:
                raise HTTPException(409, "Reset key was used with another generation")
            return payload(prior)
        if chat.reset_pending or expected_generation != chat.reset_generation:
            raise HTTPException(409, "Private Twin generation changed; reload reset status")
        old_generation = chat.reset_generation
        chat.reset_generation, chat.reset_pending = old_generation + 1, True
        row = TwinReset(owner_id=owner_id, chat_id=chat.id, idempotency_key=key,
            expected_generation=old_generation, generation=chat.reset_generation)
        db.add(row)
        db.flush()
        ids = list(db.scalars(select(ReplyRequest.id).where(ReplyRequest.chat_id == chat.id)))
        # Keep FK shells and execution identities for group audit references, erase private payloads.
        for request in db.scalars(select(ReplyRequest).where(ReplyRequest.chat_id == chat.id)):
            request.cancel_requested = True
            if request.status in {"queued", "running"}:
                command = db.get(DispatchCommand, request.id)
                if command:
                    command.kind, command.state = "cancel", "pending"
            request.status = "cancelled"
            request.raw_owner_input = request.input_body = request.frozen_prompt = request.error = None
            request.input_attachment_ids, request.execution_config = [], None
        db.execute(delete(MessageReaction).where(MessageReaction.message_id.in_(select(Message.id).where(Message.chat_id == chat.id))))
        db.execute(update(Message).where(Message.chat_id == chat.id).values(body="", raw_owner_input=None,
            attachment_ids=[], source_message_ids=[], source_provenance=[], state="withdrawn"))
        for member in service.members(db, chat.id):
            member.lower_seq, member.grant_version = chat.next_message_seq, member.grant_version + 1
        for approval in db.scalars(select(Approval).where(Approval.chat_id == chat.id)):
            approval.status, approval.title, approval.description, approval.target, approval.impact = "rejected", "", "", "", ""
            approval.sdk_reference, approval.source_message_ids, approval.source_provenance = {}, [], []
        db.execute(update(PendingChildResult).where(PendingChildResult.chat_id == chat.id).values(
            state="paused", pause_reason="private_reset", body="", notification={}, source_provenance=[]))
        db.execute(update(TwinFollowup).where(TwinFollowup.chat_id == chat.id).values(
            status="cancelled", notification_state="cancelled", source_provenance=[]))
        db.execute(update(CredentialRequest).where(CredentialRequest.chat_id == chat.id).values(
            status="cancelled", reason="", source_provenance=[]))
        db.execute(update(ContextManifest).where(ContextManifest.request_id.in_(ids)).values(sources=[], source_digest="reset"))
        db.execute(update(Summary).where(Summary.chat_id == chat.id).values(body="", source_message_ids=[], source_provenance=[], state="revoked"))
        topic_ids = select(TrackingTopic.id).where(TrackingTopic.chat_id == chat.id)
        db.execute(delete(TrackingUpdate).where(TrackingUpdate.topic_id.in_(topic_ids)))
        db.execute(delete(TrackingRevision).where(TrackingRevision.topic_id.in_(topic_ids)))
        db.execute(delete(TrackingTopic).where(TrackingTopic.chat_id == chat.id))
        db.execute(update(WorkspaceNode).where(WorkspaceNode.chat_id == chat.id).values(source_message_ids=[]))
        db.execute(delete(ReplyProjection).where(ReplyProjection.request_id.in_(ids)))
        db.execute(delete(ActivityProjection).where(ActivityProjection.chat_id == chat.id))
        db.execute(update(ProductOperation).where(ProductOperation.chat_id == chat.id).values(result={"status": "private_reset"}))
        db.execute(delete(Reminder).where(Reminder.chat_id == chat.id, Reminder.owner_id == owner_id))
        db.execute(update(TriggerCandidate).where(TriggerCandidate.chat_id == chat.id).values(state="suppressed", evidence={}))
        db.execute(update(MemoryEditAudit).where(MemoryEditAudit.owner_id == owner_id).values(content=None, key="[reset]", state="reset"))
        db.execute(delete(MemoryDreamState).where(MemoryDreamState.owner_id == owner_id))
        lane = db.get(ReplyLane, (chat.id, service.twin(db, owner_id).id))
        if lane:
            lane.active_request_id, lane.session_id = None, None
        return payload(row)


def cleanup(service, owner_id, reset_id):
    import os

    from agent_runtime.memory import LocalMemoryStore
    from agent_runtime.sessions import SessionInfo

    from .config import ROOT
    with service.transaction() as db:
        chat = db.scalar(select(Chat).where(Chat.private_owner_id == owner_id).with_for_update())
        row = db.get(TwinReset, reset_id)
        if not row or row.owner_id != owner_id:
            raise HTTPException(404, "Reset not found")
        if row.status == "completed":
            return payload(row)
        if chat.reset_generation != row.generation or not chat.reset_pending:
            raise HTTPException(409, "Reset intent no longer matches the private generation")
        # This same Chat row serializes final local Git mutations in ManagedGitMemoryStore.
        base = Path(os.getenv("AGENT_MEMORY_DIR") or ROOT / "data" / "runtime-memory").expanduser().resolve()
        identity = SessionInfo("reset-cleanup", settings.tenant_id, owner_id)
        for generation in range(row.generation):
            directory = base if generation == 0 else base / f".reset-generation-{generation}"
            if directory.is_symlink() or not directory.resolve().is_relative_to(base.resolve()):
                raise ValueError("Memory cleanup generation escapes its root")
            scope = LocalMemoryStore(identity, directory).directory
            if scope.parent != directory.resolve() or scope.is_symlink():
                raise ValueError("Memory cleanup owner scope must not be a symlink")
            if scope.exists():
                shutil.rmtree(scope)
        from sqlalchemy import cast
        from sqlalchemy.dialects.postgresql import JSONB
        for attachment in db.scalars(select(ChatAttachment).where(ChatAttachment.chat_id == chat.id)):
            if attachment.relative_path == "reset-removed":
                continue
            shared = db.scalar(select(Message.id).where(Message.chat_id != chat.id,
                cast(Message.attachment_ids, JSONB).contains([attachment.id])).limit(1))
            shared_request = db.scalar(select(ReplyRequest.id).where(ReplyRequest.chat_id != chat.id,
                cast(ReplyRequest.input_attachment_ids, JSONB).contains([attachment.id])).limit(1))
            if shared or shared_request:
                continue
            try:
                service._attachment_path(attachment).unlink(missing_ok=True)
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise  # Missing bytes are an already-completed local cleanup step.
            attachment.name, attachment.relative_path, attachment.size, attachment.sha256 = "reset", "reset-removed", 0, ""
        row.status, chat.reset_pending = "completed", False
        result = payload(row)
    notifications.emit_personal(owner_id, "reset")
    notifications.emit_personal(owner_id, "memory")
    return result


def reset(service, owner_id, key, expected_generation):
    result = fence(service, owner_id, key, expected_generation)
    if result["status"] == "completed":
        return result
    try:
        return cleanup(service, owner_id, result["reset_id"])
    except (OSError, ValueError, HTTPException):
        # The committed fence stays closed; an owner reload/retry resumes this exact intent.
        notifications.emit_personal(owner_id, "reset")
        return result
