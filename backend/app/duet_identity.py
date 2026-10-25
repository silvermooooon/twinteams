"""Explicit, repeatable upgrade of platform-generated display names only."""
from sqlalchemy import select
from sqlalchemy.orm import aliased

from . import soul_file
from .config import settings
from .models import Actor, Chat


def upgrade_default_names(service):
    """Keep identity IDs and custom names; no conversation/event history is read."""
    owner = aliased(Actor)
    headings = []
    renamed = 0
    with service.transaction() as db:
        rows = db.execute(select(Actor, owner.name, Chat)
            .join(owner, owner.id == Actor.owner_id)
            .join(Chat, (Chat.private_owner_id == Actor.owner_id) & (Chat.kind == "private"))
            .where(Actor.kind == "twin", owner.kind == "human")
            .with_for_update(of=Actor))
        for actor, owner_name, chat in rows:
            # These are the two formats previously emitted by provisioning/seeding.
            legacy = {f"{owner_name}'s Twin", f"{owner_name.split()[0]}'s Twin"} if owner_name else set()
            if actor.name in legacy:
                actor.name = actor.name.removesuffix("Twin") + "Duet"
                actor.initials = "".join(word[0] for word in actor.name.split()[:2]).upper()[:2]
                actor.profile_revision += 1
                renamed += 1
            if chat.title in legacy:
                chat.title = actor.name
            headings.append((chat.id, actor.name, legacy))
    # Rename only the generated SOUL heading. Repeating the upgrade also repairs a
    # file update interrupted after the DB commit; deleted Soul files stay deleted.
    for chat_id, name, legacy in headings:
        workspace = settings.workspace_root / chat_id
        current = soul_file.read(workspace)["content"]
        if any(soul_file.heading(old) in current for old in legacy):
            soul_file.rename(workspace, name)
    return renamed
