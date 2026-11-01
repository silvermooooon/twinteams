"""Bounded human-chat evidence in the existing owner Dream scheduling intent."""
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select, text

from .config import settings
from .models import (
    Actor,
    Chat,
    Contact,
    ContactDreamCursor,
    Membership,
    MemoryDreamState,
    Message,
    now,
)


def seed_contact_dreams(connection):
    """One provisioning pass over membership metadata; history stays in bounded Dream pages."""
    connection.execute(text("""
        WITH seeded AS (
            INSERT INTO tt_contact_dream_cursors (owner_id, chat_id, message_cursor_seq, updated_at)
            SELECT m.owner_id, m.chat_id, 0, CURRENT_TIMESTAMP
            FROM tt_memberships m JOIN tt_chats c ON c.id = m.chat_id
            WHERE c.kind = 'group' AND m.left_at IS NULL
            ON CONFLICT (owner_id, chat_id) DO NOTHING
            RETURNING owner_id
        )
        INSERT INTO tt_memory_dream_state
            (owner_id, chat_id, cursor_seq, message_cursor_seq, due_at, last_status)
        SELECT DISTINCT s.owner_id, c.id, 0, 0, CURRENT_TIMESTAMP + :dream_interval * INTERVAL '1 second', 'scheduled'
        FROM seeded s JOIN tt_chats c ON c.private_owner_id = s.owner_id
        ON CONFLICT (owner_id) DO UPDATE
        SET due_at = COALESCE(tt_memory_dream_state.due_at, CURRENT_TIMESTAMP + :dream_interval * INTERVAL '1 second'), last_status = 'scheduled'
        WHERE tt_memory_dream_state.queued_request_id IS NULL
            AND tt_memory_dream_state.last_status <> 'recovery_required'
    """), {"dream_interval": settings.memory_dream_interval_seconds})


def message_published(service, db, chat, message):
    if chat.kind != "group" or message.kind != "human" or message.state != "published":
        return
    if not chat.direct_pair_key and not message.reply_to_message_id and "@" not in message.body:
        return
    author = db.get(Actor, message.author_id)
    owners = {author.owner_id} if author and author.kind == "human" else set()
    if chat.direct_pair_key:
        owners.update(member.owner_id for member in service.members(db, chat.id))
    elif message.reply_to_message_id:
        replied = db.get(Message, message.reply_to_message_id)
        peer = db.get(Actor, replied.author_id) if replied and replied.kind == "human" else None
        if peer:
            owners.add(peer.owner_id)
    from .contacts import interaction_contacts, mentioned_owners
    owners.update(mentioned_owners(db, chat.id, message.body))
    for owner_id in owners:
        if not interaction_contacts(service, db, owner_id, [message]):
            continue
        if db.get(ContactDreamCursor, (owner_id, chat.id)) is None:
            db.add(ContactDreamCursor(owner_id=owner_id, chat_id=chat.id, message_cursor_seq=0))
        private = db.scalar(select(Chat).where(Chat.private_owner_id == owner_id))
        if not private:
            continue
        state = db.get(MemoryDreamState, owner_id)
        if state is None:
            state = MemoryDreamState(owner_id=owner_id, chat_id=private.id, cursor_seq=0,
                message_cursor_seq=0, last_status="idle")
            db.add(state)
        if state.last_status != "recovery_required" and state.due_at is None:
            state.due_at = now() + timedelta(seconds=settings.memory_dream_interval_seconds)
            if not state.queued_request_id:
                state.last_status = "scheduled"


def batch(service, db, owner_id):
    from .contacts import interaction_contacts, sync_contacts
    from .memory_dream import _message_evidence, service_hash
    from .prompt_safety import safe_text
    cursors = db.scalars(select(ContactDreamCursor).join(Chat, Chat.id == ContactDreamCursor.chat_id)
        .join(Membership, (Membership.chat_id == Chat.id) & (Membership.owner_id == owner_id))
        .where(ContactDreamCursor.owner_id == owner_id, Chat.kind == "group", Membership.left_at.is_(None),
            ContactDreamCursor.message_cursor_seq < Chat.next_message_seq - 1)
        .order_by(ContactDreamCursor.updated_at, ContactDreamCursor.chat_id).limit(20))
    pages, evidence, candidates, size, scanned = [], {}, {}, 0, 0
    for cursor in cursors:
        chat, member = service.membership(db, owner_id, cursor.chat_id)
        rows = list(db.scalars(select(Message).where(Message.chat_id == chat.id,
            Message.seq > cursor.message_cursor_seq).order_by(Message.seq).limit(100 - scanned)))
        through = cursor.message_cursor_seq
        for row in rows:
            scanned += 1
            interactions = interaction_contacts(service, db, owner_id, [row])
            contacts = sync_contacts(service, db, owner_id, [row]) if interactions else {}
            values, additions = {}, {}
            for peer, interaction in interactions.items():
                contact = contacts[peer]
                sources = []
                for source_id in interaction["source_message_ids"]:
                    source = db.get(Message, source_id)
                    if contact.manual_updated_at and source.created_at <= contact.manual_updated_at:
                        continue
                    value = _message_evidence(service, db, chat, member, source)
                    if value:
                        value["usage"] = "contact_tags_only"
                        values[source_id] = value
                        sources.append(source_id)
                if sources:
                    additions[peer] = {"owner_id": peer, "revision": contact.revision,
                        "tags": contact.tags, "source_message_ids": sources}
            extra = {key: value for key, value in values.items() if key not in evidence}
            amount = len(safe_text(extra).encode("utf-8"))
            if evidence and (len(evidence) + len(extra) > 20 or size + amount > 48 * 1024):
                break
            # A single long message is explicitly bounded by the existing evidence reader.
            if amount > 48 * 1024:
                for value in extra.values():
                    value["body"] = value["body"][:4000]
                    value["contact_excerpt"] = True
                amount = len(safe_text(extra).encode("utf-8"))
            evidence.update(extra)
            size += amount
            for peer, value in additions.items():
                if peer in candidates:
                    value["source_message_ids"] = list(dict.fromkeys([
                        *candidates[peer]["source_message_ids"], *value["source_message_ids"]]))
                candidates[peer] = value
            through = row.seq
        pages.append({"chat_id": chat.id, "cursor_before": cursor.message_cursor_seq, "through_seq": through})
        if scanned >= 100 or len(evidence) >= 20 or size >= 48 * 1024:
            break
    references = [{"id": key, "hash": service_hash(value), "excerpt": bool(value.get("contact_excerpt"))} for key, value in evidence.items()]
    return {"cursors": pages, "messages": references, "contacts": list(candidates.values())}, list(evidence.values())


def context(service, db, owner_id, payload):
    from .contacts import interaction_contacts
    from .memory_dream import _message_evidence, service_hash
    evidence = []
    for page in payload.get("cursors", []):
        service.membership(db, owner_id, page["chat_id"])
        cursor = db.get(ContactDreamCursor, (owner_id, page["chat_id"]))
        if not cursor or cursor.message_cursor_seq != page["cursor_before"]:
            raise HTTPException(409, "Contact Dream cursor changed")
    for reference in payload.get("messages", []):
        message = db.get(Message, reference["id"])
        if not message:
            raise HTTPException(409, "Contact Dream evidence is unavailable")
        chat, member = service.membership(db, owner_id, message.chat_id)
        value = _message_evidence(service, db, chat, member, message)
        if value:
            value["usage"] = "contact_tags_only"
        if reference.get("excerpt") and value:
            value["body"] = value["body"][:4000]
            value["contact_excerpt"] = True
        if not value or message.kind != "human" or chat.kind != "group" or service_hash(value) != reference["hash"]:
            raise HTTPException(409, "Contact Dream evidence changed or is no longer visible")
        evidence.append(value)
    messages = {value["id"]: db.get(Message, value["id"]) for value in evidence}
    for candidate in payload.get("contacts", []):
        contact = db.get(Contact, (owner_id, candidate["owner_id"]))
        if not contact:
            raise HTTPException(409, "Contact is no longer available")
        if contact.revision != candidate["revision"]:
            continue  # Finish skips this stale proposal; private Memory can still complete.
        refs = [messages[key] for key in candidate["source_message_ids"]]
        # Old reply targets may support identity, but never undo a later manual correction.
        if contact.manual_updated_at and any(row.created_at <= contact.manual_updated_at for row in refs):
            raise HTTPException(409, "Contact Dream evidence predates the owner's correction")
        if candidate["owner_id"] not in interaction_contacts(service, db, owner_id, refs):
            raise HTTPException(409, "Contact Dream requires an actual visible interaction")
    return evidence


def advance(db, owner_id, payload):
    for page in payload.get("cursors", []):
        cursor = db.get(ContactDreamCursor, (owner_id, page["chat_id"]))
        cursor.message_cursor_seq = page["through_seq"]
        cursor.updated_at = now()


def pending(db, owner_id):
    return db.scalar(select(ContactDreamCursor.chat_id).join(Chat, Chat.id == ContactDreamCursor.chat_id)
        .join(Membership, (Membership.chat_id == Chat.id) & (Membership.owner_id == owner_id))
        .where(ContactDreamCursor.owner_id == owner_id, Membership.left_at.is_(None),
            ContactDreamCursor.message_cursor_seq < Chat.next_message_seq - 1).limit(1)) is not None


def finish(service, db, owner_id, payload, proposals):
    from .contacts import update_contact_tags
    candidates = {item["owner_id"]: item for item in payload.get("contacts", [])}
    for proposal in proposals:
        candidate = candidates[proposal["owner_id"]]
        contact = db.scalar(select(Contact).where(Contact.owner_id == owner_id,
            Contact.contact_owner_id == proposal["owner_id"]).with_for_update())
        if not contact or contact.revision != candidate["revision"]:
            continue
        relevant = set(candidate["source_message_ids"])
        chat_ids = {db.get(Message, source_id).chat_id for source_id in relevant}
        try:
            context(service, db, owner_id, {**payload, "contacts": [candidate],
                "cursors": [page for page in payload["cursors"] if page["chat_id"] in chat_ids],
                "messages": [ref for ref in payload["messages"] if ref["id"] in relevant]})
        except HTTPException:
            # Git is already published; changed access or an owner correction only skips these tags.
            continue
        update_contact_tags(service, db, owner_id, proposal["owner_id"], proposal["tags"],
            expected_revision=candidate["revision"])
    advance(db, owner_id, payload)
