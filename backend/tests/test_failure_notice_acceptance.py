"""Independent failure projection checks: HTTP and disposable PG, no worker/model."""
from uuid import uuid4

import pytest
from test_chat_attachments_ui_acceptance import attachments as attachments  # noqa: PLC0414
from test_chat_attachments_ui_acceptance import send

from app.models import Chat, ReplyRequest


@pytest.mark.parametrize("terminal", ["completed", "cancelled"])
def test_terminal_retry_hides_old_failure_without_deleting_audit_or_messages(attachments, terminal):
    product, client = attachments
    initial = send(client, body="Synthetic retry source").json()
    old_id = initial["request"]["id"]
    with product.transaction() as db:
        old = db.get(ReplyRequest, old_id)
        old.status, old.error = "failed", "Synthetic historical failure"
    assert [row["id"] for row in client.get("/api/chats/private-alex").json()["requests"]] == [old_id]
    response = client.post(f"/api/reply-requests/{old_id}/retry", json={"client_request_key": uuid4().hex})
    assert response.status_code == 200, response.text
    new_id = response.json()["id"]
    assert new_id != old_id
    assert [row["id"] for row in client.get("/api/chats/private-alex").json()["requests"]] == [new_id]
    with product.transaction() as db:
        db.get(ReplyRequest, new_id).status = terminal
        if terminal == "completed":
            output = product.append_message(db, db.get(Chat, "private-alex"), author_id="twin-alex", kind="twin",
                body="Synthetic successful retry output", request_id=new_id)
            output_id = output.id
    # Pagination/refresh cannot resurrect a superseded failure.
    for query in ("", "?limit=1", "?limit=1&before_seq=2"):
        detail = client.get("/api/chats/private-alex" + query)
        assert detail.status_code == 200 and detail.json()["requests"] == []
    with product.transaction() as db:
        assert db.get(ReplyRequest, old_id).status == "failed"
        assert db.get(ReplyRequest, old_id).error == "Synthetic historical failure"
        assert db.get(ReplyRequest, new_id).status == terminal
    detail = client.get("/api/chats/private-alex").json()
    assert initial["message"]["id"] in {row["id"] for row in detail["messages"]}
    if terminal == "completed":
        assert output_id in {row["id"] for row in detail["messages"]}


def test_latest_failed_request_keeps_owner_retry_and_private_input_filters(attachments):
    product, client = attachments
    chat = "private-alex"
    initial = send(client, chat=chat, body="Synthetic private task input").json()["request"]
    with product.transaction() as db:
        db.get(ReplyRequest, initial["id"]).status = "failed"
    retried = client.post(f'/api/reply-requests/{initial["id"]}/retry', json={"client_request_key": uuid4().hex})
    assert retried.status_code == 200
    latest_id = retried.json()["id"]
    with product.transaction() as db:
        row = db.get(ReplyRequest, latest_id)
        row.status, row.error = "failed", "Synthetic latest failure"
    own = client.get(f"/api/chats/{chat}").json()
    other = client.get(f"/api/chats/{chat}", headers={"x-test-owner": "owner-maya"})
    assert [(row["id"], row["can_retry"]) for row in own["requests"]] == [(latest_id, True)]
    assert other.status_code in {403, 404}
    assert client.post(f"/api/reply-requests/{latest_id}/retry", headers={"x-test-owner": "owner-maya"},
                       json={"client_request_key": uuid4().hex}).status_code == 403


def test_latest_generation_filter_does_not_bypass_source_visibility(attachments):
    product, client = attachments
    from duet_test_support import append_duet, approved_chat, request_duet_reply
    chat = approved_chat(product, "duet-alex-maya")
    source = append_duet(product, chat, "owner-maya", "Synthetic visible source")
    request_id = request_duet_reply(product, "owner-maya", chat, source["id"], "twin-alex")["id"]
    with product.transaction() as db:
        db.get(ReplyRequest, request_id).status = "failed"
    assert request_id in {row["id"] for row in client.get(f"/api/chats/{chat}").json()["requests"]}
    from app.models import Message
    with product.transaction() as db:
        db.get(Message, source["id"]).state = "withdrawn"
    for owner in ("owner-alex", "owner-maya"):
        assert request_id not in {row["id"] for row in client.get(f"/api/chats/{chat}", headers={"x-test-owner": owner}).json()["requests"]}
