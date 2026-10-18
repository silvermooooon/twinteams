"""Portable Twinteams business schema; no SDK execution-event duplicates."""
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
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


class Actor(Base):
    __tablename__ = "tt_actors"
    __table_args__ = (UniqueConstraint("owner_id", "kind"), CheckConstraint("kind IN ('human','twin')", name="tt_actor_kind"))
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(128))
    initials: Mapped[str] = mapped_column(String(8))
    color: Mapped[str] = mapped_column(String(32))


class Chat(Base):
    __tablename__ = "tt_chats"
    __table_args__ = (CheckConstraint("(kind = 'private' AND private_owner_id IS NOT NULL) OR (kind = 'group' AND private_owner_id IS NULL)", name="tt_chat_kind_owner"),)
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(256))
    private_owner_id: Mapped[str | None] = mapped_column(ForeignKey("tt_owners.id"), unique=True)
    next_message_seq: Mapped[int] = mapped_column(Integer, default=1)
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
    """Product automation allowance, shared by every chat/run in one causal lineage."""
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


class AutomaticReservation(Base):
    __tablename__ = "tt_automatic_reservations"
    __table_args__ = (UniqueConstraint("causal_chain_id", "intent_key"), UniqueConstraint("causal_chain_id", "source_owner_id", "target_owner_id", "content_hash"))
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=uid)
    causal_chain_id: Mapped[str] = mapped_column(ForeignKey("tt_causal_chains.id"), index=True)
    intent_key: Mapped[str] = mapped_column(String(256))
    source_owner_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"))
    target_owner_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    accepted_request_id: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Message(Base):
    __tablename__ = "tt_messages"
    source_provenance: Mapped[list] = mapped_column(JSON, default=list)
    __table_args__ = (UniqueConstraint("chat_id", "seq"), UniqueConstraint("command_owner_id", "client_request_key"), CheckConstraint("kind IN ('human','twin','reply_for_me','system')", name="tt_message_kind"), CheckConstraint("state IN ('published','withdrawn')", name="tt_message_state"))
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=uid)
    causal_chain_id: Mapped[str | None] = mapped_column(ForeignKey("tt_causal_chains.id"), index=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("tt_chats.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    author_id: Mapped[str] = mapped_column(ForeignKey("tt_actors.id"))
    kind: Mapped[str] = mapped_column(String(20), default="human")
    body: Mapped[str] = mapped_column(Text)
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


class RoutingDecision(Base):
    __tablename__ = "tt_routing_decisions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=uid)
    message_id: Mapped[str] = mapped_column(ForeignKey("tt_messages.id"), unique=True)
    strategy: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    inferred_owner_ids: Mapped[list] = mapped_column(JSON, default=list)
    mentioned_owner_ids: Mapped[list] = mapped_column(JSON, default=list)
    needs_response: Mapped[bool | None] = mapped_column(Boolean)
    reason: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TriggerCandidate(Base):
    __tablename__ = "tt_trigger_candidates"
    __table_args__ = (UniqueConstraint("message_id", "target_twin_id"),)
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=uid)
    causal_chain_id: Mapped[str | None] = mapped_column(ForeignKey("tt_causal_chains.id"), index=True)
    chat_id: Mapped[str] = mapped_column(ForeignKey("tt_chats.id"), index=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("tt_messages.id"))
    target_twin_id: Mapped[str] = mapped_column(ForeignKey("tt_actors.id"))
    state: Mapped[str] = mapped_column(String(16), default="waiting", index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    accepted_request_id: Mapped[str | None] = mapped_column(String(80))


class ReplyLane(Base):
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
    automatic_reservation_id: Mapped[str | None] = mapped_column(ForeignKey("tt_automatic_reservations.id"))
    source_owner_id: Mapped[str | None] = mapped_column(ForeignKey("tt_owners.id"))
    chat_id: Mapped[str] = mapped_column(ForeignKey("tt_chats.id"), index=True)
    source_message_id: Mapped[str | None] = mapped_column(ForeignKey("tt_messages.id"))
    target_twin_id: Mapped[str] = mapped_column(ForeignKey("tt_actors.id"))
    requester_id: Mapped[str] = mapped_column(ForeignKey("tt_owners.id"))
    source: Mapped[str] = mapped_column(String(24))
    intent_key: Mapped[str] = mapped_column(String(256))
    generation: Mapped[int] = mapped_column(Integer, default=0)
    client_request_key: Mapped[str] = mapped_column(String(160))
    accepted_seq: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    raw_owner_input: Mapped[str | None] = mapped_column(Text)
    input_body: Mapped[str | None] = mapped_column(Text)
    frozen_prompt: Mapped[str | None] = mapped_column(Text)
    reply_to_message_id: Mapped[str | None] = mapped_column(ForeignKey("tt_messages.id"))
    error: Mapped[str | None] = mapped_column(Text)
    session_id: Mapped[str | None] = mapped_column(String(128))
    run_id: Mapped[str | None] = mapped_column(String(128))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    context_grant_version: Mapped[int | None] = mapped_column(Integer)


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
    source_provenance: Mapped[list] = mapped_column(JSON, default=list)
    id: Mapped[str] = mapped_column(String(80), primary_key=True, default=uid)
    chat_id: Mapped[str] = mapped_column(ForeignKey("tt_chats.id"), index=True)
    body: Mapped[str] = mapped_column(Text)
    source_message_ids: Mapped[list] = mapped_column(JSON, default=list)
    coverage_start_seq: Mapped[int] = mapped_column(Integer)
    coverage_end_seq: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16), default="valid")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


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
