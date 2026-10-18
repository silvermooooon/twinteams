"""Persisted product permissions, trigger decisions and FIFO scheduling intent.

The bound runtime owns model/tool execution, journal, fencing and recovery. This
module only keeps business requests, durable dispatch intent and safe projections.
"""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import threading
import unicodedata
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, inspect as sa_inspect, or_, select, text, update
from sqlalchemy.exc import IntegrityError

from .config import settings
from .db import Base, engine, SessionLocal
from .models import (Actor, Approval, AutomaticReservation, AllowanceGrant, CausalChain,
                     Chat, ChatSetting, ContextManifest, DispatchCommand, PendingChildResult,
                     Membership, Message, Owner, Presence, ProductOperation, Receipt, DatabaseIdentity, ViewInterval, DevelopmentSession,
                     Reminder, ReplyLane, ReplyRequest, ReplyProjection, RoutingDecision, Summary,
                     TrackingTopic, TrackingUpdate, TrackingRevision, TriggerCandidate, WorkspaceNode,
                     now, uid)


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
        self.lock = threading.RLock()
        self.subscribers: dict[str, set[asyncio.Queue]] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.scheduler_task = None
        self.routing_task = None
        self.routing_jobs = {}
        self.stopping = False
        self.loop = None

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

    @contextmanager
    def transaction(self):
        with self.lock, SessionLocal() as db, db.begin():
            if engine.dialect.name == "sqlite":
                # SQLite has no SELECT FOR UPDATE. Serialize product transactions
                # across processes before their first read, avoiding stale upgrades.
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            yield db

    def initialize(self, create_schema=False):
        if settings.local_identity_enabled or create_schema:
            Base.metadata.create_all(engine)
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
                "tt_messages": {"causal_chain_id": "VARCHAR(80) REFERENCES tt_causal_chains(id)"},
                "tt_reply_requests": {"causal_chain_id": "VARCHAR(80) REFERENCES tt_causal_chains(id)", "automatic_reservation_id": "VARCHAR(80) REFERENCES tt_automatic_reservations(id)", "source_owner_id": "VARCHAR(80) REFERENCES tt_owners(id)"},
                "tt_trigger_candidates": {"causal_chain_id": "VARCHAR(80) REFERENCES tt_causal_chains(id)"},
                "tt_routing_decisions": {"needs_response": "BOOLEAN", "reason": "TEXT"},
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

    def seed_local(self):
        with self.transaction() as db:
            for slug, name, initials, color in [("alex", "AAA", "AA", "#7962bd"), ("maya", "BBB", "BB", "#428b7d"), ("jordan", "CCC", "CC", "#b5784d")]:
                owner_id = f"owner-{slug}"
                if db.get(Owner, owner_id):
                    human = db.get(Actor, owner_id)
                    twin = db.get(Actor, f"twin-{slug}")
                    if human:
                        human.name, human.initials = name, initials
                    if twin:
                        twin.name = f"{name}'s Twin"
                    private = db.get(Chat, f"private-{slug}")
                    if private:
                        private.title = f"{name}'s Twin"
                    continue
                db.add(Owner(id=owner_id, auth_subject=f"local:{slug}"))
                db.flush()
                db.add_all([Actor(id=owner_id, owner_id=owner_id, kind="human", name=name, initials=initials, color=color), Actor(id=f"twin-{slug}", owner_id=owner_id, kind="twin", name=f"{name.split()[0]}'s Twin", initials=f"{initials[0]}T", color="#6dbdad")])
                db.flush()
                chat = Chat(id=f"private-{slug}", kind="private", title=f"{name.split()[0]}'s Twin", private_owner_id=owner_id)
                db.add(chat)
                db.flush()
                db.add(Membership(chat_id=chat.id, owner_id=owner_id, history_policy="all", lower_seq=1))
            db.flush()
            if not db.get(Chat, "group-team"):
                db.add(Chat(id="group-team", kind="group", title="Team workspace"))
                db.flush()
                db.add_all([Membership(chat_id="group-team", owner_id=x, history_policy="all", lower_seq=1) for x in ["owner-alex", "owner-maya", "owner-jordan"]])
            seeded_pair = db.get(Chat, "group-alex-maya")
            if seeded_pair:
                seeded_pair.title = "AAA & BBB"
            if not seeded_pair:
                db.add(Chat(id="group-alex-maya", kind="group", title="AAA & BBB"))
                db.flush()
                db.add_all([Membership(chat_id="group-alex-maya", owner_id=x, history_policy="all", lower_seq=1) for x in ["owner-alex", "owner-maya"]])

    def bind_runtime(self, runtime):
        self.runtime = runtime

    @property
    def model_configured(self):
        return bool(self.runtime and not getattr(self.runtime, "startup_error", None) and getattr(self.runtime, "configured", False))

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
                fail(403, "Authenticated identity has no provisioned Twinteams owner")
            return owner.id

    def membership(self, db, owner_id, chat_id, lock=False):
        chat = db.scalar(select(Chat).where(Chat.id == chat_id).with_for_update(key_share=True)) if lock else db.get(Chat, chat_id)
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
            fail(409, "Owner has no paired twin")
        return twin

    def actor_json(self, actor):
        return {k: getattr(actor, k) for k in ("id", "kind", "owner_id", "name", "initials", "color")}

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
        db.scalar(select(Chat).where(Chat.id == message.chat_id).with_for_update(key_share=True))
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
        db.scalar(select(Chat).where(Chat.id == request.chat_id).with_for_update(key_share=True))
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
        elif request.source in {"auto", "private", "subagent_result"}:
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

    def visible_message(self, db, member, message_id, required=True):
        msg = db.get(Message, message_id)
        if not msg or msg.chat_id != member.chat_id or msg.seq < member.lower_seq or msg.state != "published":
            if required:
                fail(404, "Message is unavailable in your authorized history")
            return None
        if not self.message_sources_visible(db, member, msg):
            if required:
                fail(404, "Message derives from sources outside your authorized history")
            return None
        return msg

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
        for source_id in msg.source_message_ids or []:
            source = db.get(Message, source_id)
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
            if source.get("kind") == "chat_context":
                # Cross-chat output keeps the complete original context boundary.
                # Every reader must still hold the original history grant.
                origin_chat = db.get(Chat, source.get("chat_id"))
                origin_member = db.scalar(select(Membership).where(Membership.chat_id == source.get("chat_id"), Membership.owner_id == member.owner_id, Membership.left_at.is_(None)))
                if not origin_chat or not origin_member or (origin_chat.kind == "private" and origin_chat.private_owner_id != member.owner_id):
                    return False
                if not self.provenance_allowed(db, origin_member, source.get("sources", []), seen, cache):
                    return False
            elif source.get("kind") == "message":
                message = db.get(Message, source["id"])
                if not message or message.chat_id != member.chat_id or message.seq < member.lower_seq or message.state != "published" or not self.message_sources_visible(db, member, message, seen, cache):
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

    def message_json(self, db, msg, member):
        result = {k: getattr(msg, k) for k in ("id", "chat_id", "seq", "author_id", "kind", "body", "raw_owner_input", "request_id", "state")}
        actor = db.get(Actor, msg.author_id)
        result.update(author_owner_id=actor.owner_id if actor else None, source_owner_id=actor.owner_id if actor else None, author_name=actor.name if actor else "Unknown", causal_chain_id=msg.causal_chain_id)
        result["created_at"] = iso(msg.created_at)
        result["read_by"] = list(db.scalars(select(Receipt.owner_id).where(Receipt.message_id == msg.id)))
        if msg.reply_to_message_id:
            quoted = self.visible_message(db, member, msg.reply_to_message_id, False)
            if quoted:
                author = db.get(Actor, quoted.author_id)
                result["reply_to"] = {"id": quoted.id, "author_name": author.name if author else "Unknown", "body": quoted.body}
            else:
                result["reply_to"] = {"id": msg.reply_to_message_id, "author_name": "", "body": "Message unavailable", "unavailable": True}
        return result

    def request_json(self, db, request, owner_id):
        target = db.get(Actor, request.target_twin_id)
        owns = target is not None and target.owner_id == owner_id
        return {**{k: getattr(request, k) for k in ("id", "chat_id", "source_message_id", "target_twin_id", "requester_id", "source", "status", "error", "session_id", "run_id", "causal_chain_id", "source_owner_id", "automatic_reservation_id")}, "accepted_at": iso(request.accepted_at), "cancel_requested": request.cancel_requested, "can_cancel": not request.cancel_requested and ( (request.status == "queued" and (owns or request.requester_id == owner_id)) or (request.status == "running" and owns)), "can_retry": request.status == "failed" and (owns or request.requester_id == owner_id)}

    def chat_json(self, db, chat, member):
        rows = list(db.scalars(select(Message).where(Message.chat_id == chat.id, Message.seq >= member.lower_seq, Message.state == "published").order_by(Message.seq)))
        visible = [m for m in rows if self.message_sources_visible(db, member, m)]
        unread = sum(1 for m in visible if m.author_id != member.owner_id and db.get(Receipt, (m.id, member.owner_id)) is None)
        return {"id": chat.id, "kind": chat.kind, "title": chat.title, "member_owner_ids": [m.owner_id for m in self.members(db, chat.id)], "unread_count": unread, "last_message": visible[-1].body if visible else None}

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
            memberships = list(db.scalars(select(Membership).where(Membership.owner_id == owner_id, Membership.left_at.is_(None))))
            return {"current_owner_id": owner_id, "actors": [self.actor_json(a) for a in db.scalars(select(Actor).order_by(Actor.kind, Actor.name))], "chats": [self.chat_json(db, db.get(Chat, m.chat_id), m) for m in memberships], "reminders": [self.reminder_json(r) for r in db.scalars(select(Reminder).where(Reminder.owner_id == owner_id).order_by(Reminder.due_at))], "model_configured": self.model_configured, "development_mode": settings.local_identity_enabled}

    def people(self, owner_id, query):
        with self.transaction() as db:
            rows = db.scalars(select(Actor).where(Actor.kind == "human", Actor.name.ilike(f"%{query}%")).order_by(Actor.name))
            return [self.actor_json(a) for a in rows]

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
        others = list(dict.fromkeys([owner_id, *owner_ids]))
        if len(others) < 2:
            fail(400, "Group chats require at least two owners; use your private chat for your own Twin")
        with self.transaction() as db:
            names = list(db.scalars(select(Actor.name).where(Actor.id.in_(others), Actor.kind == "human")))
            chat = Chat(kind="group", title=title or ", ".join(names))
            db.add(chat)
            db.flush()
            self.add_members_locked(db, chat, others, history_policy)
            member = db.scalar(select(Membership).where(Membership.chat_id == chat.id, Membership.owner_id == owner_id))
            result = self.chat_json(db, chat, member)
        self.publish_event(chat.id, {"type": "changed"})
        return result

    def add_members(self, owner_id, chat_id, owner_ids, history_policy):
        with self.transaction() as db:
            chat, member = self.membership(db, owner_id, chat_id, True)
            if chat.kind != "group":
                fail(409, "Create a group chat to add paired owners and Twins; private chats stay private")
            self.add_members_locked(db, chat, owner_ids, history_policy)
            result = self.chat_json(db, chat, member)
        self.publish_event(chat_id, {"type": "changed"})
        return result

    def tracking_json(self, db, topic, member, details=False):
        if not topic.source_message_ids:
            cutoff = member.joined_at if member.history_policy == "no_prior" else member.joined_at - timedelta(days=7) if member.history_policy == "fixed_7d" else None
            if cutoff and aware(topic.created_at) < aware(cutoff):
                return None
        if topic.deleted_at or not self.sources_allowed(db, member, topic.source_message_ids, topic.source_floor_seq) or not self.provenance_allowed(db, member, topic.source_provenance):
            return None
        result = {**{k: getattr(topic, k) for k in ("id", "chat_id", "title", "description", "status", "revision")}, "created_at": iso(topic.created_at), "updated_at": iso(topic.updated_at)}
        if details:
            result["updates"] = [{"id": u.id, "body": u.body, "created_at": iso(u.created_at)} for u in db.scalars(select(TrackingUpdate).where(TrackingUpdate.topic_id == topic.id).order_by(TrackingUpdate.created_at)) if self.sources_allowed(db, member, u.source_message_ids, u.source_floor_seq) and self.provenance_allowed(db, member, u.source_provenance) and (u.source_message_ids or member.history_policy == "all" or aware(u.created_at) >= aware(member.joined_at) - timedelta(days=7 if member.history_policy == "fixed_7d" else 0))]
        return result

    def approval_json(self, row):
        return {**{k: getattr(row, k) for k in ("id", "chat_id", "approver_id", "title", "description", "target", "impact", "status")}, "created_at": iso(row.created_at)}

    def chat_detail(self, owner_id, chat_id):
        with self.transaction() as db:
            chat, member = self.membership(db, owner_id, chat_id)
            messages = list(db.scalars(select(Message).where(Message.chat_id == chat_id, Message.seq >= member.lower_seq, Message.state == "published").order_by(Message.seq)))
            requests = list(db.scalars(select(ReplyRequest).where(ReplyRequest.chat_id == chat_id, ReplyRequest.status.in_(["queued", "running", "failed"])).order_by(ReplyRequest.accepted_at, ReplyRequest.accepted_seq)))
            latest_generations = dict(db.execute(select(ReplyRequest.intent_key, func.max(ReplyRequest.generation)).where(ReplyRequest.chat_id == chat_id).group_by(ReplyRequest.intent_key)).all())
            requests = [r for r in requests if r.generation == latest_generations.get(r.intent_key)]
            requests = [r for r in requests if (r.source_message_id and self.visible_message(db, member, r.source_message_id, False)) or (r.source_message_id is None and r.requester_id == owner_id)]
            tracking = [self.tracking_json(db, t, member) for t in db.scalars(select(TrackingTopic).where(TrackingTopic.chat_id == chat_id, TrackingTopic.deleted_at.is_(None)).order_by(TrackingTopic.created_at))]
            members = [m.owner_id for m in self.members(db, chat_id)]
            return {"chat": self.chat_json(db, chat, member), "messages": [self.message_json(db, m, member) for m in messages if self.message_sources_visible(db, member, m)], "requests": [self.request_json(db, r, owner_id) for r in requests], "automatic_conversations": self.automatic_conversations(db, chat, member), "approvals": [self.approval_json(a) for a in db.scalars(select(Approval).where(Approval.chat_id == chat_id, Approval.approver_id == owner_id, Approval.status == "pending").order_by(Approval.created_at)) if self.sources_allowed(db, member, a.source_message_ids) and self.provenance_allowed(db, member, a.source_provenance)], "tracking": [t for t in tracking if t], "settings": self.settings_json(self.settings_row(db, owner_id, chat)), "members": [self.actor_json(a) for a in db.scalars(select(Actor).where(Actor.owner_id.in_(members)).order_by(Actor.kind, Actor.name))]}

    def automatic_conversations(self, db, chat, member):
        pending = {}
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
                replay = False
            result = {"id": chain.id, "causal_chain_id": chain.id, "spent": chain.spent, "allowance": chain.allowance, "remaining": max(0, chain.allowance - chain.spent), "continuation_allowance": grant.amount, "grant_id": grant.id, "already_applied": replay}
        self.process_candidates()
        self.process_pending_child_results()
        self.publish_event(chat_id, {"type": "changed"})
        return result

    def append_message(self, db, chat, **values):
        msg = Message(chat_id=chat.id, seq=chat.next_message_seq, **values)
        chat.next_message_seq += 1
        db.add(msg)
        db.flush()
        return msg

    def accept_request(self, db, chat, target_twin_id, requester_id, source, key, source_message_id=None, raw=None, prompt=None, reply_to=None, generation=0, intent_key=None, input_body=None, causal_chain_id=None, automatic_reservation_id=None, source_owner_id=None):
        target = db.get(Actor, target_twin_id)
        if not target or target.kind != "twin":
            fail(400, "Target must be a paired Twin")
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
        if source in {"auto", "private", "subagent_result"} and not reservation:
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
        request = ReplyRequest(chat_id=chat.id, target_twin_id=target_twin_id, requester_id=requester_id, source=source, client_request_key=key, source_message_id=source_message_id, intent_key=intent_key, generation=generation, accepted_seq=lane.next_accept_seq, raw_owner_input=raw, frozen_prompt=prompt, reply_to_message_id=reply_to, input_body=input_body, causal_chain_id=chain.id, source_owner_id=source_owner_id, automatic_reservation_id=reservation.id if reservation else None)
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
        if message.state != "published" or chat.kind != "group":
            return
        if not db.scalar(select(RoutingDecision.id).where(RoutingDecision.message_id == message.id)):
            owners = self.members(db, chat.id)
            db.add(RoutingDecision(message_id=message.id, strategy="pair" if len(owners) == 2 else "llm"))

    def send_message(self, owner_id, chat_id, body, key, reply_to=None, reply_for_me=False):
        with self.transaction() as db:
            chat, member = self.membership(db, owner_id, chat_id, True)
            existing = db.scalar(select(Message).where(Message.command_owner_id == owner_id, Message.client_request_key == key))
            if existing:
                if existing.chat_id != chat_id or existing.body != body or existing.reply_to_message_id != reply_to:
                    fail(409, "Idempotency key was already used for a different message")
                return {"message": self.message_json(db, existing, member), "request": None}
            pending = db.scalar(select(ReplyRequest).where(ReplyRequest.requester_id == owner_id, ReplyRequest.client_request_key == key))
            if pending:
                if pending.chat_id != chat_id or pending.raw_owner_input != body:
                    fail(409, "Idempotency key was already used for a different request")
                return {"message": None, "request": self.request_json(db, pending, owner_id)}
            if reply_to:
                self.visible_message(db, member, reply_to)
            self.suppress_candidates(db, owner_id, chat_id, "owner_input")
            if reply_for_me:
                own_twin = self.twin(db, owner_id)
                config = self.settings_row(db, owner_id, chat)
                prompt = config.prompt_override if config.prompt_override is not None else db.get(Owner, owner_id).default_reply_prompt
                request = self.accept_request(db, chat, own_twin.id, owner_id, "reply_for_me", key, raw=body, prompt=prompt, reply_to=reply_to)
                result = {"message": None, "request": self.request_json(db, request, owner_id)}
            else:
                chain = self.new_causal_chain(db, owner_id)
                msg = self.append_message(db, chat, author_id=owner_id, kind="human", body=body, reply_to_message_id=reply_to, command_owner_id=owner_id, client_request_key=key, causal_chain_id=chain.id)
                chain.root_message_id = msg.id
                db.add(Receipt(message_id=msg.id, owner_id=owner_id))
                self.suppress_candidates(db, owner_id, chat_id, "human_reply")
                if chat.kind == "private":
                    request = self.accept_request(db, chat, self.twin(db, owner_id).id, owner_id, "private", f"auto:{msg.id}", source_message_id=msg.id)
                else:
                    self.route_committed_message(db, chat, msg)
                    request = None
                result = {"message": self.message_json(db, msg, member), "request": self.request_json(db, request, owner_id) if request else None}
        self.publish_event(chat_id, {"type": "changed"})
        return result

    def manual_request(self, owner_id, chat_id, message_id, target_twin_id, key):
        with self.transaction() as db:
            chat, member = self.membership(db, owner_id, chat_id, True)
            self.visible_message(db, member, message_id)
            request = self.accept_request(db, chat, target_twin_id, owner_id, "manual", key, source_message_id=message_id)
            result = self.request_json(db, request, owner_id)
        self.publish_event(chat_id, {"type": "changed"})
        return result

    def presence_state(self, db, owner_id, chat_id):
        leases = list(db.scalars(select(Presence).where(Presence.owner_id == owner_id, Presence.expires_at > now())))
        return bool(leases), any(p.visible and p.focused_chat_id == chat_id for p in leases)

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
            if visible and focused_chat_id:
                self.suppress_candidates(db, owner_id, focused_chat_id, "viewed_during_wait")
        return {"ok": True}

    def read(self, owner_id, chat_id, message_ids):
        with self.transaction() as db:
            _, member = self.membership(db, owner_id, chat_id, True)
            for message_id in dict.fromkeys(message_ids):
                self.visible_message(db, member, message_id)
                if not db.get(Receipt, (message_id, owner_id)):
                    db.add(Receipt(message_id=message_id, owner_id=owner_id))
            self.suppress_candidates(db, owner_id, chat_id, "read_during_wait", message_ids)
        self.publish_event(chat_id, {"type": "changed"})
        return {"ok": True}

    def create_candidates(self, db, chat, message, owner_ids):
        source_owner = self.message_owner(db, message)
        chain = self.message_chain(db, message)
        for target_owner in dict.fromkeys(owner_ids):
            if target_owner == source_owner:
                continue
            member = self.membership(db, target_owner, chat.id)[1]
            if not self.visible_message(db, member, message.id, False):
                continue
            twin = self.twin(db, target_owner)
            if db.scalar(select(TriggerCandidate).where(TriggerCandidate.message_id == message.id, TriggerCandidate.target_twin_id == twin.id)):
                continue
            online, viewing = self.presence_state(db, target_owner, chat.id)
            seen = db.get(Receipt, (message.id, target_owner)) is not None
            replied = db.scalar(select(Message.id).where(Message.chat_id == chat.id, Message.author_id == target_owner, Message.seq > message.seq).limit(1)) is not None
            viewed_in_window = db.scalar(select(ViewInterval.id).where(ViewInterval.owner_id == target_owner, ViewInterval.chat_id == chat.id, ViewInterval.ended_at >= message.created_at, ViewInterval.started_at <= now()).limit(1)) is not None
            state = "suppressed" if viewing or seen or replied or viewed_in_window else "waiting"
            candidate = TriggerCandidate(chat_id=chat.id, message_id=message.id, target_twin_id=twin.id, causal_chain_id=chain.id, state=state, due_at=aware(message.created_at) + timedelta(seconds=settings.response_delay_seconds) if online else now(), evidence={"online": online, "viewing": viewing, "read": seen, "replied": replied, "observed_at": iso(now())})
            db.add(candidate)
            db.flush()
            prior = db.scalar(select(ReplyRequest).where(ReplyRequest.intent_key == f"message:{message.id}:{twin.id}", ReplyRequest.generation == 0))
            if prior:
                candidate.state, candidate.accepted_request_id = "accepted", prior.id
            elif state == "waiting" and (not online or aware(candidate.due_at) <= now()):
                self.accept_request(db, chat, twin.id, source_owner, "auto", f"auto:{message.id}:{twin.id}", source_message_id=message.id, causal_chain_id=chain.id)

    async def route_pending(self, decision_ids=None):
        with self.transaction() as db:
            rows = list(db.scalars(select(RoutingDecision).where(RoutingDecision.status == "pending")))
            ids = [r.id for r in rows if decision_ids is None or r.id in decision_ids]
        for decision_id in ids:
            with self.transaction() as db:
                decision = db.get(RoutingDecision, decision_id)
                message = db.get(Message, decision.message_id)
                if not message or message.state != "published":
                    decision.status, decision.error = "failed", "Committed source message is unavailable"
                    continue
                source_owner = self.message_owner(db, message)
                try:
                    chat, member = self.membership(db, source_owner, message.chat_id)
                except HTTPException:
                    decision.status, decision.error = "failed", "Source owner no longer has chat access"
                    continue
                owners = [m.owner_id for m in self.members(db, chat.id)]
                context = [self.message_json(db, m, member) for m in db.scalars(select(Message).where(Message.chat_id == chat.id, Message.seq >= member.lower_seq, Message.seq <= message.seq, Message.state == "published").order_by(Message.seq)) if self.message_sources_visible(db, member, m)]
                payload = self.message_json(db, message, member)
                mentioned = [a.owner_id for a in db.scalars(select(Actor).where(Actor.kind == "human", Actor.owner_id.in_(owners))) if f"@{a.name}".casefold() in message.body.casefold() or f"@{a.id}" in message.body]
                decision.mentioned_owner_ids = mentioned
            error = None
            try:
                if not self.runtime or not self.model_configured:
                    raise RuntimeError("Automatic multi-person routing requires a configured model; use Ask a Twin manually")
                routed = await resolve(self.runtime.route_targets(chat.id, payload, context, owners))
                if not isinstance(routed, dict) or type(routed.get("needs_response")) is not bool or not isinstance(routed.get("target_owner_ids"), list) or not all(isinstance(value, str) for value in routed["target_owner_ids"]):
                    raise RuntimeError("Router must return needs_response and a valid target_owner_ids set")
                inferred = routed["target_owner_ids"]
                needs_response = routed["needs_response"]
                reason = str(routed.get("reason", ""))[:2000]
            except Exception as exc:
                from .runtime_bridge import RuntimeBusy
                from agent_runtime.storage.coordination import ExecutionLeaseError
                if isinstance(exc, (RuntimeBusy, ExecutionLeaseError)):
                    continue  # Keep pending: another holder owns the canonical result.
                inferred, needs_response, reason, error = [], False, None, str(exc)
            with self.transaction() as db:
                decision = db.get(RoutingDecision, decision_id)
                if decision.status != "pending":
                    continue
                message = db.get(Message, decision.message_id)
                chat = db.scalar(select(Chat).where(Chat.id == message.chat_id).with_for_update(key_share=True))
                source_owner = self.message_owner(db, message)
                current_owners = [m.owner_id for m in self.members(db, chat.id)]
                eligible = [owner for owner in current_owners if owner != source_owner]
                decision.inferred_owner_ids = eligible if needs_response and len(current_owners) == 2 else list(dict.fromkeys(x for x in inferred if x in eligible))
                decision.needs_response, decision.reason = needs_response, reason
                decision.status, decision.error = ("failed", error) if error else ("completed", None)
                # Mentions identify recipients; they do not force another response.
                targets = list(dict.fromkeys([*decision.inferred_owner_ids, *[x for x in decision.mentioned_owner_ids if x in eligible]])) if needs_response and not error else []
                if message.state == "published" and source_owner in current_owners:
                    self.create_candidates(db, chat, message, targets)
            self.publish_event(chat.id, {"type": "changed"})

    def process_candidates(self):
        changed = set()
        with self.transaction() as db:
            ids = list(db.scalars(select(TriggerCandidate.id).where(TriggerCandidate.state.in_(["waiting", "paused"]), TriggerCandidate.due_at <= now())))
        for candidate_id in ids:
            with self.transaction() as db:
                candidate = db.get(TriggerCandidate, candidate_id)
                if not candidate:
                    continue
                chat = db.scalar(select(Chat).where(Chat.id == candidate.chat_id).with_for_update(key_share=True))
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
                _, viewing = self.presence_state(db, target.owner_id, chat.id)
                member = db.scalar(select(Membership).where(Membership.chat_id == chat.id, Membership.owner_id == target.owner_id, Membership.left_at.is_(None)))
                seen = db.get(Receipt, (message.id, target.owner_id)) is not None
                replied = db.scalar(select(Message.id).where(Message.chat_id == chat.id, Message.author_id == target.owner_id, Message.seq > message.seq).limit(1))
                viewed_in_window = db.scalar(select(ViewInterval.id).where(ViewInterval.owner_id == target.owner_id, ViewInterval.chat_id == chat.id, ViewInterval.ended_at >= message.created_at, ViewInterval.started_at <= now()).limit(1)) is not None
                if viewing or viewed_in_window or seen or replied or not member or not self.visible_message(db, member, message.id, False):
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
        with self.transaction() as db:
            request = db.get(ReplyRequest, request_id)
            if not request:
                fail(404, "Reply request not found")
            self.membership(db, owner_id, request.chat_id, True)
            db.scalar(select(ReplyLane).where(ReplyLane.chat_id == request.chat_id, ReplyLane.twin_id == request.target_twin_id).with_for_update())
            db.refresh(request, with_for_update=True)
            target = db.get(Actor, request.target_twin_id)
            if owner_id not in {request.requester_id, target.owner_id}:
                fail(403, "Only the requester or target Twin owner may access cancellation")
            if request.status == "queued":
                if owner_id not in {request.requester_id, target.owner_id}:
                    fail(403, "Only the requester or target Twin's owner can cancel this queued request")
                request.status = "cancelled"
                command = db.get(DispatchCommand, request.id)
                command.state = "cancelled"
                result = self.request_json(db, request, owner_id)
                running = False
            elif request.status == "running":
                if owner_id != target.owner_id:
                    fail(403, "Only the target Twin's owner can stop a running reply")
                request.cancel_requested = True
                self.withdraw_outputs(db, request)
                command = db.get(DispatchCommand, request.id)
                command.kind, command.state = "cancel", "pending"
                payload = self.request_payload(request)
                running = True
            elif request.status == "cancelled":
                return self.request_json(db, request, owner_id)
            else:
                fail(409, "This request is already finished")
            chat_id = request.chat_id
        self.publish_event(chat_id, {"type": "changed", "request_id": request_id})
        if running:
            if not self.runtime:
                fail(503, "Runtime is unavailable; stop intent is saved for reconciliation")
            try:
                stop_result = await resolve(self.runtime.cancel(payload))
            except Exception as exc:
                fail(503, f"Stop intent saved, but runtime has not confirmed stopping: {exc}")
            if isinstance(stop_result, dict) and stop_result.get("terminal"):
                self.finish_request(request_id, "cancelled")
            with self.transaction() as db:
                result = self.request_json(db, db.get(ReplyRequest, request_id), owner_id)
        return result

    def retry_request(self, owner_id, request_id, key):
        with self.transaction() as db:
            old = db.get(ReplyRequest, request_id)
            if not old:
                fail(404, "Reply request not found")
            chat, member = self.membership(db, owner_id, old.chat_id, True)
            chain = self.request_chain(db, old)
            db.scalar(select(ReplyLane).where(ReplyLane.chat_id == old.chat_id, ReplyLane.twin_id == old.target_twin_id).with_for_update())
            db.refresh(old, with_for_update=True)
            target = db.get(Actor, old.target_twin_id)
            if owner_id not in {old.requester_id, target.owner_id}:
                fail(403, "Only the requester or target Twin's owner can retry")
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
            request = self.accept_request(db, chat, old.target_twin_id, owner_id, "retry", key, source_message_id=old.source_message_id, raw=old.raw_owner_input, prompt=old.frozen_prompt, reply_to=old.reply_to_message_id, generation=generation, intent_key=old.intent_key, input_body=old.input_body, causal_chain_id=chain.id, automatic_reservation_id=old.automatic_reservation_id, source_owner_id=old.source_owner_id)
            result = self.request_json(db, request, owner_id)
        self.publish_event(chat.id, {"type": "changed"})
        return result

    def request_payload(self, request):
        return {k: getattr(request, k) for k in ("id", "chat_id", "source_message_id", "target_twin_id", "requester_id", "source", "status", "session_id", "run_id", "raw_owner_input", "frozen_prompt", "input_body", "cancel_requested", "causal_chain_id", "automatic_reservation_id", "source_owner_id")}

    def get_request(self, request_id):
        with self.transaction() as db:
            request = db.get(ReplyRequest, request_id)
            if not request:
                fail(404, "Reply request not found")
            db.scalar(select(Chat).where(Chat.id == request.chat_id).with_for_update(key_share=True))
            db.refresh(request)
            self.request_chain(db, request)
            return self.request_payload(request)

    def get_runtime_context(self, request_id, include_completed=False):
        with self.transaction() as db:
            request = db.get(ReplyRequest, request_id)
            if not request or (not include_completed and (request.status not in {"queued", "running"} or request.cancel_requested)):
                fail(409, "Reply request is no longer executable")
            db.scalar(select(Chat).where(Chat.id == request.chat_id).with_for_update(key_share=True))
            db.refresh(request)
            self.request_chain(db, request)
            twin = db.get(Actor, request.target_twin_id)
            chat, member = self.membership(db, twin.owner_id, request.chat_id)
            if request.source_message_id:
                self.visible_message(db, member, request.source_message_id)
            previous_manifests = list(db.scalars(select(ContextManifest).where(ContextManifest.request_id == request.id)))
            previous_sources = {source["id"] for manifest in previous_manifests for source in manifest.sources if source.get("kind") == "message"}
            if not all(self.provenance_allowed(db, member, manifest.sources) for manifest in previous_manifests):
                fail(409, "Previously supplied runtime context is no longer authorized; explicit retry with a fresh request is required")
            rows = list(db.scalars(select(Message).where(Message.chat_id == chat.id, Message.seq >= member.lower_seq, Message.state == "published", or_(Message.request_id.is_(None), Message.request_id != request.id)).order_by(Message.seq)))
            rows = [m for m in rows if self.message_sources_visible(db, member, m)]
            sources = [{"kind": "message", "id": m.id, "seq": m.seq, "hash": digest([m.body, m.raw_owner_input])} for m in rows]
            summaries = list(db.scalars(select(Summary).where(Summary.chat_id == chat.id, Summary.state == "valid").order_by(Summary.coverage_end_seq.desc())))
            applicable = None
            for summary in summaries:
                if summary.coverage_start_seq <= member.lower_seq and self.sources_allowed(db, member, summary.source_message_ids) and self.provenance_allowed(db, member, summary.source_provenance):
                    covered = [m.id for m in rows if m.seq <= summary.coverage_end_seq]
                    if set(covered) == set(summary.source_message_ids):
                        applicable = {"id": summary.id, "body": summary.body, "coverage_end_seq": summary.coverage_end_seq, "source_message_ids": summary.source_message_ids}
                        sources.extend(summary.source_provenance or [])
                        break
            tracking = [self.tracking_json(db, topic, member) for topic in db.scalars(select(TrackingTopic).where(TrackingTopic.chat_id == chat.id, TrackingTopic.deleted_at.is_(None)))]
            for topic in db.scalars(select(TrackingTopic).where(TrackingTopic.chat_id == chat.id, TrackingTopic.deleted_at.is_(None))):
                if self.tracking_json(db, topic, member):
                    sources.extend(topic.source_provenance or [])
                    sources.append({"kind": "tracking", "id": topic.id, "revision": topic.revision, "floor_seq": topic.source_floor_seq, "created_at": iso(topic.created_at), "source_message_ids": topic.source_message_ids})
            manifest = ContextManifest(request_id=request.id, owner_id=twin.owner_id, grant_version=member.grant_version, high_water_seq=chat.next_message_seq - 1, sources=sources, source_digest=digest(sources))
            db.add(manifest)
            request.context_grant_version = member.grant_version
            db.flush()
            return {"tenant_id": settings.tenant_id, "owner_id": twin.owner_id, "target_twin_name": twin.name, "chat": {"id": chat.id, "kind": chat.kind, "title": chat.title}, "messages": [self.message_json(db, m, member) for m in rows if not applicable or m.seq > applicable["coverage_end_seq"]], "source_message": self.message_json(db, self.visible_message(db, member, request.source_message_id), member) if request.source_message_id else None, "tracking": [t for t in tracking if t], "summary": applicable, "prompt": request.frozen_prompt or "", "input_body": request.input_body, "request_id": request.id, "origin_request_id": request.id, "causal_chain_id": request.causal_chain_id, "source_owner_id": request.source_owner_id, "requester_id": request.requester_id, "target_twin_id": request.target_twin_id, "chat_id": chat.id, "raw_owner_input": request.raw_owner_input, "source_message_id": request.source_message_id, "context_manifest_id": manifest.id, "history_lower_seq": member.lower_seq, "high_water_seq": chat.next_message_seq - 1}

    def bind_runtime_run(self, request_id, session_id, run_id):
        with self.transaction() as db:
            request = db.get(ReplyRequest, request_id)
            if not request:
                fail(404, "Reply request not found")
            self.membership(db, db.get(Actor, request.target_twin_id).owner_id, request.chat_id, True)
            db.scalar(select(ReplyLane).where(ReplyLane.chat_id == request.chat_id, ReplyLane.twin_id == request.target_twin_id).with_for_update())
            db.refresh(request, with_for_update=True)
            if request.session_id and request.session_id != session_id:
                fail(409, "Runtime session conflicts with accepted request")
            if request.run_id and request.run_id != run_id:
                fail(409, "Runtime run conflicts with accepted request")
            request.session_id, request.run_id = session_id, run_id
            lane = db.get(ReplyLane, (request.chat_id, request.target_twin_id))
            lane.session_id = session_id
        self.publish_event(request.chat_id, {"type": "changed", "request_id": request_id})

    def publish_runtime_output(self, request_id, body, session_id, run_id, event_id=None, event_seq=None):
        if not body or not body.strip():
            return None
        with self.transaction() as db:
            request = db.get(ReplyRequest, request_id)
            if not request:
                fail(404, "Reply request not found")
            chat = db.scalar(select(Chat).where(Chat.id == request.chat_id).with_for_update(key_share=True))
            self.request_chain(db, request)
            db.scalar(select(ReplyLane).where(ReplyLane.chat_id == request.chat_id, ReplyLane.twin_id == request.target_twin_id).with_for_update())
            db.refresh(request, with_for_update=True)
            if request.cancel_requested or request.status in {"cancelled", "failed"}:
                return None
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
                for summary in db.scalars(select(Summary).where(Summary.chat_id == chat.id, Summary.state == "valid")):
                    if msg.id in summary.source_message_ids:
                        summary.state = "stale"
            else:
                msg = self.append_message(db, chat, source_provenance=provenance, source_message_ids=source_ids, source_floor_seq=min([db.get(Message, sid).seq for sid in source_ids], default=chat.next_message_seq), author_id=twin.id, kind="reply_for_me" if is_reply_for_me else "twin", body=body, raw_owner_input=request.raw_owner_input, reply_to_message_id=request.reply_to_message_id or request.source_message_id, request_id=request.id, sdk_output_key=output_key, causal_chain_id=self.request_chain(db, request).id)
                if is_reply_for_me:
                    db.add(ReplyProjection(request_id=request.id, message_id=msg.id, session_id=session_id, run_id=run_id, last_event_id=event_id, last_event_seq=event_seq))
            result = msg.id
        self.publish_event(chat.id, {"type": "changed", "request_id": request_id})
        return result

    def withdraw_outputs(self, db, request):
        ids = []
        for message in db.scalars(select(Message).where(Message.request_id == request.id, Message.chat_id == request.chat_id, or_(Message.sdk_output_key.is_(None), ~Message.sdk_output_key.like("tool:%")))):
            message.state = "withdrawn"
            ids.append(message.id)
        if ids:
            for summary in db.scalars(select(Summary).where(Summary.chat_id == request.chat_id, Summary.state == "valid")):
                if set(ids) & set(summary.source_message_ids):
                    summary.state = "revoked"
            # Tracking projection's source checks hide withdrawn dependencies immediately.

    def finish_request(self, request_id, status, error=None, session_id=None, run_id=None, route_message_id=None):
        with self.transaction() as db:
            request = db.get(ReplyRequest, request_id)
            if not request:
                return
            db.scalar(select(Chat).where(Chat.id == request.chat_id).with_for_update(key_share=True))
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
                    self.route_committed_message(db, db.get(Chat, request.chat_id), message)
            if session_id:
                request.session_id = session_id
            if run_id:
                request.run_id = run_id
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
        self.finish_request(request_id, "completed", session_id=result.get("session_id"), run_id=result.get("run_id"), route_message_id=final_message_id)

    def fail_request(self, request_id, error):
        self.finish_request(request_id, "failed", str(error))

    def claim_dispatches(self):
        claimed = []
        with self.transaction() as db:
            lanes = list(db.scalars(select(ReplyLane).with_for_update(skip_locked=True)))
            for lane in lanes:
                if lane.active_request_id:
                    request = db.get(ReplyRequest, lane.active_request_id)
                    if request and request.status == "running" and request.id not in self.tasks:
                        claimed.append(request.id)
                    elif not request or request.status != "running":
                        lane.active_request_id = None
                    continue
                request = db.scalar(select(ReplyRequest).where(ReplyRequest.chat_id == lane.chat_id, ReplyRequest.target_twin_id == lane.twin_id, ReplyRequest.status == "queued").order_by(ReplyRequest.accepted_seq).limit(1).with_for_update())
                if request:
                    lane.active_request_id, request.status = request.id, "running"
                    command = db.get(DispatchCommand, request.id)
                    command.state, command.attempts, command.updated_at = "dispatched", command.attempts + 1, now()
                    claimed.append(request.id)
        return claimed

    async def execute_request(self, request_id):
        try:
            payload = self.get_request(request_id)
            if payload["cancel_requested"]:
                if not self.runtime:
                    raise RuntimeError("Runtime unavailable while reconciling saved stop intent")
                stop_result = await resolve(self.runtime.cancel(payload))
                if isinstance(stop_result, dict) and stop_result.get("terminal"):
                    self.finish_request(request_id, "cancelled")
                else:
                    await asyncio.sleep(1)
                return
            if self.runtime and getattr(self.runtime, "startup_error", None):
                raise RuntimeError(f"Runtime storage is unavailable: {self.runtime.startup_error}")
            if not self.runtime or not self.model_configured:
                raise RuntimeError("Model provider is not configured. Set the supported provider API key and restart; no generated reply was produced.")
            context = self.get_runtime_context(request_id)
            self.publish_event(payload["chat_id"], {"type": "status", "request_id": request_id, "text": "running"})
            result = await resolve(self.runtime.execute(payload, context))
            if not isinstance(result, dict) or not result.get("session_id") or not result.get("run_id"):
                raise RuntimeError("Runtime returned no durable session/run identity")
            self.complete_request(request_id, result)
        except asyncio.CancelledError:
            # Shutdown leaves durable running intent intact for SDK reconciliation on restart.
            raise
        except Exception as exc:
            from .runtime_bridge import RuntimeBusy
            from agent_runtime.storage.coordination import ExecutionLeaseError
            if isinstance(exc, (RuntimeBusy, ExecutionLeaseError)):
                # A live SDK lease is not a failed/finished business request. Preserve
                # the exact active lane, including concurrent stop/completion changes.
                await asyncio.sleep(1)
            else:
                current = self.get_request(request_id)
                if current.get("cancel_requested"):
                    stop_result = await resolve(self.runtime.cancel(current)) if self.runtime else {}
                    if isinstance(stop_result, dict) and stop_result.get("terminal"):
                        self.finish_request(request_id, "cancelled")
                        return
                    with self.transaction() as db:
                        db.get(ReplyRequest, request_id).error = f"Stop awaiting runtime reconciliation: {exc}"
                    await asyncio.sleep(1)
                else:
                    self.fail_request(request_id, str(exc))
        finally:
            self.tasks.pop(request_id, None)

    async def scheduler(self):
        while not self.stopping:
            try:
                self.process_candidates()
                self.process_pending_child_results()
                if self.runtime and getattr(self.runtime, "store", None):
                    await self.reconcile_approvals()
                for request_id in self.claim_dispatches():
                    if request_id not in self.tasks:
                        self.tasks[request_id] = asyncio.create_task(self.execute_request(request_id))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                import logging
                logging.getLogger(__name__).exception("Product scheduler failed: %s", exc)
            await asyncio.sleep(0.4)

    async def route_scheduler(self):
        while not self.stopping:
            try:
                for key, task in list(self.routing_jobs.items()):
                    if task.done():
                        self.routing_jobs.pop(key)
                        if not task.cancelled() and (error := task.exception()):
                            import logging
                            logging.getLogger(__name__).error("Routing task failed: %s", error)
                with self.transaction() as db:
                    ids = list(db.scalars(select(RoutingDecision.id).where(RoutingDecision.status == "pending").order_by(RoutingDecision.created_at)))
                for decision_id in ids:
                    if len(self.routing_jobs) >= 4:
                        break
                    if decision_id not in self.routing_jobs:
                        self.routing_jobs[decision_id] = asyncio.create_task(self.route_pending({decision_id}))
            except asyncio.CancelledError:
                raise
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Routing reconciliation failed")
            await asyncio.sleep(0.4)

    async def start(self):
        self.loop = asyncio.get_running_loop()
        self.stopping = False
        self.scheduler_task = asyncio.create_task(self.scheduler())
        self.routing_task = asyncio.create_task(self.route_scheduler())

    async def close(self):
        self.stopping = True
        tasks = [t for t in [self.scheduler_task, self.routing_task, *self.routing_jobs.values(), *self.tasks.values()] if t]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
        self.routing_jobs.clear()

    def publish_event(self, chat_id, event):
        payload = {**event, "chat_id": chat_id}
        def deliver():
            for queue in list(self.subscribers.get(chat_id, set())):
                try:
                    queue.put_nowait(payload)
                except asyncio.QueueFull:
                    pass  # Canonical state is reloaded by heartbeat; deltas are disposable.
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(deliver)
        else:
            deliver()

    async def events(self, owner_id, chat_id, local_session=None):
        with self.transaction() as db:
            self.membership(db, owner_id, chat_id)
        queue = asyncio.Queue(maxsize=200)
        self.subscribers.setdefault(chat_id, set()).add(queue)
        try:
            yield "data: " + json.dumps({"type": "changed", "chat_id": chat_id}) + "\n\n"
            while True:
                if settings.local_identity_enabled:
                    self.authenticate_local(local_session)
                with self.transaction() as db:
                    self.membership(db, owner_id, chat_id)
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=3)
                except TimeoutError:
                    event = {"type": "changed", "chat_id": chat_id}
                if event.get("type") in {"token", "status", "operation_started", "output_committed"} and event.get("request_id"):
                    with self.transaction() as db:
                        _, member = self.membership(db, owner_id, chat_id)
                        request = db.get(ReplyRequest, event["request_id"])
                        if not request or request.chat_id != chat_id or (request.raw_owner_input is not None and request.requester_id != owner_id):
                            continue
                        if request.source_message_id and not self.visible_message(db, member, request.source_message_id, False):
                            continue
                        manifests = list(db.scalars(select(ContextManifest).where(ContextManifest.request_id == request.id)))
                        if not all(self.provenance_allowed(db, member, manifest.sources) for manifest in manifests):
                            continue
                yield "data: " + json.dumps(event) + "\n\n"
        finally:
            self.subscribers.get(chat_id, set()).discard(queue)

    def runtime_bindings(self):
        with self.transaction() as db:
            result = []
            for lane in db.scalars(select(ReplyLane).where(ReplyLane.session_id.is_not(None))):
                target = db.get(Actor, lane.twin_id)
                member = db.scalar(select(Membership).where(Membership.chat_id == lane.chat_id, Membership.owner_id == target.owner_id, Membership.left_at.is_(None)))
                if not member:
                    continue
                result.append({"chat_id": lane.chat_id, "target_twin_id": lane.twin_id, "owner_id": target.owner_id, "session_id": lane.session_id})
            return result

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

    def _accept_pending_child(self, db, pending):
        if pending.accepted_request_id:
            request = db.get(ReplyRequest, pending.accepted_request_id)
            return self.request_payload(request) if request else None
        chat, member = self.membership(db, pending.owner_id, pending.chat_id, True)
        original = db.get(ReplyRequest, pending.parent_request_id)
        if not original or original.cancel_requested or original.status == "cancelled":
            pending.state, pending.pause_reason = "paused", "parent_stopped"
            return {"id": pending.id, "status": "paused", "durably_retained": True, "causal_chain_id": pending.causal_chain_id, "reason": pending.pause_reason}
        if not self.provenance_allowed(db, member, pending.source_provenance):
            fail(409, "Child result inherited revoked parent sources")
        key = f"child:{digest(pending.id)}"
        request = self.accept_request(db, chat, pending.target_twin_id, pending.requester_id, "subagent_result", key,
                                      source_message_id=pending.source_message_id, reply_to=pending.source_message_id,
                                      intent_key=key, input_body=pending.body, causal_chain_id=pending.causal_chain_id,
                                      source_owner_id=pending.owner_id)
        if request is None:
            # No second reservation attempt: derive the same deterministic reason.
            repeated = db.scalar(select(AutomaticReservation.id).where(AutomaticReservation.causal_chain_id == pending.causal_chain_id, AutomaticReservation.source_owner_id == pending.owner_id, AutomaticReservation.target_owner_id == pending.owner_id, AutomaticReservation.content_hash == self.normalized_content_hash(pending.body)))
            pending.state, pending.pause_reason = "paused", "repeated_content" if repeated else "budget_exhausted"
            return {"id": pending.id, "status": "paused", "durably_retained": True, "causal_chain_id": pending.causal_chain_id, "reason": pending.pause_reason}
        if pending.source_provenance and not db.scalar(select(ContextManifest.id).where(ContextManifest.request_id == request.id).limit(1)):
            db.add(ContextManifest(request_id=request.id, owner_id=pending.owner_id, grant_version=member.grant_version, high_water_seq=chat.next_message_seq - 1, sources=pending.source_provenance, source_digest=digest(pending.source_provenance)))
        pending.state, pending.pause_reason, pending.accepted_request_id = "accepted", None, request.id
        return self.request_payload(request)

    def accept_child_result(self, notification, parent_context):
        with self.transaction() as db:
            original = db.get(ReplyRequest, parent_context.get("request_id"))
            if not original:
                fail(403, "Child result has no durable parent request")
            db.scalar(select(Chat).where(Chat.id == original.chat_id).with_for_update(key_share=True))
            db.refresh(original)
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

    def process_pending_child_results(self):
        changed = set()
        with self.transaction() as db:
            ids = list(db.scalars(select(PendingChildResult.id).where(PendingChildResult.state.in_(["pending", "paused"])).order_by(PendingChildResult.created_at)))
        for pending_id in ids:
            with self.transaction() as db:
                pending = db.get(PendingChildResult, pending_id)
                if not pending:
                    continue
                db.scalar(select(Chat).where(Chat.id == pending.chat_id).with_for_update(key_share=True))
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
            parent = root / node_id
            if root.is_symlink() or not root.resolve().is_relative_to(settings.workspace_root):
                fail(403, "Invalid workspace root")
            parent.mkdir(parents=True, exist_ok=False)
            path = parent / filename
            try:
                with path.open("xb") as file:
                    file.write(data)
                db.add(WorkspaceNode(id=node_id, chat_id=chat_id, relative_path=f"{node_id}/{filename}", source_message_ids=source_ids or [], granted_owner_ids=[m.owner_id for m in self.members(db, chat_id)]))
                db.flush()
            except Exception:
                path.unlink(missing_ok=True)
                parent.rmdir()
                raise
            result = {"id": node_id, "name": filename, "kind": "file", "path": f"{node_id}/{filename}", "size": len(data)}
        self.publish_event(chat_id, {"type": "changed"})
        return result

    def workspace_file(self, owner_id, chat_id, node_id):
        with self.transaction() as db:
            _, member = self.membership(db, owner_id, chat_id)
            node = db.get(WorkspaceNode, node_id)
            if not node or node.chat_id != chat_id or not self.workspace_node_allowed(db, member, node):
                fail(404, "Workspace file is unavailable")
            root_path = settings.workspace_root / chat_id
            if root_path.is_symlink():
                fail(403, "Workspace symlinks cannot be opened")
            root = root_path.resolve()
            relative = Path(node.relative_path)
            if relative.is_absolute() or ".." in relative.parts:
                fail(403, "Invalid workspace path")
            path = root / relative
            if any(part.is_symlink() for part in [path, *path.parents] if part.is_relative_to(settings.workspace_root)):
                fail(403, "Workspace symlinks cannot be opened")
            path = path.resolve()
            if not path.is_relative_to(root) or not path.is_file():
                fail(404, "Workspace file is unavailable")
            return path

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
            return {"messages": [self.message_json(db, m, member) for m in reversed(rows)], "history_lower_seq": member.lower_seq, "next_before_seq": rows[-1].seq if len(rows) == limit else None}
        if name == "list_tracking":
            topics = [self.tracking_json(db, t, member) for t in db.scalars(select(TrackingTopic).where(TrackingTopic.chat_id == chat.id, TrackingTopic.deleted_at.is_(None)).order_by(TrackingTopic.created_at))]
            return {"tracking": [t for t in topics if t]}
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
            action = args.get("action")
            if not isinstance(action, dict) or action.get("tool") != "create_reminder" or set(action) != {"tool", "arguments"}:
                fail(400, "Only the installed create_reminder action can currently request approval")
            arguments = action.get("arguments")
            if not isinstance(arguments, dict) or not arguments.get("title") or set(arguments) - {"title", "detail", "due_at"}:
                fail(400, "Approval requires exact reminder arguments")
            if not context.get("runtime_session_id") or not context.get("runtime_run_id") or not context.get("request_id"):
                fail(400, "Approval requires a durable originating run")
            manifest = db.get(ContextManifest, context.get("context_manifest_id")) if context.get("context_manifest_id") else None
            sources = [s["id"] for s in manifest.sources if s.get("kind") == "message"] if manifest else []
            reference = {"session_id": context["runtime_session_id"], "run_id": context["runtime_run_id"], "request_id": context["request_id"], "action": action}
            approval = Approval(id=uid(), chat_id=chat.id, source_provenance=context.get("inherited_provenance", []), source_message_ids=sources, approver_id=owner_id, title=str(args["title"])[:256], description=str(args["description"]), target=str(args["target"]), impact=str(args["impact"]), sdk_reference=reference)
            db.add(approval)
            db.flush()
            return self.approval_json(approval)
        if name == "get_approval":
            approval = db.get(Approval, args["id"])
            if not approval or approval.chat_id != chat.id or approval.approver_id != owner_id or not self.sources_allowed(db, member, approval.source_message_ids) or not self.provenance_allowed(db, member, approval.source_provenance):
                fail(404, "Approval not found")
            return self.approval_json(approval)
        if name == "create_reminder":
            requester = context.get("request_requester_id", owner_id)
            if requester != owner_id and not context.get("approved_approval_id"):
                fail(403, "Another participant cannot create personal reminders for this Twin's owner")
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
        chat_ids = {request.chat_id, destination_id}
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
                elif source.get("kind") == "message":
                    visit_message(source.get("id"))
                elif source.get("kind") == "tracking":
                    for source_id in source.get("source_message_ids", []):
                        visit_message(source_id)
        visit_sources(self.manifest_provenance(db, request.id))
        for message_id in explicit_source_ids:
            if isinstance(message_id, str):
                visit_message(message_id)
        return chat_ids

    def _lock_twin_message_scope(self, db, args, context):
        request = db.get(ReplyRequest, context.get("request_id"))
        destination_id = args.get("chat_id")
        if not request or not isinstance(destination_id, str):
            fail(403, "Twin messaging has no authorized origin or destination")
        source_ids = args.get("source_message_ids", [])
        if not isinstance(source_ids, list):
            fail(400, "Twin message source_message_ids must be message IDs")
        chat_ids = self._message_source_chat_ids(db, request, destination_id, source_ids)
        for chat_id in sorted(chat_ids):
            db.scalar(select(Chat).where(Chat.id == chat_id).with_for_update(key_share=True))
        # Discard all pre-lock ORM permission snapshots, not just the origin's
        # stop flag. Nested source messages/history grants can also be revoked.
        db.expire_all()
        request = db.get(ReplyRequest, context.get("request_id"))
        if not self._message_source_chat_ids(db, request, destination_id, source_ids).issubset(chat_ids):
            fail(409, "Originating context changed while waiting; retry the same message operation")

    def _message_tool_origin(self, db, context):
        request = db.get(ReplyRequest, context.get("request_id"))
        if not request or request.cancel_requested or request.status in {"failed", "cancelled"}:
            fail(403, "Twin messaging requires an authorized originating request")
        twin = db.get(Actor, request.target_twin_id)
        chat, member = self.membership(db, twin.owner_id, request.chat_id)
        context_chat = context.get("chat", {})
        context_chat_id = context_chat.get("id") if isinstance(context_chat, dict) else context_chat
        if (context_chat_id and context_chat_id != chat.id) or (context.get("chat_id") and context["chat_id"] != chat.id):
            fail(403, "Twin messaging cannot replace its originating chat")
        manifest = db.get(ContextManifest, context.get("context_manifest_id"))
        if context.get("owner_id") != twin.owner_id or context.get("target_twin_id") != twin.id or not manifest or manifest.request_id != request.id or manifest.owner_id != twin.owner_id:
            fail(403, "Twin messaging has no matching model-input provenance")
        if context.get("causal_chain_id") and context["causal_chain_id"] != self.request_chain(db, request).id:
            fail(403, "Twin messaging cannot replace its causal lineage")
        if context.get("main_session_id") and request.session_id and context["main_session_id"] != request.session_id:
            fail(403, "Twin messaging belongs to another originating session")
        if context.get("main_run_id") and request.run_id and context["main_run_id"] != request.run_id:
            fail(403, "Twin messaging belongs to another originating run")
        inherited = self.manifest_provenance(db, request.id)
        if not self.provenance_allowed(db, member, inherited):
            fail(409, "Originating context is no longer authorized")
        return request, twin, chat, member, inherited

    def _twin_message_tool(self, db, name, args, context):
        request, twin, origin, origin_member, inherited = self._message_tool_origin(db, context)
        if name == "list_authorized_chats":
            chats = []
            memberships = list(db.scalars(select(Membership).where(Membership.owner_id == twin.owner_id, Membership.left_at.is_(None)))) if origin.kind == "private" else [origin_member]
            for membership in memberships:
                chat = db.get(Chat, membership.chat_id)
                if not chat or (chat.kind == "private" and chat.private_owner_id != twin.owner_id):
                    continue
                chats.append({"id": chat.id, "kind": chat.kind, "title": chat.title, "is_current_chat": chat.id == origin.id})
            return {"chats": chats, "notice": "Chat metadata does not authorize sending originating private contents. Sending independently checks the full source context and every recipient's history access."}
        body = args.get("body")
        source_ids = args.get("source_message_ids", [])
        if not isinstance(body, str) or not body.strip() or len(body) > 30000:
            fail(400, "Twin message body must contain 1 to 30000 characters")
        if not isinstance(source_ids, list) or len(source_ids) > 500 or not all(isinstance(source_id, str) for source_id in source_ids):
            fail(400, "Twin message source_message_ids must be message IDs")
        target, target_member = self.membership(db, twin.owner_id, args.get("chat_id"), True)
        sources = self.check_sources(db, origin_member, source_ids)
        inherited = list({digest(source): source for source in [*inherited, *[{"kind": "message", "id": message.id, "seq": message.seq, "hash": digest(message.body)} for message in sources]]}.values())
        if target.id != origin.id:
            if origin.kind == "private":
                fail(403, "Private originating context cannot be sent to another chat")
            # Membership in the destination alone is insufficient. Every audience
            # member must be able to read the complete originating model input.
            for audience in self.members(db, target.id):
                source_member = db.scalar(select(Membership).where(Membership.chat_id == origin.id, Membership.owner_id == audience.owner_id, Membership.left_at.is_(None)))
                if not source_member or not self.provenance_allowed(db, source_member, inherited):
                    fail(403, "Destination audience is not authorized for the complete originating context")
            provenance = [{"kind": "chat_context", "chat_id": origin.id, "sources": inherited}]
            local_source_ids = []
        else:
            provenance = inherited
            local_source_ids = list(dict.fromkeys(source["id"] for source in inherited if source.get("kind") == "message"))
        chain = self.request_chain(db, request)
        content_hash = self.normalized_content_hash(body)
        for previous in db.scalars(select(Message).where(Message.chat_id == target.id, Message.causal_chain_id == chain.id, Message.author_id == twin.id, Message.state == "published")):
            if self.normalized_content_hash(previous.body) == content_hash:
                return {"status": "held", "reason": "repeated_content", "message_id": previous.id, "duplicate_message_id": previous.id,
                        "chat_id": target.id, "causal_chain_id": chain.id, "author_id": twin.id, "author_owner_id": twin.owner_id,
                        "message": None, "sent": False}
        message = self.append_message(db, target, author_id=twin.id, kind="twin", body=body,
                                      request_id=request.id, causal_chain_id=chain.id,
                                      source_message_ids=local_source_ids, source_provenance=provenance,
                                      source_floor_seq=min([db.get(Message, source_id).seq for source_id in local_source_ids], default=target.next_message_seq),
                                      sdk_output_key=f"tool:{digest(context['operation_id'])}")
        self.route_committed_message(db, target, message)
        return {"status": "sent", "sent": True, "message": self.message_json(db, message, target_member), "message_id": message.id,
                "chat_id": target.id, "causal_chain_id": message.causal_chain_id, "author_id": twin.id,
                "author_owner_id": twin.owner_id}

    async def runtime_tool(self, name, args, context):
        if name == "execute_approved_action":
            approval = self.approval_for_execution(context["owner_id"], context["chat"]["id"], args["id"])
            operation_id = f"approval-effect:{approval['id']}"
            action = await self.runtime.authorize_approval(approval, operation_id)
            return await self.runtime_tool(action["tool"], action["arguments"], {
                **context, "operation_id": operation_id, "approved_approval_id": approval["id"],
            })
        chat_value = context.get("chat", {})
        chat_id = chat_value.get("id") if isinstance(chat_value, dict) else chat_value
        chat_id = chat_id or context.get("chat_id")
        owner_id = context.get("owner_id")
        mutations = {"create_tracking", "update_tracking", "delete_tracking", "add_tracking_update", "save_summary", "create_approval", "create_reminder", "send_chat_message"}
        with self.transaction() as db:
            if name == "send_chat_message":
                self._lock_twin_message_scope(db, args, context)
            chat, member = self.membership(db, owner_id, chat_id, (name in mutations or bool(context.get("context_manifest_id"))) and name != "send_chat_message")
            operation_id = context.get("operation_id")
            arguments_hash = digest(args)
            if name in mutations:
                if not operation_id:
                    fail(400, "Mutating runtime tools require a stable operation_id")
                prior = db.get(ProductOperation, operation_id)
                if prior:
                    if (prior.chat_id, prior.owner_id, prior.name, prior.arguments_hash) != (chat_id, owner_id, name, arguments_hash):
                        fail(409, "Product operation identity conflicts with its original action")
                    if name == "send_chat_message":
                        self._message_tool_origin(db, context)
                        destination_member = self.membership(db, owner_id, prior.result["chat_id"])[1]
                        self.visible_message(db, destination_member, prior.result["message_id"])
                    if name in {"create_tracking", "update_tracking", "add_tracking_update"}:
                        topic = db.get(TrackingTopic, prior.result.get("id"))
                        if not topic or not self.tracking_json(db, topic, member):
                            return {"id": prior.result.get("id"), "status": "deleted_or_unavailable", "operation_already_applied": True}
                    if name == "create_approval":
                        approval = db.get(Approval, prior.result.get("id"))
                        if not approval or not self.sources_allowed(db, member, approval.source_message_ids) or not self.provenance_allowed(db, member, approval.source_provenance):
                            return {"id": prior.result.get("id"), "status": "unavailable", "operation_already_applied": True}
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
            result = self._twin_message_tool(db, name, args, context) if name in {"send_chat_message", "list_authorized_chats"} else self._tool_dispatch(db, name, args, context, chat, member)
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
                        if self.sources_allowed(db, member, update.source_message_ids, update.source_floor_seq):
                            source_ids.extend(update.source_message_ids)
                extra_provenance = []
                tracking_ids = [row["id"] for row in result.get("tracking", [])] if name == "list_tracking" else [result["id"]] if name in {"get_tracking", "create_tracking", "update_tracking", "add_tracking_update"} and result.get("id") else []
                for topic_id in tracking_ids:
                    topic = db.get(TrackingTopic, topic_id)
                    if topic and self.tracking_json(db, topic, member):
                        extra_provenance.extend(topic.source_provenance or [])
                        extra_provenance.append({"kind": "tracking", "id": topic.id, "revision": topic.revision, "floor_seq": topic.source_floor_seq, "created_at": iso(topic.created_at), "source_message_ids": topic.source_message_ids})
                        if name != "list_tracking":
                            for update in db.scalars(select(TrackingUpdate).where(TrackingUpdate.topic_id == topic.id)):
                                if self.sources_allowed(db, member, update.source_message_ids, update.source_floor_seq) and self.provenance_allowed(db, member, update.source_provenance):
                                    extra_provenance.extend(update.source_provenance or [])
                known = {source["id"] for source in manifest.sources}
                added = [{"kind": "message", "id": sid, "seq": db.get(Message, sid).seq, "hash": digest(db.get(Message, sid).body)} for sid in dict.fromkeys(source_ids) if sid not in known]
                if added or extra_provenance:
                    unique_sources = {digest(source): source for source in [*manifest.sources, *added, *extra_provenance]}
                    manifest.sources = list(unique_sources.values())
                    manifest.source_digest = digest(manifest.sources)
            if name in mutations:
                db.add(ProductOperation(operation_id=operation_id, chat_id=chat_id, owner_id=owner_id, name=name, arguments_hash=arguments_hash, result=result))
        if name in mutations:
            self.publish_event(chat_id, {"type": "changed"})
            if name == "send_chat_message" and result.get("chat_id") != chat_id:
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

    async def decide_approval(self, owner_id, approval_id, decision):
        with self.transaction() as db:
            approval = db.get(Approval, approval_id)
            if not approval or approval.approver_id != owner_id:
                fail(404, "Approval not found")
            _, approval_member = self.membership(db, owner_id, approval.chat_id, True)
            if not self.sources_allowed(db, approval_member, approval.source_message_ids) or not self.provenance_allowed(db, approval_member, approval.source_provenance):
                fail(409, "Approval sources are no longer available")
            if approval.status != "pending":
                if approval.status == decision:
                    return self.approval_json(approval)
                fail(409, "This approval already has a different final decision")
            payload = {**self.approval_json(approval), "sdk_reference": approval.sdk_reference}
        if not self.runtime:
            fail(503, "Runtime unavailable; no authorization decision was submitted")
        self.approval_for_execution(owner_id, payload["chat_id"], approval_id)
        decided = await resolve(self.runtime.decide_approval(payload, decision))
        self.accept_approval_decision(approval_id, decided)
        with self.transaction() as db:
            approval = db.get(Approval, approval_id)
            self.membership(db, owner_id, approval.chat_id, True)
            result = self.approval_json(approval)
        self.publish_event(approval.chat_id, {"type": "changed"})
        return result

    def approval_for_execution(self, owner_id, chat_id, approval_id):
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
            request = self.accept_request(db, chat, origin.target_twin_id, row.approver_id,
                "approval_result", key, source_message_id=origin.source_message_id,
                intent_key=key, causal_chain_id=origin.causal_chain_id,
                input_body=f"Owner decision for approval {row.id}: {row.status}. "
                "If approved, use execute_approved_action with that ID to perform the frozen action. "
                "If rejected, do not execute it. Report the actual tool result; consent is not execution.")
            row.continuation_request_id = request.id
            db.add(ContextManifest(request_id=request.id, owner_id=row.approver_id,
                grant_version=member.grant_version, high_water_seq=chat.next_message_seq - 1,
                sources=row.source_provenance, source_digest=digest(row.source_provenance)))
        self.publish_event(chat.id, {"type": "changed"})

    async def reconcile_approvals(self):
        with self.transaction() as db:
            pending = [{**self.approval_json(row), "sdk_reference": row.sdk_reference}
                       for row in db.scalars(select(Approval).where(Approval.continuation_request_id.is_(None)))
                       if row.sdk_reference.get("action")]
        for row in pending:
            try:
                approval = await self.runtime.ensure_approval(row)
                if approval["status"] != "pending":
                    self.accept_approval_decision(row["id"], approval)
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Approval reconciliation failed for %s", row["id"])

    def workspace(self, owner_id, chat_id):
        with self.transaction() as db:
            _, member = self.membership(db, owner_id, chat_id)
            root_path = settings.workspace_root / chat_id
            root = root_path.resolve()
            if not root.is_relative_to(settings.workspace_root) or root_path.is_symlink():
                fail(403, "Invalid workspace root")
            entries = []
            for node in db.scalars(select(WorkspaceNode).where(WorkspaceNode.chat_id == chat_id)):
                if not self.workspace_node_allowed(db, member, node):
                    continue
                relative = Path(node.relative_path)
                if relative.is_absolute() or ".." in relative.parts:
                    continue
                path = root / relative
                if any(part.is_symlink() for part in [path, *path.parents] if part.is_relative_to(root)):
                    continue
                resolved = path.resolve()
                if not resolved.is_relative_to(root) or not resolved.exists():
                    continue
                entries.append({"id": node.id, "name": resolved.name, "kind": "folder" if resolved.is_dir() else "file", "path": node.relative_path, "size": None if resolved.is_dir() else resolved.stat().st_size})
            return entries

    async def activity(self, owner_id, chat_id, session_id=None, run_id=None):
        with self.transaction() as db:
            chat, _ = self.membership(db, owner_id, chat_id)
            if chat.kind != "private" or chat.private_owner_id != owner_id:
                fail(403, "Activity is limited to your current private chat and its child runs")
        if not self.runtime:
            fail(503, "Runtime unavailable")
        if session_id and run_id:
            return await resolve(self.runtime.get_activity(chat_id, owner_id, session_id, run_id))
        return await resolve(self.runtime.activity(chat_id, owner_id))


service = ProductService()


def bind_runtime(runtime):
    service.bind_runtime(runtime)
