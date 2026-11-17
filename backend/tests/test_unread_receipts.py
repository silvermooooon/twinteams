"""Explicit read acknowledgements against real HTTP and disposable PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import event, select
from test_chat_attachments_ui_acceptance import attachments as attachments  # noqa: PLC0414
from test_chat_notification_metadata import append
from test_context_acceptance import seed

from app import service as service_module
from app.models import ChangeOutbox, Chat, Membership, Receipt

CHAT = "group-alex-maya"
OWNER = "owner-alex"


def read(client, ids, chat_id=CHAT):
    return client.post(f"/api/chats/{chat_id}/read", json={"message_ids": ids})


def receipt_ids(product):
    with product.transaction() as db:
        return set(db.scalars(select(Receipt.message_id).where(Receipt.owner_id == OWNER)))


def test_read_ack_only_clears_the_explicit_visible_subset_of_group_history(attachments):
    product, client = attachments
    messages = seed(product, CHAT, 40, owner="owner-maya")
    page = client.get(f"/api/chats/{CHAT}?limit=30").json()
    assert page["has_more"] and page["chat"]["unread_count"] == 40
    loaded = {row["id"] for row in page["messages"]}
    visible = [row["id"] for row in page["messages"][-3:]]
    assert len(loaded) == 30

    response = read(client, visible)
    assert response.status_code == 200
    ack = response.json()
    assert ack["ok"] and set(ack["message_ids"]) == set(visible)
    assert ack["chat"]["unread_count"] == 37 and ack["chat"]["revision"] > page["revision"]
    assert ack["chat"]["last_message_meta"]["unread"] is False
    assert receipt_ids(product) == set(visible)
    assert not receipt_ids(product).intersection(row["id"] for row in messages[:10])
    assert not receipt_ids(product).intersection(loaded - set(visible))
    detail = client.get(f"/api/chats/{CHAT}").json()
    assert detail["chat"] == ack["chat"]
    assert detail["chat"]["unread_count"] == 37
    assert {row["id"] for row in detail["messages"] if OWNER in row["read_by"]} == set(visible)

    remaining = [row["id"] for row in messages if row["id"] not in visible]
    assert read(client, remaining).json()["chat"]["unread_count"] == 0
    bootstrap = client.get("/api/bootstrap").json()
    assert next(chat for chat in bootstrap["chats"] if chat["id"] == CHAT)["unread_count"] == 0


def test_replayed_ack_keeps_exact_ids_and_current_revision_without_new_outbox(attachments):
    product, client = attachments
    message_id = append(product)
    first = read(client, [message_id]).json()
    assert first["message_ids"] == [message_id] and first["chat"]["unread_count"] == 0
    with product.transaction() as db:
        outbox = set(db.scalars(select(ChangeOutbox.id).where(ChangeOutbox.chat_id == CHAT)))
        assert db.get(Chat, CHAT).revision == first["chat"]["revision"]

    repeated = read(client, [message_id, message_id])
    assert repeated.status_code == 200 and repeated.json() == first
    with product.transaction() as db:
        assert set(db.scalars(select(ChangeOutbox.id).where(ChangeOutbox.chat_id == CHAT))) == outbox
    assert receipt_ids(product) == {message_id}

    new_message_id = append(product)
    refreshed = read(client, [message_id]).json()
    assert refreshed["message_ids"] == [message_id] and refreshed["chat"]["unread_count"] == 1
    assert refreshed["chat"]["revision"] > first["chat"]["revision"]
    assert refreshed["chat"]["last_message_meta"]["unread"] is True
    assert new_message_id not in receipt_ids(product)


@pytest.mark.parametrize("unavailable", ["foreign", "withdrawn", "legacy_twin", "system", "history_floor"])
def test_failed_read_is_atomic_and_valid_retry_can_recover(attachments, unavailable):
    product, client = attachments
    values = {"chat_id": "private-maya"} if unavailable == "foreign" else {}
    if unavailable == "withdrawn":
        values["state"] = "withdrawn"
    elif unavailable == "legacy_twin":
        values.update(author_id="twin-maya", kind="twin")
    elif unavailable == "system":
        values["kind"] = "system"
    rejected_id = append(product, **values)
    if unavailable == "history_floor":
        with product.transaction() as db:
            member = db.scalar(select(Membership).where(Membership.chat_id == CHAT, Membership.owner_id == OWNER))
            member.history_policy, member.lower_seq = "no_prior", 2
    valid_id = append(product)
    before = client.get(f"/api/chats/{CHAT}/summary").json()

    failed = read(client, [valid_id, rejected_id])
    assert failed.status_code == 404
    assert receipt_ids(product) == set()
    after = client.get(f"/api/chats/{CHAT}/summary").json()
    assert after["unread_count"] == before["unread_count"] == 1
    assert after["revision"] == before["revision"]

    recovered = read(client, [valid_id])
    assert recovered.status_code == 200 and recovered.json()["chat"]["unread_count"] == 0
    assert receipt_ids(product) == {valid_id}


@pytest.mark.parametrize("count", [1, 100])
def test_new_receipt_batch_uses_bounded_sql(attachments, count):
    product, client = attachments
    messages = seed(product, CHAT, count, owner="owner-maya")
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(service_module.engine, "before_cursor_execute", record)
    try:
        response = read(client, [row["id"] for row in messages])
    finally:
        event.remove(service_module.engine, "before_cursor_execute", record)
    assert response.status_code == 200 and response.json()["chat"]["unread_count"] == 0
    assert len(receipt_ids(product)) == count
    assert len(statements) < 20, "New receipts must not load one Message row per receipt"


def test_concurrent_read_retries_preserve_unseen_messages(attachments):
    product, client = attachments
    messages = seed(product, CHAT, 100, owner="owner-maya")
    seen = [row["id"] for row in messages[-10:]]
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(lambda _: read(client, seen), range(10)))
    assert all(response.status_code == 200 for response in results)
    assert all(response.json()["chat"]["unread_count"] == 90 for response in results)
    assert len({response.json()["chat"]["revision"] for response in results}) == 1
    assert receipt_ids(product) == set(seen)
