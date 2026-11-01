"""Owner-private contact discovery and short responsibility tags."""
import re

from sqlalchemy import select

from .models import Actor, Chat, Contact, Membership, now


def message_published(service, db, chat, message):
    if chat.kind != "group" or message.kind != "human":
        return
    if chat.direct_pair_key:
        owners = [member.owner_id for member in service.members(db, chat.id)]
    else:
        owners = mentioned_owners(db, chat.id, message.body)
        if message.reply_to_message_id:
            from .models import Message
            replied = db.get(Message, message.reply_to_message_id)
            if replied and replied.chat_id == chat.id and replied.kind == "human":
                owners.add(service.message_owner(db, replied))
        author = service.message_owner(db, message)
        owners.discard(author)
        if not owners:
            return
        owners.add(author)
    for owner_id in owners:
        sync_contacts(service, db, owner_id, [message])


def interaction_contacts(service, db, owner_id, messages):
    contacts = {}
    for message in messages:
        chat = db.get(Chat, message.chat_id)
        member = db.scalar(select(Membership).where(Membership.chat_id == message.chat_id,
            Membership.owner_id == owner_id, Membership.left_at.is_(None)))
        if (not chat or chat.kind != "group" or not member or message.kind != "human"
                or not service.visible_message(db, member, message.id, False)):
            continue
        author = db.get(Actor, message.author_id)
        if not author or author.kind != "human":
            continue
        peers = {}
        if chat.direct_pair_key:
            members = service.members(db, chat.id)
            if len(members) == 2 and author.owner_id in {row.owner_id for row in members}:
                peer = next(row.owner_id for row in members if row.owner_id != owner_id)
                peers[peer] = [message.id]
        else:
            mentioned = mentioned_owners(db, chat.id, message.body) - {author.owner_id}
            if author.owner_id == owner_id:
                peers.update({peer: [message.id] for peer in mentioned})
            elif owner_id in mentioned:
                peers[author.owner_id] = [message.id]
            if message.reply_to_message_id:
                replied = service.visible_message(db, member, message.reply_to_message_id, False)
                replied_author = db.get(Actor, replied.author_id) if replied else None
                if replied and replied.kind == "human" and replied_author and replied_author.kind == "human":
                    participants = {author.owner_id, replied_author.owner_id}
                    if owner_id in participants and len(participants) == 2:
                        peer = next(value for value in participants if value != owner_id)
                        peers[peer] = [message.id, replied.id]
        for peer, sources in peers.items():
            previous = contacts.get(peer)
            contacts[peer] = {"source_message_ids": list(dict.fromkeys([
                *(previous["source_message_ids"] if previous else []), *sources])),
                "last_interaction_at": max(previous["last_interaction_at"], message.created_at)
                    if previous else message.created_at}
    return contacts


def mentioned_owners(db, chat_id, body):
    tokens = {match.rstrip(".").casefold() for match in re.findall(r"(?<![\w@])@([\w.-]+)", body)}
    if not tokens:
        return set()
    aliases = {}
    actors = db.scalars(select(Actor).join(Membership, Membership.owner_id == Actor.owner_id)
        .where(Membership.chat_id == chat_id, Membership.left_at.is_(None), Actor.kind == "human"))
    for actor in actors:
        for alias in {actor.name.casefold(), actor.id.casefold(), actor.owner_id.casefold()}:
            aliases.setdefault(alias, set()).add(actor.owner_id)
    # Names are usable only when the entire mention token identifies one member.
    return {next(iter(aliases[token])) for token in tokens if len(aliases.get(token, set())) == 1}


def sync_contacts(service, db, owner_id, messages):
    result = {}
    for peer, evidence in interaction_contacts(service, db, owner_id, messages).items():
        row = db.get(Contact, (owner_id, peer))
        if row is None:
            row = Contact(owner_id=owner_id, contact_owner_id=peer, tags=[], revision=1,
                manual_revision=0, **evidence)
            db.add(row)
        elif evidence["last_interaction_at"] >= row.last_interaction_at:
            row.last_interaction_at = evidence["last_interaction_at"]
            row.source_message_ids = evidence["source_message_ids"]
        result[peer] = row
    db.flush()
    return result


def contact_json(service, db, row, human=None):
    if human is None:
        human = db.scalar(select(Actor).where(Actor.owner_id == row.contact_owner_id, Actor.kind == "human"))
    return {"owner_id": row.contact_owner_id, "human": service.actor_json(human),
        "tags": row.tags, "revision": row.revision, "manual_revision": row.manual_revision,
        "manual_updated_at": row.manual_updated_at.isoformat() if row.manual_updated_at else None,
        "last_interaction_at": row.last_interaction_at.isoformat(),
        "updated_at": row.updated_at.isoformat()}


def search_contacts(service, db, owner_id, query="", limit=100):
    rows = db.execute(select(Contact, Actor).join(Actor, Actor.owner_id == Contact.contact_owner_id)
        .where(Contact.owner_id == owner_id, Actor.kind == "human")
        .order_by(Contact.last_interaction_at.desc(), Contact.contact_owner_id))
    terms = query.casefold().split()
    result = []
    for row, human in rows:
        value = contact_json(service, db, row, human)
        searchable = " ".join([value["owner_id"], value["human"]["id"],
            value["human"]["name"], *value["tags"]]).casefold()
        if all(term in searchable for term in terms):
            result.append(value)
        if len(result) >= limit:
            break
    return {"items": result}


def validate_tags(tags):
    from .service import fail
    if not isinstance(tags, list) or len(tags) > 8 or any(
            not isinstance(tag, str) or not 1 <= len(tag.strip()) <= 64 for tag in tags):
        fail(400, "tags must contain at most 8 nonempty strings of at most 64 characters")
    return list(dict.fromkeys(tag.strip() for tag in tags))


def update_contact_tags(service, db, owner_id, peer, tags, *, manual=False, expected_revision=None):
    from .service import fail
    row = db.scalar(select(Contact).where(Contact.owner_id == owner_id,
        Contact.contact_owner_id == peer).with_for_update())
    if row is None:
        fail(404, "Contact requires an actual visible human interaction")
    if expected_revision is not None and row.revision != expected_revision:
        fail(409, "Contact tags changed; read the current tags before updating")
    row.tags = tags
    row.revision += 1
    if manual:
        row.manual_revision += 1
        row.manual_updated_at = now()
    row.updated_at = now()
    return contact_json(service, db, row)


def get_contact(service, owner_id, peer):
    with service.transaction() as db:
        row = db.get(Contact, (owner_id, peer))
        return {"contact": contact_json(service, db, row) if row else None}


def list_contacts(service, owner_id, query="", limit=100):
    with service.transaction() as db:
        return search_contacts(service, db, owner_id, query, limit)


def edit_tags(service, owner_id, peer, tags, expected_revision=None):
    tags = validate_tags(tags)
    with service.transaction() as db:
        result = update_contact_tags(service, db, owner_id, peer, tags, manual=True,
            expected_revision=expected_revision)
    from .notifications import notifications
    notifications.emit_personal(owner_id, "contacts")
    return result
