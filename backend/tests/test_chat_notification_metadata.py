"""Authorized notification summaries on disposable PG; no model or worker calls."""
from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from test_chat_attachments_ui_acceptance import attachments as attachments  # noqa: PLC0414
from test_chat_attachments_ui_acceptance import send, upload

from app import service as service_module
from app.models import Chat, Membership, Message


def summaries(client, chat_id="group-alex-maya", owner="owner-alex"):
    headers = {"x-test-owner": owner}
    bootstrap = client.get("/api/bootstrap", headers=headers)
    detail = client.get(f"/api/chats/{chat_id}", headers=headers)
    assert bootstrap.status_code == detail.status_code == 200
    summary = next(chat for chat in bootstrap.json()["chats"] if chat["id"] == chat_id)
    assert summary["last_message"] == detail.json()["chat"]["last_message"]
    assert summary["last_message_meta"] == detail.json()["chat"]["last_message_meta"]
    return summary, bootstrap.json()


def append(product, chat_id="group-alex-maya", **values):
    with product.transaction() as db:
        row = product.append_message(db, db.get(Chat, chat_id), consume_followup=False,
            author_id=values.pop("author_id", "owner-maya"), kind=values.pop("kind", "human"),
            body=values.pop("body", "Synthetic visible summary"), **values)
        return row.id


def test_empty_chat_and_pending_reply_for_me_have_no_message_metadata(attachments):
    _, client = attachments
    summary, bootstrap = summaries(client)
    server_time = datetime.fromisoformat(bootstrap["server_time"])
    assert server_time.tzinfo is not None
    assert summary["last_message"] is None and summary["last_message_meta"] is None
    pending = send(client, "group-alex-maya", body="Owner draft", reply_for_me=True)
    assert pending.status_code == 200
    assert pending.json()["request"]["status"] == "queued"
    summary, _ = summaries(client)
    assert summary["last_message"] is None and summary["last_message_meta"] is None


@pytest.mark.parametrize(("author", "kind", "owner"), [
    ("owner-alex", "human", "owner-alex"),
    ("twin-alex", "twin", "owner-alex"),
    ("twin-alex", "reply_for_me", "owner-alex"),
    ("twin-maya", "reply_for_me", "owner-maya"),
])
def test_summary_preserves_actual_actor_kind_and_existing_owner_mapping(attachments, author, kind, owner):
    product, client = attachments
    message_id = append(product, author_id=author, kind=kind)
    summary, bootstrap = summaries(client)
    meta = summary["last_message_meta"]
    assert set(meta) == {"id", "seq", "author_id", "kind", "created_at"}
    assert meta["id"] == message_id and meta["seq"] == 1
    assert meta["author_id"] == author and meta["kind"] == kind and meta["created_at"]
    actor = next(row for row in bootstrap["actors"] if row["id"] == meta["author_id"])
    assert actor["owner_id"] == owner


def test_metadata_uses_same_authorized_last_and_rechecks_withdrawal(attachments):
    product, client = attachments
    visible = append(product)
    private = append(product, "private-maya", body="Synthetic owner-only source")
    hidden = append(product, author_id="twin-maya", kind="twin", body="Derived private output",
        source_provenance=[{"kind": "chat_context", "chat_id": "private-maya",
                            "sources": [{"kind": "message", "id": private}]}])
    withdrawn = append(product, state="withdrawn", body="Already withdrawn")
    alex, _ = summaries(client)
    maya, _ = summaries(client, owner="owner-maya")
    assert alex["last_message_meta"]["id"] == visible
    assert maya["last_message_meta"]["id"] == hidden
    assert withdrawn not in {alex["last_message_meta"]["id"], maya["last_message_meta"]["id"]}
    with product.transaction() as db:
        db.get(Message, private).state = "withdrawn"
    maya, _ = summaries(client, owner="owner-maya")
    assert maya["last_message_meta"]["id"] == visible
    assert maya["last_message"] == "Synthetic visible summary"


def test_member_history_boundary_does_not_expose_last_metadata(attachments):
    product, client = attachments
    append(product, "group-team")
    with product.transaction() as db:
        member = db.scalar(select(Membership).where(Membership.chat_id == "group-team", Membership.owner_id == "owner-jordan"))
        member.history_policy, member.lower_seq = "no_prior", 2
    summary, _ = summaries(client, "group-team", "owner-jordan")
    assert summary["last_message_meta"] is None and summary["last_message"] is None


def test_attachment_only_message_has_metadata_without_changing_preview_body(attachments):
    _, client = attachments
    uploaded = upload(client, "group-alex-maya", data=b"Synthetic attachment bytes", name="plan.bin", mime="application/octet-stream")
    assert uploaded.status_code == 200
    sent = send(client, "group-alex-maya", ids=[uploaded.json()["id"]], body="")
    assert sent.status_code == 200
    message = sent.json()["message"]
    summary, _ = summaries(client)
    assert summary["last_message"] == ""
    assert summary["last_message_meta"]["id"] == message["id"]
    assert summary["last_message_meta"]["kind"] == "human"


def test_notification_metadata_is_batched_with_existing_message_selection(attachments):
    product, _ = attachments
    with product.transaction() as db:
        chat = db.get(Chat, "group-alex-maya")
        for seq in range(1, 101):
            db.add(Message(id=uuid4().hex, chat_id=chat.id, seq=seq,
                author_id="owner-maya", kind="human", body="Synthetic summary performance row"))
        chat.next_message_seq = 101
    statements = []
    def count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(service_module.engine, "before_cursor_execute", count)
    try:
        with product.transaction() as db:
            chat, member = product.membership(db, "owner-alex", "group-alex-maya")
            summary = product.chat_json(db, chat, member, ["owner-alex", "owner-maya"])
        assert summary["last_message_meta"]["seq"] == 100
    finally:
        event.remove(service_module.engine, "before_cursor_execute", count)
    # Metadata travels in the already-paged authorization selection. Only the
    # existing final body preview can need its own deferred-column lookup.
    assert not any(statement.startswith(("SELECT tt_messages.kind ", "SELECT tt_messages.created_at ")) for statement in statements)
    message_selects = [statement for statement in statements if statement.startswith("SELECT") and "FROM tt_messages" in statement]
    assert len(message_selects) == 4
