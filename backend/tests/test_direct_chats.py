"""Real PostgreSQL uniqueness and history-preserving pair reuse; no provider calls."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import inspect, select, text
from test_memory_dream import product as product  # noqa: PLC0414

from app import service as service_module
from app.direct_chat_schema import check_direct_chat_schema, pair_key, upgrade_direct_chat_schema
from app.models import Chat, ChatSetting, Membership


def test_pair_get_or_create_is_bidirectional_and_preserves_existing_history_and_settings(product, monkeypatch):
    pair = product.create_chat("owner-alex", ["owner-jordan"], "Ignored", "no_prior")
    with product.transaction() as db:
        chat = db.get(Chat, pair["id"])
        chat.title = "Stored legacy title"
        grant = db.scalar(select(Membership).where(Membership.chat_id == chat.id, Membership.owner_id == "owner-jordan"))
        grant.lower_seq, grant.grant_version = 7, 4
        db.add(ChatSetting(owner_id="owner-jordan", chat_id=chat.id, reply_for_me_enabled=True,
            prompt_override="Preserve owner's custom settings", sidebar_open=False, sidebar_tab="workspace"))
    notifications = []
    monkeypatch.setattr(product, "publish_event", lambda *args: notifications.append(args))
    reused = product.create_chat("owner-jordan", ["owner-alex"], "Another ignored name", "all")
    assert reused["id"] == pair["id"] and reused["title"] == "AAA"
    assert notifications == []
    assert product.create_chat("owner-alex", ["owner-jordan"], history_policy="fixed_7d")["id"] == pair["id"]
    with product.transaction() as db:
        grant = db.scalar(select(Membership).where(Membership.chat_id == pair["id"], Membership.owner_id == "owner-jordan"))
        assert (grant.history_policy, grant.lower_seq, grant.grant_version) == ("no_prior", 7, 4)
        settings = db.get(ChatSetting, ("owner-jordan", pair["id"]))
        assert settings.reply_for_me_enabled and not settings.sidebar_open and settings.prompt_override
        assert db.get(Chat, pair["id"]).title == "Stored legacy title"
    assert product.create_chat("owner-maya", ["owner-alex"])["id"] == "group-alex-maya"


def test_opposite_concurrent_creates_return_exactly_one_pair(product):
    barrier = Barrier(6)
    def create(index):
        barrier.wait()
        owner, other = ("owner-alex", "owner-jordan") if index % 2 else ("owner-jordan", "owner-alex")
        return product.create_chat(owner, [other])["id"]
    with ThreadPoolExecutor(max_workers=6) as pool:
        ids = list(pool.map(create, range(6)))
    assert len(set(ids)) == 1
    with product.transaction() as db:
        rows = list(db.scalars(select(Chat).where(Chat.direct_pair_key == pair_key(["owner-alex", "owner-jordan"]))))
        assert len(rows) == 1 and rows[0].id == ids[0]
        assert len(product.members(db, rows[0].id)) == 2


def test_promoting_pair_frees_identity_and_preserves_the_group(product):
    pair = product.create_chat("owner-alex", ["owner-jordan"])
    group = product.add_members("owner-jordan", pair["id"], ["owner-maya"], "all")
    product.rename_chat("owner-alex", group["id"], "Keep promoted group")
    with product.transaction() as db:
        assert db.get(Chat, group["id"]).direct_pair_key is None
    new_pair = product.create_chat("owner-jordan", ["owner-alex"])
    assert new_pair["id"] != group["id"] and len(new_pair["member_owner_ids"]) == 2
    assert product.create_chat("owner-alex", ["owner-jordan"])["id"] == new_pair["id"]
    assert product.chat_detail("owner-alex", group["id"])["chat"]["title"] == "Keep promoted group"


def test_migration_backfills_only_unique_pairs_and_refuses_duplicate_history(product):
    with service_module.engine.begin() as connection:
        connection.execute(text("DROP INDEX tt_direct_pair_key"))
        connection.execute(text("ALTER TABLE tt_chats DROP COLUMN direct_pair_key"))
    upgrade_direct_chat_schema(service_module.engine)
    upgrade_direct_chat_schema(service_module.engine)
    check_direct_chat_schema(inspect(service_module.engine))
    with product.transaction() as db:
        assert db.get(Chat, "group-alex-maya").direct_pair_key == pair_key(["owner-alex", "owner-maya"])
        duplicate = Chat(kind="group", title="Legacy duplicate remains")
        db.add(duplicate)
        db.flush()
        product.add_members_locked(db, duplicate, ["owner-alex", "owner-maya"], "all")
        message = product.append_message(db, duplicate, author_id="owner-alex", kind="human", body="Existing history")
        duplicate_id, message_id = duplicate.id, message.id
    with pytest.raises(RuntimeError, match="Duplicate active two-owner"):
        upgrade_direct_chat_schema(service_module.engine)
    with product.transaction() as db:
        from app.models import Message
        assert db.get(Chat, duplicate_id) and db.get(Message, message_id).body == "Existing history"
