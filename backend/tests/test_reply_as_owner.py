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

from app import question_api, questions
from app.models import Membership, Message, ProductOperation, ReplyRequest
from app.question_models import QuestionRequest


@pytest.fixture(autouse=True)
def question_route(files, monkeypatch):
    product, client, _ = files
    monkeypatch.setattr(question_api, "service", product)
    client.app.include_router(question_api.router)


def choose(client, question, answer, **kwargs):
    return client.post(f"/api/questions/{question['id']}/answer",
                       json={"selected_option": True, "answer": answer}, **kwargs)



def owner_turn(files, text, **kwargs):
    product, client, _ = files
    result = send(client, body=text, **kwargs).json()
    request = result["request"]
    return result["message"]["id"], {
        **product.get_runtime_context(request["id"]), "runtime_session_id": request["session_id"],
        "runtime_run_id": "test-run-" + request["id"], "runtime_command_id": "test-command-" + request["id"]}


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
    pending = outgoing(product, send_ctx, anchor, attachment_ids=[file_id], operation=operation)
    assert not pending["sent"] and pending["status"] == "confirmation_required"
    question = pending["question"]
    assert question["reply_preview"]["original_files"] == ["story.txt"] and question["reply_preview"]["files"] == ["review.md"]
    assert question["options"] == ["Confirm send", "Cancel"]
    assert pending == outgoing(product, send_ctx, anchor, attachment_ids=[file_id], operation=operation)
    assert question["id"] == outgoing(product, send_ctx, anchor, attachment_ids=[file_id])["question"]["id"]
    assert choose(client, question, "Confirm send", headers={"x-test-owner": "owner-maya"}).status_code == 404
    assert client.post(f"/api/questions/{question['id']}/answer", json={"answer": "Confirm send"}).status_code == 422
    assert choose(client, question, "I guess so").status_code == 422
    assert send(client, body="Confirm send", reply_to_message_id=question["message_id"]).status_code == 422
    with pytest.raises(HTTPException):
        questions.answer_from_message(product, {"question_id": question["id"], "message_id": send_ctx["source_message_id"]}, send_ctx)
    with product.transaction() as db:
        assert not db.scalar(select(Message.id).where(Message.reply_to_message_id == original["id"]))
    assert choose(client, question, "Confirm send").json()["status"] == "answered"
    assert choose(client, question, "Confirm send").json()["status"] == "answered"
    assert choose(client, question, "Cancel").status_code == 409
    assert outgoing(product, send_ctx, anchor, attachment_ids=[file_id], operation=operation)["sent"]
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
    pending = outgoing(product, current, anchor, body=reviewed["body"], review_message_id=reviewed["id"])
    q = pending["question"]
    assert q["reply_preview"]["body"] == reviewed["body"] and q["reply_preview"]["audience"]
    assert choose(client, q, "Cancel").json()["status"] == "cancelled"
    assert client.post(f"/api/questions/{q['id']}/dismiss").json()["status"] == "cancelled"
    assert choose(client, q, "Confirm send").status_code == 409
    with product.transaction() as db:
        assert not db.scalar(select(Message.id).where(Message.reply_to_message_id == original["id"]))
    with pytest.raises(HTTPException):
        outgoing(product, current, original["id"], body=reviewed["body"], review_message_id=reviewed["id"])
    with product.transaction() as db:
        db.get(Message, original["id"]).state = "withdrawn"
    with pytest.raises(HTTPException):
        outgoing(product, current, anchor, body=reviewed["body"], review_message_id=reviewed["id"])

@pytest.mark.parametrize("change", ["membership", "original", "bytes", "rejection"])
def test_no_delivery_when_confirmation_is_no_longer_valid(files, change):
    product, client, root = files
    received = send(client, chat="group-team", owner="owner-maya", body="Review the spec").json()["message"]
    anchor, ctx = owner_turn(files, "Review this.", reference_message_id=received["id"])
    path = root / "private-alex" / "notes.txt"
    path.parent.mkdir(exist_ok=True, parents=True)
    path.write_text("Reviewed")
    review = present(product, ctx, [path], caption="Ready.")["message"]
    _, ctx = owner_turn(files, "Send the notes.")
    file = review["attachments"][0]
    q = outgoing(product, ctx, anchor, attachment_ids=[file["id"]])["question"]
    if change == "bytes":
        from pathlib import Path
        Path(file["workspace_path"]).write_text("Replaced")
    elif change == "rejection":
        assert client.post(f"/api/questions/{q['id']}/dismiss").json()["status"] == "cancelled"
    else:
        with product.transaction() as db:
            if change == "membership":
                from app.models import now
                db.scalar(select(Membership).where(Membership.chat_id == "group-team",
                    Membership.owner_id == "owner-jordan")).left_at = now()
            else:
                db.get(Message, received["id"]).state = "withdrawn"
    assert choose(client, q, "Confirm send").status_code in {404, 409}
    with product.transaction() as db:
        assert not db.scalar(select(Message.id).where(Message.reply_to_message_id == received["id"]))
        assert db.get(QuestionRequest, q["id"]).status != "answered"
