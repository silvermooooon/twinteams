"""PostgreSQL Twinteams business schema; no SDK execution-event duplicates."""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    cast,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def uid() -> str:
    return str(uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


class Owner(Base):
    __tablename__ = "tt_owners"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    auth_subject: Mapped[str] = mapped_column(String(256), unique=True)
    default_reply_prompt: Mapped[str] = mapped_column(Text, default="Rewrite my input clearly, preserving my meaning. Reply directly in my voice without inventing commitments.")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OnboardingState(Base):
    __tablename__ = "tt_onboarding"
    __table_args__ = (CheckConstraint("status IN ('active','completed','skipped')", name="tt_onboarding_status"),)
    owner_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"), primary_key=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("tt_chats.id"))
    status: Mapped[str] = mapped_column(String(16))
    welcome_message_id: Mapped[str | None] = mapped_column(String(80))
    first_message_id: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Actor(Base):
    __tablename__ = "tt_actors"
    __table_args__ = (UniqueConstraint("owner_id", "kind"), CheckConstraint("kind IN ('human','twin')", name="tt_actor_kind"))
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(128))
    initials: Mapped[str] = mapped_column(String(8))
    color: Mapped[str] = mapped_column(String(32))
    avatar_data: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    avatar_digest: Mapped[str | None] = mapped_column(String(64))
    avatar_preset: Mapped[str | None] = mapped_column(String(24))
    profile_revision: Mapped[int] = mapped_column(BigInteger, default=1)
    enabled_skill_names: Mapped[list] = mapped_column(JSON, default=list)


class Chat(Base):
    __tablename__ = "tt_chats"
    __table_args__ = (CheckConstraint("(kind = 'private' AND private_owner_id IS NOT NULL) OR (kind IN ('group','duet') AND private_owner_id IS NULL)", name="tt_chat_kind_owner"), Index("tt_direct_pair_key", "direct_pair_key", unique=True))
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(256))
    private_owner_id: Mapped[str | None] = mapped_column(ForeignKey("tt_owners.id"), unique=True)
    direct_pair_key: Mapped[str | None] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(BigInteger, default=1)
    next_message_seq: Mapped[int] = mapped_column(Integer, default=1)
    reset_generation: Mapped[int] = mapped_column(Integer, default=0)
    reset_pending: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DuetConversation(Base):
    """Consent metadata; messages and execution use ordinary chat machinery."""
    __tablename__ = "tt_duet_conversations"
    __table_args__ = (CheckConstraint("status IN ('pending','active','rejected','closed')", name="tt_duet_conversation_status"),)
    chat_id: Mapped[str] = mapped_column(ForeignKey("tt_chats.id"), primary_key=True)
    origin_chat_id: Mapped[str] = mapped_column(ForeignKey("tt_chats.id"), index=True)
    initiating_request_id: Mapped[str] = mapped_column(ForeignKey("tt_reply_requests.id"))
    initiating_owner_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"))
    operation_id: Mapped[str] = mapped_column(String(256), unique=True)
    purpose: Mapped[str] = mapped_column(Text)
    opening_message: Mapped[str] = mapped_column(Text)
    source_message_ids: Mapped[list] = mapped_column(JSON, default=list)
    causal_chain_id: Mapped[str] = mapped_column(ForeignKey("tt_causal_chains.id"))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    close_reason: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DuetContactGrant(Base):
    __tablename__ = "tt_duet_contact_grants"
    owner_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"), primary_key=True)
    peer_owner_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Membership(Base):
    __tablename__ = "tt_memberships"
    __table_args__ = (UniqueConstraint("chat_id", "owner_id"), CheckConstraint("history_policy IN ('all','fixed_7d','no_prior')", name="tt_history_policy"),)
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=uid)
    chat_id: Mapped[str] = mapped_column(ForeignKey("tt_chats.id"), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"), index=True)
    history_policy: Mapped[str] = mapped_column(String(16), default="all")
    lower_seq: Mapped[int] = mapped_column(Integer, default=1)
    grant_version: Mapped[int] = mapped_column(Integer, default=1)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CausalChain(Base):
    """Duet-channel send allowance shared across a causal lineage."""
    __tablename__ = "tt_causal_chains"
    __table_args__ = (CheckConstraint("spent >= 0 AND allowance >= 0 AND spent <= allowance", name="tt_chain_budget"),)
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=uid)
    root_owner_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"))
    root_message_id: Mapped[str | None] = mapped_column(String(80))
    allowance: Mapped[int] = mapped_column(Integer, default=6)
    spent: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AllowanceGrant(Base):
    __tablename__ = "tt_automatic_allowance_grants"
    __table_args__ = (UniqueConstraint("owner_id", "client_request_key"),)
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=uid)
    causal_chain_id: Mapped[str] = mapped_column(ForeignKey("tt_causal_chains.id"), index=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("tt_chats.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"))
    client_request_key: Mapped[str] = mapped_column(String(160))
    amount: Mapped[int] = mapped_column(Integer)
    base_allowance: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Message(Base):
    __tablename__ = "tt_messages"
    source_provenance: Mapped[list] = mapped_column(JSON, default=list)
    __table_args__ = (UniqueConstraint("chat_id", "seq"), UniqueConstraint("command_owner_id", "client_request_key"), CheckConstraint("kind IN ('human','twin','reply_for_me','system')", name="tt_message_kind"), CheckConstraint("state IN ('pending','published','withdrawn') AND (state != 'published' OR seq IS NOT NULL) AND (state != 'pending' OR seq IS NULL)", name="tt_message_state"))
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=uid)
    causal_chain_id: Mapped[str | None] = mapped_column(ForeignKey("tt_causal_chains.id"), index=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("tt_chats.id"), index=True)
    seq: Mapped[int | None] = mapped_column(Integer)
    author_id: Mapped[str] = mapped_column(ForeignKey("tt_actors.id"))
    kind: Mapped[str] = mapped_column(String(20), default="human")
    body: Mapped[str] = mapped_column(Text)
    attachment_ids: Mapped[list] = mapped_column(JSON, default=list)
    command_fingerprint: Mapped[str | None] = mapped_column(String(64))
    raw_owner_input: Mapped[str | None] = mapped_column(Text)
    reply_to_message_id: Mapped[str | None] = mapped_column(ForeignKey("tt_messages.id"))
    request_id: Mapped[str | None] = mapped_column(String(80), index=True)
    command_owner_id: Mapped[str | None] = mapped_column(ForeignKey("tt_owners.id"))
    client_request_key: Mapped[str | None] = mapped_column(String(160))
    source_message_ids: Mapped[list] = mapped_column(JSON, default=list)
    source_floor_seq: Mapped[int | None] = mapped_column(Integer)
    sdk_output_key: Mapped[str | None] = mapped_column(String(256), unique=True)
    state: Mapped[str] = mapped_column(String(16), default="published")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MessageReaction(Base):
    __tablename__ = "tt_message_reactions"
    message_id: Mapped[str] = mapped_column(ForeignKey("tt_messages.id", ondelete="CASCADE"), primary_key=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("tt_actors.id"), primary_key=True)
    emoji: Mapped[str] = mapped_column(String(32), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ChatAttachment(Base):
    __tablename__ = "tt_chat_attachments"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=uid)
    chat_id: Mapped[str] = mapped_column(ForeignKey("tt_chats.id"), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    mime_type: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(16))
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    relative_path: Mapped[str] = mapped_column(Text)
    private_owner_input: Mapped[bool] = mapped_column(Boolean, default=False)
    message_id: Mapped[str | None] = mapped_column(ForeignKey("tt_messages.id"), index=True)
    request_id: Mapped[str | None] = mapped_column(ForeignKey("tt_reply_requests.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Receipt(Base):
    __tablename__ = "tt_read_receipts"
    message_id: Mapped[str] = mapped_column(ForeignKey("tt_messages.id"), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Presence(Base):
    __tablename__ = "tt_presence"
    owner_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    focused_chat_id: Mapped[str | None] = mapped_column(ForeignKey("tt_chats.id"))
    visible: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ReplyLane(Base):
    """Short-lived acceptance numbering; legacy session columns are not execution locks."""
    __tablename__ = "tt_reply_lanes"
    chat_id: Mapped[str] = mapped_column(ForeignKey("tt_chats.id"), primary_key=True)
    twin_id: Mapped[str] = mapped_column(ForeignKey("tt_actors.id"), primary_key=True)
    next_accept_seq: Mapped[int] = mapped_column(Integer, default=1)
    active_request_id: Mapped[str | None] = mapped_column(String(80))
    session_id: Mapped[str | None] = mapped_column(String(128), unique=True)


class ReplyRequest(Base):
    __tablename__ = "tt_reply_requests"
    __table_args__ = (UniqueConstraint("intent_key", "generation"), UniqueConstraint("requester_id", "client_request_key"), UniqueConstraint("chat_id", "target_twin_id", "accepted_seq"), CheckConstraint("status IN ('queued','running','completed','failed','cancelled')", name="tt_request_status"), Index("tt_queue_order", "chat_id", "target_twin_id", "status", "accepted_seq"))
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=uid)
    causal_chain_id: Mapped[str | None] = mapped_column(ForeignKey("tt_causal_chains.id"), index=True)
    source_owner_id: Mapped[str | None] = mapped_column(ForeignKey("tt_owners.id"))
    chat_id: Mapped[str] = mapped_column(ForeignKey("tt_chats.id"), index=True)
    source_message_id: Mapped[str | None] = mapped_column(ForeignKey("tt_messages.id"))
    target_twin_id: Mapped[str] = mapped_column(ForeignKey("tt_actors.id"))
    requester_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"))
    source: Mapped[str] = mapped_column(String(24))
    continuation_of_request_id: Mapped[str | None] = mapped_column(ForeignKey("tt_reply_requests.id"), index=True)
    background: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    intent_key: Mapped[str] = mapped_column(String(256))
    generation: Mapped[int] = mapped_column(Integer, default=0)
    client_request_key: Mapped[str] = mapped_column(String(160))
    accepted_seq: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    input_attachment_ids: Mapped[list] = mapped_column(JSON, default=list)
    command_fingerprint: Mapped[str | None] = mapped_column(String(64))
    raw_owner_input: Mapped[str | None] = mapped_column(Text)
    input_body: Mapped[str | None] = mapped_column(Text)
    frozen_prompt: Mapped[str | None] = mapped_column(Text)
    reply_to_message_id: Mapped[str | None] = mapped_column(ForeignKey("tt_messages.id"))
    error: Mapped[str | None] = mapped_column(Text)
    session_id: Mapped[str | None] = mapped_column(String(128))
    run_id: Mapped[str | None] = mapped_column(String(128))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    context_grant_version: Mapped[int | None] = mapped_column(Integer)
    execution_config: Mapped[dict | None] = mapped_column(JSON)
    private_generation: Mapped[int | None] = mapped_column(Integer)


class PendingChildResult(Base):
    """Durably retain a child notification before the SDK may acknowledge delivery."""
    __tablename__ = "tt_pending_child_results"
    id: Mapped[str] = mapped_column(String(256), primary_key=True)
    causal_chain_id: Mapped[str] = mapped_column(ForeignKey("tt_causal_chains.id"), index=True)
    parent_request_id: Mapped[str] = mapped_column(ForeignKey("tt_reply_requests.id"))
    chat_id: Mapped[str] = mapped_column(ForeignKey("tt_chats.id"), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"))
    requester_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"))
    target_twin_id: Mapped[str] = mapped_column(ForeignKey("tt_actors.id"))
    source_message_id: Mapped[str | None] = mapped_column(ForeignKey("tt_messages.id"))
    body: Mapped[str] = mapped_column(Text)
    notification: Mapped[dict] = mapped_column(JSON)
    source_provenance: Mapped[list] = mapped_column(JSON, default=list)
    state: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    pause_reason: Mapped[str | None] = mapped_column(String(32))
    accepted_request_id: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DispatchCommand(Base):
    __tablename__ = "tt_dispatch_outbox"
    request_id: Mapped[str] = mapped_column(ForeignKey("tt_reply_requests.id"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="execute")
    state: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ScheduledTask(Base):
    """Durable owner schedule; execution history remains in ReplyRequest/SDK journals."""
    __tablename__ = "tt_scheduled_tasks"
    __table_args__ = (Index("tt_scheduled_due", "enabled", "next_run_at"),
                      Index("tt_scheduled_dirty", "index_dirty", "id"),
                      Index("tt_scheduled_owner", "owner_id", "created_at"),
                      CheckConstraint("kind IN ('user','system_review')", name="tt_scheduled_kind"),
                      CheckConstraint("revision > 0", name="tt_scheduled_revision"))
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"))
    kind: Mapped[str] = mapped_column(String(24), default="user")
    title: Mapped[str] = mapped_column(String(256))
    instructions: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(String(80), default="UTC")
    schedule: Mapped[dict] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    index_dirty: Mapped[bool] = mapped_column(Boolean, default=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MemoryEditAudit(Base):
    """Real owner configuration events referenced by user-authored Git memory."""
    __tablename__ = "tt_memory_edit_audit"
    __table_args__ = (Index("tt_memory_edit_owner_time", "owner_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"), index=True)
    private_generation: Mapped[int] = mapped_column(Integer, default=0)
    key: Mapped[str] = mapped_column(String(512))
    action: Mapped[str] = mapped_column(String(16))
    content: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(24), default="requested")
    version: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MemoryDreamState(Base):
    """Incremental consolidation cursor and bounded task scheduling intent."""
    __tablename__ = "tt_memory_dream_state"
    __table_args__ = (Index("tt_memory_dream_due", "due_at", "queued_request_id"),)
    owner_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"), primary_key=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("tt_chats.id"), unique=True)
    cursor_seq: Mapped[int] = mapped_column(Integer, default=0)
    message_cursor_seq: Mapped[int | None] = mapped_column(Integer)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    queued_request_id: Mapped[str | None] = mapped_column(ForeignKey("tt_reply_requests.id"))
    last_status: Mapped[str] = mapped_column(String(24), default="idle")
    last_version: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChatSetting(Base):
    __tablename__ = "tt_chat_settings"
    owner_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"), primary_key=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("tt_chats.id"), primary_key=True)
    reply_for_me_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    prompt_override: Mapped[str | None] = mapped_column(Text)
    sidebar_open: Mapped[bool] = mapped_column(Boolean, default=True)
    sidebar_tab: Mapped[str] = mapped_column(String(16), default="activity")


class TrackingTopic(Base):
    __tablename__ = "tt_tracking_topics"
    source_provenance: Mapped[list] = mapped_column(JSON, default=list)
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=uid)
    chat_id: Mapped[str] = mapped_column(ForeignKey("tt_chats.id"), index=True)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="active")
    source_message_ids: Mapped[list] = mapped_column(JSON, default=list)
    source_floor_seq: Mapped[int] = mapped_column(Integer)
    subject_key: Mapped[str] = mapped_column(String(256), index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[str | None] = mapped_column(ForeignKey("tt_owners.id"))
    source_cutoff_seq: Mapped[int | None] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TrackingUpdate(Base):
    __tablename__ = "tt_tracking_updates"
    source_provenance: Mapped[list] = mapped_column(JSON, default=list)
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=uid)
    topic_id: Mapped[str] = mapped_column(ForeignKey("tt_tracking_topics.id"), index=True)
    body: Mapped[str] = mapped_column(Text)
    source_message_ids: Mapped[list] = mapped_column(JSON, default=list)
    source_floor_seq: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Reminder(Base):
    __tablename__ = "tt_reminders"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"), index=True)
    title: Mapped[str] = mapped_column(String(256))
    detail: Mapped[str] = mapped_column(Text, default="")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    chat_id: Mapped[str | None] = mapped_column(ForeignKey("tt_chats.id"))
    kind: Mapped[str] = mapped_column(String(24), default="reminder")


class Approval(Base):
    __tablename__ = "tt_approval_projections"
    source_provenance: Mapped[list] = mapped_column(JSON, default=list)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("tt_chats.id"), index=True)
    approver_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"), index=True)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text)
    target: Mapped[str] = mapped_column(Text)
    impact: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    sdk_reference: Mapped[dict] = mapped_column(JSON, default=dict)
    continuation_request_id: Mapped[str | None] = mapped_column(ForeignKey("tt_reply_requests.id"))
    source_message_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Summary(Base):
    __tablename__ = "tt_summaries"
    __table_args__ = (Index("tt_summary_checkpoint", "chat_id", "owner_id", "grant_version", "coverage_end_seq"),)
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("tt_owners.id"), index=True)
    grant_version: Mapped[int | None] = mapped_column(Integer)
    private_generation: Mapped[int | None] = mapped_column(Integer)
    source_provenance: Mapped[list] = mapped_column(JSON, default=list)
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=uid)
    chat_id: Mapped[str] = mapped_column(ForeignKey("tt_chats.id"), index=True)
    body: Mapped[str] = mapped_column(Text)
    source_message_ids: Mapped[list] = mapped_column(JSON, default=list)
    coverage_start_seq: Mapped[int] = mapped_column(Integer)
    coverage_end_seq: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16), default="valid")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


Index("tt_summary_sources_gin", cast(Summary.source_message_ids, JSONB),
      postgresql_using="gin", postgresql_where=Summary.state == "valid")

class ContextManifest(Base):
    __tablename__ = "tt_context_manifests"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=uid)
    request_id: Mapped[str] = mapped_column(ForeignKey("tt_reply_requests.id"), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"))
    grant_version: Mapped[int] = mapped_column(Integer)
    high_water_seq: Mapped[int] = mapped_column(Integer)
    sources: Mapped[list] = mapped_column(JSON)
    source_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ProductOperation(Base):
    __tablename__ = "tt_product_operations"
    operation_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("tt_chats.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"))
    name: Mapped[str] = mapped_column(String(64))
    arguments_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSON)


class WorkspaceNode(Base):
    __tablename__ = "tt_workspace_nodes"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=uid)
    chat_id: Mapped[str] = mapped_column(ForeignKey("tt_chats.id"), index=True)
    relative_path: Mapped[str] = mapped_column(String(1024))
    source_message_ids: Mapped[list] = mapped_column(JSON, default=list)
    granted_owner_ids: Mapped[list] = mapped_column(JSON, default=list)
    shared_with_chat: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=now)


class ViewInterval(Base):
    __tablename__ = "tt_view_intervals"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"), index=True)
    device_id: Mapped[str] = mapped_column(String(128))
    chat_id: Mapped[str] = mapped_column(ForeignKey("tt_chats.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DatabaseIdentity(Base):
    __tablename__ = "tt_database_identity"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), unique=True)


class DevelopmentSession(Base):
    __tablename__ = "tt_development_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class TrackingRevision(Base):
    __tablename__ = "tt_tracking_revisions"
    topic_id: Mapped[str] = mapped_column(ForeignKey("tt_tracking_topics.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32))
    source_message_ids: Mapped[list] = mapped_column(JSON)
    changed_by: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"))
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ReplyProjection(Base):
    """Latest SDK semantic output projected into one reply-for-me bubble.

    This is a projection cursor/reference, not a copy of the runtime journal.
    """
    __tablename__ = "tt_reply_projections"
    request_id: Mapped[str] = mapped_column(ForeignKey("tt_reply_requests.id"), primary_key=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("tt_messages.id"), unique=True)
    session_id: Mapped[str] = mapped_column(String(128))
    run_id: Mapped[str] = mapped_column(String(128))
    last_event_id: Mapped[str | None] = mapped_column(String(256))
    last_event_seq: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ChangeOutbox(Base):
    """Only delivery bookkeeping; never execution ownership or task status."""
    __tablename__ = "tt_change_outbox"
    __table_args__ = (Index("tt_change_pending", "published", "created_at"), Index("tt_change_scope_revision", "chat_id", "revision"))
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=uid)
    chat_id: Mapped[str] = mapped_column(ForeignKey("tt_chats.id"))
    revision: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(16), default="changed")
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ActivityProjection(Base):
    __tablename__ = "tt_activity_runs"
    __table_args__ = (Index("tt_activity_chat_order", "chat_id", "started_at", "session_id", "run_id"),
                     Index("tt_activity_user_visible_order", "chat_id", "private_generation", "started_at", "session_id", "run_id", postgresql_where=text("user_visible")))
    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("tt_chats.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"))
    private_generation: Mapped[int] = mapped_column(Integer, default=0)
    user_visible: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    parent_run_id: Mapped[str | None] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(32), default="user")
    status: Mapped[str] = mapped_column(String(32), default="running")
    title: Mapped[str] = mapped_column(Text, default="Duet response")
    summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TwinReset(Base):
    """An owner-confirmed generation transition, resumable using its original key."""
    __tablename__ = "tt_twin_resets"
    __table_args__ = (UniqueConstraint("owner_id", "idempotency_key"),)
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"), index=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("tt_chats.id"))
    idempotency_key: Mapped[str] = mapped_column(String(160))
    expected_generation: Mapped[int] = mapped_column(Integer)
    generation: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
