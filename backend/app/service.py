"""Persisted product permissions, trigger decisions and FIFO scheduling intent.

The bound runtime owns model/tool execution, journal, fencing and recovery. This
module only keeps business requests, durable dispatch intent and safe projections.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone
import hashlib
import inspect
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import unicodedata
from typing import Any

from fastapi import HTTPException
from sqlalchemy import event as sa_event, cast, func, inspect as sa_inspect, or_, select, text, update
from sqlalchemy.exc import IntegrityError

from .notifications import notifications
from .config import settings
from .db import Base, engine, SessionLocal
from .models import (Actor, Approval, AutomaticReservation, AllowanceGrant, CausalChain,
                     Chat, ChatAttachment, ChatSetting, ContextManifest, DispatchCommand, PendingChildResult,
                     Membership, MemoryDreamState, Message, MessageReaction, Owner, Presence, ProductOperation, Receipt, DatabaseIdentity, ViewInterval, DevelopmentSession,
                     Reminder, ReplyLane, ReplyRequest, ReplyProjection, RoutingDecision, ScheduledTask, Summary,
                     TrackingTopic, TrackingUpdate, TrackingRevision, TriggerCandidate, TwinFollowup, WorkspaceNode, ChangeOutbox, ActivityProjection,
                     now, uid)
from .credential_models import CredentialRequest
from .direct_chat_schema import pair_key


def iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def aware(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def fail(status, detail):
    raise HTTPException(status_code=status, detail=detail)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


async def resolve(value):
    return await value if inspect.isawaitable(value) else value


class ProductService:
    def __init__(self):
        self.runtime = None
        self.tasks: dict[str, asyncio.Task] = {}
        self.scheduler_task = None
        self.routing_task = None
        self.routing_jobs = {}
        self.stopping = False
        self.loop = None
        self._db_gate = None
        self._db_gate_loop = None
        self.db_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="twinteams-db")
        self.wakeup = None
        self.routing_wakeup = None
        self.worker_mode = False
        self._presence_cache = {}
        self._presence_known = False
        self.deferred = {}
        self.work_scopes = set()
        self.ready_scopes = set()
        self.runtime_scopes = set()
        self.deadlines = {}
        self.scheduled_task_loop = None
        self.scheduled_due_index = None

    @property
    def automatic_reply_allowance(self):
        # Configurable without coupling provider setup to product safety policy.
        value = os.getenv("TWINTEAMS_AUTOMATIC_REPLY_ALLOWANCE", "6")
        try:
            amount = int(value)
        except (TypeError, ValueError):
            raise RuntimeError("TWINTEAMS_AUTOMATIC_REPLY_ALLOWANCE must be a positive integer") from None
        if amount < 1:
            raise RuntimeError("TWINTEAMS_AUTOMATIC_REPLY_ALLOWANCE must be a positive integer")
        return amount

    async def db_call(self, method, *args, **kwargs):
        """One complete transaction/function per bounded pool job; no ORM across awaits."""
        callback = getattr(self, method) if isinstance(method, str) else method
        loop = asyncio.get_running_loop()
        if self._db_gate_loop is not loop:
            self._db_gate, self._db_gate_loop = asyncio.Semaphore(64), loop
        async with self._db_gate:
            future = loop.run_in_executor(self.db_pool, partial(callback, *args, **kwargs))
            try:
                return await asyncio.shield(future)
            except asyncio.CancelledError:
                # Retain the admission slot until the transaction settles.
                await asyncio.shield(future)
                raise

    @contextmanager
    def transaction(self):
        scopes = set()
        work_scopes = set()
        new_members = set()
        tracking_scopes, tracking_owners = set(), set()
        scheduled_owners = set()
        with SessionLocal() as db, db.begin():
            def collect(session, context, instances):
                from .conversation_context import invalidate_changed_messages
                invalidate_changed_messages(session)
                for row in (*session.new, *session.dirty, *session.deleted):
                    if isinstance(row, ScheduledTask):
                        scheduled_owners.add(row.owner_id)
                    if isinstance(row, Membership) and row in session.new:
                        new_members.add(row.owner_id)
                    if isinstance(row, ChangeOutbox) or (isinstance(row, ProductOperation) and row.name == "set_message_reaction"):
                        continue
                    chat_id = row.id if isinstance(row, Chat) else getattr(row, "chat_id", None)
                    if isinstance(row, (Receipt, MessageReaction)):
                        message = session.get(Message, row.message_id)
                        chat_id = message.chat_id if message else None
                    if isinstance(row, (TrackingTopic, Membership)) or (
                            isinstance(row, Message) and row not in session.new and row.state == "withdrawn" and
                            sa_inspect(row).attrs.state.history.has_changes()):
                        if chat_id:
                            tracking_scopes.add(chat_id)
                        if isinstance(row, Membership):
                            tracking_owners.add(row.owner_id)
                    if chat_id:
                        scopes.add(chat_id)
                        if isinstance(row, (ReplyRequest, RoutingDecision, TriggerCandidate, PendingChildResult, AllowanceGrant, Approval, TwinFollowup, MemoryDreamState)):
                            work_scopes.add(chat_id)
                    if isinstance(row, RoutingDecision) and not chat_id:
                        message = session.get(Message, row.message_id)
                        if message:
                            scopes.add(message.chat_id)
                            work_scopes.add(message.chat_id)
            sa_event.listen(db, "before_flush", collect)
            yield db
            db.flush()
            if tracking_scopes:
                tracking_owners.update(db.scalars(select(Membership.owner_id).where(
                    Membership.chat_id.in_(tracking_scopes), Membership.left_at.is_(None)).distinct()))
            for chat_id in sorted(scopes):
                revision = db.scalar(update(Chat).where(Chat.id == chat_id).values(revision=Chat.revision + 1).returning(Chat.revision))
                if revision is not None:
                    db.info.setdefault("chat_revisions", {})[chat_id] = revision
                    db.add(ChangeOutbox(chat_id=chat_id, revision=revision, kind="work" if chat_id in work_scopes else "changed"))
        for chat_id in scopes:
            notifications.emit(chat_id, work=chat_id in work_scopes)
        for owner_id in new_members:
            notifications.emit_personal(owner_id, "chats")
        for owner_id in tracking_owners:
            notifications.emit_personal(owner_id, "tracking")
        for owner_id in scheduled_owners:
            from .scheduled_tasks import changed
            changed(owner_id)

    def initialize(self, create_schema=False):
        if settings.local_identity_enabled or create_schema:
            Base.metadata.create_all(engine)
            from .conversation_context import upgrade_schema as upgrade_context_schema
            upgrade_context_schema(engine)
            from .personal_schema import upgrade_personal_schema
            upgrade_personal_schema(engine)
            from .followup_schema import upgrade_followup_schema
            upgrade_followup_schema(engine)
            from .scheduled_task_schema import upgrade_scheduled_task_schema
            upgrade_scheduled_task_schema(engine)
            from .onboarding_schema import upgrade_onboarding_schema
            upgrade_onboarding_schema(engine)
            from .question_schema import upgrade_question_schema
            upgrade_question_schema(engine)
            from .direct_chat_schema import upgrade_direct_chat_schema
            upgrade_direct_chat_schema(engine)
            from .reaction_schema import upgrade_reaction_schema
            upgrade_reaction_schema(engine)
            from .attachments import upgrade_attachment_schema
            upgrade_attachment_schema(engine)
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE tt_chats ADD COLUMN IF NOT EXISTS revision BIGINT NOT NULL DEFAULT 1"))
                connection.execute(text("ALTER TABLE tt_change_outbox ADD COLUMN IF NOT EXISTS kind VARCHAR(16) NOT NULL DEFAULT 'changed'"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS tt_candidates_due ON tt_trigger_candidates (state, due_at)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS tt_routing_pending ON tt_routing_decisions (status, created_at)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS tt_ready_requests ON tt_reply_requests (status, accepted_at, accepted_seq)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS tt_ready_order ON tt_reply_requests (accepted_at, accepted_seq) WHERE status IN ('queued','running')"))
            # Explicit additive v1 migration, never a reset of existing records.
            columns = {column["name"] for column in sa_inspect(engine).get_columns("tt_reply_requests")}
            if "input_body" not in columns:
                with engine.begin() as connection:
                    connection.execute(text("ALTER TABLE tt_reply_requests ADD COLUMN input_body TEXT"))
            workspace_columns = {column["name"] for column in sa_inspect(engine).get_columns("tt_workspace_nodes")}
            approval_columns = {column["name"] for column in sa_inspect(engine).get_columns("tt_approval_projections")}
            with engine.begin() as connection:
                if "source_message_ids" not in approval_columns:
                    connection.execute(text("ALTER TABLE tt_approval_projections ADD COLUMN source_message_ids JSON NOT NULL DEFAULT '[]'"))
                if "continuation_request_id" not in approval_columns:
                    connection.execute(text("ALTER TABLE tt_approval_projections ADD COLUMN continuation_request_id VARCHAR(80) REFERENCES tt_reply_requests(id)"))
                if "shared_with_chat" not in workspace_columns:
                    connection.execute(text("ALTER TABLE tt_workspace_nodes ADD COLUMN shared_with_chat BOOLEAN NOT NULL DEFAULT TRUE"))
                if "created_at" not in workspace_columns:
                    connection.execute(text("ALTER TABLE tt_workspace_nodes ADD COLUMN created_at TIMESTAMP"))
            for table in ("tt_messages", "tt_tracking_topics", "tt_tracking_updates", "tt_summaries", "tt_approval_projections"):
                columns = {column["name"] for column in sa_inspect(engine).get_columns(table)}
                if "source_provenance" not in columns:
                    with engine.begin() as connection:
                        connection.execute(text(f"ALTER TABLE {table} ADD COLUMN source_provenance JSON NOT NULL DEFAULT '[]'"))
            additive = {
                "tt_messages": {"causal_chain_id": "VARCHAR(80) REFERENCES tt_causal_chains(id)", "attachment_ids": "JSON NOT NULL DEFAULT '[]'", "command_fingerprint": "VARCHAR(64)"},
                "tt_reply_requests": {"input_attachment_ids": "JSON NOT NULL DEFAULT '[]'", "command_fingerprint": "VARCHAR(64)", "causal_chain_id": "VARCHAR(80) REFERENCES tt_causal_chains(id)", "automatic_reservation_id": "VARCHAR(80) REFERENCES tt_automatic_reservations(id)", "source_owner_id": "VARCHAR(80) REFERENCES tt_owners(id)"},
                "tt_trigger_candidates": {"causal_chain_id": "VARCHAR(80) REFERENCES tt_causal_chains(id)"},
                "tt_routing_decisions": {"needs_response": "BOOLEAN", "reason": "TEXT", "command_id": "VARCHAR(80)"},
                "tt_automatic_allowance_grants": {"base_allowance": "INTEGER NOT NULL DEFAULT 0"},
            }
            for table, additions in additive.items():
                columns = {column["name"] for column in sa_inspect(engine).get_columns(table)}
                with engine.begin() as connection:
                    for column, declaration in additions.items():
                        if column not in columns:
                            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"))
                    if "causal_chain_id" in additions:
                        connection.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table}_causal_chain_id ON {table} (causal_chain_id)"))
        with self.transaction() as db:
            identity = db.get(DatabaseIdentity, 1)
            if identity is None:
                if not (settings.local_identity_enabled or create_schema):
                    raise RuntimeError("Database is not provisioned; run python -m app.init_db")
                db.add(DatabaseIdentity(id=1, tenant_id=settings.tenant_id))
            elif identity.tenant_id != settings.tenant_id:
                raise RuntimeError("Database belongs to a different tenant; each deployment requires its own database")
        if settings.local_identity_enabled:
            self.seed_local()
        self.initialize_scheduled_tasks()

    def check_initialized(self):
        from sqlalchemy import inspect
        inspector = inspect(engine)
        if not {"owner_id", "grant_version", "private_generation"}.issubset(
                {column["name"] for column in inspector.get_columns("tt_summaries")}):
            raise RuntimeError("Run python -m app.init_db before starting API or worker")
        from .personal_schema import check_personal_schema
        check_personal_schema(inspector)
        from .followup_schema import check_followup_schema
        check_followup_schema(inspector)
        from .scheduled_task_schema import check_scheduled_task_schema
        check_scheduled_task_schema(inspector)
        from .onboarding_schema import check_onboarding_schema
        check_onboarding_schema(inspector)
        from .question_schema import check_question_schema
        check_question_schema(inspector)
        from .direct_chat_schema import check_direct_chat_schema
        check_direct_chat_schema(inspector)
        from .reaction_schema import check_reaction_schema
        check_reaction_schema(inspector)
        if not inspector.has_table("tt_change_outbox") or not inspector.has_table("tt_chat_attachments") or "revision" not in {col["name"] for col in inspector.get_columns("tt_chats")} or "attachment_ids" not in {col["name"] for col in inspector.get_columns("tt_messages")}:
            raise RuntimeError("Run python -m app.init_db before starting API or worker")
        if "command_id" not in {column["name"] for column in inspector.get_columns("tt_routing_decisions")}:
            raise RuntimeError("Run python -m app.init_db before starting API or worker")
        if any(column["name"] == "relative_path" and getattr(column["type"], "length", None) is not None
               for column in inspector.get_columns("tt_chat_attachments")):
            raise RuntimeError("Run python -m app.init_db before starting API or worker")
        with self.transaction() as db:
            identity = db.get(DatabaseIdentity, 1)
            if not identity or identity.tenant_id != settings.tenant_id:
                raise RuntimeError("Database tenant identity is missing or differs")

    def seed_local(self):
        with self.transaction() as db:
            for slug, name, initials, color in [("alex", "AAA", "AA", "#7962bd"), ("maya", "BBB", "BB", "#428b7d"), ("jordan", "CCC", "CC", "#b5784d")]:
                owner_id = f"owner-{slug}"
                if db.get(Owner, owner_id):
                    continue
                db.add(Owner(id=owner_id, auth_subject=f"local:{slug}"))
                db.flush()
                db.add_all([Actor(id=owner_id, owner_id=owner_id, kind="human", name=name, initials=initials, color=color), Actor(id=f"twin-{slug}", owner_id=owner_id, kind="twin", name=f"{name.split()[0]}'s Duet", initials=f"{initials[0]}D", color="#6dbdad")])
                db.flush()
                chat = Chat(id=f"private-{slug}", kind="private", title=f"{name.split()[0]}'s Duet", private_owner_id=owner_id)
                db.add(chat)
                db.flush()
                db.add(Membership(chat_id=chat.id, owner_id=owner_id, history_policy="all", lower_seq=1))
            db.flush()
            if not db.get(Chat, "group-team"):
                db.add(Chat(id="group-team", kind="group", title="Team workspace"))
                db.flush()
                db.add_all([Membership(chat_id="group-team", owner_id=x, history_policy="all", lower_seq=1) for x in ["owner-alex", "owner-maya", "owner-jordan"]])
            seeded_pair = db.get(Chat, "group-alex-maya")
            if not seeded_pair:
                db.add(Chat(id="group-alex-maya", kind="group", title="AAA & BBB", direct_pair_key=pair_key(["owner-alex", "owner-maya"])))
                db.flush()
                db.add_all([Membership(chat_id="group-alex-maya", owner_id=x, history_policy="all", lower_seq=1) for x in ["owner-alex", "owner-maya"]])

    def bind_runtime(self, runtime):
        self.runtime = runtime

    @property
    def model_configured(self):
        import time
        if self.worker_mode or notifications.loop is None:
            return bool(self.runtime and not getattr(self.runtime, "startup_error", None) and getattr(self.runtime, "configured", False))
        status = notifications.worker_status
        return bool(status.get("configured") and status.get("expires_at", 0) > time.time())

    def login_local(self, username):
        if not settings.local_identity_enabled:
            fail(404, "Development login is disabled")
        owner_id = {"DEV-AAA": "owner-alex", "DEV-BBB": "owner-maya", "DEV-CCC": "owner-jordan"}.get(username.strip().upper())
        if not owner_id:
            fail(401, "Use DEV-AAA, DEV-BBB, or DEV-CCC for local development")
        token = secrets.token_urlsafe(40)
        with self.transaction() as db:
            db.add(DevelopmentSession(token_hash=hashlib.sha256(token.encode()).hexdigest(), owner_id=owner_id, expires_at=now() + timedelta(hours=12)))
            owner = self.actor_json(db.get(Actor, owner_id))
        return token, {"owner": owner, "current_owner_id": owner_id, "development_mode": True}

    def authenticate_local(self, token):
        if not token:
            fail(401, "Sign in with a local development username")
        with self.transaction() as db:
            session = db.get(DevelopmentSession, hashlib.sha256(token.encode()).hexdigest())
            if not session or aware(session.expires_at) <= now():
                fail(401, "Local development session is missing or expired")
            return session.owner_id

    def logout_local(self, token, device_id=None):
        if token:
            with self.transaction() as db:
                session = db.get(DevelopmentSession, hashlib.sha256(token.encode()).hexdigest())
                if session:
                    if device_id:
                        presence = db.get(Presence, (session.owner_id, device_id))
                        if presence:
                            db.delete(presence)
                        for interval in db.scalars(select(ViewInterval).where(ViewInterval.owner_id == session.owner_id, ViewInterval.device_id == device_id, ViewInterval.ended_at > now())):
                            interval.ended_at = now()
                    db.delete(session)

    def account(self, owner_id):
        with self.transaction() as db:
            return {"owner": self.actor_json(db.get(Actor, owner_id)), "current_owner_id": owner_id, "development_mode": settings.local_identity_enabled}

    def get_owner(self, owner_id):
        with self.transaction() as db:
            owner = db.get(Owner, owner_id)
            if not owner:
                fail(401, "Unknown owner identity")
            return owner

    def owner_for_subject(self, subject):
        with self.transaction() as db:
            owner = db.scalar(select(Owner).where(Owner.auth_subject == subject))
            if not owner:
                fail(403, "Authenticated identity has no provisioned Duet owner")
            return owner.id

    def membership(self, db, owner_id, chat_id, lock=False):
        chat = db.scalar(select(Chat).where(Chat.id == chat_id).with_for_update()) if lock else db.get(Chat, chat_id)
        if not chat:
            fail(404, "Chat not found")
        member = db.scalar(select(Membership).where(Membership.chat_id == chat_id, Membership.owner_id == owner_id, Membership.left_at.is_(None)).execution_options(populate_existing=True))
        if not member:
            fail(403, "You are not a current member of this chat")
        if chat.kind == "private" and chat.private_owner_id != owner_id:
            fail(403, "Private chat belongs to another owner")
        return chat, member

    def members(self, db, chat_id):
        return list(db.scalars(select(Membership).where(Membership.chat_id == chat_id, Membership.left_at.is_(None))))

    def twin(self, db, owner_id):
        twin = db.scalar(select(Actor).where(Actor.owner_id == owner_id, Actor.kind == "twin"))
        if not twin:
            fail(409, "Owner has no paired Duet")
        return twin

    def actor_json(self, actor):
        return {**{k: getattr(actor, k) for k in ("id", "kind", "owner_id", "name", "initials", "color", "avatar_preset", "profile_revision")},
                "avatar_url": f"/api/actors/{actor.id}/avatar?v={actor.profile_revision}" if actor.avatar_digest else None}

    def message_owner(self, db, message):
        actor = db.get(Actor, message.author_id)
        if not actor:
            fail(409, "Message has no authenticated actor owner")
        return actor.owner_id

    def new_causal_chain(self, db, owner_id, root_message_id=None):
        chain = CausalChain(root_owner_id=owner_id, root_message_id=root_message_id,
                            allowance=self.automatic_reply_allowance, spent=0)
        db.add(chain)
        db.flush()
        return chain

    def message_chain(self, db, message, seen=None):
        if message.causal_chain_id:
            return db.get(CausalChain, message.causal_chain_id)
        db.scalar(select(Chat).where(Chat.id == message.chat_id).with_for_update())
        db.refresh(message)
        if message.causal_chain_id:
            return db.get(CausalChain, message.causal_chain_id)
        seen = set() if seen is None else seen
        if ("message", message.id) in seen:
            fail(409, "Legacy message lineage contains a cycle")
        seen = seen | {("message", message.id)}
        # A new human message is a new cause even when quoting an earlier chain.
        request = db.get(ReplyRequest, message.request_id) if message.request_id else None
        if request and request.chat_id != message.chat_id:
            fail(409, "Cross-chat output is missing its durable causal lineage")
        if message.kind != "human" and request:
            chain = self.request_chain(db, request, seen)
        elif message.kind != "human" and message.reply_to_message_id:
            source = db.get(Message, message.reply_to_message_id)
            chain = self.message_chain(db, source, seen) if source else self.new_causal_chain(db, self.message_owner(db, message), message.id)
        else:
            chain = self.new_causal_chain(db, self.message_owner(db, message), message.id)
        message.causal_chain_id = chain.id
        return chain

    def request_chain(self, db, request, seen=None):
        if request.causal_chain_id:
            return db.get(CausalChain, request.causal_chain_id)
        db.scalar(select(Chat).where(Chat.id == request.chat_id).with_for_update())
        db.refresh(request)
        if request.causal_chain_id:
            return db.get(CausalChain, request.causal_chain_id)
        seen = set() if seen is None else seen
        if ("request", request.id) in seen:
            fail(409, "Legacy request lineage contains a cycle")
        seen = seen | {("request", request.id)}
        previous = db.scalar(select(ReplyRequest).where(ReplyRequest.intent_key == request.intent_key, ReplyRequest.generation < request.generation).order_by(ReplyRequest.generation.desc()).limit(1))
        source = db.get(Message, request.source_message_id) if request.source_message_id else None
        chain = self.request_chain(db, previous, seen) if previous else self.message_chain(db, source, seen) if source else self.new_causal_chain(db, request.requester_id)
        request.causal_chain_id = chain.id
        request.source_owner_id = self.message_owner(db, source) if source else request.requester_id
        if previous:
            request.automatic_reservation_id = previous.automatic_reservation_id
        elif request.source in {"auto", "private", "subagent_result", "followup_result", "scheduled_task", "system_review"}:
            # Additive migration lazily accounts already accepted legacy intent.
            # It cannot revoke queued/running work, even if older traffic exceeded
            # today's configured cap. New acceptance still uses the guarded path.
            chain = db.scalar(select(CausalChain).where(CausalChain.id == chain.id).with_for_update())
            reservation = db.scalar(select(AutomaticReservation).where(AutomaticReservation.causal_chain_id == chain.id, AutomaticReservation.intent_key == request.intent_key))
            if not reservation:
                chain.spent += 1
                chain.allowance = max(chain.allowance, chain.spent)
                reservation = AutomaticReservation(causal_chain_id=chain.id, intent_key=request.intent_key, source_owner_id=request.source_owner_id, target_owner_id=db.get(Actor, request.target_twin_id).owner_id, content_hash=None, accepted_request_id=request.id)
                db.add(reservation)
                db.flush()
            request.automatic_reservation_id = reservation.id
        return chain

    def normalized_content_hash(self, body):
        normalized = " ".join(unicodedata.normalize("NFKC", body or "").casefold().split())
        return hashlib.sha256(normalized.encode()).hexdigest() if normalized else None

    def reserve_automatic(self, db, chain_id, intent_key, source_owner_id, target_owner_id, body):
        chain = db.scalar(select(CausalChain).where(CausalChain.id == chain_id).with_for_update())
        if not chain:
            fail(409, "Automatic request has no durable causal lineage")
        existing = db.scalar(select(AutomaticReservation).where(AutomaticReservation.causal_chain_id == chain_id, AutomaticReservation.intent_key == intent_key))
        if existing:
            return existing, None
        content_hash = self.normalized_content_hash(body)
        if content_hash and db.scalar(select(AutomaticReservation.id).where(AutomaticReservation.causal_chain_id == chain_id, AutomaticReservation.source_owner_id == source_owner_id, AutomaticReservation.target_owner_id == target_owner_id, AutomaticReservation.content_hash == content_hash)):
            return None, "repeated_content"
        # Conditional UPDATE is the actual cross-process acceptance boundary.
        changed = db.execute(update(CausalChain).where(CausalChain.id == chain_id, CausalChain.spent < CausalChain.allowance).values(spent=CausalChain.spent + 1))
        if changed.rowcount != 1:
            return None, "budget_exhausted"
        reservation = AutomaticReservation(causal_chain_id=chain_id, intent_key=intent_key, source_owner_id=source_owner_id, target_owner_id=target_owner_id, content_hash=content_hash)
        db.add(reservation)
        db.flush()
        return reservation, None

    def visible_message(self, db, member, message_id, required=True, *, cache=None):
        msg = db.get(Message, message_id)
        if not msg or msg.chat_id != member.chat_id or msg.seq < member.lower_seq or msg.state != "published":
            if required:
                fail(404, "Message is unavailable in your authorized history")
            return None
        if not self.message_sources_visible(db, member, msg, cache=cache):
            if required:
                fail(404, "Message derives from sources outside your authorized history")
            return None
        return msg

    def _message_metadata(self, db, message_id, cache=None):
        from sqlalchemy.orm import load_only
        key = ("metadata", message_id)
        if cache is not None and key in cache:
            return cache[key]
        row = db.get(Message, message_id, options=[load_only(
            Message.id, Message.chat_id, Message.seq, Message.author_id, Message.state,
            Message.source_floor_seq, Message.source_message_ids, Message.source_provenance)])
        if cache is not None:
            cache[key] = row
        return row

    def message_sources_visible(self, db, member, msg, seen=None, cache=None):
        seen = set() if seen is None else seen
        cache = {} if cache is None else cache
        if msg.id in cache:
            return cache[msg.id]
        if msg.id in seen:
            return False
        seen = seen | {msg.id}
        if msg.source_floor_seq is not None and msg.source_floor_seq < member.lower_seq:
            cache[msg.id] = False
            return False
        covering = cache.get(("covering_summary", member.chat_id))
        dependencies = list(dict.fromkeys([*(msg.source_message_ids or []), *[
            item["id"] for item in (msg.source_provenance or []) if item.get("kind") == "message"]]))
        if covering and dependencies:
            from sqlalchemy.dialects.postgresql import JSONB
            uncovered, count = db.execute(select(
                func.array_agg(Message.id).filter(~cast(Summary.source_message_ids, JSONB).has_key(Message.id)),
                func.count()).select_from(Message).join(Summary, Summary.id == covering).where(
                    Summary.state == "valid", Message.id.in_(dependencies), Message.chat_id == member.chat_id,
                    Message.seq >= member.lower_seq, Message.state == "published")).one()
            if count == len(dependencies):
                # PostgreSQL checks the storage-only covered set, returning only
                # uncovered IDs from this tail. No historical metadata rows or
                # historical source arrays leave the database.
                uncovered = set(uncovered or [])
                for source_id in dependencies:
                    if source_id not in uncovered:
                        cache[("covered_message", member.chat_id, source_id)] = True
        for source_id in msg.source_message_ids or []:
            if cache.get(("covered_message", member.chat_id, source_id)):
                continue
            source = self._message_metadata(db, source_id, cache)
            if not source or source.chat_id != member.chat_id or source.seq < member.lower_seq or source.state != "published" or not self.message_sources_visible(db, member, source, seen, cache):
                cache[msg.id] = False
                return False
        cache[msg.id] = self.provenance_allowed(db, member, msg.source_provenance, seen, cache)
        return cache[msg.id]

    def sources_allowed(self, db, member, source_ids, source_floor=None):
        if source_floor is not None and source_floor < member.lower_seq:
            return False
        return all(self.visible_message(db, member, source_id, False) for source_id in source_ids)

    def provenance_allowed(self, db, member, sources, seen=None, cache=None):
        for source in sources or []:
            if source.get("kind") == "summary":
                from .conversation_context import summary_allowed, summary_metadata
                if not summary_allowed(self, db, member, summary_metadata(db, source.get("id")), seen, cache):
                    return False
            elif source.get("kind") == "history_range":
                origin = db.get(Chat, source.get("chat_id"))
                grant = db.scalar(select(Membership).where(Membership.chat_id == source.get("chat_id"),
                    Membership.owner_id == member.owner_id, Membership.left_at.is_(None)))
                if not origin or not grant or grant.lower_seq > source.get("lower_seq", 0):
                    return False
                if origin.kind == "private" and (origin.private_owner_id != member.owner_id or origin.reset_pending or
                        origin.reset_generation != source.get("private_generation")):
                    return False
            elif source.get("kind") == "chat_context":
                # Cross-chat output keeps the complete original context boundary.
                # Every reader must still hold the original history grant.
                origin_chat = db.get(Chat, source.get("chat_id"))
                origin_member = db.scalar(select(Membership).where(Membership.chat_id == source.get("chat_id"), Membership.owner_id == member.owner_id, Membership.left_at.is_(None)))
                if not origin_chat or not origin_member or (origin_chat.kind == "private" and
                        (origin_chat.private_owner_id != member.owner_id or origin_chat.reset_pending or
                         source.get("private_generation", origin_chat.reset_generation) != origin_chat.reset_generation)):
                    return False
                if not self.provenance_allowed(db, origin_member, source.get("sources", []), seen, cache):
                    return False
            elif source.get("kind") == "message":
                if not source.get("hash") and cache is not None and cache.get(("covered_message", member.chat_id, source["id"])):
                    continue
                message = self._message_metadata(db, source["id"], cache)
                if not message or message.chat_id != member.chat_id or message.seq < member.lower_seq or message.state != "published" or not self.message_sources_visible(db, member, message, seen, cache):
                    return False
                if source.get("hash"):
                    from .conversation_context import message_source
                    current_hash = message_source(db, message)["hash"]
                    # Frozen pre-checkpoint tool manifests used a body-only
                    # digest; new sources explicitly version the full snapshot.
                    if source["hash"] != current_hash and (source.get("hash_version") == 1 or source["hash"] != digest(message.body)):
                        return False
            elif source.get("kind") == "workspace":
                if str(source.get("id", "")).startswith("path:"):
                    try:
                        self._workspace_file_db(db, member.owner_id, member.chat_id, source["id"], allow_directory=True)
                    except HTTPException:
                        return False
                    continue
                node = db.get(WorkspaceNode, source.get("id"))
                if not node or node.chat_id != member.chat_id or not self.workspace_node_allowed(db, member, node):
                    return False
            elif source.get("kind") == "tracking":
                if source.get("floor_seq", member.lower_seq) < member.lower_seq or not self.sources_allowed(db, member, source.get("source_message_ids", [])):
                    return False
                if member.history_policy != "all":
                    if not source.get("created_at"):
                        return False
                    created = datetime.fromisoformat(source["created_at"])
                    cutoff = aware(member.joined_at) - timedelta(days=7 if member.history_policy == "fixed_7d" else 0)
                    if aware(created) < cutoff:
                        return False
            else:
                return False  # Unrecognized provenance must never broaden access.
        return True

    def manifest_provenance(self, db, request_id):
        manifests = db.scalars(select(ContextManifest).where(ContextManifest.request_id == request_id))
        distinct = {}
        for manifest in manifests:
            for source in manifest.sources:
                distinct[digest(source)] = source
        return list(distinct.values())

    def check_sources(self, db, member, source_ids):
        return [self.visible_message(db, member, source_id) for source_id in dict.fromkeys(source_ids)]

    def messages_json(self, db, messages, member, include_attachments=True, *, cache=None):
        from .reactions import prime
        from sqlalchemy.orm import load_only
        messages = list(messages)
        if not messages:
            return []
        ids = [message.id for message in messages]
        prime(db, ids)
        cache = cache if cache is not None else {}
        failures = {row.message_id: row for row in db.scalars(select(RoutingDecision).where(
            RoutingDecision.message_id.in_(ids), RoutingDecision.status == "failed"))}
        cache.update({("routing_failure", message_id): failures.get(message_id) for message_id in ids})
        receipts = {message_id: [] for message_id in ids}
        for message_id, owner_id in db.execute(select(Receipt.message_id, Receipt.owner_id).where(Receipt.message_id.in_(ids))):
            receipts[message_id].append(owner_id)
        # Keep these page-scoped rows alive in SQLAlchemy's identity map while
        # projecting. No cross-request authorization or content cache is used.
        _actors = list(db.scalars(select(Actor).where(Actor.id.in_({message.author_id for message in messages}))))
        _requests = list(db.scalars(select(ReplyRequest).where(ReplyRequest.id.in_({message.request_id for message in messages if message.request_id}))
            .options(load_only(ReplyRequest.id, ReplyRequest.chat_id, ReplyRequest.requester_id, ReplyRequest.client_request_key))))
        _attachments = list(db.scalars(select(ChatAttachment).where(ChatAttachment.id.in_({attachment_id for message in messages for attachment_id in (message.attachment_ids or [])})))) if include_attachments else []
        return [self.message_json(db, message, member, include_attachments, cache=cache, receipts=receipts) for message in messages]

    def set_message_reaction(self, owner_id, chat_id, message_id, emoji, active, key):
        from .reactions import projection, set_reaction, validate
        validate(emoji, active)
        if not isinstance(key, str) or not 1 <= len(key) <= 160:
            fail(400, "client_request_key must contain 1 to 160 characters")
        operation_id = "reaction-http:" + digest([owner_id, key])
        args_hash = digest({"message_id": message_id, "emoji": emoji, "active": active})
        with self.transaction() as db:
            from .platform_tools import lock_scope
            lock_scope(self, db, "set_message_reaction", {"chat_id": chat_id, "message_id": message_id},
                {"owner_id": owner_id, "chat": {"id": chat_id}})
            chat, member = self.membership(db, owner_id, chat_id)
            snapshot_revision = chat.revision
            if chat.kind == "private" and chat.reset_pending:
                fail(409, "Private chat reset is pending")
            self.visible_message(db, member, message_id)
            prior = db.get(ProductOperation, operation_id)
            if prior:
                if (prior.chat_id, prior.owner_id, prior.name, prior.arguments_hash) != (chat_id, owner_id, "set_message_reaction", args_hash):
                    fail(409, "Reaction operation key belongs to another action")
                # Never reapply an older desired state after a later operation.
                result = {"chat_id": chat_id, "message_id": message_id, "reactions": projection(db, message_id, owner_id)}
            else:
                actor = db.scalar(select(Actor).where(Actor.owner_id == owner_id, Actor.kind == "human"))
                if not actor:
                    fail(403, "Owner has no human actor")
                result = set_reaction(self, db, owner_id, chat_id, message_id, actor.id, emoji, active)
                db.add(ProductOperation(operation_id=operation_id, chat_id=chat_id, owner_id=owner_id,
                    name="set_message_reaction", arguments_hash=args_hash, result=result))
        # Capture this commit's returned revision, never a later post-commit DB
        # read. No-op/replay snapshots retain the revision held by our Chat lock.
        revision = db.info.get("chat_revisions", {}).get(chat_id, snapshot_revision)
        return {**{name: result[name] for name in ("chat_id", "message_id", "reactions")}, "revision": revision}

    def message_json(self, db, msg, member, include_attachments=True, *, cache=None, receipts=None):
        result = {k: getattr(msg, k) for k in ("id", "chat_id", "seq", "author_id", "kind", "body", "raw_owner_input", "request_id", "state")}
        if msg.command_owner_id == member.owner_id and msg.client_request_key:
            result["client_request_key"] = msg.client_request_key
        if msg.request_id:
            audit_origin = db.get(ReplyRequest, msg.request_id)
            if (msg.kind == "reply_for_me" and audit_origin
                    and audit_origin.chat_id == msg.chat_id
                    and audit_origin.requester_id == member.owner_id):
                result["client_request_key"] = audit_origin.client_request_key
            if audit_origin and audit_origin.chat_id != msg.chat_id:
                origin_chat = db.get(Chat, audit_origin.chat_id)
                if origin_chat:
                    result["request_id"] = None
        if include_attachments:
            result["attachments"] = [self.attachment_json(attachment) for attachment_id in (msg.attachment_ids or []) if (attachment := db.get(ChatAttachment, attachment_id)) and (not attachment.private_owner_input or attachment.owner_id == member.owner_id)]
        actor = db.get(Actor, msg.author_id)
        result.update(author_owner_id=actor.owner_id if actor else None, source_owner_id=actor.owner_id if actor else None, author_name=actor.name if actor else "Unknown", causal_chain_id=msg.causal_chain_id)
        from .reactions import projection
        result["reactions"] = projection(db, msg.id, member.owner_id)
        result["created_at"] = iso(msg.created_at)
        route = (cache.get(("routing_failure", msg.id)) if cache is not None and ("routing_failure", msg.id) in cache
                 else db.scalar(select(RoutingDecision).where(RoutingDecision.message_id == msg.id, RoutingDecision.status == "failed")))
        if route:
            result["routing_failure"] = {"id": route.id, "chat_id": msg.chat_id, "command_id": route.command_id or msg.id,
                "error": "Automatic reply could not start. Try again.",
                "can_retry": actor is not None and actor.owner_id == member.owner_id}
        result["read_by"] = receipts[msg.id] if receipts is not None else list(db.scalars(select(Receipt.owner_id).where(Receipt.message_id == msg.id)))
        if msg.reply_to_message_id:
            quoted = self.visible_message(db, member, msg.reply_to_message_id, False, cache=cache)
            if quoted:
                author = db.get(Actor, quoted.author_id)
                result["reply_to"] = {"id": quoted.id, "author_name": author.name if author else "Unknown", "body": quoted.body}
            else:
                result["reply_to"] = {"id": msg.reply_to_message_id, "author_name": "", "body": "Message unavailable", "unavailable": True}
        return result

    def request_json(self, db, request, owner_id):
        target = db.get(Actor, request.target_twin_id)
        owns = target is not None and target.owner_id == owner_id
        result = {**{k: getattr(request, k) for k in ("id", "chat_id", "source_message_id", "target_twin_id", "requester_id", "source", "status", "error", "session_id", "run_id", "causal_chain_id", "source_owner_id", "automatic_reservation_id")}, "accepted_at": iso(request.accepted_at), "cancel_requested": request.cancel_requested, "can_cancel": not request.cancel_requested and ( (request.status == "queued" and (owns or request.requester_id == owner_id)) or (request.status == "running" and owns)), "can_retry": request.source != "memory_dream" and request.status == "failed" and owns}
        from .scheduled_tasks import snapshot as scheduled_snapshot
        result["quiet_background"] = bool(scheduled_snapshot(request))
        if request.requester_id == owner_id:
            result["client_request_key"] = request.client_request_key
        return result

    def chat_title(self, db, chat, viewer_id, owner_ids=None):
        if chat.kind == "private":
            return self.twin(db, chat.private_owner_id).name
        owner_ids = owner_ids if owner_ids is not None else [row.owner_id for row in self.members(db, chat.id)]
        if len(owner_ids) == 2 and viewer_id in owner_ids:
            peer_id = next(owner_id for owner_id in owner_ids if owner_id != viewer_id)
            peer = db.scalar(select(Actor).where(Actor.owner_id == peer_id, Actor.kind == "human"))
            if peer:
                return peer.name
        return chat.title

    def chat_json(self, db, chat, member, owner_ids=None):
        from sqlalchemy.dialects.postgresql import JSONB
        from sqlalchemy.orm import load_only
        metadata = load_only(Message.id, Message.chat_id, Message.seq, Message.author_id, Message.kind, Message.created_at, Message.state,
                             Message.source_floor_seq, Message.source_message_ids, Message.source_provenance)
        visible = select(Message).options(metadata).where(Message.chat_id == chat.id, Message.seq >= member.lower_seq, Message.state == "published")
        last, cursor = None, None
        checked = {}
        # Only authorization metadata is paged; fetch one body for the final preview.
        while last is None:
            query = visible.where(Message.seq < cursor) if cursor else visible
            rows = list(db.scalars(query.order_by(Message.seq.desc()).limit(100)))
            if not rows:
                break
            last = next((message for message in rows if self.message_sources_visible(db, member, message, cache=checked)), None)
            if len(rows) < 100:
                break
            cursor = rows[-1].seq
        unread_filter = (Message.chat_id == chat.id, Message.seq >= member.lower_seq, Message.state == "published",
                         Message.author_id != member.owner_id,
                         ~select(Receipt.message_id).where(Receipt.message_id == Message.id, Receipt.owner_id == member.owner_id).exists())
        simple = (cast(Message.source_message_ids, JSONB) == []) & (cast(Message.source_provenance, JSONB) == [])
        unread = db.scalar(select(func.count()).select_from(Message).where(*unread_filter, simple,
            or_(Message.source_floor_seq.is_(None), Message.source_floor_seq >= member.lower_seq))) or 0
        # Dependent messages need current recursive grants; never disclose their count
        # without those checks. No historical message bodies are loaded for counting.
        for message in db.scalars(select(Message).options(metadata).where(*unread_filter, ~simple).execution_options(yield_per=100)):
            unread += int(self.message_sources_visible(db, member, message, cache=checked))
        owner_ids = owner_ids if owner_ids is not None else [m.owner_id for m in self.members(db, chat.id)]
        return {"id": chat.id, "kind": chat.kind, "title": self.chat_title(db, chat, member.owner_id, owner_ids), "revision": chat.revision,
                "can_rename": chat.kind == "group" and len(owner_ids) >= 3,
                "member_owner_ids": owner_ids, "unread_count": unread,
                "last_message": last.body if last else None,
                "last_message_meta": {"id": last.id, "seq": last.seq, "author_id": last.author_id,
                                      "kind": last.kind, "created_at": iso(last.created_at)} if last else None}

    def settings_row(self, db, owner_id, chat):
        row = db.get(ChatSetting, (owner_id, chat.id))
        if not row:
            row = ChatSetting(owner_id=owner_id, chat_id=chat.id, sidebar_tab="activity" if chat.kind == "private" else "workspace")
            db.add(row)
            db.flush()
        return row

    def settings_json(self, row):
        return {k: getattr(row, k) for k in ("reply_for_me_enabled", "prompt_override", "sidebar_open", "sidebar_tab")}

    def reminder_json(self, row):
        return {**{k: getattr(row, k) for k in ("id", "title", "detail", "done", "chat_id", "kind")}, "due_at": iso(row.due_at)}

    def bootstrap(self, owner_id):
        with self.transaction() as db:
            if not db.get(Owner, owner_id):
                fail(401, "Unknown owner identity")
            from .scheduled_tasks import ensure_system
            ensure_system(self, db, owner_id)
            memberships = list(db.scalars(select(Membership).where(Membership.owner_id == owner_id, Membership.left_at.is_(None))))
            return {"server_time": iso(now()), "current_owner_id": owner_id, "actors": [self.actor_json(a) for a in db.scalars(select(Actor).order_by(Actor.kind, Actor.name))], "chats": [self.chat_json(db, db.get(Chat, m.chat_id), m) for m in memberships], "reminders": [self.reminder_json(r) for r in db.scalars(select(Reminder).where(Reminder.owner_id == owner_id).order_by(Reminder.due_at))], "model_configured": self.model_configured, "development_mode": settings.local_identity_enabled}

    def people(self, owner_id, query):
        with self.transaction() as db:
            rows = db.scalars(select(Actor).where(Actor.kind == "human", Actor.name.ilike(f"%{query}%")).order_by(Actor.name))
            return [self.actor_json(a) for a in rows]

    def _validate_owner_ids(self, owner_ids):
        if (not isinstance(owner_ids, list) or not 1 <= len(owner_ids) <= 100
                or not all(isinstance(value, str) and value for value in owner_ids)):
            fail(400, "owner_ids must contain 1 to 100 owner IDs")

    def add_members_locked(self, db, chat, owner_ids, history_policy):
        if history_policy not in {"all", "fixed_7d", "no_prior"}:
            fail(400, "Invalid history policy")
        joined = now()
        for owner_id in dict.fromkeys(owner_ids):
            if not db.get(Owner, owner_id):
                fail(404, "Owner not found")
            self.twin(db, owner_id)
            existing = db.scalar(select(Membership).where(Membership.chat_id == chat.id, Membership.owner_id == owner_id))
            if existing:
                if existing.left_at is None:
                    continue
                fail(409, "Rejoining requires an explicit new history grant")
            lower = 1
            if history_policy == "no_prior":
                lower = chat.next_message_seq
            elif history_policy == "fixed_7d":
                lower = db.scalar(select(func.min(Message.seq)).where(Message.chat_id == chat.id, Message.created_at >= joined - timedelta(days=7))) or chat.next_message_seq
            db.add(Membership(chat_id=chat.id, owner_id=owner_id, history_policy=history_policy, lower_seq=lower, joined_at=joined))
        db.flush()

    def create_chat(self, owner_id, owner_ids, title=None, history_policy="all"):
        with self.transaction() as db:
            chat, result, created = self._create_chat_db(db, owner_id, owner_ids, title, history_policy)
        if created:
            self.publish_event(chat.id, {"type": "changed"})
        return result

    def _create_chat_db(self, db, owner_id, owner_ids, title=None, history_policy="all", *, locked_chat_ids=None):
        self._validate_owner_ids(owner_ids)
        if title is not None and not isinstance(title, str):
            fail(400, "title must be a string")
        if history_policy not in {"all", "fixed_7d", "no_prior"}:
            fail(400, "Invalid history policy")
        others = list(dict.fromkeys([owner_id, *owner_ids]))
        if len(others) < 2:
            fail(400, "Group chats require at least two owners; use your private chat for your own Duet")
        created = True
        names = list(db.scalars(select(Actor.name).where(Actor.owner_id.in_(others), Actor.kind == "human")))
        title = title.strip() if title and len(others) >= 3 else None
        if title is not None and (not title or len(title) > 256):
            fail(400, "Chat title must contain 1 to 256 characters")
        if len(others) == 2:
            from sqlalchemy.dialects.postgresql import insert
            key = pair_key(others)
            while True:
                inserted = db.scalar(insert(Chat).values(kind="group", title=", ".join(names)[:256], direct_pair_key=key)
                    .on_conflict_do_nothing(index_elements=[Chat.direct_pair_key]).returning(Chat.id))
                candidate = db.scalar(select(Chat).where(Chat.direct_pair_key == key))
                if candidate and not inserted and locked_chat_ids is not None and candidate.id not in locked_chat_ids:
                    fail(409, "Pair scope changed during creation; retry the operation")
                chat = db.scalar(select(Chat).where(Chat.direct_pair_key == key).with_for_update()
                    .execution_options(populate_existing=True))
                if chat is None:
                    # A conflicting conversation became a multi-owner group
                    # while INSERT waited. Its key is free for a new pair.
                    continue
                created = bool(inserted)
                if inserted:
                    self.add_members_locked(db, chat, others, history_policy)
                current_ids = [row.owner_id for row in self.members(db, chat.id)]
                if chat.kind != "group" or sorted(current_ids) != sorted(others):
                    fail(409, "Pair identity no longer matches current membership")
                # Reusing a pair never widens history grants or resets settings.
                member = self.membership(db, owner_id, chat.id)[1]
                result = self.chat_json(db, chat, member, current_ids)
                break
        else:
            chat = Chat(kind="group", title=title or ", ".join(names)[:256])
            db.add(chat)
            db.flush()
            self.add_members_locked(db, chat, others, history_policy)
            member = self.membership(db, owner_id, chat.id)[1]
            result = self.chat_json(db, chat, member)
        return chat, result, created

    def rename_chat(self, owner_id, chat_id, title):
        with self.transaction() as db:
            chat, result = self._rename_chat_db(db, owner_id, chat_id, title)
        result["revision"] = chat.revision
        self.publish_event(chat_id, {"type": "changed"})
        return result

    def _rename_chat_db(self, db, owner_id, chat_id, title):
        chat, member = self.membership(db, owner_id, chat_id, True)
        owner_ids = [row.owner_id for row in self.members(db, chat.id)]
        if chat.kind != "group" or len(owner_ids) < 3:
            fail(409, "Only group chats with at least three owners have an editable name")
        title = title.strip()
        if not title or len(title) > 256:
            fail(400, "Chat title must contain 1 to 256 characters")
        chat.title = title
        db.flush()
        result = self.chat_json(db, chat, member, owner_ids)
        return chat, result

    def add_members(self, owner_id, chat_id, owner_ids, history_policy):
        with self.transaction() as db:
            chat, result = self._add_chat_members_db(db, owner_id, chat_id, owner_ids, history_policy)
        result["revision"] = chat.revision
        self.publish_event(chat_id, {"type": "changed"})
        return result

    def _add_chat_members_db(self, db, owner_id, chat_id, owner_ids, history_policy):
        self._validate_owner_ids(owner_ids)
        chat, member = self.membership(db, owner_id, chat_id, True)
        if chat.kind != "group":
            fail(409, "Create a group chat to add paired owners and Duets; private chats stay private")
        was_pair = len(self.members(db, chat.id)) == 2
        self.add_members_locked(db, chat, owner_ids, history_policy)
        current_ids = [row.owner_id for row in self.members(db, chat.id)]
        if was_pair and len(current_ids) >= 3:
            chat.direct_pair_key = None
            names = db.scalars(select(Actor.name).where(Actor.owner_id.in_(current_ids), Actor.kind == "human").order_by(Actor.name))
            chat.title = ", ".join(names)[:256]
        result = self.chat_json(db, chat, member, current_ids)
        return chat, result

    def tracking_json(self, db, topic, member, details=False, *, chat=None, title=None):
        if not topic.source_message_ids:
            cutoff = member.joined_at if member.history_policy == "no_prior" else member.joined_at - timedelta(days=7) if member.history_policy == "fixed_7d" else None
            if cutoff and aware(topic.created_at) < aware(cutoff):
                return None
        if topic.deleted_at or not self.sources_allowed(db, member, topic.source_message_ids, topic.source_floor_seq) or not self.provenance_allowed(db, member, topic.source_provenance):
            return None
        result = {**{k: getattr(topic, k) for k in ("id", "chat_id", "title", "description", "status", "revision")}, "created_at": iso(topic.created_at), "updated_at": iso(topic.updated_at)}
        chat = chat or db.get(Chat, topic.chat_id)
        result.update(chat_kind=chat.kind, chat_title=title if title is not None else self.chat_title(db, chat, member.owner_id))
        if details:
            result["updates"] = [{"id": u.id, "body": u.body, "created_at": iso(u.created_at)} for u in db.scalars(select(TrackingUpdate).where(TrackingUpdate.topic_id == topic.id).order_by(TrackingUpdate.created_at)) if self.sources_allowed(db, member, u.source_message_ids, u.source_floor_seq) and self.provenance_allowed(db, member, u.source_provenance) and (u.source_message_ids or member.history_policy == "all" or aware(u.created_at) >= aware(member.joined_at) - timedelta(days=7 if member.history_policy == "fixed_7d" else 0))]
        return result

    def _tracking_scope(self, db, chat, member, limit=None):
        """An owner's private index unifies their authorized chats; groups stay local."""
        query = (select(TrackingTopic, Chat, Membership).join(Chat, Chat.id == TrackingTopic.chat_id)
                 .join(Membership, Membership.chat_id == Chat.id).where(
                     Membership.owner_id == member.owner_id, Membership.left_at.is_(None),
                     TrackingTopic.deleted_at.is_(None)))
        if chat.kind == "private" and chat.private_owner_id == member.owner_id:
            query = query.where(or_(Chat.id == chat.id, Chat.kind == "group"))
        else:
            query = query.where(Chat.id == chat.id)
        titles, rows = {}, []
        query = query.order_by(TrackingTopic.created_at, TrackingTopic.id)
        if limit is not None:
            query = query.limit(limit)
        for topic, origin, source_member in db.execute(query):
            if origin.id not in titles:
                titles[origin.id] = self.chat_title(db, origin, member.owner_id)
            projection = self.tracking_json(db, topic, source_member, chat=origin, title=titles[origin.id])
            if projection:
                rows.append((topic, origin, source_member, projection))
        return rows

    @staticmethod
    def _tracking_provenance(topic):
        return [*(topic.source_provenance or []), {"kind": "tracking", "id": topic.id,
            "revision": topic.revision, "floor_seq": topic.source_floor_seq,
            "created_at": iso(topic.created_at), "source_message_ids": topic.source_message_ids}]

    def tracking_index(self, owner_id, chat_id):
        with self.transaction() as db:
            chat, member = self.membership(db, owner_id, chat_id)
            return {"tracking": [row[3] for row in self._tracking_scope(db, chat, member)]}

    def approval_json(self, row):
        return {**{k: getattr(row, k) for k in ("id", "chat_id", "approver_id", "title", "description", "target", "impact", "status")}, "created_at": iso(row.created_at), "action": row.sdk_reference.get("action")}

    def chat_detail(self, owner_id, chat_id, limit=100, before_seq=None):
        from sqlalchemy.orm import aliased

        with self.transaction() as db:
            chat, member = self.membership(db, owner_id, chat_id)
            query = select(Message).where(Message.chat_id == chat_id, Message.seq >= member.lower_seq, Message.state == "published")
            if before_seq is not None:
                query = query.where(Message.seq < before_seq)
            messages = list(reversed(list(db.scalars(query.order_by(Message.seq.desc()).limit(limit + 1)))))
            has_more = len(messages) > limit
            messages = messages[-limit:]
            checked = {}
            newer = aliased(ReplyRequest)
            # Terminal retries also supersede older failures. Select the latest
            # generation before limiting so old attempts cannot consume the page.
            superseded = select(newer.id).where(newer.intent_key == ReplyRequest.intent_key,
                newer.generation > ReplyRequest.generation).exists()
            requests = list(db.scalars(select(ReplyRequest).where(ReplyRequest.chat_id == chat_id,
                ReplyRequest.status.in_(["queued", "running", "failed"]), ~superseded)
                .order_by(ReplyRequest.accepted_at.desc(), ReplyRequest.accepted_seq.desc()).limit(200)))
            requests = [r for r in requests if chat.kind != "private" or r.private_generation == chat.reset_generation]
            requests = [r for r in requests if r.raw_owner_input is None or r.requester_id == owner_id]
            requests = [r for r in requests if (r.source_message_id and self.visible_message(db, member, r.source_message_id, False, cache=checked)) or (r.source_message_id is None and r.requester_id == owner_id)]
            tracking = [row[3] for row in self._tracking_scope(db, chat, member)]
            members = [m.owner_id for m in self.members(db, chat_id)]
            from .questions import for_chat
            return {"questions": for_chat(self, db, chat, member, [m.id for m in messages if self.message_sources_visible(db, member, m, cache=checked)]), "revision": chat.revision, "has_more": has_more, "before_seq": messages[0].seq if messages else None, "chat": self.chat_json(db, chat, member), "messages": self.messages_json(db, [m for m in messages if self.message_sources_visible(db, member, m, cache=checked)], member, cache=checked), "requests": [self.request_json(db, r, owner_id) for r in requests], "automatic_conversations": self.automatic_conversations(db, chat, member), "approvals": self._approval_inbox(db, chat, member), "tracking": [t for t in tracking if t], "settings": self.settings_json(self.settings_row(db, owner_id, chat)), "members": [self.actor_json(a) for a in db.scalars(select(Actor).where(Actor.owner_id.in_(members)).order_by(Actor.kind, Actor.name))]}

    def _approval_inbox(self, db, chat, member):
        query = select(Approval).where(Approval.approver_id == member.owner_id, Approval.status == "pending")
        if chat.kind != "private":
            query = query.where(Approval.chat_id == chat.id)
        result = []
        for approval in db.scalars(query.order_by(Approval.created_at.desc()).limit(100)):
            try:
                _, origin_member = self.membership(db, member.owner_id, approval.chat_id)
                origin = db.get(ReplyRequest, approval.sdk_reference.get("request_id"))
                if not origin or origin.cancel_requested or origin.status == "cancelled":
                    continue
                self._check_private_request(db, origin)
                if self.sources_allowed(db, origin_member, approval.source_message_ids) and self.provenance_allowed(db, origin_member, approval.source_provenance):
                    result.append(self.approval_json(approval))
            except HTTPException:
                continue
        return result

    def automatic_conversations(self, db, chat, member):
        pending = {}
        for decision in db.scalars(select(RoutingDecision).join(Message, Message.id == RoutingDecision.message_id).where(
                Message.chat_id == chat.id, RoutingDecision.status == "paused", RoutingDecision.reason == "budget_exhausted")):
            message = self.visible_message(db, member, decision.message_id, False)
            if message:
                pending.setdefault(message.causal_chain_id, []).append(("budget_exhausted", message.id))
        for candidate in db.scalars(select(TriggerCandidate).where(TriggerCandidate.chat_id == chat.id, TriggerCandidate.state == "paused")):
            if not self.visible_message(db, member, candidate.message_id, False):
                continue
            reason = candidate.evidence.get("pause_reason")
            pending.setdefault(candidate.causal_chain_id, []).append((reason, candidate.message_id))
        for child in db.scalars(select(PendingChildResult).where(PendingChildResult.chat_id == chat.id, PendingChildResult.state == "paused")):
            if child.pause_reason not in {"budget_exhausted", "repeated_content"}:
                continue
            if not self.provenance_allowed(db, member, child.source_provenance) or (child.source_message_id and not self.visible_message(db, member, child.source_message_id, False)):
                continue
            # A source-free private result belongs only to its original owner.
            if not child.source_message_id and child.owner_id != member.owner_id:
                continue
            pending.setdefault(child.causal_chain_id, []).append((child.pause_reason, child.source_message_id))
        from .followups import _authorize
        for followup in db.scalars(select(TwinFollowup).where(TwinFollowup.chat_id == chat.id,
                TwinFollowup.owner_id == member.owner_id, TwinFollowup.notification_state == "paused")):
            if followup.pause_reason not in {"budget_exhausted", "repeated_content"}:
                continue
            try:
                origin, *_ = _authorize(self, db, followup)
            except HTTPException:
                continue
            pending.setdefault(followup.causal_chain_id, []).append((followup.pause_reason, origin.source_message_id))
        result = []
        for chain_id, items in pending.items():
            chain = db.get(CausalChain, chain_id)
            if not chain:
                continue
            budget = any(reason == "budget_exhausted" for reason, _ in items)
            result.append({"id": chain.id, "causal_chain_id": chain.id, "status": "paused", "reason": "budget_exhausted" if budget else "repeated_content", "spent": chain.spent, "allowance": chain.allowance, "remaining": max(0, chain.allowance - chain.spent), "paused_pending_count": len(items), "continuation_allowance": self.automatic_reply_allowance, "can_continue": budget and chain.spent >= chain.allowance, "source_owner_id": chain.root_owner_id, "source_message_id": next((message_id for _, message_id in items if message_id), None)})
        return result

    def continue_automatic_conversation(self, owner_id, chat_id, chain_id, key, additional_replies, expected_allowance):
        with self.transaction() as db:
            chat, member = self.membership(db, owner_id, chat_id, True)
            chain = db.scalar(select(CausalChain).where(CausalChain.id == chain_id).with_for_update())
            if not chain:
                fail(404, "Automatic conversation not found")
            prior = db.scalar(select(AllowanceGrant).where(AllowanceGrant.owner_id == owner_id, AllowanceGrant.client_request_key == key))
            if prior:
                if prior.chat_id != chat_id or prior.causal_chain_id != chain_id or prior.amount != additional_replies or prior.base_allowance != expected_allowance:
                    fail(409, "Continue idempotency key belongs to another authorization")
                chain = db.get(CausalChain, chain_id)
                grant = prior
                replay = True
            else:
                if type(additional_replies) is not int or additional_replies != self.automatic_reply_allowance:
                    fail(409, "Automatic reply allowance changed; refresh before authorizing Continue")
                if type(expected_allowance) is not int or chain.allowance != expected_allowance or chain.spent != chain.allowance:
                    fail(409, "Automatic conversation allowance changed; refresh before authorizing Continue")
                rows = self.automatic_conversations(db, chat, member)
                row = next((row for row in rows if row["id"] == chain_id), None)
                if not row or not row["can_continue"]:
                    fail(409, "No visible budget-paused work is available to continue")
                chain = db.scalar(select(CausalChain).where(CausalChain.id == chain_id).with_for_update())
                grant = AllowanceGrant(causal_chain_id=chain.id, chat_id=chat_id, owner_id=owner_id, client_request_key=key, amount=additional_replies, base_allowance=expected_allowance)
                db.add(grant)
                # Atomic increment preserves lifetime spending and other grants.
                updated = db.execute(update(CausalChain).where(CausalChain.id == chain.id, CausalChain.allowance == expected_allowance, CausalChain.spent == CausalChain.allowance).values(allowance=CausalChain.allowance + additional_replies))
                if updated.rowcount != 1:
                    fail(409, "Automatic conversation allowance changed; refresh before authorizing Continue")
                db.flush()
                for candidate in db.scalars(select(TriggerCandidate).where(TriggerCandidate.causal_chain_id == chain_id, TriggerCandidate.state == "paused")):
                    if candidate.evidence.get("pause_reason") == "budget_exhausted":
                        candidate.state = "waiting"
                for pending in db.scalars(select(PendingChildResult).where(PendingChildResult.causal_chain_id == chain_id, PendingChildResult.state == "paused", PendingChildResult.pause_reason == "budget_exhausted")):
                    pending.state = "pending"
                for followup in db.scalars(select(TwinFollowup).where(TwinFollowup.causal_chain_id == chain_id,
                        TwinFollowup.notification_state == "paused", TwinFollowup.pause_reason == "budget_exhausted")):
                    followup.notification_state, followup.pause_reason = "pending", None
                for decision in db.scalars(select(RoutingDecision).join(Message, Message.id == RoutingDecision.message_id).where(
                        Message.causal_chain_id == chain_id, RoutingDecision.status == "paused", RoutingDecision.reason == "budget_exhausted")):
                    decision.status, decision.reason = "pending", None
                replay = False
            result = {"id": chain.id, "causal_chain_id": chain.id, "spent": chain.spent, "allowance": chain.allowance, "remaining": max(0, chain.allowance - chain.spent), "continuation_allowance": grant.amount, "grant_id": grant.id, "already_applied": replay}
        self.process_candidates({chat_id})
        self.process_pending_child_results({chat_id})
        self.process_followups({chat_id})
        self.publish_event(chat_id, {"type": "changed"})
        return result

    def append_message(self, db, chat, *, consume_followup=True, **values):
        msg = Message(chat_id=chat.id, seq=chat.next_message_seq, **values)
        chat.next_message_seq += 1
        db.add(msg)
        db.flush()
        from .memory_dream import message_published
        message_published(self, db, chat, msg)
        if consume_followup:
            from .followups import consume_reply
            consume_reply(self, db, msg)
        return msg

    def accept_request(self, db, chat, target_twin_id, requester_id, source, key, source_message_id=None, raw=None, prompt=None, reply_to=None, generation=0, intent_key=None, input_body=None, causal_chain_id=None, automatic_reservation_id=None, source_owner_id=None, input_attachment_ids=None, command_fingerprint=None):
        if chat.kind == "private" and chat.reset_pending:
            fail(409, "Private reset cleanup is pending; retry the original reset")
        target = db.get(Actor, target_twin_id)
        if not target or target.kind != "twin":
            fail(400, "Target must be a paired Duet")
        self.membership(db, target.owner_id, chat.id)
        member = self.membership(db, requester_id, chat.id)[1]
        if source_message_id:
            self.visible_message(db, member, source_message_id)
            self.visible_message(db, self.membership(db, target.owner_id, chat.id)[1], source_message_id)
        existing = db.scalar(select(ReplyRequest).where(ReplyRequest.requester_id == requester_id, ReplyRequest.client_request_key == key))
        if existing:
            if existing.chat_id != chat.id or existing.target_twin_id != target_twin_id or existing.source_message_id != source_message_id or existing.raw_owner_input != raw:
                fail(409, "Idempotency key was already used for a different request")
            return existing
        intent_key = intent_key or (f"message:{source_message_id}:{target_twin_id}" if source_message_id else f"input:{requester_id}:{key}")
        prior = db.scalar(select(ReplyRequest).where(ReplyRequest.intent_key == intent_key, ReplyRequest.generation == generation))
        if prior:
            return prior
        source_message = db.get(Message, source_message_id) if source_message_id else None
        chain = db.get(CausalChain, causal_chain_id) if causal_chain_id else self.message_chain(db, source_message) if source_message else self.new_causal_chain(db, requester_id)
        if not chain:
            fail(409, "Request has no durable causal lineage")
        source_owner_id = source_owner_id or (self.message_owner(db, source_message) if source_message else requester_id)
        reservation = db.get(AutomaticReservation, automatic_reservation_id) if automatic_reservation_id else None
        if reservation and reservation.causal_chain_id != chain.id:
            fail(409, "Retry reservation belongs to another causal chain")
        if source in {"auto", "private", "subagent_result", "followup_result", "scheduled_task", "system_review"} and not reservation:
            reservation, pause_reason = self.reserve_automatic(db, chain.id, intent_key, source_owner_id, target.owner_id, input_body if input_body is not None else source_message.body if source_message else "")
            if pause_reason:
                if source_message_id and source != "subagent_result":
                    candidate = db.scalar(select(TriggerCandidate).where(TriggerCandidate.message_id == source_message_id, TriggerCandidate.target_twin_id == target_twin_id))
                    if not candidate:
                        candidate = TriggerCandidate(chat_id=chat.id, message_id=source_message_id, target_twin_id=target_twin_id, causal_chain_id=chain.id, due_at=now(), evidence={})
                        db.add(candidate)
                    candidate.state = "paused"
                    candidate.causal_chain_id = chain.id
                    candidate.evidence = {**(candidate.evidence or {}), "pause_reason": pause_reason, "paused_at": iso(now())}
                    db.flush()
                return None
        lane = db.scalar(select(ReplyLane).where(ReplyLane.chat_id == chat.id, ReplyLane.twin_id == target_twin_id).with_for_update())
        if not lane:
            lane = ReplyLane(chat_id=chat.id, twin_id=target_twin_id, next_accept_seq=1)
            db.add(lane)
            db.flush()
        request = ReplyRequest(private_generation=chat.reset_generation if chat.kind == "private" else None, input_attachment_ids=input_attachment_ids or [], command_fingerprint=command_fingerprint, chat_id=chat.id, target_twin_id=target_twin_id, requester_id=requester_id, source=source, client_request_key=key, source_message_id=source_message_id, intent_key=intent_key, generation=generation, accepted_seq=lane.next_accept_seq, raw_owner_input=raw, frozen_prompt=prompt, reply_to_message_id=reply_to, input_body=input_body, causal_chain_id=chain.id, source_owner_id=source_owner_id, automatic_reservation_id=reservation.id if reservation else None)
        lane.next_accept_seq += 1
        db.add(request)
        db.flush()
        if reservation and not reservation.accepted_request_id:
            reservation.accepted_request_id = request.id
        db.add(DispatchCommand(request_id=request.id))
        if source_message_id:
            candidate = db.scalar(select(TriggerCandidate).where(TriggerCandidate.message_id == source_message_id, TriggerCandidate.target_twin_id == target_twin_id))
            if candidate:
                candidate.state, candidate.accepted_request_id = "accepted", request.id
        db.flush()
        return request

    def route_committed_message(self, db, chat, message):
        """Called once for a full committed message, never tokens/tool-use drafts."""
        if message.state != "published" or chat.kind != "group" or not self.message_has_model_input(db, message):
            return
        if not db.scalar(select(RoutingDecision.id).where(RoutingDecision.message_id == message.id)):
            owners = self.members(db, chat.id)
            db.add(RoutingDecision(message_id=message.id, strategy="pair" if len(owners) == 2 else "llm"))

    def send_message(self, owner_id, chat_id, body, key, reply_to=None, reply_for_me=False, attachment_ids=None):
        attachment_ids = list(attachment_ids or [])
        with self.transaction() as db:
            from .followups import lock_reply_scopes
            lock_reply_scopes(self, db, chat_id, reply_to)
            chat, member = self.membership(db, owner_id, chat_id, True)
            if chat.kind == "private" and chat.reset_pending:
                fail(409, "Private reset cleanup is pending")
            attachments = self._attachment_selection(db, owner_id, chat_id, attachment_ids)
            if not body.strip() and not attachments:
                fail(400, "Message cannot be blank")
            fingerprint = digest([body, reply_to, reply_for_me, [[row.id, row.sha256] for row in attachments]])
            existing = db.scalar(select(Message).where(Message.command_owner_id == owner_id, Message.client_request_key == key))
            if existing:
                if (existing.command_fingerprint and existing.command_fingerprint != fingerprint) or existing.chat_id != chat_id or existing.body != body or existing.reply_to_message_id != reply_to or (not existing.command_fingerprint and attachment_ids):
                    fail(409, "Idempotency key was already used for a different message")
                return {"message": self.message_json(db, existing, member), "request": None}
            pending = db.scalar(select(ReplyRequest).where(ReplyRequest.requester_id == owner_id, ReplyRequest.client_request_key == key))
            if pending:
                if (pending.command_fingerprint and pending.command_fingerprint != fingerprint) or pending.chat_id != chat_id or pending.raw_owner_input != body or (not pending.command_fingerprint and attachment_ids):
                    fail(409, "Idempotency key was already used for a different request")
                return {"message": None, "request": self.request_json(db, pending, owner_id)}
            if any(row.message_id or row.request_id for row in attachments):
                fail(409, "An attachment is already bound to another message")
            has_model_input = bool(body.strip() or attachments)
            if reply_to:
                self.visible_message(db, member, reply_to)
            self.suppress_candidates(db, owner_id, chat_id, "owner_input")
            if reply_for_me:
                own_twin = self.twin(db, owner_id)
                config = self.settings_row(db, owner_id, chat)
                prompt = config.prompt_override if config.prompt_override is not None else db.get(Owner, owner_id).default_reply_prompt
                request = self.accept_request(db, chat, own_twin.id, owner_id, "reply_for_me", key, raw=body, prompt=prompt, reply_to=reply_to, input_attachment_ids=attachment_ids, command_fingerprint=fingerprint)
                for row in attachments:
                    row.request_id, row.private_owner_input = request.id, True
                result = {"message": None, "request": self.request_json(db, request, owner_id)}
            else:
                chain = self.new_causal_chain(db, owner_id)
                msg = self.append_message(db, chat, author_id=owner_id, kind="human", body=body, reply_to_message_id=reply_to, command_owner_id=owner_id, client_request_key=key, causal_chain_id=chain.id, attachment_ids=attachment_ids, command_fingerprint=fingerprint)
                for row in attachments:
                    row.message_id = msg.id
                from .onboarding import owner_message
                owner_message(db, chat, msg)
                chain.root_message_id = msg.id
                db.add(Receipt(message_id=msg.id, owner_id=owner_id))
                self.suppress_candidates(db, owner_id, chat_id, "human_reply")
                from .questions import consume_reply
                question_continuation = consume_reply(self, db, msg)
                if question_continuation:
                    request = question_continuation
                elif chat.kind == "private" and has_model_input:
                    request = self.accept_request(db, chat, self.twin(db, owner_id).id, owner_id, "private", f"auto:{msg.id}", source_message_id=msg.id)
                else:
                    if has_model_input:
                        self.route_committed_message(db, chat, msg)
                    request = None
                result = {"message": self.message_json(db, msg, member), "request": self.request_json(db, request, owner_id) if request else None}
        self.publish_event(chat_id, {"type": "changed"})
        return result

    def manual_request(self, owner_id, chat_id, message_id, target_twin_id, key):
        with self.transaction() as db:
            chat, member = self.membership(db, owner_id, chat_id, True)
            target = db.get(Actor, target_twin_id)
            if not target or target.kind != "twin" or target.owner_id != owner_id:
                fail(403, "You can request replies only from your own Duet")
            source = self.visible_message(db, member, message_id)
            if not self.message_has_model_input(db, source):
                fail(400, "This message has no text or files to send to the model")
            request = self.accept_request(db, chat, target_twin_id, owner_id, "manual", key, source_message_id=message_id)
            result = self.request_json(db, request, owner_id)
        self.publish_event(chat_id, {"type": "changed"})
        return result

    def presence_state(self, db, owner_id, chat_id):
        value = self._presence_cache.get(owner_id)
        if value is None:
            return None, None
        online = value.get("expires_at", 0) > datetime.now(timezone.utc).timestamp()
        return online, bool(online and chat_id in value.get("focused_chats", []))

    async def update_presence(self, owner_id, device_id, focused_chat_id, visible):
        client = notifications.redis
        if not client:
            fail(503, "Presence unavailable; automatic replies are paused")
        key = f"{settings.redis_prefix}:presence:{owner_id}:{device_id}"
        value = json.dumps({"chat_id": focused_chat_id, "visible": visible}, sort_keys=True)
        try:
            prior = await client.get(key)
            if prior != value:
                await self.db_call("presence", owner_id, device_id, focused_chat_id, visible)
            async with client.pipeline(transaction=True) as pipe:
                pipe.set(key, value, ex=settings.presence_ttl_seconds)
                pipe.sadd(f"{settings.redis_prefix}:devices:{owner_id}", device_id)
                pipe.expire(f"{settings.redis_prefix}:devices:{owner_id}", 86400)
                await pipe.execute()
            if prior != value:
                notifications.emit(focused_chat_id)
        except HTTPException:
            raise
        except Exception:
            fail(503, "Presence unavailable; automatic replies are paused")
        return {"ok": True}

    def _presence_owners(self, scopes=None):
        with self.transaction() as db:
            query = select(Actor.owner_id).distinct().join(TriggerCandidate, TriggerCandidate.target_twin_id == Actor.id).where(TriggerCandidate.state == "waiting")
            if scopes is not None:
                query = query.where(TriggerCandidate.chat_id.in_(scopes))
            return list(db.scalars(query.limit(100)))

    async def refresh_presence(self, scopes=None):
        owners = await self.db_call("_presence_owners", scopes)
        try:
            if not notifications.redis:
                raise RuntimeError("Redis is unavailable")
            for owner_id in owners:
                devices_key = f"{settings.redis_prefix}:devices:{owner_id}"
                devices = await notifications.redis.smembers(devices_key)
                values = await notifications.redis.mget([f"{settings.redis_prefix}:presence:{owner_id}:{d}" for d in devices]) if devices else []
                live = [json.loads(value) for value in values if value]
                self._presence_cache[owner_id] = {"expires_at": datetime.now(timezone.utc).timestamp() + (settings.presence_ttl_seconds if live else 0), "focused_chats": [v["chat_id"] for v in live if v["visible"] and v["chat_id"]]}
            self._presence_known = True
        except Exception:
            self._presence_cache.clear()
            self._presence_known = False

    def suppress_candidates(self, db, owner_id, chat_id, reason, message_ids=None):
        twin = self.twin(db, owner_id)
        query = select(TriggerCandidate).where(TriggerCandidate.chat_id == chat_id, TriggerCandidate.target_twin_id == twin.id, TriggerCandidate.state.in_(["waiting", "paused"]), TriggerCandidate.accepted_request_id.is_(None))
        if message_ids is not None:
            query = query.where(TriggerCandidate.message_id.in_(message_ids))
        for candidate in db.scalars(query):
            candidate.state = "suppressed"
            candidate.evidence = {**candidate.evidence, "suppression": reason, "suppressed_at": iso(now())}

    def presence(self, owner_id, device_id, focused_chat_id, visible):
        with self.transaction() as db:
            if focused_chat_id:
                self.membership(db, owner_id, focused_chat_id, True)
            row = db.get(Presence, (owner_id, device_id))
            if not row:
                row = Presence(owner_id=owner_id, device_id=device_id, expires_at=now() + timedelta(seconds=settings.presence_ttl_seconds), visible=False)
                db.add(row)
            previous_live = bool(row.expires_at and aware(row.expires_at) > now())
            same_view = previous_live and row.visible and row.focused_chat_id == focused_chat_id and visible
            if not same_view:
                for interval in db.scalars(select(ViewInterval).where(ViewInterval.owner_id == owner_id, ViewInterval.device_id == device_id, ViewInterval.ended_at > now())):
                    interval.ended_at = min(now(), aware(row.expires_at)) if row.expires_at else now()
                if visible and focused_chat_id:
                    db.add(ViewInterval(owner_id=owner_id, device_id=device_id, chat_id=focused_chat_id, ended_at=now() + timedelta(seconds=settings.presence_ttl_seconds)))
            elif visible and focused_chat_id:
                for interval in db.scalars(select(ViewInterval).where(ViewInterval.owner_id == owner_id, ViewInterval.device_id == device_id, ViewInterval.chat_id == focused_chat_id, ViewInterval.ended_at > now())):
                    interval.ended_at = now() + timedelta(seconds=settings.presence_ttl_seconds)
            row.focused_chat_id, row.visible = focused_chat_id, visible
            row.expires_at = now() + timedelta(seconds=settings.presence_ttl_seconds)
            live = list(db.scalars(select(Presence).where(Presence.owner_id == owner_id, Presence.expires_at > now())))
            self._presence_cache[owner_id] = {"expires_at": max((aware(item.expires_at).timestamp() for item in live), default=0), "focused_chats": [item.focused_chat_id for item in live if item.visible and item.focused_chat_id]}
        return {"ok": True}

    def read(self, owner_id, chat_id, message_ids):
        with self.transaction() as db:
            chat, member = self.membership(db, owner_id, chat_id, True)
            for message_id in dict.fromkeys(message_ids):
                self.visible_message(db, member, message_id)
                if not db.get(Receipt, (message_id, owner_id)):
                    db.add(Receipt(message_id=message_id, owner_id=owner_id))
            if chat.kind == "group":
                self.suppress_candidates(db, owner_id, chat_id, "owner_read", message_ids=message_ids)
        self.publish_event(chat_id, {"type": "changed"})
        return {"ok": True}

    def owner_has_replied(self, db, owner_id, chat_id, message):
        human = select(Message.id).where(Message.chat_id == chat_id,
            Message.author_id == owner_id, Message.seq > message.seq).exists()
        composing = select(ReplyRequest.id).where(ReplyRequest.chat_id == chat_id,
            ReplyRequest.requester_id == owner_id, ReplyRequest.source == "reply_for_me",
            ReplyRequest.accepted_at > message.created_at).exists()
        return db.scalar(select(or_(human, composing)))

    def create_candidates(self, db, chat, message, owner_ids):
        from .followups import consumed_owners
        consumed = consumed_owners(db, message)
        source_owner = self.message_owner(db, message)
        chain = self.message_chain(db, message)
        for target_owner in dict.fromkeys(owner_ids):
            if target_owner == source_owner or target_owner in consumed:
                continue
            member = self.membership(db, target_owner, chat.id)[1]
            if not self.visible_message(db, member, message.id, False):
                continue
            twin = self.twin(db, target_owner)
            if db.scalar(select(TriggerCandidate).where(TriggerCandidate.message_id == message.id, TriggerCandidate.target_twin_id == twin.id)):
                continue
            online, viewing = self.presence_state(db, target_owner, chat.id)
            seen = db.get(Receipt, (message.id, target_owner)) is not None
            replied = self.owner_has_replied(db, target_owner, chat.id, message)
            state = "suppressed" if replied or seen else "waiting"
            candidate = TriggerCandidate(chat_id=chat.id, message_id=message.id, target_twin_id=twin.id, causal_chain_id=chain.id, state=state, due_at=aware(message.created_at) + timedelta(seconds=settings.response_delay_seconds) if online else now(), evidence={"online": online, "viewing": viewing, "read": seen, "replied": replied, "observed_at": iso(now())})
            if seen:
                candidate.evidence = {**candidate.evidence, "suppression": "owner_read"}
            db.add(candidate)
            db.flush()
            # New candidates settle only after the worker refreshes presence; a
            # previously cached offline observation cannot authorize a new reply.
            prior = db.scalar(select(ReplyRequest).where(ReplyRequest.intent_key == f"message:{message.id}:{twin.id}", ReplyRequest.generation == 0))
            if prior:
                candidate.state, candidate.accepted_request_id = "accepted", prior.id

    def retry_route(self, owner_id, chat_id, decision_id, key, expected_command_id):
        if not isinstance(key, str) or not 1 <= len(key) <= 160:
            fail(400, "client_request_key must contain 1 to 160 characters")
        operation_id = "route-retry:" + digest([owner_id, key])
        arguments_hash = digest([chat_id, decision_id, expected_command_id])
        with self.transaction() as db:
            chat, member = self.membership(db, owner_id, chat_id, True)
            decision = db.scalar(select(RoutingDecision).where(RoutingDecision.id == decision_id).with_for_update())
            if not decision:
                fail(404, "Routing decision not found")
            message = self.visible_message(db, member, decision.message_id)
            if self.message_owner(db, message) != owner_id:
                fail(403, "Only the source message's owner can retry routing")
            prior = db.get(ProductOperation, operation_id)
            if prior:
                if (prior.chat_id, prior.owner_id, prior.name, prior.arguments_hash) != (chat_id, owner_id, "retry_route", arguments_hash):
                    fail(409, "Retry key belongs to another action")
                return prior.result
            if expected_command_id != (decision.command_id or message.id):
                fail(409, "This routing attempt has already been retried; refresh the conversation")
            if decision.status != "failed":
                fail(409, "Only failed routing decisions can be retried")
            # A fresh durable command is one explicit attempt. Replaying a failed
            # immutable SDK command would only return its cached failure; automatic
            # resume here could repeat a failed click after a worker restart.
            decision.command_id, decision.status = uid(), "pending"
            decision.error, decision.reason, decision.needs_response = None, None, None
            decision.inferred_owner_ids, decision.mentioned_owner_ids = [], []
            result = {"id": decision.id, "chat_id": chat.id, "command_id": decision.command_id, "status": decision.status}
            db.add(ProductOperation(operation_id=operation_id, chat_id=chat.id, owner_id=owner_id,
                name="retry_route", arguments_hash=arguments_hash, result=result))
        self.publish_event(chat_id, {"type": "changed"})
        return result

    def pending_routes(self, scopes=None):
        with self.transaction() as db:
            query = select(RoutingDecision.id).where(RoutingDecision.status == "pending")
            if scopes is not None:
                query = query.join(Message, Message.id == RoutingDecision.message_id).where(Message.chat_id.in_(scopes))
            return list(db.scalars(query.order_by(RoutingDecision.created_at).limit(100)))

    def _prepare_route(self, decision_id):
        with self.transaction() as db:
            decision = db.get(RoutingDecision, decision_id)
            if not decision or decision.status != "pending":
                return None
            message = db.get(Message, decision.message_id)
            if not message or message.state != "published":
                decision.status, decision.error = "failed", "Committed source message is unavailable"
                return None
            source_owner = self.message_owner(db, message)
            try:
                chat, member = self.membership(db, source_owner, message.chat_id)
                from .conversation_context import latest_summary, read_context, scope_for
                _, _, summary_scope = scope_for(self, db, {"chat_id": chat.id, "owner_id": member.owner_id,
                    "high_water_seq": message.seq})
                checked = {}
                covering = latest_summary(self, db, chat, member, summary_scope)
                if covering:
                    checked[("covering_summary", member.chat_id)] = covering.id
                self.visible_message(db, member, message.id, cache=checked)
            except HTTPException:
                decision.status, decision.error = "failed", "Source message is no longer authorized"
                return None
            owners = [m.owner_id for m in self.members(db, chat.id)]
            participants = [{key: getattr(actor, key) for key in ("id", "kind", "owner_id", "name")}
                for actor in db.scalars(select(Actor).where(Actor.owner_id.in_(owners)).order_by(Actor.id))]
            from .followups import consumed_owners
            consumed = consumed_owners(db, message)
            owners = [owner for owner in owners if owner not in consumed]
            if not any(owner != source_owner for owner in owners):
                decision.status, decision.needs_response = "completed", False
                decision.reason = "handled_by_followup" if consumed else "no_other_recipient"
                return None
            chain = self.message_chain(db, message)
            if chain.spent >= chain.allowance:
                decision.status, decision.reason = "paused", "budget_exhausted"
                return None
            context, _, _ = read_context(self, db, chat, member, summary_scope, cache=checked)
            payload = self.message_json(db, message, member, False, cache=checked)
            payload["attachments"] = self.attachment_references(db, message.attachment_ids, member)
            aliases = {}
            for actor in participants:
                if actor["owner_id"] in owners:
                    for alias in (actor["id"], actor["name"]):
                        aliases.setdefault(alias.casefold(), []).append(actor["owner_id"])
            # Prefer the longest current name/ID and require mention boundaries:
            # @Anna must not also select Ann, or an owner-ID prefix.
            pattern = r"(?<![\w@])@(" + "|".join(re.escape(alias) for alias in sorted(aliases, key=len, reverse=True)) + r")(?![\w-])"
            mentioned = list(dict.fromkeys(owner for match in re.finditer(pattern, message.body.casefold())
                for owner in aliases[match[1]]))
            decision.mentioned_owner_ids = mentioned
        return chat.id, payload, context, owners, participants, decision.command_id or message.id

    def _settle_route(self, decision_id, inferred, needs_response, reason, error, command_id=None):
        with self.transaction() as db:
            decision = db.get(RoutingDecision, decision_id)
            if not decision:
                return
            message = db.get(Message, decision.message_id)
            chat = db.scalar(select(Chat).where(Chat.id == message.chat_id).with_for_update())
            db.refresh(decision, with_for_update=True)
            if decision.status != "pending" or (command_id is not None and command_id != (decision.command_id or message.id)):
                return
            source_owner = self.message_owner(db, message)
            current_owners = [m.owner_id for m in self.members(db, chat.id)]
            from .followups import consumed_owners
            consumed = consumed_owners(db, message)
            eligible = [owner for owner in current_owners if owner != source_owner and owner not in consumed]
            decision.inferred_owner_ids = list(dict.fromkeys(x for x in inferred if x in eligible))
            decision.needs_response, decision.reason = needs_response, reason
            decision.status, decision.error = ("failed", error) if error else ("completed", None)
            # Mentions identify recipients; they do not force another response.
            targets = list(dict.fromkeys([*decision.inferred_owner_ids, *[x for x in decision.mentioned_owner_ids if x in eligible]])) if needs_response and not error else []
            if message.state == "published" and source_owner in current_owners:
                self.create_candidates(db, chat, message, targets)
    async def route_pending(self, decision_ids=None):
        touched = set()
        ids = decision_ids if decision_ids is not None else await self.db_call("pending_routes")
        for decision_id in ids:
            prepared = await self.db_call("_prepare_route", decision_id)
            if not prepared:
                continue
            chat_id, payload, context, owners, participants, command_id = prepared
            error = None
            try:
                if not self.runtime or not self.model_configured:
                    raise RuntimeError("Automatic routing requires a configured model")
                routed = await resolve(self.runtime.route_targets(chat_id, payload, context, owners, participants, command_id=command_id))
                if not isinstance(routed, dict) or type(routed.get("needs_response")) is not bool or not isinstance(routed.get("target_owner_ids"), list) or not all(isinstance(value, str) for value in routed["target_owner_ids"]):
                    raise RuntimeError("Router returned an invalid decision")
                inferred, needs_response = routed["target_owner_ids"], routed["needs_response"]
                reason = str(routed.get("reason", ""))[:2000]
            except Exception as exc:
                from .runtime_bridge import RuntimeBusy
                from agent_runtime.storage.coordination import ExecutionLeaseError
                if isinstance(exc, (RuntimeBusy, ExecutionLeaseError)):
                    continue
                inferred, needs_response, reason, error = [], False, None, str(exc)
            await self.db_call("_settle_route", decision_id, inferred, needs_response, reason, error, command_id)
            self.publish_event(chat_id, {"type": "changed"})
            touched.add(chat_id)
        return touched

    def process_candidates(self, scopes=None):
        changed = set()
        with self.transaction() as db:
            query = select(TriggerCandidate.id).where(TriggerCandidate.state == "waiting", TriggerCandidate.due_at <= now())
            if scopes is not None:
                query = query.where(TriggerCandidate.chat_id.in_(scopes))
            ids = list(db.scalars(query.order_by(TriggerCandidate.due_at).limit(100)))
        for candidate_id in ids:
            with self.transaction() as db:
                candidate = db.get(TriggerCandidate, candidate_id)
                if not candidate:
                    continue
                chat = db.scalar(select(Chat).where(Chat.id == candidate.chat_id).with_for_update())
                db.refresh(candidate, with_for_update=True)
                if candidate.state == "paused":
                    if candidate.evidence.get("pause_reason") != "budget_exhausted":
                        continue
                    chain = db.get(CausalChain, candidate.causal_chain_id)
                    if not chain or chain.spent >= chain.allowance:
                        continue
                elif candidate.state != "waiting":
                    continue
                if chat.kind == "group":
                    decision = db.scalar(select(RoutingDecision).where(RoutingDecision.message_id == candidate.message_id))
                    if not decision:
                        self.route_committed_message(db, chat, db.get(Message, candidate.message_id))
                        continue
                    if decision.needs_response is None:
                        # Pre-upgrade waiting work was never accepted. It must
                        # pass today's response gate before spending allowance.
                        decision.status, decision.error = "pending", None
                        continue
                    if decision.needs_response is not True or decision.status != "completed":
                        candidate.state = "suppressed"
                        candidate.evidence = {**candidate.evidence, "suppression": "no_response_required"}
                        continue
                target = db.get(Actor, candidate.target_twin_id)
                if chat.kind == "group":
                    routed_owners = set(decision.inferred_owner_ids or []) | set(decision.mentioned_owner_ids or [])
                    if not target or target.owner_id not in routed_owners:
                        candidate.state = "suppressed"
                        candidate.evidence = {**candidate.evidence, "suppression": "not_a_routed_recipient"}
                        changed.add(chat.id)
                        continue
                message = db.get(Message, candidate.message_id)
                if chat.kind == "group" and db.get(Receipt, (message.id, target.owner_id)):
                    candidate.state = "suppressed"
                    candidate.evidence = {**candidate.evidence, "suppression": "owner_read", "observed_at": iso(now())}
                    changed.add(chat.id)
                    continue
                online, _ = self.presence_state(db, target.owner_id, chat.id)
                if online is None:
                    continue
                deadline = aware(message.created_at) + timedelta(seconds=settings.response_delay_seconds)
                if online and deadline > now():
                    candidate.due_at = deadline
                    continue
                member = db.scalar(select(Membership).where(Membership.chat_id == chat.id, Membership.owner_id == target.owner_id, Membership.left_at.is_(None)))
                replied = self.owner_has_replied(db, target.owner_id, chat.id, message)
                if replied or not member or not self.visible_message(db, member, message.id, False):
                    candidate.state = "suppressed"
                    candidate.evidence = {**candidate.evidence, "suppression": "eligibility_at_deadline", "observed_at": iso(now())}
                else:
                    source_owner = self.message_owner(db, message)
                    if source_owner == target.owner_id and chat.kind != "private":
                        candidate.state = "suppressed"
                        candidate.evidence = {**candidate.evidence, "suppression": "same_source_owner"}
                    else:
                        self.accept_request(db, chat, target.id, source_owner, "private" if chat.kind == "private" else "auto", f"auto:{message.id}:{target.id}", source_message_id=message.id, causal_chain_id=candidate.causal_chain_id)
                changed.add(chat.id)
        for chat_id in changed:
            self.publish_event(chat_id, {"type": "changed"})

    def patch_chat_settings(self, owner_id, chat_id, values):
        with self.transaction() as db:
            chat, _ = self.membership(db, owner_id, chat_id)
            row = self.settings_row(db, owner_id, chat)
            if chat.kind == "group" and values.get("sidebar_tab") == "activity":
                fail(400, "Activity is available only in your private chat")
            for key, value in values.items():
                setattr(row, key, value)
            db.flush()
            return self.settings_json(row)

    def owner_settings(self, owner_id, prompt=None):
        with self.transaction() as db:
            row = db.get(Owner, owner_id)
            if prompt is not None:
                row.default_reply_prompt = prompt
            return {"default_reply_prompt": row.default_reply_prompt}

    async def cancel_request(self, owner_id, request_id):
        return await self.db_call("_cancel_intent", owner_id, request_id)

    def _cancel_intent(self, owner_id, request_id):
        with self.transaction() as db:
            result = self._cancel_request_db(db, owner_id, request_id)
            target_owner = db.get(Actor, result["target_twin_id"]).owner_id
        self.publish_event(result["chat_id"], {"type": "changed"})
        notifications.emit_personal(target_owner, "credentials")
        return {**result, "accepted": "business_persisted"}

    def _cancel_request_db(self, db, owner_id, request_id):
        request = db.get(ReplyRequest, request_id)
        if not request:
            fail(404, "Reply request not found")
        self.membership(db, owner_id, request.chat_id, True)
        db.scalar(select(ReplyLane).where(ReplyLane.chat_id == request.chat_id, ReplyLane.twin_id == request.target_twin_id).with_for_update())
        db.refresh(request, with_for_update=True)
        target = db.get(Actor, request.target_twin_id)
        if owner_id not in {request.requester_id, target.owner_id}:
            fail(403, "Only the requester or target Duet owner may access cancellation")
        if request.status == "queued":
            if owner_id not in {request.requester_id, target.owner_id}:
                fail(403, "Only the requester or target Duet's owner can cancel this queued request")
            request.status = "cancelled"
            request.cancel_requested = True
            if request.source == "memory_dream":
                from .memory_dream import finish
                finish(self, db, request, "cancelled")
            command = db.get(DispatchCommand, request.id)
            command.kind, command.state = "cancel", "pending"
            result = self.request_json(db, request, owner_id)
        elif request.status == "running":
            if owner_id != target.owner_id:
                fail(403, "Only the target Duet's owner can stop a running reply")
            request.cancel_requested = True
            self.withdraw_outputs(db, request)
            command = db.get(DispatchCommand, request.id)
            command.kind, command.state = "cancel", "pending"
            result = self.request_json(db, request, owner_id)
        elif request.status == "cancelled":
            return self.request_json(db, request, owner_id)
        else:
            fail(409, "This request is already finished")
        for credential in db.scalars(select(CredentialRequest).where(
                CredentialRequest.origin_request_id == request.id)):
            if credential.status == "pending":
                credential.status = "cancelled"
            elif credential.continuation_request_id:
                continuation = db.get(ReplyRequest, credential.continuation_request_id)
                if continuation and continuation.status in {"queued", "running"}:
                    continuation.cancel_requested = True
                    if continuation.status == "queued":
                        continuation.status = "cancelled"
                    else:
                        self.withdraw_outputs(db, continuation)
                    command = db.get(DispatchCommand, continuation.id)
                    if command:
                        command.kind, command.state = "cancel", "pending"
        from .followups import cancel
        cancel(db, request)
        from .questions import cancel_origin
        cancel_origin(db, request.id)
        if request.status == "cancelled":
            from .scheduled_tasks import finish as scheduled_finish
            scheduled_finish(db, request, "cancelled")
        return result

    def retry_request(self, owner_id, request_id, key):
        with self.transaction() as db:
            result = self._retry_request_db(db, owner_id, request_id, key)
        self.publish_event(result["chat_id"], {"type": "changed"})
        return result

    def _retry_request_db(self, db, owner_id, request_id, key, *, automatic_reservation_id=None, causal_chain_id=None, automatic=False):
        old = db.get(ReplyRequest, request_id)
        if not old:
            fail(404, "Reply request not found")
        chat, member = self.membership(db, owner_id, old.chat_id, True)
        chain = self.request_chain(db, old)
        db.scalar(select(ReplyLane).where(ReplyLane.chat_id == old.chat_id, ReplyLane.twin_id == old.target_twin_id).with_for_update())
        db.refresh(old, with_for_update=True)
        target = db.get(Actor, old.target_twin_id)
        if (not automatic and owner_id != target.owner_id) or (automatic and owner_id not in {old.requester_id, target.owner_id}):
            fail(403, "Only the target Duet's owner can manually retry")
        self._check_private_request(db, old)
        if old.source == "memory_dream":
            fail(409, "Memory dreams use fresh scheduled attempts; uncertain publication requires reconciliation")
        if old.status != "failed":
            fail(409, "Only failed requests can be retried")
        prior = db.scalar(select(ReplyRequest).where(ReplyRequest.requester_id == owner_id, ReplyRequest.client_request_key == key))
        if prior:
            if prior.intent_key != old.intent_key:
                fail(409, "Idempotency key was used for another request")
            return self.request_json(db, prior, owner_id)
        # Each failed generation can have only one user retry, even with different click keys.
        generation = old.generation + 1
        chain = self.request_chain(db, old)
        # Retry is a new generation of this delivery, retaining its private
        # authorization checks and terminal-only/silent output policy.
        source = old.source if old.source in {"question_result", "followup_result", "scheduled_task", "system_review"} else "tool_retry" if automatic else "retry"
        request = self.accept_request(db, chat, old.target_twin_id, owner_id, source, key, source_message_id=old.source_message_id, raw=old.raw_owner_input, prompt=old.frozen_prompt, reply_to=old.reply_to_message_id, generation=generation, intent_key=old.intent_key, input_body=old.input_body, causal_chain_id=causal_chain_id or chain.id, automatic_reservation_id=automatic_reservation_id or old.automatic_reservation_id, source_owner_id=old.source_owner_id, input_attachment_ids=old.input_attachment_ids, command_fingerprint=old.command_fingerprint)
        from .scheduled_tasks import snapshot as scheduled_snapshot
        if automatic or scheduled_snapshot(old):
            from copy import deepcopy
            request.execution_config = deepcopy(old.execution_config) if old.execution_config is not None else None if scheduled_snapshot(old) else {
                "version": 0, "skills": [], "credentials": [], "execution_enabled": False}
        if source == "followup_result" and not db.scalar(select(ContextManifest.id).where(ContextManifest.request_id == request.id).limit(1)):
            # Keep the reply's cross-chat provenance for checks during a
            # retried run, as well as the initial context authorization.
            sources = self.manifest_provenance(db, old.id)
            db.add(ContextManifest(request_id=request.id, owner_id=owner_id,
                grant_version=member.grant_version, high_water_seq=chat.next_message_seq - 1,
                sources=sources, source_digest=digest(sources)))
        result = self.request_json(db, request, owner_id)
        return result

    def request_payload(self, request):
        return {k: getattr(request, k) for k in ("id", "chat_id", "source_message_id", "target_twin_id", "requester_id", "source", "status", "session_id", "run_id", "raw_owner_input", "frozen_prompt", "input_body", "input_attachment_ids", "cancel_requested", "causal_chain_id", "automatic_reservation_id", "source_owner_id", "execution_config", "private_generation")}

    def _check_private_request(self, db, request):
        from .twin_reset import check_request
        return check_request(db, request)

    def _authorize_generation(self, db, context):
        request_id = context.get("request_id") or context.get("origin_request_id")
        if request_id is None and context.get("private_generation") is None:
            return True  # Explicit authenticated product actions have no SDK request.
        request = db.get(ReplyRequest, request_id) if request_id else None
        if not request:
            fail(409, "Runtime request no longer exists")
        chat = self._check_private_request(db, request)
        if request.cancel_requested or request.status in {"failed", "cancelled"}:
            fail(409, "Runtime request is no longer authorized")
        if request.source == "question_result":
            from .questions import continuation
            continuation(self, db, request)
        if context.get("question_request_id"):
            from .questions import continuation
            answered = db.get(ReplyRequest, context["question_request_id"])
            if not answered or answered.cancel_requested or answered.status in {"cancelled", "failed"}:
                fail(409, "Owner-answer continuation is no longer authorized")
            continuation(self, db, answered)
        twin = db.get(Actor, request.target_twin_id)
        member = self.membership(db, twin.owner_id, chat.id)[1]
        if not self.provenance_allowed(db, member, self.manifest_provenance(db, request.id)):
            fail(409, "Runtime sources are no longer authorized")
        if chat.kind == "private" and context.get("private_generation", 0) != chat.reset_generation:
            fail(409, "Private runtime generation is no longer authorized")
        from .scheduled_tasks import snapshot
        task = snapshot(request)
        if task:
            twin = db.get(Actor, request.target_twin_id)
            if chat.kind != "private" or chat.private_owner_id != context.get("owner_id") or twin.owner_id != chat.private_owner_id:
                fail(403, "Scheduled execution requires the owner's private Duet")
            member = self.membership(db, twin.owner_id, chat.id)[1]
            if not self.provenance_allowed(db, member, self.manifest_provenance(db, request.id)):
                fail(409, "Scheduled runtime sources are no longer authorized")
        return True

    def authorize_runtime_generation(self, context):
        with self.transaction() as db:
            return self._authorize_generation(db, context)

    def reset_status(self, owner_id):
        from .twin_reset import status
        return status(self, owner_id)

    def personal_reset(self, owner_id, key, expected_generation):
        from .twin_reset import reset
        return reset(self, owner_id, key, expected_generation)

    def get_request(self, request_id):
        with self.transaction() as db:
            request = db.get(ReplyRequest, request_id)
            if not request:
                fail(404, "Reply request not found")
            db.scalar(select(Chat).where(Chat.id == request.chat_id).with_for_update())
            db.refresh(request)
            self.request_chain(db, request)
            return self.request_payload(request)

    def get_runtime_context(self, request_id, include_completed=False):
        with self.transaction() as db:
            request = db.get(ReplyRequest, request_id)
            if not request or (not include_completed and (request.status not in {"queued", "running"} or request.cancel_requested)):
                fail(409, "Reply request is no longer executable")
            db.scalar(select(Chat).where(Chat.id == request.chat_id).with_for_update())
            db.refresh(request)
            self._check_private_request(db, request)
            self.request_chain(db, request)
            if request.source == "memory_dream":
                from .memory_dream import context
                return context(self, db, request)
            twin = db.get(Actor, request.target_twin_id)
            chat, member = self.membership(db, twin.owner_id, request.chat_id)
            from .conversation_context import latest_summary, read_context, scope_for
            _, _, summary_scope = scope_for(self, db, {"chat_id": chat.id, "owner_id": twin.owner_id,
                "high_water_seq": chat.next_message_seq - 1, "exclude_request_id": request.id})
            checked = {}
            covering = latest_summary(self, db, chat, member, summary_scope)
            if covering:
                checked[("covering_summary", member.chat_id)] = covering.id
            conversation, sources, rows = read_context(self, db, chat, member, summary_scope, cache=checked)
            if request.source_message_id:
                self.visible_message(db, member, request.source_message_id, cache=checked)
            previous_manifests = list(db.scalars(select(ContextManifest).where(ContextManifest.request_id == request.id)))
            if not all(self.provenance_allowed(db, member, manifest.sources, cache=checked) for manifest in previous_manifests):
                fail(409, "Previously supplied runtime context is no longer authorized; explicit retry with a fresh request is required")
            applicable = conversation["summary"]
            tracking = []
            for topic, origin, _, projection in self._tracking_scope(db, chat, member):
                tracking.append(projection)
                topic_sources = self._tracking_provenance(topic)
                sources.extend(topic_sources if origin.id == chat.id else [
                    {"kind": "chat_context", "chat_id": origin.id, "sources": topic_sources}])
            from .questions import pending_questions
            pending = pending_questions(self, db, request, twin.owner_id) if chat.kind == "private" else []
            sources.extend({"kind": "message", "id": item["message_id"], "context_role": "pending_question"} for item in pending)
            manifest = ContextManifest(request_id=request.id, owner_id=twin.owner_id, grant_version=member.grant_version, high_water_seq=chat.next_message_seq - 1, sources=sources, source_digest=digest(sources))
            db.add(manifest)
            request.context_grant_version = member.grant_version
            db.flush()
            summary_scope["context_manifest_id"] = manifest.id
            from .scheduled_tasks import snapshot as scheduled_snapshot
            scheduled_payload = scheduled_snapshot(request)
            followup_payload = None
            if request.source == "followup_result":
                from .followups import context as followup_context
                followup_payload = followup_context(self, db, request, twin.owner_id)
            source_message = self.visible_message(db, member, request.source_message_id, cache=checked) if request.source_message_id else None
            file_ids = request.input_attachment_ids if request.raw_owner_input is not None else source_message.attachment_ids if source_message else []
            files = self.attachment_references(db, file_ids, member, private_request=request if request.raw_owner_input is not None else None)
            from .onboarding import context_for_request
            return {"pending_questions": pending, "onboarding": context_for_request(db, request, chat), "scheduled_task": scheduled_payload, "system_review": scheduled_payload.get("system_review") if scheduled_payload else None, "private_generation": request.private_generation, "followup_result": followup_payload, "input_files": files, "workspace_path": str((settings.workspace_root / chat.id).resolve()), "tenant_id": settings.tenant_id, "owner_id": twin.owner_id, "target_twin_name": twin.name, "chat": {"id": chat.id, "kind": chat.kind, "title": self.chat_title(db, chat, twin.owner_id), "audience": [{"owner_id": m.owner_id, "name": db.get(Actor, m.owner_id).name} for m in self.members(db, chat.id)]}, "messages": conversation["messages"], "_summary_scope": summary_scope, "context_incomplete": conversation["context_incomplete"], "source_message": self.message_json(db, source_message, member, cache=checked) if source_message else None, "tracking": [t for t in tracking if t], "summary": applicable, "prompt": request.frozen_prompt or "", "input_body": scheduled_payload["instructions"] if scheduled_payload else request.input_body, "request_id": request.id, "request_source": request.source, "origin_request_id": request.id, "causal_chain_id": request.causal_chain_id, "source_owner_id": request.source_owner_id, "requester_id": request.requester_id, "target_twin_id": request.target_twin_id, "chat_id": chat.id, "raw_owner_input": request.raw_owner_input, "source_message_id": request.source_message_id, "context_manifest_id": manifest.id, "history_lower_seq": member.lower_seq, "high_water_seq": chat.next_message_seq - 1}

    def get_conversation_context(self, scope):
        from .conversation_context import get_context
        return get_context(self, scope)

    def prepare_conversation_summary(self, scope):
        from .conversation_context import prepare
        return prepare(self, scope)

    def save_conversation_summary(self, scope, body):
        from .conversation_context import save
        return save(self, scope, body)

    def bind_runtime_run(self, request_id, session_id, run_id):
        with self.transaction() as db:
            request = db.get(ReplyRequest, request_id)
            if not request:
                fail(404, "Reply request not found")
            self.membership(db, db.get(Actor, request.target_twin_id).owner_id, request.chat_id, True)
            db.scalar(select(ReplyLane).where(ReplyLane.chat_id == request.chat_id, ReplyLane.twin_id == request.target_twin_id).with_for_update())
            db.refresh(request, with_for_update=True)
            self._check_private_request(db, request)
            if request.session_id and request.session_id != session_id:
                fail(409, "Runtime session conflicts with accepted request")
            if request.run_id and request.run_id != run_id:
                fail(409, "Runtime run conflicts with accepted request")
            if request.cancel_requested or request.status == "cancelled":
                fail(409, "Request was cancelled before runtime binding")
            request.session_id, request.run_id = session_id, run_id
            request.status = "running"
            if request.source == "memory_dream":
                state = db.get(MemoryDreamState, request.requester_id)
                if state and state.queued_request_id == request.id:
                    state.last_status = "running"
            lane = db.get(ReplyLane, (request.chat_id, request.target_twin_id))
            if request.source not in {"memory_dream", "question_result"}:
                lane.session_id = session_id
        self.publish_event(request.chat_id, {"type": "changed", "request_id": request_id})

    def publish_runtime_output(self, request_id, body, session_id, run_id, event_id=None, event_seq=None):
        if not body or not body.strip():
            return None
        with self.transaction() as db:
            request = db.get(ReplyRequest, request_id)
            if not request:
                fail(404, "Reply request not found")
            if request.source == "memory_dream":
                return None  # Internal maintenance never projects into a conversation.
            chat = db.scalar(select(Chat).where(Chat.id == request.chat_id).with_for_update())
            self.request_chain(db, request)
            db.scalar(select(ReplyLane).where(ReplyLane.chat_id == request.chat_id, ReplyLane.twin_id == request.target_twin_id).with_for_update())
            db.refresh(request, with_for_update=True)
            if request.cancel_requested or request.status in {"cancelled", "failed"}:
                return None
            self._check_private_request(db, request)
            if request.session_id and request.session_id != session_id:
                fail(409, "Runtime session does not match the accepted request")
            if request.run_id and request.run_id != run_id:
                fail(409, "Runtime run does not match the accepted request")
            twin = db.get(Actor, request.target_twin_id)
            _, member = self.membership(db, twin.owner_id, chat.id)
            if request.context_grant_version is not None and request.context_grant_version != member.grant_version:
                fail(409, "History grant changed during generation; output withheld")
            request.session_id, request.run_id = session_id, run_id
            lane = db.get(ReplyLane, (chat.id, twin.id))
            lane.session_id = session_id
            is_reply_for_me = request.raw_owner_input is not None
            projection = db.get(ReplyProjection, request.id) if is_reply_for_me else None
            if is_reply_for_me:
                if event_seq is not None and (isinstance(event_seq, bool) or not isinstance(event_seq, int) or event_seq < 0):
                    fail(400, "SDK semantic event sequence must be a nonnegative integer")
                if projection:
                    if projection.session_id != session_id or projection.run_id != run_id:
                        fail(409, "Reply-for-me projection belongs to another SDK run")
                    if request.status == "completed":
                        return projection.message_id
                    if event_id and event_id == projection.last_event_id:
                        return projection.message_id
                    if event_seq is None:
                        fail(409, "Ordered SDK semantic event sequence is required to update reply-for-me")
                    if projection.last_event_seq is not None and event_seq <= projection.last_event_seq:
                        return projection.message_id
                elif request.status == "completed":
                    return None  # Never create a new bubble from a late replay.
                elif event_seq is None:
                    fail(409, "Ordered SDK semantic event sequence is required to project reply-for-me")
            output_key = f"request:{request.id}:reply_for_me" if is_reply_for_me else (f"{session_id}:{run_id}:{event_id}" if event_id else f"request:{request.id}:final")
            prior = db.scalar(select(Message).where(Message.sdk_output_key == output_key))
            if prior and not is_reply_for_me:
                return prior.id
            from .scheduled_tasks import notice as scheduled_notice
            if not scheduled_notice(db, request, body):
                return None
            manifests = list(db.scalars(select(ContextManifest).where(ContextManifest.request_id == request.id)))
            source_ids = list({s["id"] for manifest in manifests for s in manifest.sources if s.get("kind") == "message"}) if manifests else ([request.source_message_id] if request.source_message_id else [])
            provenance = self.manifest_provenance(db, request.id)
            if not self.provenance_allowed(db, member, provenance):
                fail(409, "Context sources changed during generation; output withheld")
            if projection:
                msg = db.get(Message, projection.message_id)
                if not msg or msg.state != "published":
                    return None
                msg.body = body
                msg.source_provenance = provenance
                msg.source_message_ids = source_ids
                msg.source_floor_seq = min([db.get(Message, sid).seq for sid in source_ids], default=msg.seq)
                projection.last_event_id, projection.last_event_seq, projection.updated_at = event_id, event_seq, now()
            else:
                msg = self.append_message(db, chat, consume_followup=False, source_provenance=provenance, source_message_ids=source_ids, source_floor_seq=min([db.get(Message, sid).seq for sid in source_ids], default=chat.next_message_seq), author_id=twin.id, kind="reply_for_me" if is_reply_for_me else "twin", body=body, raw_owner_input=request.raw_owner_input, reply_to_message_id=request.reply_to_message_id or (request.source_message_id if chat.kind != "private" else None), request_id=request.id, sdk_output_key=output_key, causal_chain_id=self.request_chain(db, request).id)
                if is_reply_for_me:
                    db.add(ReplyProjection(request_id=request.id, message_id=msg.id, session_id=session_id, run_id=run_id, last_event_id=event_id, last_event_seq=event_seq))
            if is_reply_for_me:
                msg.attachment_ids = list(request.input_attachment_ids or [])
                for attachment_id in msg.attachment_ids:
                    attachment = db.get(ChatAttachment, attachment_id)
                    if attachment:
                        attachment.message_id = msg.id
            result = msg.id
        self.publish_event(chat.id, {"type": "changed", "request_id": request_id})
        return result

    def withdraw_outputs(self, db, request):
        ids = []
        for message in db.scalars(select(Message).where(Message.request_id == request.id, Message.chat_id == request.chat_id, or_(Message.sdk_output_key.is_(None), ~Message.sdk_output_key.like("tool:%")))):
            message.state = "withdrawn"
            ids.append(message.id)

    def finish_request(self, request_id, status, error=None, session_id=None, run_id=None, route_message_id=None, dream_result=None):
        with self.transaction() as db:
            request = db.get(ReplyRequest, request_id)
            if not request:
                return
            if status == "completed" and route_message_id:
                from .followups import lock_reply_scopes
                final_message = db.get(Message, route_message_id)
                if final_message:
                    lock_reply_scopes(self, db, request.chat_id, final_message.reply_to_message_id)
            db.scalar(select(Chat).where(Chat.id == request.chat_id).with_for_update())
            db.scalar(select(ReplyLane).where(ReplyLane.chat_id == request.chat_id, ReplyLane.twin_id == request.target_twin_id).with_for_update())
            db.refresh(request, with_for_update=True)
            if request.status in {"completed", "failed", "cancelled"}:
                return  # A late observer cannot rewrite a committed terminal fact.
            if request.cancel_requested:
                status = "cancelled"
                self.withdraw_outputs(db, request)
            request.status, request.error = status, error
            if status == "completed" and route_message_id:
                message = db.get(Message, route_message_id)
                if message and message.request_id == request.id and message.chat_id == request.chat_id and message.state == "published":
                    from .followups import consume_reply
                    consume_reply(self, db, message)
                    self.route_committed_message(db, db.get(Chat, request.chat_id), message)
            if session_id:
                request.session_id = session_id
            if run_id:
                request.run_id = run_id
            if request.source == "memory_dream":
                from .memory_dream import finish
                finish(self, db, request, status, dream_result, error)
            elif status == "completed":
                from .memory_dream import experience_completed
                experience_completed(self, db, request)
            from .scheduled_tasks import finish as scheduled_finish
            scheduled_finish(db, request, status)
            lane = db.get(ReplyLane, (request.chat_id, request.target_twin_id))
            if lane and lane.active_request_id == request.id:
                lane.active_request_id = None
            command = db.get(DispatchCommand, request.id)
            if command:
                command.state, command.updated_at = "delivered", now()
            chat_id = request.chat_id
        self.publish_event(chat_id, {"type": "changed", "request_id": request_id})

    def complete_request(self, request_id, result=None, **kwargs):
        result = {**(result or {}), **kwargs}
        final_message_id = None
        if result.get("body"):
            final_message_id = self.publish_runtime_output(request_id, result["body"], result["session_id"], result["run_id"], result.get("event_id"), result.get("event_seq"))
        self.finish_request(request_id, "completed", session_id=result.get("session_id"), run_id=result.get("run_id"), route_message_id=final_message_id, dream_result=result.get("memory_dream"))

    def fail_request(self, request_id, error):
        self.finish_request(request_id, "failed", str(error))

    def claim_dispatches(self, scopes=None):
        """Select FIFO heads with owner admission before LIMIT; SDK alone claims execution."""
        from sqlalchemy.orm import aliased
        earlier = aliased(ReplyRequest)
        counts, result = {}, []
        with self.transaction() as db:
            if scopes is not None:
                pending_scopes = set(db.scalars(select(ReplyRequest.chat_id).where(ReplyRequest.chat_id.in_(scopes), ReplyRequest.status.in_(["queued", "running"])).distinct().limit(1000)))
                self.ready_scopes.difference_update(set(scopes) - pending_scopes)
            for request_id in tuple(self.tasks):
                owner = db.scalar(select(Actor.owner_id).join(ReplyRequest, ReplyRequest.target_twin_id == Actor.id).where(ReplyRequest.id == request_id))
                if owner:
                    counts[owner] = counts.get(owner, 0) + 1
            for _ in range(settings.max_running):
                capacity = settings.max_running - len(self.tasks) - len(result)
                if capacity <= 0:
                    break
                exhausted = [owner for owner, count in counts.items() if count >= settings.max_per_owner]
                query = select(ReplyRequest, Actor.owner_id).join(Actor, Actor.id == ReplyRequest.target_twin_id).where(
                    ReplyRequest.status.in_(["queued", "running"]),
                    ~select(earlier.id).where(earlier.chat_id == ReplyRequest.chat_id,
                        earlier.target_twin_id == ReplyRequest.target_twin_id,
                        earlier.status.in_(["queued", "running"]),
                        earlier.accepted_seq < ReplyRequest.accepted_seq).exists(),
                )
                if scopes is not None:
                    query = query.where(ReplyRequest.chat_id.in_(scopes))
                if exhausted:
                    query = query.where(Actor.owner_id.not_in(exhausted))
                omitted = set(self.tasks) | set(result) | {key for key, until in tuple(self.deferred.items()) if until > (self.loop.time() if self.loop else 0)}
                if omitted:
                    query = query.where(ReplyRequest.id.not_in(omitted))
                rows = list(db.execute(query.order_by(ReplyRequest.accepted_at, ReplyRequest.accepted_seq).limit(100)))
                if not rows:
                    break
                before = len(result)
                for request, owner in rows:
                    if counts.get(owner, 0) < settings.max_per_owner and len(result) < capacity + before:
                        counts[owner] = counts.get(owner, 0) + 1
                        result.append(request.id)
                        self.ready_scopes.add(request.chat_id)
                if len(result) == before:
                    break
        return result

    def pending_stops(self, scopes=None):
        with self.transaction() as db:
            query = select(ReplyRequest).join(DispatchCommand, DispatchCommand.request_id == ReplyRequest.id).where(
                ReplyRequest.cancel_requested.is_(True), DispatchCommand.kind == "cancel", DispatchCommand.state == "pending")
            if scopes is not None:
                query = query.where(ReplyRequest.chat_id.in_(scopes))
            return [self.request_payload(row) for row in db.scalars(query.order_by(DispatchCommand.updated_at).limit(100))]

    def acknowledge_stop(self, request_id):
        with self.transaction() as db:
            db.execute(update(DispatchCommand).where(DispatchCommand.request_id == request_id,
                DispatchCommand.kind == "cancel", DispatchCommand.state == "pending").values(state="stop_signalled", updated_at=now()))

    def next_deadlines(self, scopes=None):
        with self.transaction() as db:
            query = select(TriggerCandidate.chat_id, func.min(TriggerCandidate.due_at)).where(TriggerCandidate.state == "waiting", TriggerCandidate.due_at > now())
            if scopes is not None:
                query = query.where(TriggerCandidate.chat_id.in_(scopes))
            result = dict(db.execute(query.group_by(TriggerCandidate.chat_id).limit(100)).all())
            followups = select(TwinFollowup.chat_id, func.min(TwinFollowup.due_at)).where(
                TwinFollowup.status == "waiting", TwinFollowup.due_at > now())
            if scopes is not None:
                followups = followups.where(or_(TwinFollowup.chat_id.in_(scopes), TwinFollowup.target_chat_id.in_(scopes)))
            for chat_id, deadline in db.execute(followups.group_by(TwinFollowup.chat_id).limit(100)):
                result[chat_id] = min(result.get(chat_id, deadline), deadline)
            dreams = select(MemoryDreamState.chat_id, MemoryDreamState.due_at).where(
                MemoryDreamState.due_at > now(), MemoryDreamState.queued_request_id.is_(None))
            if scopes is not None:
                dreams = dreams.where(MemoryDreamState.chat_id.in_(scopes))
            for chat_id, deadline in db.execute(dreams.limit(100)):
                result[chat_id] = min(result.get(chat_id, deadline), deadline)
            return result

    async def request_memory_dream(self, owner_id):
        from .personal import own_memory_store
        from .memory_dream import manual_request
        def existing():
            with self.transaction() as db:
                state = db.get(MemoryDreamState, owner_id)
                if state and state.last_status == "recovery_required":
                    fail(409, "Memory publication requires reconciliation before another Dream")
                return state.queued_request_id if state else None
        queued = await self.db_call(existing)
        if queued:
            return {"status": "already_queued", "request_id": queued}
        store = await self.db_call(own_memory_store, owner_id)
        if store is None:
            fail(503, "Memory is disabled")
        page = await store.list_entries(limit=100)
        entries = [entry.to_dict() for entry in page["items"]]
        return await self.db_call(manual_request, self, owner_id, entries, store.generation)

    def process_memory_dreams(self, scopes=None):
        from .memory_dream import process
        return process(self, scopes)

    def mark_memory_dream_recovery(self, request_id, error):
        from .memory_dream import recovery
        return recovery(self, request_id, error)

    def confirm_memory_dream_publication(self, request_id, version):
        from .memory_dream import confirm
        return confirm(self, request_id, version)

    def get_memory_edit_source(self, owner_id, session_id, event_id):
        from .personal import get_memory_edit_source
        return get_memory_edit_source(owner_id, session_id, event_id)

    def process_followups(self, scopes=None):
        from .followups import process
        return process(self, scopes)

    def initialize_scheduled_tasks(self):
        from .scheduled_tasks import ensure_system, system_id
        with self.transaction() as db:
            # Provisioning/startup only, never part of the short due-index loop.
            for owner_id in db.scalars(select(Owner.id).order_by(Owner.id)):
                if not db.get(ScheduledTask, system_id(owner_id)):
                    ensure_system(self, db, owner_id)

    async def scheduled_tasks_scheduler(self):
        import logging
        while not self.stopping:
            try:
                delay = await self.scheduled_due_index.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.getLogger(__name__).exception("Scheduled task admission failed")
                delay = 30
            await asyncio.sleep(delay)

    def pending_notices(self):
        with self.transaction() as db:
            return [{"id": row.id, "chat_id": row.chat_id, "revision": row.revision, "kind": row.kind} for row in db.scalars(select(ChangeOutbox).where(ChangeOutbox.published.is_(False)).order_by(ChangeOutbox.created_at).limit(100))]

    def acknowledge_notices(self, ids):
        with self.transaction() as db:
            db.execute(update(ChangeOutbox).where(ChangeOutbox.id.in_(ids)).values(published=True))

    def scope_revision(self, owner_id, chat_id):
        with self.transaction() as db:
            chat, _ = self.membership(db, owner_id, chat_id)
            return chat.revision

    async def execute_request(self, request_id):
        payload = None
        try:
            payload = await self.db_call("get_request", request_id)
            if payload["cancel_requested"]:
                if not self.runtime:
                    raise RuntimeError("Runtime unavailable while reconciling saved stop intent")
                stop_result = await resolve(self.runtime.cancel(payload))
                if isinstance(stop_result, dict) and stop_result.get("terminal"):
                    await self.db_call("finish_request", request_id, "cancelled")
                else:
                    self.deferred[request_id] = (self.loop.time() if self.loop else 0) + settings.compensation_seconds
                return
            if self.runtime and getattr(self.runtime, "startup_error", None):
                raise RuntimeError(f"Runtime storage is unavailable: {self.runtime.startup_error}")
            if not self.runtime or not self.model_configured:
                raise RuntimeError("Model provider is not configured. Set the supported provider API key and restart; no generated reply was produced.")
            context = await self.db_call("get_runtime_context", request_id)
            self.publish_event(payload["chat_id"], {"type": "status", "request_id": request_id, "text": "running"})
            result = await resolve(self.runtime.execute(payload, context))
            if not isinstance(result, dict) or not result.get("session_id") or not result.get("run_id"):
                raise RuntimeError("Runtime returned no durable session/run identity")
            await self.db_call("complete_request", request_id, result)
        except asyncio.CancelledError:
            # Shutdown leaves durable running intent intact for SDK reconciliation on restart.
            raise
        except Exception as exc:
            from .runtime_bridge import RuntimeBusy
            from agent_runtime.storage.coordination import ExecutionLeaseError
            if isinstance(exc, (RuntimeBusy, ExecutionLeaseError)):
                # A live SDK lease is not a failed/finished business request. Preserve
                # the exact active lane, including concurrent stop/completion changes.
                if self.loop:
                    self.deferred[request_id] = self.loop.time() + settings.compensation_seconds
            else:
                current = await self.db_call("get_request", request_id)
                if current.get("cancel_requested"):
                    stop_result = await resolve(self.runtime.cancel(current)) if self.runtime else {}
                    if isinstance(stop_result, dict) and stop_result.get("terminal"):
                        await self.db_call("finish_request", request_id, "cancelled")
                        return
                    await self.db_call("_save_stop_error", request_id, str(exc))
                    self.deferred[request_id] = (self.loop.time() if self.loop else 0) + settings.compensation_seconds
                else:
                    await self.db_call("fail_request", request_id, str(exc))
        finally:
            self.tasks.pop(request_id, None)
            if self.wakeup and payload:
                # A terminal outbox hint may arrive before this coroutine releases
                # its local slot. Reconsider the exact scope after releasing it.
                self.work_scopes.add(payload["chat_id"])
                self.wakeup.set()

    def _save_stop_error(self, request_id, error):
        with self.transaction() as db:
            db.get(ReplyRequest, request_id).error = f"Stop awaiting runtime reconciliation: {error}"

    def _notify(self, event):
        if event.get("kind") == "scheduled_tasks" and self.scheduled_due_index:
            self.scheduled_due_index.dirty = True
        if self.runtime and hasattr(self.runtime, "notify"):
            self.runtime.notify(event)
        if not self.wakeup or event.get("worker_status") or event.get("relayed"):
            return
        if event.get("session_id"):
            self.runtime_scopes.add(event["session_id"])
        chat_id = event.get("chat_id")
        if chat_id and event.get("kind") == "work":
            self.work_scopes.add(chat_id)
        if chat_id or event.get("session_id"):
            self.wakeup.set()

    async def scheduler(self):
        import logging
        compensated_at = -float("inf")
        while not self.stopping:
            self.wakeup.clear()
            moment = self.loop.time()
            compensate = moment - compensated_at >= settings.compensation_seconds
            scopes = set(self.work_scopes)
            if scopes:
                scopes.update(self.ready_scopes)
            self.work_scopes.clear()
            scopes.update(chat_id for chat_id, deadline in self.deadlines.items() if aware(deadline) <= now())
            for chat_id in scopes:
                self.deadlines.pop(chat_id, None)
            try:
                runtime_scopes = tuple(self.runtime_scopes)
                self.runtime_scopes.clear()
                if self.runtime and hasattr(self.runtime, "drain_child_results"):
                    for parent_session_id in runtime_scopes:
                        await self.runtime.drain_child_results(parent_session_id)
                if compensate:
                    runtime_status = self.runtime.configuration_status if self.runtime else {}
                    await notifications.report_worker(self.model_configured, **{
                        key: runtime_status.get(key) for key in
                        ("provider", "model", "api", "memory")})
                    compensated_at = moment
                    if self.runtime and hasattr(self.runtime, "recover_pending"):
                        await self.runtime.recover_pending()
                        await self.runtime.drain_child_results()
                if compensate or scopes:
                    selected = None if compensate else scopes
                    self.ready_scopes.update(scopes)
                    if self.runtime and hasattr(self.runtime, "signal_stop"):
                        for request in await self.db_call("pending_stops", selected):
                            await self.runtime.signal_stop(request)
                            await self.db_call("acknowledge_stop", request["id"])
                    await self.refresh_presence(selected)
                    await self.db_call("process_candidates", selected)
                    await self.db_call("process_pending_child_results", selected)
                    await self.db_call("process_followups", selected)
                    await self.db_call("process_memory_dreams", selected)
                    if self.runtime and getattr(self.runtime, "store", None):
                        await self.reconcile_approvals(selected)
                    for request_id in await self.db_call("claim_dispatches", selected):
                        if request_id not in self.tasks:
                            self.tasks[request_id] = asyncio.create_task(self.execute_request(request_id))
                    for decision_id in await self.db_call("pending_routes", selected):
                        if len(self.routing_jobs) >= settings.routing_concurrency:
                            break
                        if decision_id not in self.routing_jobs:
                            task = asyncio.create_task(self.route_pending({decision_id}))
                            self.routing_jobs[decision_id] = task
                            def settled(task, key=decision_id):
                                self.routing_jobs.pop(key, None)
                                if not task.cancelled():
                                    error = task.exception()
                                    if error:
                                        logging.getLogger(__name__).error("Routing failed: %s", error)
                                    elif task.result():
                                        self.work_scopes.update(task.result())
                                        self.wakeup.set()
                            task.add_done_callback(settled)
                    self.deadlines.update(await self.db_call("next_deadlines", selected))
                notices = await self.db_call("pending_notices")
                delivered = []
                for notice in notices:
                    try:
                        if notifications.redis:
                            await notifications.redis.publish(notifications.channel, json.dumps({"chat_id": notice["chat_id"], "kind": notice["kind"], "relayed": True}))
                            delivered.append(notice["id"])
                    except Exception:
                        break
                if delivered:
                    await self.db_call("acknowledge_notices", delivered)
                if len(notices) == 100 and delivered:
                    self.wakeup.set()
                timeout = max(.1, settings.compensation_seconds - (self.loop.time() - compensated_at))
                deadlines = [(aware(value) - now()).total_seconds() for value in self.deadlines.values() if aware(value) > now()]
                if deadlines:
                    timeout = min(timeout, max(.1, min(deadlines)))
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.getLogger(__name__).exception("Execution scheduler failed")
                timeout = settings.compensation_seconds
            try:
                await asyncio.wait_for(self.wakeup.wait(), timeout=timeout)
            except TimeoutError:
                pass

    async def start(self, *, worker=True):
        self.loop = asyncio.get_running_loop()
        self.stopping = False
        self.worker_mode = worker
        if notifications.loop is None:
            await notifications.start()
        if worker:
            self.wakeup = asyncio.Event()
            notifications.listeners.add(self._notify)
            self.scheduler_task = asyncio.create_task(self.scheduler())
            from .scheduled_tasks import DueIndex
            self.scheduled_due_index = DueIndex(self)
            self.scheduled_task_loop = asyncio.create_task(self.scheduled_tasks_scheduler())

    async def close(self):
        self.stopping = True
        notifications.listeners.discard(self._notify)
        tasks = [t for t in [self.scheduler_task, self.scheduled_task_loop, *self.routing_jobs.values(), *self.tasks.values()] if t]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
        self.routing_jobs.clear()
        await notifications.close()

    def publish_event(self, chat_id, event):
        # Never transport user/model contents or private request identifiers.
        if event.get("type") == "token":
            return
        notifications.emit(chat_id)


    def is_retired_private_run(self, parent_session_id, parent_run_id, owner_id):
        with self.transaction() as db:
            request = db.scalar(select(ReplyRequest).where(ReplyRequest.session_id == parent_session_id,
                ReplyRequest.run_id == parent_run_id).order_by(ReplyRequest.accepted_at.desc()).limit(1))
            if not request:
                return False
            chat, twin = db.get(Chat, request.chat_id), db.get(Actor, request.target_twin_id)
            return bool(chat.kind == "private" and chat.private_owner_id == owner_id
                and twin.owner_id == owner_id and request.private_generation is not None
                and request.private_generation < chat.reset_generation)

    def get_background_context(self, parent_session_id, parent_run_id):
        with self.transaction() as db:
            request = db.scalar(select(ReplyRequest).where(ReplyRequest.session_id == parent_session_id, ReplyRequest.run_id == parent_run_id).order_by(ReplyRequest.accepted_at.desc()).limit(1))
            if not request:
                raise LookupError("Parent run has no authorized product origin")
            request_id = request.id
        try:
            return self.get_runtime_context(request_id, include_completed=True)
        except HTTPException as exc:
            if exc.status_code in {401, 403, 404, 409}:
                raise PermissionError(exc.detail) from exc
            raise

    def pending_owner_question_for_run(self, session_id, run_id):
        from .questions import pending_for_run
        return pending_for_run(self, session_id, run_id)

    def _accept_pending_child(self, db, pending):
        if pending.accepted_request_id:
            request = db.get(ReplyRequest, pending.accepted_request_id)
            return self.request_payload(request) if request else None
        original = db.get(ReplyRequest, pending.parent_request_id)
        self._check_private_request(db, original)
        chat, member = self.membership(db, pending.owner_id, pending.chat_id, True)
        original = db.get(ReplyRequest, pending.parent_request_id)
        if not original or original.cancel_requested or original.status == "cancelled":
            pending.state, pending.pause_reason = "paused", "parent_stopped"
            return {"id": pending.id, "status": "paused", "durably_retained": True, "causal_chain_id": pending.causal_chain_id, "reason": pending.pause_reason}
        if not self.provenance_allowed(db, member, pending.source_provenance):
            fail(409, "Child result inherited revoked parent sources")
        key = f"child:{digest(pending.id)}"
        request = self.accept_request(db, chat, pending.target_twin_id, pending.requester_id, "subagent_result", key,
                                      source_message_id=pending.source_message_id,
                                      reply_to=original.reply_to_message_id if chat.kind == "private" else pending.source_message_id,
                                      intent_key=key, input_body=pending.body, causal_chain_id=pending.causal_chain_id,
                                      source_owner_id=pending.owner_id)
        if request is None:
            # No second reservation attempt: derive the same deterministic reason.
            repeated = db.scalar(select(AutomaticReservation.id).where(AutomaticReservation.causal_chain_id == pending.causal_chain_id, AutomaticReservation.source_owner_id == pending.owner_id, AutomaticReservation.target_owner_id == pending.owner_id, AutomaticReservation.content_hash == self.normalized_content_hash(pending.body)))
            pending.state, pending.pause_reason = "paused", "repeated_content" if repeated else "budget_exhausted"
            return {"id": pending.id, "status": "paused", "durably_retained": True, "causal_chain_id": pending.causal_chain_id, "reason": pending.pause_reason}
        if pending.source_provenance and not db.scalar(select(ContextManifest.id).where(ContextManifest.request_id == request.id).limit(1)):
            db.add(ContextManifest(request_id=request.id, owner_id=pending.owner_id, grant_version=member.grant_version, high_water_seq=chat.next_message_seq - 1, sources=pending.source_provenance, source_digest=digest(pending.source_provenance)))
        if request.execution_config is None:
            request.execution_config = json.loads(json.dumps(original.execution_config)) if original.execution_config is not None else {
                "version": 0, "skills": [], "credentials": [], "execution_enabled": False}
        pending.state, pending.pause_reason, pending.accepted_request_id = "accepted", None, request.id
        return self.request_payload(request)

    def accept_child_result(self, notification, parent_context):
        with self.transaction() as db:
            original = db.get(ReplyRequest, parent_context.get("request_id"))
            if not original:
                fail(403, "Child result has no durable parent request")
            db.scalar(select(Chat).where(Chat.id == original.chat_id).with_for_update())
            db.refresh(original)
            self._check_private_request(db, original)
            chain = self.request_chain(db, original)
            owner_id = db.get(Actor, original.target_twin_id).owner_id
            chat_id = original.chat_id
            chat, member = self.membership(db, owner_id, chat_id, True)
            if parent_context.get("owner_id") != owner_id or parent_context.get("target_twin_id") != original.target_twin_id:
                fail(403, "Child result target does not match its durable parent owner")
            pending = db.get(PendingChildResult, str(notification["id"]))
            if pending:
                if pending.parent_request_id != original.id or pending.body != str(notification["body"]):
                    fail(409, "Child notification identity conflicts with the retained payload")
            else:
                inherited = self.manifest_provenance(db, original.id)
                if not self.provenance_allowed(db, member, inherited):
                    fail(409, "Child result inherited revoked parent sources")
                pending = PendingChildResult(id=str(notification["id"]), causal_chain_id=chain.id, parent_request_id=original.id,
                                             chat_id=chat_id, owner_id=owner_id, requester_id=original.requester_id,
                                             target_twin_id=original.target_twin_id, source_message_id=original.source_message_id,
                                             body=str(notification["body"]), notification=notification, source_provenance=inherited)
                db.add(pending)
                db.flush()
            result = self._accept_pending_child(db, pending)
        # A truthy response acknowledges durable retention, even while allowance
        # pauses acceptance. The SDK may now ack without losing the result.
        self.publish_event(chat_id, {"type": "changed"})
        return result

    def process_pending_child_results(self, scopes=None):
        changed = set()
        with self.transaction() as db:
            query = select(PendingChildResult.id).where(PendingChildResult.state == "pending")
            if scopes is not None:
                query = query.where(PendingChildResult.chat_id.in_(scopes))
            ids = list(db.scalars(query.order_by(PendingChildResult.created_at).limit(100)))
        for pending_id in ids:
            with self.transaction() as db:
                pending = db.get(PendingChildResult, pending_id)
                if not pending:
                    continue
                db.scalar(select(Chat).where(Chat.id == pending.chat_id).with_for_update())
                db.refresh(pending, with_for_update=True)
                chain = db.get(CausalChain, pending.causal_chain_id)
                if pending.state == "paused" and (pending.pause_reason != "budget_exhausted" or not chain or chain.spent >= chain.allowance):
                    continue
                try:
                    with db.begin_nested():
                        self._accept_pending_child(db, pending)
                except HTTPException:
                    # Revoked access never discards retained data or manufactures
                    # a manual request. It stays paused for authorized recovery.
                    pending.state, pending.pause_reason = "paused", "access_changed"
                changed.add(pending.chat_id)
        for chat_id in changed:
            self.publish_event(chat_id, {"type": "changed"})

    def workspace_node_allowed(self, db, member, node):
        if not self.sources_allowed(db, member, node.source_message_ids):
            return False
        if member.owner_id in node.granted_owner_ids:
            return True
        if not node.shared_with_chat:
            return False
        if member.history_policy == "all":
            return True
        if not node.created_at:
            return False  # Unknown legacy creation time cannot broaden a new grant.
        cutoff = aware(member.joined_at) - timedelta(days=7 if member.history_policy == "fixed_7d" else 0)
        return aware(node.created_at) >= cutoff

    def attachment_json(self, attachment):
        from .attachments import preview_kind
        kind = preview_kind(attachment.name, attachment.mime_type) if attachment.kind != "image" else None
        return {"preview_kind": kind, "preview_url": f"/api/chats/{attachment.chat_id}/attachments/{attachment.id}/preview" if kind else None} | {key: getattr(attachment, key) for key in ("id", "name", "mime_type", "size", "kind", "width", "height")} | {"url": f"/api/chats/{attachment.chat_id}/attachments/{attachment.id}", "workspace_path": str(settings.workspace_root / attachment.relative_path)}

    def _attachment_path(self, attachment):
        root = settings.workspace_root
        relative = Path(attachment.relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            fail(403, "Invalid attachment path")
        path = root / relative
        if any(part.is_symlink() for part in [path, *path.parents] if part == root or part.is_relative_to(root)):
            fail(403, "Attachment symlinks cannot be opened")
        resolved = path.resolve()
        if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
            fail(404, "Attachment is unavailable")
        return resolved

    def upload_attachment(self, owner_id, chat_id, name, data, mime_type="application/octet-stream"):
        from .attachments import inspect_upload
        with self.transaction() as db:
            self.membership(db, owner_id, chat_id, True)
            info = inspect_upload(name, mime_type, data)
            attachment_id = uid()
            root = settings.workspace_root
            parent = root / chat_id / "uploads" / attachment_id
            if any(part.is_symlink() for part in [parent, *parent.parents] if part == root or part.is_relative_to(root)) or not parent.resolve().is_relative_to(root.resolve()):
                fail(403, "Invalid attachment root")
            parent.mkdir(parents=True, exist_ok=False)
            path = parent / name
            try:
                with path.open("xb") as stream:
                    stream.write(data)
                attachment = ChatAttachment(id=attachment_id, chat_id=chat_id, owner_id=owner_id, name=name, relative_path=str(path.relative_to(root)), **info)
                db.add(attachment)
                db.flush()
                result = self.attachment_json(attachment)
            except Exception:
                path.unlink(missing_ok=True)
                parent.rmdir()
                raise
        return result

    def attachment_file(self, owner_id, chat_id, attachment_id):
        with self.transaction() as db:
            _, member = self.membership(db, owner_id, chat_id)
            attachment = db.get(ChatAttachment, attachment_id)
            if not attachment or attachment.chat_id != chat_id:
                fail(404, "Attachment is unavailable")
            if attachment.private_owner_input and attachment.owner_id != owner_id:
                fail(404, "Attachment is unavailable")
            if attachment.message_id:
                self.visible_message(db, member, attachment.message_id)
            elif attachment.owner_id != owner_id:
                fail(404, "Attachment is unavailable")
            return self._attachment_path(attachment), self.attachment_json(attachment) | {"sha256": attachment.sha256}

    def attachment_content(self, owner_id, chat_id, attachment_id):
        from .attachments import MAX_ATTACHMENT_BYTES
        path, info = self.attachment_file(owner_id, chat_id, attachment_id)
        with path.open("rb") as stream:
            data = stream.read(MAX_ATTACHMENT_BYTES + 1)
        if len(data) != info["size"] or hashlib.sha256(data).hexdigest() != info["sha256"]:
            fail(409, "Immutable attachment content changed")
        return data, info

    def _attachment_selection(self, db, owner_id, chat_id, attachment_ids):
        from .attachments import MAX_ATTACHMENT_BYTES, MAX_ATTACHMENTS
        if len(attachment_ids) > MAX_ATTACHMENTS or len(set(attachment_ids)) != len(attachment_ids):
            fail(400, "A message supports up to 4 distinct attachments")
        rows = {row.id: row for row in db.scalars(select(ChatAttachment).where(ChatAttachment.id.in_(attachment_ids)).order_by(ChatAttachment.id).with_for_update())} if attachment_ids else {}
        if any(attachment_id not in rows or rows[attachment_id].owner_id != owner_id or rows[attachment_id].chat_id != chat_id for attachment_id in attachment_ids):
            fail(404, "An attachment is unavailable")
        selected = [rows[attachment_id] for attachment_id in attachment_ids]
        if sum(row.size for row in selected) > MAX_ATTACHMENT_BYTES:
            fail(413, "Message attachments are limited to 20 MiB in total")
        return selected

    def message_has_model_input(self, db, message):
        return bool(message.body.strip() or any((row := db.get(ChatAttachment, attachment_id)) and not row.private_owner_input for attachment_id in (message.attachment_ids or [])))

    def attachment_references(self, db, attachment_ids, member, *, private_request=None):
        references = []
        for attachment_id in attachment_ids or []:
            attachment = db.get(ChatAttachment, attachment_id)
            if not attachment or attachment.chat_id != member.chat_id:
                continue
            if attachment.private_owner_input:
                if not private_request or private_request.requester_id != member.owner_id or attachment.owner_id != member.owner_id or attachment_id not in (private_request.input_attachment_ids or []):
                    continue
            elif attachment.message_id:
                self.visible_message(db, member, attachment.message_id)
            else:
                fail(409, "Model attachment has not been committed to this request")
            data = self._attachment_path(attachment).read_bytes()
            if len(data) != attachment.size or hashlib.sha256(data).hexdigest() != attachment.sha256:
                fail(409, "Immutable attachment content changed; reply withheld")
            references.append({key: value for key, value in self.attachment_json(attachment).items() if key not in {"url", "preview_url"}})
        return references

    def upload_workspace_file(self, owner_id, chat_id, filename, data, source_ids=None):
        if not data or len(data) > 20 * 1024 * 1024:
            fail(400, "Workspace files must contain 1 byte to 20 MiB")
        if Path(filename).name != filename or filename in {".", ".."} or not filename.strip() or len(filename) > 200 or "\\" in filename:
            fail(400, "File name must be a simple name without path separators")
        with self.transaction() as db:
            _, member = self.membership(db, owner_id, chat_id, True)
            self.check_sources(db, member, source_ids or [])
            node_id = uid()
            root = settings.workspace_root / chat_id
            parent = root / "uploads" / node_id
            if any(part.is_symlink() for part in [parent, *parent.parents] if part.is_relative_to(settings.workspace_root)) or not parent.resolve().is_relative_to(settings.workspace_root):
                fail(403, "Invalid workspace root")
            parent.mkdir(parents=True, exist_ok=False)
            path = parent / filename
            try:
                with path.open("xb") as file:
                    file.write(data)
                db.add(WorkspaceNode(id=node_id, chat_id=chat_id, relative_path=f"uploads/{node_id}/{filename}", source_message_ids=source_ids or [], granted_owner_ids=[m.owner_id for m in self.members(db, chat_id)]))
                db.flush()
            except Exception:
                path.unlink(missing_ok=True)
                parent.rmdir()
                raise
            result = {"id": node_id, "name": filename, "kind": "file", "path": f"uploads/{node_id}/{filename}", "size": len(data)}
        self.publish_event(chat_id, {"type": "changed"})
        return result

    def workspace_file(self, owner_id, chat_id, node_id):
        with self.transaction() as db:
            return self._workspace_file_db(db, owner_id, chat_id, node_id)

    def _workspace_file_db(self, db, owner_id, chat_id, node_id, *, allow_directory=False):
        if node_id.startswith("path:"):
            import base64
            try:
                encoded = node_id[5:]
                relative = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True).decode("utf-8")
            except (ValueError, UnicodeError):
                fail(404, "Workspace file is unavailable")
            return self._workspace_path_db(db, owner_id, chat_id, relative, allow_directory=allow_directory)
        _, member = self.membership(db, owner_id, chat_id)
        node = db.get(WorkspaceNode, node_id)
        if not node or node.chat_id != chat_id or not self.workspace_node_allowed(db, member, node):
            fail(404, "Workspace file is unavailable")
        return self._workspace_path_db(db, owner_id, chat_id, node.relative_path, allow_directory=allow_directory)

    @property
    def database_url(self):
        return settings.database_url

    def _tracking_row(self, db, member, topic_id):
        topic = db.get(TrackingTopic, topic_id)
        if not topic or topic.chat_id != member.chat_id or not self.tracking_json(db, topic, member):
            fail(404, "Tracking topic is unavailable")
        return topic

    def save_tracking_revision(self, db, topic, owner_id):
        db.add(TrackingRevision(topic_id=topic.id, revision=topic.revision, title=topic.title, description=topic.description, status=topic.status, source_message_ids=topic.source_message_ids, changed_by=owner_id, deleted=topic.deleted_at is not None))

    def _tool_dispatch(self, db, name, args, context, chat, member):
        owner_id = member.owner_id
        if name == "activity_scope":
            if chat.kind != "private" or chat.private_owner_id != owner_id:
                fail(403, "Activity only includes your current private chat")
            return {"twin_id": self.twin(db, owner_id).id}
        if name == "history":
            query = select(Message).where(Message.chat_id == chat.id, Message.seq >= member.lower_seq, Message.state == "published")
            if args.get("before_seq") is not None:
                query = query.where(Message.seq < int(args["before_seq"]))
            if args.get("query"):
                query = query.where(Message.body.ilike(f"%{args['query']}%"))
            limit = max(1, min(int(args.get("limit", 100)), 100))
            rows = [m for m in db.scalars(query.order_by(Message.seq.desc()).limit(limit)) if self.message_sources_visible(db, member, m)]
            return {"messages": self.messages_json(db, reversed(rows), member), "history_lower_seq": member.lower_seq, "next_before_seq": rows[-1].seq if len(rows) == limit else None}
        if name == "list_tracking":
            return {"tracking": [row[3] for row in self._tracking_scope(db, chat, member)]}
        if name == "get_tracking":
            return self.tracking_json(db, self._tracking_row(db, member, args["id"]), member, True)
        if name == "create_tracking":
            source_ids = list(dict.fromkeys(args.get("source_message_ids", [])))
            messages = self.check_sources(db, member, source_ids)
            title = str(args["title"]).strip()
            description = str(args.get("description", "")).strip()
            if not title or len(title) > 256 or len(description) > 30000:
                fail(400, "Tracking title or description is invalid")
            subject = re.sub(r"\W+", " ", title.casefold()).strip()
            tombstones = list(db.scalars(select(TrackingTopic).where(TrackingTopic.chat_id == chat.id, TrackingTopic.deleted_at.is_not(None))))
            if tombstones and context.get("context_manifest_id") and not messages:
                fail(400, "Automatic Tracking requires explicit source messages")
            for tombstone in tombstones:
                uses_deleted_sources = any(m.seq <= (tombstone.source_cutoff_seq or 0) for m in messages)
                uses_deleted_topic = any(s.get("kind") == "tracking" and s.get("id") == tombstone.id for s in context.get("inherited_provenance", []))
                if not context.get("explicit_reintroduction") and (tombstone.subject_key == subject or uses_deleted_sources or uses_deleted_topic):
                    fail(409, "This Tracking topic was deleted. Old sources cannot recreate it; a new explicit owner request is required")
            current = db.scalar(select(TrackingTopic).where(TrackingTopic.chat_id == chat.id, TrackingTopic.subject_key == subject, TrackingTopic.deleted_at.is_(None)))
            if current:
                visible = self.tracking_json(db, current, member, True)
                if not visible:
                    fail(409, "A matching topic is outside your history grant")
                return visible
            topic = TrackingTopic(chat_id=chat.id, source_provenance=context.get("inherited_provenance", []), title=title, description=description, source_message_ids=source_ids, source_floor_seq=min([m.seq for m in messages], default=chat.next_message_seq), subject_key=subject)
            db.add(topic)
            db.flush()
            self.save_tracking_revision(db, topic, owner_id)
            return self.tracking_json(db, topic, member, True)
        if name in {"update_tracking", "delete_tracking", "add_tracking_update"}:
            topic = self._tracking_row(db, member, args["id"])
            if args.get("expected_revision") is not None and int(args["expected_revision"]) != topic.revision:
                fail(409, "Tracking changed since it was opened; reload before saving")
            if name == "delete_tracking":
                topic.deleted_at, topic.deleted_by, topic.source_cutoff_seq = now(), owner_id, chat.next_message_seq - 1
                topic.revision += 1
                self.save_tracking_revision(db, topic, owner_id)
                return {"ok": True}
            if name == "update_tracking":
                if context.get("inherited_provenance"):
                    topic.source_provenance = [*topic.source_provenance, *context["inherited_provenance"]]
                    topic.source_message_ids = list(dict.fromkeys([*topic.source_message_ids, *[s["id"] for s in context["inherited_provenance"] if s.get("kind") == "message"]]))
                for field in ("title", "description", "status"):
                    if field in args and args[field] is not None:
                        value = str(args[field]).strip()
                        if field in {"title", "status"} and (not value or len(value) > (256 if field == "title" else 32)):
                            fail(400, f"Invalid Tracking {field}")
                        setattr(topic, field, value)
                topic.subject_key = re.sub(r"\W+", " ", topic.title.casefold()).strip()
            else:
                ids = list(dict.fromkeys(args.get("source_message_ids", [])))
                messages = self.check_sources(db, member, ids)
                body = str(args["body"]).strip()
                if not body or len(body) > 30000:
                    fail(400, "Invalid Tracking update")
                db.add(TrackingUpdate(topic_id=topic.id, source_provenance=context.get("inherited_provenance", []), body=body, source_message_ids=ids, source_floor_seq=min([m.seq for m in messages], default=chat.next_message_seq)))
            topic.updated_at, topic.revision = now(), topic.revision + 1
            self.save_tracking_revision(db, topic, owner_id)
            db.flush()
            return self.tracking_json(db, topic, member, True)
        if name == "save_summary":
            ids = list(dict.fromkeys(args["source_message_ids"]))
            messages = self.check_sources(db, member, ids)
            if not messages or not str(args["body"]).strip():
                fail(400, "Summary requires body and exact original source messages")
            start, end = min(m.seq for m in messages), max(m.seq for m in messages)
            expected = {message.id for message in db.scalars(select(Message).where(Message.chat_id == chat.id, Message.seq >= member.lower_seq, Message.seq <= end, Message.state == "published")) if self.message_sources_visible(db, member, message)}
            if set(ids) != expected:
                fail(400, "Summary must cover every authorized original message through its boundary, with no omitted sources")
            summary = Summary(chat_id=chat.id, source_provenance=context.get("inherited_provenance", []), body=str(args["body"]), source_message_ids=ids, coverage_start_seq=member.lower_seq, coverage_end_seq=end)
            db.add(summary)
            db.flush()
            return {"id": summary.id, "coverage_end_seq": end, "source_message_ids": ids}
        if name == "create_approval":
            from .platform_tools import exact_action
            action = exact_action(args.get("action"), context.get("execution_action_schemas"))
            if action["tool"] == "retry_reply_request":
                from .platform_tools import request_row
                retry_target, retry_chat, _ = request_row(self, db, owner_id, action["arguments"]["id"])
                if retry_chat.kind == "private" and chat.id != retry_chat.id:
                    from .credentials import private_origin
                    private_origin(self, db, owner_id, retry_target.id, executable=False)
                    args = {**args, "target": f"Private task {retry_target.id}",
                            "impact": "Re-execute this existing personal task using its private execution tools."}
            if not context.get("runtime_session_id") or not context.get("runtime_run_id") or not context.get("request_id"):
                fail(400, "Approval requires a durable originating run")
            manifest = db.get(ContextManifest, context.get("context_manifest_id")) if context.get("context_manifest_id") else None
            sources = [s["id"] for s in manifest.sources if s.get("kind") == "message"] if manifest else []
            reference = {"session_id": context["runtime_session_id"], "run_id": context["runtime_run_id"], "request_id": context["request_id"], "action": action}
            if action["tool"] in context.get("execution_action_schemas", {}):
                origin = db.get(ReplyRequest, context["request_id"])
                from .credentials import runtime_origin
                runtime_origin(self, db, owner_id, origin.id)
                if not origin.execution_config or origin.execution_config != context.get("execution_config"):
                    fail(409, "Approval execution configuration is not frozen for this request")
                reference.update(execution_config_digest=digest(origin.execution_config), target_twin_id=origin.target_twin_id)
            approval = Approval(id=uid(), chat_id=chat.id, source_provenance=context.get("inherited_provenance", []), source_message_ids=sources, approver_id=owner_id, title=str(args["title"])[:256], description=str(args["description"]), target=str(args["target"]), impact=str(args["impact"]), sdk_reference=reference)
            db.add(approval)
            db.flush()
            private = db.scalar(select(Chat).where(Chat.kind == "private", Chat.private_owner_id == owner_id))
            if private and private.id != chat.id:
                if private.reset_pending:
                    fail(409, "Private chat reset is pending")
                # Durable private seq/outbox refresh; this is a notice, not a
                # new user task or an authorization to change execution scope.
                self.append_message(db, private, author_id=self.twin(db, owner_id).id, kind="twin",
                    body=f"Approval requested: {approval.title}", request_id=context["request_id"],
                    causal_chain_id=context.get("causal_chain_id"),
                    source_provenance=[{"kind": "chat_context", "chat_id": chat.id, "sources": context.get("inherited_provenance", [])}],
                    sdk_output_key=f"approval-notice:{approval.id}")
            return self.approval_json(approval)
        if name == "get_approval":
            approval = db.get(Approval, args["id"])
            if not approval or approval.approver_id != owner_id:
                fail(404, "Approval not found")
            _, origin_member = self.membership(db, owner_id, approval.chat_id)
            origin = db.get(ReplyRequest, approval.sdk_reference.get("request_id"))
            if not origin or origin.cancel_requested or origin.status == "cancelled":
                fail(409, "Approval origin is unavailable")
            self._check_private_request(db, origin)
            if not self.sources_allowed(db, origin_member, approval.source_message_ids) or not self.provenance_allowed(db, origin_member, approval.source_provenance):
                fail(404, "Approval not found")
            return self.approval_json(approval)
        if name == "create_reminder":
            requester = context.get("request_requester_id", owner_id)
            if requester != owner_id and not context.get("approved_approval_id"):
                fail(403, "Another participant cannot create personal reminders for this Duet's owner")
            due = args.get("due_at")
            if isinstance(due, str):
                try:
                    due = datetime.fromisoformat(due.replace("Z", "+00:00"))
                except ValueError:
                    fail(400, "due_at must be an ISO date-time")
                if due.tzinfo is None:
                    fail(400, "due_at must include a timezone")
            title = str(args["title"]).strip()
            if not title or len(title) > 256:
                fail(400, "Invalid reminder title")
            reminder = Reminder(owner_id=owner_id, title=title, detail=str(args.get("detail", "")), due_at=due, chat_id=chat.id, kind="reminder")
            db.add(reminder)
            db.flush()
            return self.reminder_json(reminder)
        fail(400, "Unknown product tool")

    def _message_source_chat_ids(self, db, request, destination_id, explicit_source_ids):
        """Find the complete permission boundary without exposing any source data."""
        chat_ids = {destination_id}
        if request is not None:
            chat_ids.add(request.chat_id)
        seen_messages = set()
        def visit_message(message_id):
            if message_id in seen_messages:
                return
            seen_messages.add(message_id)
            message = db.get(Message, message_id)
            if message:
                chat_ids.add(message.chat_id)
                for source_id in message.source_message_ids or []:
                    visit_message(source_id)
                visit_sources(message.source_provenance)
        def visit_sources(sources):
            for source in sources or []:
                if source.get("kind") == "chat_context":
                    if isinstance(source.get("chat_id"), str):
                        chat_ids.add(source["chat_id"])
                    visit_sources(source.get("sources", []))
                elif source.get("kind") == "summary":
                    from .conversation_context import summary_metadata
                    summary = summary_metadata(db, source.get("id"))
                    if summary:
                        chat_ids.add(summary.chat_id)
                        visit_sources(summary.source_provenance)
                elif source.get("kind") == "history_range":
                    chat_ids.add(source["chat_id"])
                elif source.get("kind") == "message":
                    visit_message(source.get("id"))
                elif source.get("kind") == "tracking":
                    for source_id in source.get("source_message_ids", []):
                        visit_message(source_id)
        if request is not None:
            visit_sources(self.manifest_provenance(db, request.id))
        for message_id in explicit_source_ids:
            if isinstance(message_id, str):
                visit_message(message_id)
        return chat_ids

    def _message_tool_origin(self, db, context):
        request = db.get(ReplyRequest, context.get("request_id"))
        if not request or request.cancel_requested or request.status in {"failed", "cancelled"}:
            fail(403, "Duet messaging requires an authorized originating request")
        twin = db.get(Actor, request.target_twin_id)
        chat, member = self.membership(db, twin.owner_id, request.chat_id)
        context_chat = context.get("chat", {})
        context_chat_id = context_chat.get("id") if isinstance(context_chat, dict) else context_chat
        if (context_chat_id and context_chat_id != chat.id) or (context.get("chat_id") and context["chat_id"] != chat.id):
            fail(403, "Duet messaging cannot replace its originating chat")
        manifest = db.get(ContextManifest, context.get("context_manifest_id"))
        if context.get("owner_id") != twin.owner_id or context.get("target_twin_id") != twin.id or not manifest or manifest.request_id != request.id or manifest.owner_id != twin.owner_id:
            fail(403, "Duet messaging has no matching model-input provenance")
        if context.get("causal_chain_id") and context["causal_chain_id"] != self.request_chain(db, request).id:
            fail(403, "Duet messaging cannot replace its causal lineage")
        if context.get("main_session_id") and request.session_id and context["main_session_id"] != request.session_id:
            fail(403, "Duet messaging belongs to another originating session")
        if context.get("main_run_id") and request.run_id and context["main_run_id"] != request.run_id:
            fail(403, "Duet messaging belongs to another originating run")
        inherited = self.manifest_provenance(db, request.id)
        if not self.provenance_allowed(db, member, inherited):
            fail(409, "Originating context is no longer authorized")
        return request, twin, chat, member, inherited

    def _twin_message_tool(self, db, name, args, context):
        if name == "present_files":
            from .deliverables import present_files
            return present_files(self, db, args, context)
        request, twin, origin, origin_member, inherited = self._message_tool_origin(db, context)
        if name == "list_authorized_chats":
            chats = []
            memberships = list(db.scalars(select(Membership).where(Membership.owner_id == twin.owner_id, Membership.left_at.is_(None))))
            for membership in memberships:
                chat = db.get(Chat, membership.chat_id)
                if not chat or (chat.kind == "private" and chat.private_owner_id != twin.owner_id):
                    continue
                chats.append({"id": chat.id, "kind": chat.kind, "title": self.chat_title(db, chat, twin.owner_id), "is_current_chat": chat.id == origin.id, "is_owner_private": chat.kind == "private" and chat.private_owner_id == twin.owner_id, "member_owner_ids": [row.owner_id for row in self.members(db, chat.id)]})
            return {"chats": chats, "owner_private_chat_id": next((row["id"] for row in chats if row["is_owner_private"]), None)}
        body = args.get("body")
        source_ids = args.get("source_message_ids", [])
        if not isinstance(body, str) or not body.strip():
            fail(400, "Duet message body must contain text")
        if not isinstance(source_ids, list) or len(source_ids) > 500 or not all(isinstance(source_id, str) for source_id in source_ids):
            fail(400, "Duet message source_message_ids must be message IDs")
        target, target_member = self.membership(db, twin.owner_id, args.get("chat_id"), True)
        if target.id == origin.id:
            fail(400, "Use your normal assistant reply for the current chat; send_chat_message is only for other chats")
        if target.kind == "private" and target.reset_pending:
            fail(409, "Private chat reset is pending")
        reply_to = args.get("reply_to_message_id")
        if reply_to is not None:
            if not isinstance(reply_to, str):
                fail(400, "reply_to_message_id must be a message ID")
            self.visible_message(db, target_member, reply_to)
        wait_for_reply = args.get("wait_for_reply", False)
        if type(wait_for_reply) is not bool:
            fail(400, "wait_for_reply must be a boolean")
        if wait_for_reply and (origin.kind != "private" or origin.private_owner_id != twin.owner_id
                or request.requester_id != twin.owner_id or target.id == origin.id or target.kind != "group"):
            fail(403, "Reply followup is available only from your own private Duet to an existing authorized group")
        explicit = []
        for source_id in dict.fromkeys(source_ids):
            source = db.get(Message, source_id)
            if not source:
                fail(404, "Message is unavailable in your authorized history")
            source_chat, source_member = self.membership(db, twin.owner_id, source.chat_id)
            source = self.visible_message(db, source_member, source_id)
            from .conversation_context import message_source
            reference = message_source(db, source)
            if source_chat.id != origin.id:
                envelope = {"kind": "chat_context", "chat_id": source_chat.id, "sources": [reference]}
                if source_chat.kind == "private":
                    envelope["private_generation"] = source_chat.reset_generation
                reference = envelope
            explicit.append(reference)
        inherited = list({digest(source): source for source in [*inherited, *explicit]}.values())
        if not self.provenance_allowed(db, origin_member, inherited):
            fail(409, "Message sources are no longer authorized")
        manifest = db.get(ContextManifest, context["context_manifest_id"])
        manifest.sources, manifest.source_digest = inherited, digest(inherited)
        # Prepared owner-attributed text retains its internal audit, but
        # grants recipients no access to any originating records.
        provenance, local_source_ids = [], []
        chain = self.request_chain(db, request)
        content_hash = self.normalized_content_hash(body)
        for previous in db.scalars(select(Message).where(Message.chat_id == target.id, Message.causal_chain_id == chain.id, Message.author_id == twin.id, Message.state == "published")):
            if self.normalized_content_hash(previous.body) == content_hash:
                return {"status": "held", "reason": "repeated_content", "message_id": previous.id, "duplicate_message_id": previous.id,
                        "chat_id": target.id, "causal_chain_id": chain.id, "author_id": twin.id, "author_owner_id": twin.owner_id,
                        "message": None, "sent": False}
        message = self.append_message(db, target, author_id=twin.id, kind="twin", body=body,
                                      request_id=request.id, causal_chain_id=chain.id,
                                      reply_to_message_id=reply_to,
                                      source_message_ids=local_source_ids, source_provenance=provenance,
                                      source_floor_seq=min([db.get(Message, source_id).seq for source_id in local_source_ids], default=target.next_message_seq),
                                      sdk_output_key=f"tool:{digest(context['operation_id'])}")
        self.route_committed_message(db, target, message)
        followup = None
        if wait_for_reply:
            from .followups import create
            followup = create(self, db, request, twin.owner_id, target, message, inherited)
        return {"status": "sent", "sent": True, "message": self.message_json(db, message, target_member), "message_id": message.id,
                "followup": followup,
                "chat_id": target.id, "causal_chain_id": message.causal_chain_id, "author_id": twin.id,
                "author_owner_id": twin.owner_id}

    async def runtime_tool(self, name, args, context):
        if name == "execute_approved_action":
            if not isinstance(args, dict) or set(args) != {"id"} or not isinstance(args["id"], str):
                fail(400, "execute_approved_action accepts only the approval ID")
            approval = await self.db_call("approval_for_execution", context["owner_id"], context["chat"]["id"], args["id"])
            operation_id = f"approval-effect:{approval['id']}"
            action = await self.runtime.authorize_approval(approval, operation_id)
            return await self.runtime_tool(action["tool"], action["arguments"], {
                **context, "operation_id": operation_id, "approved_approval_id": approval["id"],
            })
        return await self.db_call("_runtime_tool_transaction", name, args, context)

    def _runtime_tool_transaction(self, name, args, context):
        chat_value = context.get("chat", {})
        chat_id = chat_value.get("id") if isinstance(chat_value, dict) else chat_value
        chat_id = chat_id or context.get("chat_id")
        owner_id = context.get("owner_id")
        from .onboarding import TOOL as ONBOARDING_TOOL
        from .onboarding import finish as finish_onboarding
        from .platform_tools import (
            PLATFORM_TOOLS,
            SCOPED_TOOLS,
            TRACKING_TOOLS,
            WRITE_TOOLS,
            dispatch,
            lock_scope,
        )
        from .proactivity import MUTATIONS as PROACTIVITY_MUTATIONS
        from .proactivity import TOOLS as PROACTIVITY_TOOLS
        from .proactivity import dispatch as proactivity_dispatch
        from .scheduled_tasks import MUTATIONS as SCHEDULED_MUTATIONS
        from .scheduled_tasks import TOOLS as SCHEDULED_TOOLS
        from .scheduled_tasks import runtime_dispatch
        mutations = WRITE_TOOLS | {"create_approval", ONBOARDING_TOOL} | SCHEDULED_MUTATIONS | PROACTIVITY_MUTATIONS
        with self.transaction() as db:
            locked_ids = lock_scope(self, db, name, args, context)
            chat, member = self.membership(db, owner_id, chat_id)
            self._authorize_generation(db, context)
            if name in TRACKING_TOOLS and chat.kind == "group" and args.get("chat_id", chat.id) != chat.id:
                fail(403, "Group Tracking tools are limited to the originating group")
            if context.get("context_manifest_id"):
                origin_request, *_ = self._message_tool_origin(db, context)
                context = {**context, "request_requester_id": origin_request.requester_id}
            approved_id = context.get("approved_approval_id")
            if approved_id:
                approved = db.get(Approval, approved_id)
                if (not approved or approved.approver_id != owner_id or approved.chat_id != chat_id
                        or approved.status != "approved" or approved.sdk_reference.get("action") != {"tool": name, "arguments": args}):
                    fail(403, "Action does not match the owner's exact approved action")
            operation_id = context.get("operation_id")
            arguments_hash = digest(args)
            if name in mutations:
                if not operation_id:
                    fail(400, "Mutating runtime tools require a stable operation_id")
                prior = db.get(ProductOperation, operation_id)
                if prior:
                    if (prior.chat_id, prior.owner_id, prior.name, prior.arguments_hash) != (chat_id, owner_id, name, arguments_hash):
                        fail(409, "Product operation identity conflicts with its original action")
                    if name in {"send_chat_message", "present_files"}:
                        self._message_tool_origin(db, context)
                        destination_member = self.membership(db, owner_id, prior.result["chat_id"])[1]
                        if prior.result.get("message_id"):
                            self.visible_message(db, destination_member, prior.result["message_id"])
                    if name in {"create_tracking", "update_tracking", "add_tracking_update"}:
                        topic = db.get(TrackingTopic, prior.result.get("id"))
                        if not topic or not self.tracking_json(db, topic, self.membership(db, owner_id, topic.chat_id)[1]):
                            return {"id": prior.result.get("id"), "status": "deleted_or_unavailable", "operation_already_applied": True}
                    if name == "create_approval":
                        approval = db.get(Approval, prior.result.get("id"))
                        if not approval or not self.sources_allowed(db, member, approval.source_message_ids) or not self.provenance_allowed(db, member, approval.source_provenance):
                            return {"id": prior.result.get("id"), "status": "unavailable", "operation_already_applied": True}
                    from .platform_tools import revalidate_result
                    revalidate_result(self, db, name, args, prior.result, owner_id)
                    return prior.result
            if name in mutations and context.get("context_manifest_id"):
                manifest = db.get(ContextManifest, context["context_manifest_id"])
                if not manifest or manifest.owner_id != owner_id:
                    fail(403, "Tool has no matching model-input provenance")
                inherited = self.manifest_provenance(db, manifest.request_id)
                if not self.provenance_allowed(db, member, inherited):
                    fail(409, "Model-input sources are no longer authorized")
                context = {**context, "inherited_provenance": inherited}
                # Citations identify the topic's actual sources. Full model-input
                # provenance is retained separately and still gates every read.
            target_chat, target_member = chat, member
            if name in SCOPED_TOOLS and args.get("chat_id"):
                target_chat, target_member = self.membership(db, owner_id, args["chat_id"])
                if target_chat.kind == "private" and target_chat.reset_pending:
                    fail(409, "Private chat reset is pending")
            dispatch_context = context
            if target_chat.id != chat.id:
                dispatch_context = {**context, "inherited_provenance": [{"kind": "chat_context", "chat_id": chat.id, "sources": context.get("inherited_provenance", [])}]}
            if name == ONBOARDING_TOOL:
                result = finish_onboarding(self, db, args, context, chat)
            elif name in SCHEDULED_TOOLS:
                result = runtime_dispatch(self, db, name, args, context, chat)
            elif name in PROACTIVITY_TOOLS:
                result = proactivity_dispatch(self, db, name, args, context)
            elif name in PLATFORM_TOOLS:
                result = dispatch(self, db, name, args, context, chat, member, locked_ids)
            elif name in {"send_chat_message", "present_files", "list_authorized_chats"}:
                result = self._twin_message_tool(db, name, args, context)
            else:
                result = self._tool_dispatch(db, name, args, dispatch_context, target_chat, target_member)
            manifest_id = context.get("context_manifest_id")
            manifest = db.get(ContextManifest, manifest_id) if manifest_id else None
            if manifest and manifest.owner_id == owner_id:
                source_ids = []
                if name == "history":
                    source_ids = [m["id"] for m in result.get("messages", [])]
                elif name == "get_tracking":
                    topic = db.get(TrackingTopic, args["id"])
                    source_ids = list(topic.source_message_ids)
                    for update in db.scalars(select(TrackingUpdate).where(TrackingUpdate.topic_id == topic.id)):
                        if self.sources_allowed(db, target_member, update.source_message_ids, update.source_floor_seq):
                            source_ids.extend(update.source_message_ids)
                extra_provenance = []
                tracking_ids = [row["id"] for row in result.get("tracking", [])] if name == "list_tracking" else [result["id"]] if name in {"get_tracking", "create_tracking", "update_tracking", "add_tracking_update"} and result.get("id") else []
                for topic_id in tracking_ids:
                    topic = db.get(TrackingTopic, topic_id)
                    if not topic:
                        continue
                    topic_member = target_member
                    if topic.chat_id != target_chat.id:
                        topic_member = self.membership(db, owner_id, topic.chat_id)[1]
                    if self.tracking_json(db, topic, topic_member):
                        topic_sources = self._tracking_provenance(topic)
                        extra_provenance.extend(topic_sources if topic.chat_id == target_chat.id else [
                            {"kind": "chat_context", "chat_id": topic.chat_id, "sources": topic_sources}])
                        if name != "list_tracking":
                            for update in db.scalars(select(TrackingUpdate).where(TrackingUpdate.topic_id == topic.id)):
                                if self.sources_allowed(db, target_member, update.source_message_ids, update.source_floor_seq) and self.provenance_allowed(db, target_member, update.source_provenance):
                                    extra_provenance.extend(update.source_provenance or [])
                known = {source.get("id") for source in manifest.sources if source.get("kind") == "message" and target_chat.id == chat.id}
                from .conversation_context import message_source
                added = [message_source(db, db.get(Message, sid)) for sid in dict.fromkeys(source_ids) if sid not in known]
                if name == "read_workspace_file":
                    extra_provenance.append({"kind": "workspace", "id": result["id"]})
                    target_chat = db.get(Chat, result["chat_id"])
                from .platform_tools import read_provenance
                metadata_sources = read_provenance(self, db, name, args, result, context)
                if added or extra_provenance or metadata_sources:
                    new_sources = [*added, *extra_provenance]
                    if target_chat.id != chat.id:
                        new_sources = [{"kind": "chat_context", "chat_id": target_chat.id, "sources": new_sources}]
                    unique_sources = {digest(source): source for source in [*manifest.sources, *new_sources, *metadata_sources]}
                    manifest.sources = list(unique_sources.values())
                    manifest.source_digest = digest(manifest.sources)
            if name in mutations:
                db.add(ProductOperation(operation_id=operation_id, chat_id=chat_id, owner_id=owner_id, name=name, arguments_hash=arguments_hash, result=result))
        if name in mutations and name != "set_message_reaction":
            self.publish_event(chat_id, {"type": "changed"})
            if result.get("chat_id") and result["chat_id"] != chat_id:
                self.publish_event(result["chat_id"], {"type": "changed"})
        return result

    async def tracking_action(self, name, owner_id, chat_id, args, explicit=False):
        return await self.runtime_tool(name, args, {"owner_id": owner_id, "chat": {"id": chat_id}, "operation_id": f"http:{uid()}", "explicit_reintroduction": explicit})

    def create_reminder(self, owner_id, title, detail="", due_at=None, chat_id=None):
        with self.transaction() as db:
            if chat_id:
                self.membership(db, owner_id, chat_id)
            reminder = Reminder(owner_id=owner_id, title=title, detail=detail or "", due_at=due_at, chat_id=chat_id)
            db.add(reminder)
            db.flush()
            return self.reminder_json(reminder)

    def update_reminder(self, owner_id, reminder_id, done):
        with self.transaction() as db:
            reminder = db.get(Reminder, reminder_id)
            if not reminder or reminder.owner_id != owner_id:
                fail(404, "Reminder not found")
            reminder.done = done
            return self.reminder_json(reminder)

    def _queue_approval_decision(self, owner_id, approval_id, decision):
        with self.transaction() as db:
            approval = db.get(Approval, approval_id)
            if not approval or approval.approver_id != owner_id:
                fail(404, "Approval not found")
            self.membership(db, owner_id, approval.chat_id, True)
            prior = approval.sdk_reference.get("requested_decision")
            if approval.status != "pending" and approval.status != decision or prior and prior != decision:
                fail(409, "This approval already has a different decision")
            approval.sdk_reference = {**approval.sdk_reference, "requested_decision": decision}
            return {**self.approval_json(approval), "accepted": "business_persisted"}

    async def decide_approval(self, owner_id, approval_id, decision):
        # Validation includes current provenance, independently of any socket subscription.
        def validate():
            with self.transaction() as db:
                row = db.get(Approval, approval_id)
                if not row:
                    fail(404, "Approval not found")
                return row.chat_id
        chat_id = await self.db_call(validate)
        await self.db_call("approval_for_execution", owner_id, chat_id, approval_id)
        return await self.db_call("_queue_approval_decision", owner_id, approval_id, decision)

    def approval_for_execution(self, owner_id, chat_id, approval_id, execution_context=None):
        with self.transaction() as db:
            _, member = self.membership(db, owner_id, chat_id)
            row = db.get(Approval, approval_id)
            if not row or row.chat_id != chat_id or row.approver_id != owner_id:
                fail(404, "Approval not found")
            if not self.sources_allowed(db, member, row.source_message_ids) or not self.provenance_allowed(db, member, row.source_provenance):
                fail(409, "Approval sources are no longer authorized")
            origin = db.get(ReplyRequest, row.sdk_reference.get("request_id"))
            if not origin or origin.cancel_requested or origin.status == "cancelled":
                fail(409, "Approval origin was cancelled or is unavailable")
            if row.sdk_reference.get("execution_config_digest") is not None:
                from .credentials import runtime_origin
                runtime_origin(self, db, owner_id, origin.id)
                if execution_context is not None:
                    self._authorize_generation(db, execution_context)
                    self._message_tool_origin(db, execution_context)
                    current, _, _ = runtime_origin(self, db, owner_id, execution_context.get("request_id"))
                    frozen_digest = row.sdk_reference["execution_config_digest"]
                    if (current.causal_chain_id != origin.causal_chain_id
                            or current.target_twin_id != row.sdk_reference.get("target_twin_id")
                            or digest(current.execution_config) != frozen_digest
                            or digest(execution_context.get("execution_config")) != frozen_digest):
                        fail(403, "Approved execution does not match the original owner configuration")
            return {**self.approval_json(row), "sdk_reference": row.sdk_reference}

    def accept_approval_decision(self, approval_id, decision):
        """Project the SDK decision and enqueue its continuation in one business commit."""
        with self.transaction() as db:
            row = db.get(Approval, approval_id)
            chat, member = self.membership(db, row.approver_id, row.chat_id, True)
            db.refresh(row)
            if row.continuation_request_id:
                return
            row.status = decision["status"]
            origin = db.get(ReplyRequest, row.sdk_reference.get("request_id"))
            if not origin or origin.cancel_requested or origin.status == "cancelled":
                return
            if not self.provenance_allowed(db, member, row.source_provenance):
                return
            key = decision["decision_id"]
            request = self.accept_request(db, chat, origin.target_twin_id, origin.requester_id,
                "approval_result", key, source_message_id=origin.source_message_id,
                intent_key=key, causal_chain_id=origin.causal_chain_id, source_owner_id=origin.source_owner_id,
                input_body=f"Owner decision for approval {row.id}: {row.status}. "
                "If approved, use execute_approved_action with that ID to perform the frozen action. "
                "If rejected, do not execute it. Report the actual tool result; consent is not execution.")
            request.execution_config = origin.execution_config
            row.continuation_request_id = request.id
            db.add(ContextManifest(request_id=request.id, owner_id=row.approver_id,
                grant_version=member.grant_version, high_water_seq=chat.next_message_seq - 1,
                sources=row.source_provenance, source_digest=digest(row.source_provenance)))
        self.publish_event(chat.id, {"type": "changed"})

    def _pending_approvals(self, scopes=None):
        with self.transaction() as db:
            query = select(Approval).where(Approval.continuation_request_id.is_(None))
            if scopes is not None:
                query = query.where(Approval.chat_id.in_(scopes))
            return [{**self.approval_json(row), "sdk_reference": row.sdk_reference}
                    for row in db.scalars(query.limit(100))
                    if row.sdk_reference.get("action")]

    async def reconcile_approvals(self, scopes=None):
        for row in await self.db_call("_pending_approvals", scopes):
            try:
                decision = row["sdk_reference"].get("requested_decision")
                approval = await self.runtime.decide_approval(row, decision) if decision else await self.runtime.ensure_approval(row)
                if approval["status"] != "pending":
                    await self.db_call("accept_approval_decision", row["id"], approval)
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Approval reconciliation failed for %s", row["id"])

    def workspace(self, owner_id, chat_id, path=""):
        with self.transaction() as db:
            return self._workspace_db(db, owner_id, chat_id, path)

    def _workspace_path_db(self, db, owner_id, chat_id, relative, *, allow_directory=False):
        _, member = self.membership(db, owner_id, chat_id)
        root_path = settings.workspace_root / chat_id
        root = root_path.resolve()
        if not root.is_relative_to(settings.workspace_root) or root_path.is_symlink():
            fail(403, "Invalid workspace root")
        relative = Path(relative)
        if relative.is_absolute() or ".." in relative.parts or "\\" in str(relative):
            fail(403, "Invalid workspace path")
        path = root / relative
        if any(part.is_symlink() for part in [path, *path.parents] if part.is_relative_to(root)):
            fail(403, "Workspace symlinks cannot be opened")
        if not path.is_file() and not (allow_directory and path.is_dir()):
            fail(404, "Workspace file is unavailable")
        if relative.parts and not self._workspace_path_allowed(db, member, path, relative):
            fail(404, "Workspace file is unavailable")
        return path

    def _workspace_path_allowed(self, db, member, path, relative, *, nodes=None, attachments=None):
        nodes = list(db.scalars(select(WorkspaceNode).where(WorkspaceNode.chat_id == member.chat_id))) if nodes is None else nodes
        attachments = list(db.scalars(select(ChatAttachment).where(ChatAttachment.chat_id == member.chat_id))) if attachments is None else attachments
        ancestors = [node for node in nodes if relative.is_relative_to(Path(node.relative_path))]
        if any(not self.workspace_node_allowed(db, member, node) for node in ancestors):
            return False
        matched_attachment = False
        for attachment in attachments:
            attached_path = Path(attachment.relative_path)
            if not attached_path.is_relative_to(member.chat_id):
                continue
            attached_path = attached_path.relative_to(member.chat_id)
            if attached_path != relative and not (path.is_dir() and attached_path.is_relative_to(relative)):
                continue
            matched_attachment = True
            if attachment.private_owner_input and attachment.owner_id != member.owner_id:
                continue
            if attachment.message_id:
                if not self.visible_message(db, member, attachment.message_id, False):
                    continue
            elif attachment.owner_id != member.owner_id:
                continue
            return True
        if matched_attachment and path.is_file():
            return False
        if ancestors:
            return True
        known_descendants = [node for node in nodes if Path(node.relative_path).is_relative_to(relative)] if path.is_dir() else []
        if any(self.workspace_node_allowed(db, member, node) for node in known_descendants):
            return True
        if relative.parts[0] in {"uploads", ".attachments"} or any(part.startswith(".") for part in relative.parts):
            return False
        created = getattr(path.stat(), "st_birthtime", None)
        discovered = WorkspaceNode(source_message_ids=[], granted_owner_ids=[], shared_with_chat=True,
            created_at=datetime.fromtimestamp(created, UTC) if created is not None else None)
        return self.workspace_node_allowed(db, member, discovered)

    def _workspace_db(self, db, owner_id, chat_id, relative=""):
        import base64
        _, member = self.membership(db, owner_id, chat_id)
        root = settings.workspace_root / chat_id
        root.mkdir(parents=True, exist_ok=True)
        parent = self._workspace_path_db(db, owner_id, chat_id, relative, allow_directory=True)
        if not parent.is_dir():
            fail(400, "Workspace path must be a directory")
        nodes = list(db.scalars(select(WorkspaceNode).where(WorkspaceNode.chat_id == chat_id)))
        attachments = list(db.scalars(select(ChatAttachment).where(ChatAttachment.chat_id == chat_id)))
        entries = []
        for path in sorted(parent.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold())):
            local = path.relative_to(root)
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                continue
            if not self._workspace_path_allowed(db, member, path, local, nodes=nodes, attachments=attachments):
                continue
            node = next((node for node in nodes if Path(node.relative_path) == local), None)
            identifier = node.id if node else "path:" + base64.urlsafe_b64encode(str(local).encode()).decode().rstrip("=")
            entries.append({"id": identifier, "name": path.name, "kind": "folder" if path.is_dir() else "file",
                            "path": str(local), "workspace_path": str(path), "size": None if path.is_dir() else path.stat().st_size})
        return entries

    def record_activity(self, chat_id, owner_id, session_id, run_id, values):
        with self.transaction() as db:
            chat = db.scalar(select(Chat).where(Chat.id == chat_id).with_for_update())
            generation = values.get("private_generation", 0)
            if chat.kind == "private" and (chat.reset_pending or generation != chat.reset_generation):
                return
            row = db.get(ActivityProjection, (session_id, run_id))
            if not row:
                row = ActivityProjection(session_id=session_id, run_id=run_id, chat_id=chat_id, owner_id=owner_id, private_generation=generation)
                db.add(row)
            for name in ("parent_run_id", "source", "status", "title", "summary", "started_at", "finished_at"):
                if values.get(name) is not None:
                    setattr(row, name, values[name])

    def _activity_scope(self, owner_id, chat_id, session_id=None, run_id=None):
        with self.transaction() as db:
            chat, _ = self.membership(db, owner_id, chat_id)
            if chat.kind != "private" or chat.private_owner_id != owner_id:
                fail(403, "Activity is limited to your current private chat and its child runs")
            if session_id and run_id:
                row = db.get(ActivityProjection, (session_id, run_id))
                if not row or row.chat_id != chat_id or row.owner_id != owner_id or row.private_generation != chat.reset_generation:
                    fail(404, "Activity run not found")
                return self._activity_json(row)

    def _activity_json(self, row):
        return {name: iso(getattr(row, name)) if name in ("started_at", "finished_at") else getattr(row, name) for name in ("session_id", "run_id", "parent_run_id", "source", "status", "title", "summary", "started_at", "finished_at")}

    def activity_page(self, owner_id, chat_id, limit=50, before=None):
        self._activity_scope(owner_id, chat_id)
        with self.transaction() as db:
            query = select(ActivityProjection).join(Chat, Chat.id == ActivityProjection.chat_id).where(ActivityProjection.chat_id == chat_id, ActivityProjection.owner_id == owner_id, ActivityProjection.private_generation == Chat.reset_generation)
            if before:
                from sqlalchemy import tuple_
                query = query.where(tuple_(ActivityProjection.started_at, ActivityProjection.session_id, ActivityProjection.run_id) < tuple(before))
            rows = list(db.scalars(query.order_by(ActivityProjection.started_at.desc(), ActivityProjection.session_id.desc(), ActivityProjection.run_id.desc()).limit(limit + 1)))
            return {"items": [self._activity_json(row) for row in rows[:limit]], "has_more": len(rows) > limit, "next_cursor": [iso(rows[limit-1].started_at), rows[limit-1].session_id, rows[limit-1].run_id] if len(rows) > limit else None}

    async def activity(self, owner_id, chat_id, session_id=None, run_id=None, limit=100, after_seq=-1):
        run = await self.db_call("_activity_scope", owner_id, chat_id, session_id, run_id)
        if session_id and run_id:
            if not self.runtime or not self.runtime.store:
                fail(503, "Runtime journal unavailable")
            records = await self.runtime.store.read_run_records(settings.tenant_id, session_id, run_id, after_seq=after_seq, limit=limit + 1)
            return {"run": run, "events": [event for record in records[:limit] if (event := self.runtime._event_view(record)) is not None], "has_more": len(records) > limit, "next_seq": records[limit-1]["seq"] if len(records) > limit else None}
        return (await self.db_call("activity_page", owner_id, chat_id, limit))["items"]


service = ProductService()


def bind_runtime(runtime):
    service.bind_runtime(runtime)
