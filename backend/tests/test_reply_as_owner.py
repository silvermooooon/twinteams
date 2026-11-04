"""The human chat receives only what its owner chose to send after private review."""
import asyncio
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from test_chat_attachments_ui_acceptance import attachments as attachments  # noqa: PLC0414
from test_chat_attachments_ui_acceptance import send, upload
from test_deliverables import present
from test_file_workspace_acceptance import files as files  # noqa: PLC0414

from app.models import Message, ProductOperation, ReplyRequest


def owner_turn(files, text, **kwargs):
    product, client, _ = files
    result = send(client, body=text, **kwargs).json()
    request = result["request"]
    return result["message"]["id"], {
        **product.get_runtime_context(request["id"]), "runtime_session_id": request["session_id"]}


def outgoing(product, context, anchor, *, operation=None, body="", **kwargs):
    return asyncio.run(product.runtime_tool("reply_as_owner",
        {"reference_message_id": anchor, "body": body, **kwargs},
        {**context, "operation_id": operation or uuid4().hex}))


def test_review_then_send_file_as_owner_once(files):
    product, client, root = files
    old_path = root / "private-alex" / "old.txt"
    old_path.parent.mkdir(exist_ok=True, parents=True)
    old_path.write_text("Different private file")
    _, old_ctx = owner_turn(files, "Prepare a private file")
    old = present(product, old_ctx, [old_path])["message"]["attachments"][0]
    original_file = upload(client, chat="group-team", owner="owner-maya", name="story.txt", mime="text/plain",
                           data=b"Please review the schedule.").json()
    original = send(client, chat="group-team", owner="owner-maya", ids=[original_file["id"]]).json()["message"]
    anchor, review_ctx = owner_turn(files, "Review the attached story privately.", reference_message_id=original["id"])
    path = root / "private-alex" / "review.md"
    path.write_text("# Recommendation\nSet the deadline after the pilot.")
    review = present(product, review_ctx, [path], caption="Please allow time for a pilot.")["message"]
    file_id = review["attachments"][0]["id"]
    with pytest.raises(HTTPException, match="later owner instruction"):
        outgoing(product, review_ctx, anchor, attachment_ids=[file_id])
    _, send_ctx = owner_turn(files, "Send them the reviewed file.")
    with pytest.raises(HTTPException, match="Show the file"):
        outgoing(product, send_ctx, anchor, attachment_ids=[old["id"]])
    with pytest.raises(HTTPException, match="exact reply text"):
        outgoing(product, send_ctx, anchor, body="Our private financial forecast", review_message_id=review["id"])
    with pytest.raises(HTTPException, match="primary private Duet"):
        outgoing(product, {**send_ctx, "runtime_session_id": "worker-session"}, anchor, attachment_ids=[file_id])
    with product.transaction() as db:
        requests_before = db.scalar(select(func.count()).select_from(ReplyRequest))
    operation = uuid4().hex
    sent = outgoing(product, send_ctx, anchor, attachment_ids=[file_id], operation=operation)
    # Both replay and a redundant second tool call in this turn return the posted message.
    assert sent == outgoing(product, send_ctx, anchor, attachment_ids=[file_id], operation=operation)
    assert sent["message_id"] == outgoing(product, send_ctx, anchor, attachment_ids=[file_id])["message_id"]
    maya = {"x-test-owner": "owner-maya"}
    rows = client.get("/api/chats/group-team", headers=maya).json()["messages"]
    msg, = [row for row in rows if (row.get("reply_to") or {}).get("id") == original["id"]]
    assert msg["kind"] == "human" and msg["author_id"] == "owner-alex"
    assert msg["body"] == "" and msg["request_id"] is None and msg["reply_to"]["body"] == "story.txt"
    attachment, = msg["attachments"]
    assert attachment["id"] != file_id
    assert client.get(attachment["url"], headers=maya).content == path.read_bytes()
    assert client.get(review["attachments"][0]["url"], headers=maya).status_code in {403, 404}
    with product.transaction() as db:
        assert db.get(Message, msg["id"]).request_id == send_ctx["request_id"]
        assert db.get(ProductOperation, operation).name == "reply_as_owner"
        assert db.scalar(select(func.count()).select_from(ReplyRequest)) == requests_before


def test_exact_text_and_origin_boundaries(files):
    product, client, root = files
    original = send(client, chat="group-team", owner="owner-maya", body="Please review").json()["message"]
    anchor, ctx = owner_turn(files, "Review this.", reference_message_id=original["id"])
    path = root / "private-alex" / "check.txt"
    path.parent.mkdir(exist_ok=True, parents=True)
    path.write_text("Check")
    reviewed = present(product, ctx, [path], caption="Run one pilot first.")["message"]
    _, current = owner_turn(files, "Yes, reply to them.")
    assert outgoing(product, current, anchor, body=reviewed["body"], review_message_id=reviewed["id"])["sent"]
    with pytest.raises(HTTPException):
        outgoing(product, current, original["id"], body=reviewed["body"], review_message_id=reviewed["id"])
    with product.transaction() as db:
        db.get(Message, original["id"]).state = "withdrawn"
    with pytest.raises(HTTPException):
        outgoing(product, current, anchor, body=reviewed["body"], review_message_id=reviewed["id"])
