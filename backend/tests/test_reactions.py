"""Desired-state reactions with isolated PG; no live model or production writes."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import event, func, select
from test_memory_dream import product as product  # noqa: PLC0414

from app import service as service_module
from app.models import (
    Chat,
    Message,
    MessageReaction,
    ReplyRequest,
)
from app.reactions import REACTION_EMOJIS


def message(product, chat_id="group-alex-maya", author="owner-alex"):
    with product.transaction() as db:
        return product.append_message(db, db.get(Chat, chat_id), author_id=author,
            kind="human", body="A message for reaction").id


def react(product, message_id, emoji="👍", active=True, *, owner="owner-alex", chat="group-alex-maya", key=None):
    return product.set_message_reaction(owner, chat, message_id, emoji, active, key or uuid4().hex)


def twin_context(product, message_id):
    request = product.send_message("owner-alex", "private-alex", "React within authorized scope", uuid4().hex)["request"]
    return product.get_runtime_context(request["id"])


def test_desired_state_actor_multiemoji_revision_and_no_model_triggers(product):
    mid = message(product)
    with product.transaction() as db:
        revision = db.get(Chat, "group-alex-maya").revision
        before = [db.scalar(select(func.count()).select_from(model)) for model in (ReplyRequest,)]
    first = react(product, mid, key="add")
    assert first["reactions"] == [{"emoji": "👍", "count": 1, "actors": [{"id": "owner-alex", "owner_id": "owner-alex", "name": "AAA", "kind": "human"}], "mine": True}]
    react(product, mid, key="different-key-same-state")
    react(product, mid, key="add")
    with product.transaction() as db:
        assert db.get(Chat, "group-alex-maya").revision == revision + 1
        assert [db.scalar(select(func.count()).select_from(model)) for model in (ReplyRequest,)] == before
    react(product, mid, "❤️")
    both = react(product, mid, owner="owner-maya")
    assert next(row for row in both["reactions"] if row["emoji"] == "👍")["count"] == 2
    react(product, mid, active=False, key="remove")
    replay = react(product, mid, key="add")
    thumbs = next(row for row in replay["reactions"] if row["emoji"] == "👍")
    assert thumbs["count"] == 1 and not thumbs["mine"] and thumbs["actors"][0]["id"] == "owner-maya"
    with pytest.raises(HTTPException) as error:
        react(product, mid, "❤️", key="add")
    assert error.value.status_code == 409


def test_actor_cap_and_concurrent_noop_adds(product):
    mid = message(product)
    for emoji in REACTION_EMOJIS[:20]:
        react(product, mid, emoji)
    with pytest.raises(HTTPException) as error:
        react(product, mid, REACTION_EMOJIS[20])
    assert error.value.status_code == 409
    # Another owner has an independent cap; removal frees exactly one slot.
    react(product, mid, REACTION_EMOJIS[20], owner="owner-maya")
    react(product, mid, REACTION_EMOJIS[0], active=False)
    react(product, mid, REACTION_EMOJIS[20])
    barrier = Barrier(4)
    second = message(product)
    with product.transaction() as db:
        revision = db.get(Chat, "group-alex-maya").revision
    def concurrent(index):
        barrier.wait()
        return react(product, second, key=f"parallel-{index}")
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(concurrent, range(4)))
    assert all(row["reactions"][0]["count"] == 1 for row in rows)
    with product.transaction() as db:
        assert db.get(Chat, "group-alex-maya").revision == revision + 1
        assert db.scalar(select(func.count()).select_from(MessageReaction).where(MessageReaction.message_id == second)) == 1


def test_acl_withdrawal_reset_and_tool_twin_identity(product):
    mid = message(product, "private-alex")
    context = twin_context(product, mid)
    result = asyncio.run(product.runtime_tool("set_message_reaction",
        {"message_id": mid, "emoji": "👍", "active": True}, {**context, "operation_id": "twin-reaction"}))
    assert result["actor_id"] == "twin-alex" and not result["reactions"][0]["mine"]
    react(product, mid, chat="private-alex")
    projected = next(row for row in product.chat_detail("owner-alex", "private-alex")["messages"] if row["id"] == mid)["reactions"][0]
    assert projected["count"] == 2 and {row["kind"] for row in projected["actors"]} == {"human", "twin"}
    with pytest.raises(HTTPException):
        react(product, mid, owner="owner-jordan", chat="private-alex")
    with product.transaction() as db:
        db.get(Message, mid).state = "withdrawn"
    with pytest.raises(HTTPException):
        react(product, mid, chat="private-alex", key="new-after-withdraw")
    private = message(product, "private-alex")
    react(product, private, chat="private-alex", key="before-reset")
    product.personal_reset("owner-alex", uuid4().hex, 0)
    with product.transaction() as db:
        assert not db.scalar(select(MessageReaction).where(MessageReaction.message_id == private))
    with pytest.raises(HTTPException):
        react(product, private, chat="private-alex", key="before-reset")


def test_batch_projection_has_one_reaction_query_per_message_page(product):
    mids = [message(product) for _ in range(8)]
    for mid in mids:
        react(product, mid)
    seen = []
    def count(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT") and "tt_message_reactions" in statement:
            seen.append(statement)
    event.listen(service_module.engine, "before_cursor_execute", count)
    try:
        detail = product.chat_detail("owner-alex", "group-alex-maya")
        assert len(seen) == 1 and all(row["reactions"] for row in detail["messages"])
        seen.clear()
        context = twin_context(product, mids[-1])
        seen.clear()
        history = asyncio.run(product.runtime_tool("history", {"chat_id": "group-alex-maya"}, {**context, "operation_id": "history-batch"}))
        assert len(seen) == 1 and len(history["messages"]) == 8
    finally:
        event.remove(service_module.engine, "before_cursor_execute", count)


def test_explicit_reaction_migration_is_repeatable_and_checks_primary_key(product):
    from sqlalchemy import inspect

    from app.reaction_schema import check_reaction_schema, upgrade_reaction_schema
    upgrade_reaction_schema(service_module.engine)
    upgrade_reaction_schema(service_module.engine)
    check_reaction_schema(inspect(service_module.engine))
    with pytest.raises(HTTPException):
        react(product, message(product), "not an emoji")


def test_catalog_matches_generated_gallery_including_skin_tones():
    import json
    from pathlib import Path

    from app.reactions import reaction_emojis, validate
    root = Path(__file__).resolve().parents[2]
    source = json.loads((root / "backend/app/data/reaction_emojis.json").read_text(encoding="utf-8"))
    assert set(source) == set(REACTION_EMOJIS) and len(source) == 3145
    assert reaction_emojis() is reaction_emojis()
    for emoji in ("👍", "❤️", "👍🏽", "🫶🏿"):
        validate(emoji, True)
    gallery = json.loads((root / "frontend/public/emoji/catalog.json").read_text(encoding="utf-8"))
    gallery_tokens = {row["emoji"] for item in gallery for row in item["variants"]}
    assert gallery_tokens <= set(source) and len(gallery) == 1595


def test_nested_message_source_chats_share_the_same_sorted_reaction_lock_scope(product):
    from app.platform_tools import scope_ids
    source = message(product, "private-alex")
    with product.transaction() as db:
        target = product.append_message(db, db.get(Chat, "group-alex-maya"), author_id="twin-alex",
            kind="twin", body="Owner-visible derived text", source_provenance=[{
                "kind": "chat_context", "chat_id": "private-alex", "sources": [{"kind": "message", "id": source}]}])
        target_id = target.id
        ids = scope_ids(product, db, "set_message_reaction", {"message_id": target_id},
            {"owner_id": "owner-alex", "chat": {"id": "group-alex-maya"}})
        assert ids == {"group-alex-maya", "private-alex"}
    with pytest.raises(HTTPException):
        react(product, target_id)
    with pytest.raises(HTTPException):
        react(product, target_id, owner="owner-maya")


def test_response_revision_belongs_to_its_snapshot_even_when_delivery_is_late(product, monkeypatch):
    from app.notifications import notifications
    mid = message(product)
    with product.transaction() as db:
        baseline = db.get(Chat, "group-alex-maya").revision
    later = []
    entered = False
    def update_after_first_commit(_chat_id=None, *, work=False):
        nonlocal entered
        if entered:
            return
        entered = True
        # The original transaction has committed, but its HTTP result has not
        # returned yet. This models another actor's already-visible WS update.
        later.append(react(product, mid, owner="owner-maya", key="later-add"))
    monkeypatch.setattr(notifications, "emit", update_after_first_commit)
    older = react(product, mid, key="first-add")
    assert older["revision"] == baseline + 1 and older["reactions"][0]["count"] == 1
    assert later[0]["revision"] == baseline + 2 and later[0]["reactions"][0]["count"] == 2
    replay = react(product, mid, key="first-add")
    noop = react(product, mid, key="noop-after-both")
    assert replay["revision"] == noop["revision"] == later[0]["revision"]
    assert replay["reactions"][0]["count"] == noop["reactions"][0]["count"] == 2


def test_concurrent_actor_response_revisions_match_their_aggregates(product):
    mid = message(product)
    with product.transaction() as db:
        baseline = db.get(Chat, "group-alex-maya").revision
    barrier = Barrier(2)
    def add(owner):
        barrier.wait()
        return react(product, mid, owner=owner)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(add, ["owner-alex", "owner-maya"]), key=lambda row: row["revision"])
    assert [(row["revision"], row["reactions"][0]["count"]) for row in results] == [(baseline + 1, 1), (baseline + 2, 2)]
