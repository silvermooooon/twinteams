"""Owner-created immutable BOT nodes, bearer invitations and independent installs."""
import secrets
from datetime import UTC

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import undefer

from . import bot_skills
from . import bot_snapshot_store as assets
from .models import Actor, BotInstall, BotShareToken, BotSnapshot, now, uid
from .personal_bots import bot_json, create_bot_db, require_bot
from .service import fail, iso


def freeze_share(service, owner_id, bot_id, key):
    with service.transaction() as db:
        actor, chat, _ = require_bot(db, owner_id, bot_id)
        db.refresh(actor, with_for_update=True)
        prior = prior_token(db, owner_id, bot_id, key, "freeze")
        if prior:
            snapshot = db.get(BotSnapshot, prior.snapshot_id)
            return {"snapshot": snapshot_json(snapshot, [prior]), "token": token_json(prior)}
        snapshot_id = uid()
        skills = bot_skills.freeze_skills_db(db, actor)
        rules = assets.freeze_rules(assets.settings.workspace_root / chat.id)
        memory_commit, included = assets.freeze_memory(owner_id, bot_id, snapshot_id)
        avatar_digest = None
        if actor.avatar_digest:
            if actor.avatar_data is not None:
                avatar_digest = assets.put_blob(actor.avatar_data)
            else:
                # Installed avatars already refer to the immutable shared blob.
                avatar_digest = actor.avatar_digest
                if not assets.blob_path(avatar_digest).is_file():
                    fail(503, "BOT avatar is unavailable")
        parent_id = db.scalar(select(BotSnapshot.id).where(
            BotSnapshot.source_owner_id == owner_id, BotSnapshot.source_bot_id == bot_id)
            .order_by(BotSnapshot.created_at.desc(), BotSnapshot.id.desc()).limit(1))
        snapshot = BotSnapshot(id=snapshot_id, parent_snapshot_id=parent_id or actor.installed_snapshot_id,
            source_bot_id=actor.id, source_owner_id=owner_id, name=actor.name,
            assignment=actor.assignment, assignment_summary=actor.assignment[:500],
            initials=actor.initials, color=actor.color, avatar_digest=avatar_digest,
            avatar_preset=actor.avatar_preset, skill_pack_digest=skills["pack_digest"],
            skill_count=len(skills["enabled"]), rules_digest=rules,
            memory_commit=memory_commit, memory_included=included)
        db.add(snapshot)
        db.flush()
        token = new_token(db, owner_id, bot_id, snapshot.id, key, "freeze")
        return {"snapshot": snapshot_json(snapshot, [token]), "token": token_json(token)}


def list_shares(service, owner_id, bot_id):
    with service.transaction() as db:
        actor, _, _ = require_bot(db, owner_id, bot_id)
        snapshots = list(db.scalars(select(BotSnapshot).where(
            BotSnapshot.source_owner_id == owner_id, BotSnapshot.source_bot_id == bot_id)
            .order_by(BotSnapshot.created_at.desc(), BotSnapshot.id)))
        installed = db.get(BotSnapshot, actor.installed_snapshot_id) if actor.installed_snapshot_id else None
        ids = [snapshot.id for snapshot in snapshots] + ([installed.id] if installed else [])
        tokens = list(db.scalars(select(BotShareToken).where(
            BotShareToken.owner_id == owner_id, BotShareToken.bot_id == bot_id,
            BotShareToken.snapshot_id.in_(ids)).order_by(BotShareToken.created_at, BotShareToken.id))) if ids else []
        grouped = {}
        for token in tokens:
            grouped.setdefault(token.snapshot_id, []).append(token)
        return {"snapshots": [snapshot_json(row, grouped.get(row.id, [])) for row in snapshots],
                "installed_snapshot": snapshot_json(installed, grouped.get(installed.id, [])) if installed else None}


def issue_token(service, owner_id, bot_id, snapshot_id, key):
    with service.transaction() as db:
        actor, _, _ = require_bot(db, owner_id, bot_id)
        db.refresh(actor, with_for_update=True)
        snapshot = db.scalar(select(BotSnapshot).where(BotSnapshot.id == snapshot_id,
            ((BotSnapshot.source_owner_id == owner_id) & (BotSnapshot.source_bot_id == bot_id))
            | (BotSnapshot.id == actor.installed_snapshot_id)))
        if snapshot is None:
            fail(404, "Snapshot is not available from this BOT")
        token = prior_token(db, owner_id, bot_id, key, "token", snapshot_id)
        return token_json(token or new_token(db, owner_id, bot_id, snapshot_id, key, "token"))


def revoke_token(service, owner_id, bot_id, token_id):
    with service.transaction() as db:
        require_bot(db, owner_id, bot_id)
        token = db.scalar(select(BotShareToken).where(BotShareToken.id == token_id,
            BotShareToken.owner_id == owner_id, BotShareToken.bot_id == bot_id).with_for_update())
        if token is None:
            fail(404, "Share link not found")
        if token.revoked_at is None:
            token.revoked_at = now()
    return {"ok": True}


def preview_share(service, owner_id, token):
    with service.transaction() as db:
        link = require_token(db, token, owner_id)
        snapshot = db.get(BotSnapshot, link.snapshot_id)
        installed = db.get(BotInstall, (owner_id, snapshot.id))
        bot = bot_json(service, db, *require_bot(db, owner_id, installed.bot_id)) if installed else None
        return {"snapshot": snapshot_json(snapshot), "installed_bot": bot,
                "source_explanation": "A snapshot shared by a personal BOT owner. Adding it creates your own independent BOT; later changes and conversations stay separate."}


def install_share(service, owner_id, token):
    with service.transaction() as db:
        # Shared readers allow different recipients to install concurrently; a
        # revoke waits for accepted installs and prevents later ones.
        link = require_token(db, token, owner_id, lock=True)
        snapshot = db.scalar(select(BotSnapshot).options(undefer(BotSnapshot.assignment))
                             .where(BotSnapshot.id == link.snapshot_id))
        bot_id = uid()
        reserved = db.scalar(insert(BotInstall).values(owner_id=owner_id,
            snapshot_id=snapshot.id, bot_id=bot_id).on_conflict_do_nothing(
                index_elements=[BotInstall.owner_id, BotInstall.snapshot_id]).returning(BotInstall.bot_id))
        if reserved is None:
            installed = db.get(BotInstall, (owner_id, snapshot.id))
            return bot_json(service, db, *require_bot(db, owner_id, installed.bot_id))
        created = create_bot_db(service, db, owner_id, snapshot.name, snapshot.assignment, bot_id=bot_id)
        actor = db.get(Actor, bot_id)
        actor.installed_snapshot_id = snapshot.id
        actor.bot_skill_pack_digest = snapshot.skill_pack_digest
        inventory = assets.read_manifest("skills", snapshot.skill_pack_digest)
        actor.enabled_skill_names = inventory["enabled"]
        actor.avatar_digest, actor.avatar_preset = snapshot.avatar_digest, snapshot.avatar_preset
        actor.initials, actor.color = snapshot.initials, snapshot.color
        assets.install_memory(owner_id, bot_id, snapshot.memory_commit)
        assets.install_rules(assets.settings.workspace_root / created["chat_id"], snapshot.rules_digest)
        created.update(installed_snapshot_id=snapshot.id, actor=service.actor_json(actor))
        return created


def require_token(db, value, owner_id, *, lock=False):
    if not value or len(value) > 64:
        fail(404, "Share link is unavailable")
    query = select(BotShareToken).where(BotShareToken.token == value)
    row = db.scalar(query.with_for_update(read=True) if lock else query)
    if row is None or row.revoked_at is not None and db.get(BotInstall, (owner_id, row.snapshot_id)) is None:
        fail(404, "Share link is unavailable or has been revoked")
    return row


def prior_token(db, owner_id, bot_id, key, kind, snapshot_id=None):
    token = db.scalar(select(BotShareToken).where(BotShareToken.owner_id == owner_id,
        BotShareToken.bot_id == bot_id, BotShareToken.client_request_key == key))
    if token and (token.request_kind != kind or snapshot_id and token.snapshot_id != snapshot_id):
        fail(409, "This request key was already used for another share action")
    return token


def new_token(db, owner_id, bot_id, snapshot_id, key, kind):
    token = BotShareToken(token=secrets.token_urlsafe(32), owner_id=owner_id,
        bot_id=bot_id, snapshot_id=snapshot_id, client_request_key=key, request_kind=kind)
    db.add(token)
    db.flush()
    return token


def token_json(token):
    return {"id": token.id, "token": token.token,
            "created_at": iso(token.created_at.astimezone(UTC)),
            "revoked_at": iso(token.revoked_at.astimezone(UTC)) if token.revoked_at else None}


def snapshot_json(snapshot, tokens=None):
    result = {"id": snapshot.id, "parent_snapshot_id": snapshot.parent_snapshot_id,
        "source_bot_id": snapshot.source_bot_id, "source_owner_id": snapshot.source_owner_id,
        "name": snapshot.name, "assignment": snapshot.assignment_summary,
        "skill_count": snapshot.skill_count, "memory_included": snapshot.memory_included,
        "created_at": iso(snapshot.created_at.astimezone(UTC))}
    if tokens is not None:
        result["tokens"] = [token_json(token) for token in tokens]
    return result
