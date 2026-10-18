"""Desired-state message reactions; identities come exclusively from the host."""
import json
from functools import lru_cache
from pathlib import Path

from sqlalchemy import func, select

from .models import Actor, MessageReaction


@lru_cache(maxsize=1)
def reaction_emojis():
    """The gallery and service validate the same pinned, generated Unicode data."""
    values = json.loads((Path(__file__).parent / "data" / "reaction_emojis.json").read_text(encoding="utf-8"))
    if (not isinstance(values, list) or not values or
            not all(isinstance(value, str) and 1 <= len(value) <= 32 for value in values)):
        raise RuntimeError("The shared reaction emoji catalog is invalid")
    return tuple(dict.fromkeys(values))


REACTION_EMOJIS = reaction_emojis()
_REACTION_SET = frozenset(REACTION_EMOJIS)


def validate(emoji, active):
    from .service import fail
    if not isinstance(emoji, str) or emoji not in _REACTION_SET:
        fail(400, "Choose a supported reaction emoji")
    if type(active) is not bool:
        fail(400, "active must be a boolean")


def prime(db, message_ids):
    """One joined query per batch; viewer-specific mine is applied at projection."""
    cache = db.info.setdefault("message_reactions", {})
    missing = set(message_ids) - cache.keys()
    if not missing:
        return
    cache.update({message_id: [] for message_id in missing})
    rows = db.execute(select(MessageReaction, Actor).join(Actor, Actor.id == MessageReaction.actor_id)
        .where(MessageReaction.message_id.in_(missing))
        .order_by(MessageReaction.emoji, Actor.name, Actor.id))
    grouped = {}
    for reaction, actor in rows:
        key = (reaction.message_id, reaction.emoji)
        if key not in grouped:
            item = {"emoji": reaction.emoji, "count": 0, "actors": []}
            grouped[key] = item
            cache[reaction.message_id].append(item)
        item = grouped[key]
        item["count"] += 1
        item["actors"].append({"id": actor.id, "owner_id": actor.owner_id, "name": actor.name, "kind": actor.kind})


def projection(db, message_id, viewer_owner_id):
    prime(db, [message_id])
    return [{**item, "mine": any(actor["kind"] == "human" and actor["owner_id"] == viewer_owner_id
                                for actor in item["actors"])}
            for item in db.info["message_reactions"][message_id]]


def set_reaction(service, db, owner_id, chat_id, message_id, actor_id, emoji, active):
    validate(emoji, active)
    chat, member = service.membership(db, owner_id, chat_id)
    if chat.kind == "private" and chat.reset_pending:
        from .service import fail
        fail(409, "Private chat reset is pending")
    service.visible_message(db, member, message_id)
    actor = db.get(Actor, actor_id)
    if not actor or actor.owner_id != owner_id or actor.kind not in {"human", "twin"}:
        from .service import fail
        fail(403, "Reaction actor does not belong to this owner")
    key = (message_id, actor_id, emoji)
    existing = db.get(MessageReaction, key)
    changed = active != (existing is not None)
    if changed:
        if active:
            count = db.scalar(select(func.count()).select_from(MessageReaction).where(
                MessageReaction.message_id == message_id, MessageReaction.actor_id == actor_id))
            if count >= 20:
                from .service import fail
                fail(409, "An actor may use at most 20 different reactions on one message")
            db.add(MessageReaction(message_id=message_id, actor_id=actor_id, emoji=emoji))
        else:
            db.delete(existing)
        db.flush()
        db.info.setdefault("message_reactions", {}).pop(message_id, None)
    return {"chat_id": chat_id, "message_id": message_id, "actor_id": actor_id,
            "emoji": emoji, "active": active, "reactions": projection(db, message_id, owner_id)}
