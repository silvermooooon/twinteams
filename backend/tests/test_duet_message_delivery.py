"""Forum writes and targeted, content-free notifications on disposable PostgreSQL."""
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from duet_test_support import approved_chat
from fastapi import HTTPException
from sqlalchemy import func, select
from test_memory_dream import product as product  # noqa: PLC0414

from app import duet_conversations
from app.models import Actor, CausalChain, Chat, Message, ReplyRequest


def origin(product, owner="owner-alex"):
    request = product.send_message(owner, owner.replace("owner-", "private-"), "Coordinate the authorized task", uuid4().hex)["request"]
    return request, product.get_runtime_context(request["id"])


def tool(product, context, name, args, *, operation=None):
    return product._runtime_tool_transaction(name, args, {**context, "operation_id": operation or uuid4().hex})


def send(product, context, chat_id, body, mentions=(), *, operation=None):
    return tool(product, context, "post_duet_reply", {"chat_id": chat_id, "body": body,
        "source_message_ids": [], "mention_owner_ids": list(mentions)}, operation=operation)


def receipts(db, message_id, revision=1):
    return list(db.scalars(select(ReplyRequest).where(
        ReplyRequest.source.in_(["duet_topic_update", "duet_topic_mention"]),
        ReplyRequest.input_body.contains(f'"update_id":"{message_id}"'),
        ReplyRequest.input_body.contains(f'"revision":{revision},'), ReplyRequest.generation == 0)))


@pytest.mark.parametrize("mentions, expected", [([], {"owner-alex"}),
    (["owner-maya"], {"owner-alex", "owner-maya"}),
    (["owner-alex", "owner-maya"], {"owner-alex", "owner-maya"})])
def test_post_wakes_admin_and_only_explicit_mentions_without_content(product, mentions, expected):
    chat = approved_chat(product, "duet-delivery", ["owner-alex", "owner-maya", "owner-jordan"])
    _, context = origin(product)
    operation = uuid4().hex
    first = send(product, context, chat, "Confidential topic body", mentions, operation=operation)
    assert send(product, context, chat, "Confidential topic body", mentions, operation=operation) == first
    with product.transaction() as db:
        message = db.get(Message, first["message_id"])
        assert message.state == "published" and message.seq is not None
        rows = receipts(db, message.id)
        assert {db.get(Actor, row.target_twin_id).owner_id for row in rows} == expected
        assert len(rows) == len(expected)
        assert all(row.background and row.source_message_id is None for row in rows)
        assert all("Confidential" not in row.input_body and row.raw_owner_input is None for row in rows)
        for row in rows:
            assert json.loads(row.input_body)["topic_id"] == chat
        product._publish_duet_message(db, db.get(Chat, chat), message)
        assert len(receipts(db, message.id)) == len(rows)
    for row in rows:
        incoming = product.get_runtime_context(row.id)
        assert incoming["messages"] == [] and not incoming.get("source_message") and not incoming.get("summary")
        assert not incoming.get("input_files") and not incoming.get("tracking")


def test_ordinary_update_notifies_admin_even_without_mentions_and_admin_can_finish_silently(product):
    chat = approved_chat(product, "duet-admin")
    _, context = origin(product, "owner-maya")
    sent = send(product, context, chat, "Progress without a mention")
    with product.transaction() as db:
        rows = receipts(db, sent["message_id"])
        assert len(rows) == 1 and rows[0].target_twin_id == "twin-alex"
        notification = rows[0]
    assert product.publish_runtime_output(notification.id, "No public reply is needed", notification.session_id, "synthetic") is None
    assert len(product.chat_detail("owner-alex", chat)["messages"]) == 1


def test_read_is_explicit_and_does_not_wake_anyone(product):
    chat = approved_chat(product, "duet-read")
    _, context = origin(product)
    sent = send(product, context, chat, "Read this through the tool", ["owner-maya"])
    with product.transaction() as db:
        row = next(row for row in receipts(db, sent["message_id"]) if row.target_twin_id == "twin-maya")
        before = db.scalar(select(func.count()).select_from(ReplyRequest))
    incoming = product.get_runtime_context(row.id)
    page = tool(product, incoming, "read_duet_topic", {"chat_id": chat, "limit": 10})
    assert page["messages"][0]["body"] == "Read this through the tool"
    with product.transaction() as db:
        assert db.scalar(select(func.count()).select_from(ReplyRequest)) == before
    reply = send(product, incoming, chat, "Explicit tool reply from current topic")
    assert reply["sent"]


def test_more_than_six_posts_are_published_without_budget_or_continue(product):
    chat = approved_chat(product, "duet-unlimited")
    request, context = origin(product)
    with product.transaction() as db:
        chain = db.get(CausalChain, request["causal_chain_id"])
        chain.spent = chain.allowance = 6
    sent = [send(product, context, chat, f"Post {index}") for index in range(13)]
    assert all(row["sent"] and row["status"] == "sent" for row in sent)
    detail = duet_conversations.detail(product, "owner-alex", chat)
    assert len(detail["messages"]) == 13 and detail["pending_messages"] == []
    assert "send_budgets" not in detail
    with product.transaction() as db:
        assert db.get(CausalChain, request["causal_chain_id"]).spent == 6
        assert all(len(receipts(db, row["message_id"])) == 1 for row in sent)


def test_message_and_notifications_are_atomic(product):
    chat_id = approved_chat(product, "duet-atomic", ["owner-alex", "owner-maya", "owner-jordan"])
    with product.transaction() as db:
        before = db.scalar(select(func.count()).select_from(ReplyRequest))
    message_id = None
    with pytest.raises(RuntimeError, match="synthetic rollback"), product.transaction() as db:
        chat = db.get(Chat, chat_id)
        message = product.append_message(db, chat, author_id="twin-alex", kind="twin", body="Atomic fixture",
            mention_owner_ids=["owner-maya"], causal_chain_id=product.new_causal_chain(db, "owner-alex").id)
        message_id = message.id
        assert len(receipts(db, message.id)) == 2
        raise RuntimeError("synthetic rollback")
    with product.transaction() as db:
        assert db.get(Message, message_id) is None
        assert db.scalar(select(func.count()).select_from(ReplyRequest)) == before


def test_concurrent_replay_and_service_restart_do_not_duplicate_notifications(product):
    chat = approved_chat(product, "duet-replay")
    _, context = origin(product)
    operation = uuid4().hex
    with ThreadPoolExecutor(max_workers=4) as pool:
        sent = list(pool.map(lambda _: send(product, context, chat, "One committed post", ["owner-maya"], operation=operation), range(4)))
    assert len({value["message_id"] for value in sent}) == 1
    from app.service import ProductService
    restarted = ProductService()
    try:
        assert send(restarted, context, chat, "One committed post", ["owner-maya"], operation=operation) == sent[0]
        with restarted.transaction() as db:
            assert len(receipts(db, sent[0]["message_id"])) == 2
    finally:
        restarted.db_pool.shutdown()


def test_mentions_cannot_admit_outsiders_or_bypass_connection_permissions(product):
    chat = approved_chat(product, "duet-mentions")
    _, context = origin(product)
    with pytest.raises(HTTPException, match="Only current topic participants"):
        send(product, context, chat, "@ outsider", ["owner-jordan"])
    with pytest.raises(HTTPException):
        send(product, context, chat, "Duplicate mention", ["owner-maya", "owner-maya"])
    assert product.chat_detail("owner-alex", chat)["messages"] == []


def test_edit_own_post_preserves_history_and_sends_metadata_notification_once(product):
    chat = approved_chat(product, "duet-edit")
    _, context = origin(product)
    sent = send(product, context, chat, "Original post")
    args = {"chat_id": chat, "message_id": sent["message_id"], "expected_revision": 1,
        "body": "Revised post", "mention_owner_ids": ["owner-maya"]}
    operation = uuid4().hex
    edited = tool(product, context, "edit_duet_post", args, operation=operation)
    assert edited["revision"] == 2
    assert tool(product, context, "edit_duet_post", args, operation=operation) == edited
    with product.transaction() as db:
        rows = receipts(db, sent["message_id"], 2)
        assert len(rows) == 2 and all("Revised post" not in row.input_body for row in rows)
    page = tool(product, context, "read_duet_post_history", {"chat_id": chat, "message_id": sent["message_id"], "limit": 1})
    assert page["revisions"][0]["body"] == "Revised post" and page["has_more"]
    older = tool(product, context, "read_duet_post_history", {"chat_id": chat, "message_id": sent["message_id"], "before_revision": 2})
    assert older["revisions"][0]["body"] == "Original post" and not older["has_more"]
    with pytest.raises(HTTPException, match="Post changed"):
        tool(product, context, "edit_duet_post", args)
    _, other = origin(product, "owner-maya")
    with pytest.raises(HTTPException, match="only its own"):
        tool(product, other, "edit_duet_post", {**args, "expected_revision": 2})


def test_history_rechecks_current_post_access(product):
    chat = approved_chat(product, "duet-history")
    _, context = origin(product)
    sent = send(product, context, chat, "Original")
    tool(product, context, "edit_duet_post", {"chat_id": chat, "message_id": sent["message_id"],
        "expected_revision": 1, "body": "Revision", "mention_owner_ids": []})
    with product.transaction() as db:
        db.get(Message, sent["message_id"]).state = "withdrawn"
    with pytest.raises(HTTPException):
        tool(product, context, "read_duet_post_history", {"chat_id": chat, "message_id": sent["message_id"]})


def test_close_blocks_notifications_execution_and_late_posts(product):
    chat = approved_chat(product, "duet-closed")
    _, context = origin(product)
    sent = send(product, context, chat, "Committed before close", ["owner-maya"])
    with product.transaction() as db:
        notification_id = receipts(db, sent["message_id"])[0].id
    duet_conversations.close_conversation(product, "owner-maya", chat)
    with pytest.raises(HTTPException):
        product.get_runtime_context(notification_id)
    with pytest.raises(HTTPException):
        send(product, context, chat, "Forbidden late post")


def test_read_then_edit_keeps_consumed_revision_valid_for_followup_tools(product):
    chat = approved_chat(product, "duet-read-edit")
    _, context = origin(product)
    sent = send(product, context, chat, "Original finding")
    tool(product, context, "read_duet_topic", {"chat_id": chat, "limit": 10})
    tool(product, context, "edit_duet_post", {"chat_id": chat, "message_id": sent["message_id"],
        "expected_revision": 1, "body": "Corrected finding", "mention_owner_ids": []})
    assert send(product, context, chat, "Correction is complete")["sent"]
