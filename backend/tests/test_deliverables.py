"""Explicit file publication and safe preview through real isolated product paths."""
import asyncio
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from test_chat_attachments_ui_acceptance import attachments as attachments  # noqa: PLC0414
from test_chat_attachments_ui_acceptance import send, upload
from test_file_workspace_acceptance import files as files  # noqa: PLC0414

from app.models import ChatAttachment, Message, ProductOperation


def prepare(files, name="result.md", data=b"# Completed report\n", chat="private-alex"):
    product, client, root = files
    sent = send(client, chat=chat, body="Prepare a synthetic deliverable").json()
    request = sent.get("request")
    if not request:
        with product.transaction() as db:
            group, _ = product.membership(db, "owner-alex", chat)
            request = product.request_json(db, product.accept_request(db, group, "twin-alex", "owner-alex", "manual", uuid4().hex, source_message_id=sent["message"]["id"]), "owner-alex")
    context = product.get_runtime_context(request["id"])
    path = root / chat / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path, context


def present(product, context, paths, *, operation=None, **extra):
    return asyncio.run(product.runtime_tool("present_files", {"paths": [str(p) for p in paths], **extra},
        {**context, "operation_id": operation or uuid4().hex}))


def test_published_snapshot_is_an_actual_twin_message_and_replay_never_recopies(files):
    product, client, _ = files
    source, context = prepare(files)
    operation = uuid4().hex
    result = present(product, context, [source], operation=operation, caption="Report ready")
    message = result["message"]
    assert message["kind"] == "twin" and message["author_id"] == "twin-alex"
    assert message["causal_chain_id"] == context["causal_chain_id"]
    attachment = message["attachments"][0]
    assert attachment["preview_kind"] == "markdown"
    assert client.get(attachment["url"]).content == b"# Completed report\n"
    source.write_bytes(b"Changed working file")
    replay = present(product, context, [source], operation=operation, caption="Report ready")
    assert replay["message_id"] == result["message_id"]
    assert client.get(attachment["url"]).content == b"# Completed report\n"
    assert client.get(attachment["preview_url"]).text == "# Completed report\n"
    with pytest.raises(HTTPException) as conflict:
        present(product, context, [source], operation=operation, caption="Different action")
    assert conflict.value.status_code == 409
    with product.transaction() as db:
        row = db.get(ChatAttachment, attachment["id"])
        assert row.message_id == message["id"] and row.request_id == context["request_id"]
        assert db.scalar(select(func.count()).select_from(ChatAttachment)) == 1
        assert db.get(ProductOperation, operation)
    # Same caption with a different file is a separate explicit delivery.
    second = present(product, context, [source], caption="Report ready")
    assert second["message_id"] != result["message_id"]
    assert client.get(second["message"]["attachments"][0]["url"]).content == b"Changed working file"


@pytest.mark.parametrize(("name", "data", "kind", "mime"), [
    ("report.md", b"# Title\n<script>bad()</script>", "markdown", "text/plain"),
    ("report.txt", b"<script>bad()</script>", "text", "text/plain"),
    ("report.html", b"<!doctype html><button onclick='this.textContent=1'>0</button>", "html", "text/plain"),
    ("report.pdf", b"%PDF-1.4\nsynthetic header", "pdf", "application/pdf"),
])
def test_preview_is_authorized_bytes_and_html_never_runs_on_application_origin(files, name, data, kind, mime):
    product, client, _ = files
    path, context = prepare(files, name, data)
    row = present(product, context, [path])["message"]["attachments"][0]
    assert row["preview_kind"] == kind
    preview = client.get(row["preview_url"])
    assert preview.status_code == 200 and preview.content == data
    assert preview.headers["content-type"].startswith(mime)
    assert preview.headers["x-content-type-options"] == "nosniff"
    assert preview.headers["cache-control"] == "private, no-store"
    assert "sandbox" in preview.headers["content-security-policy"]
    download = client.get(row["url"])
    assert download.headers["content-disposition"].startswith("attachment;")
    assert client.get(row["preview_url"], headers={"x-test-owner": "owner-maya"}).status_code in {403, 404}


def test_preview_limit_and_immutable_snapshot_tampering_are_explicit_errors(files):
    product, client, root = files
    path, context = prepare(files, "large.txt", b"x" * (1024 * 1024 + 1))
    row = present(product, context, [path])["message"]["attachments"][0]
    assert client.get(row["preview_url"]).status_code == 415
    assert len(client.get(row["url"]).content) == 1024 * 1024 + 1
    with product.transaction() as db:
        snapshot = root / db.get(ChatAttachment, row["id"]).relative_path
    snapshot.write_bytes(b"Changed snapshot")
    assert client.get(row["url"]).status_code == 409
    assert client.get(row["preview_url"]).status_code == 409


def test_group_shared_source_and_owner_source_can_be_delivered_but_private_input_cannot(files):
    product, client, root = files
    shared, context = prepare(files, "shared.txt", b"shared report", "group-alex-maya")
    result = present(product, context, [shared])
    item = result["message"]["attachments"][0]
    assert client.get(item["url"], headers={"x-test-owner": "owner-maya"}).content == b"shared report"
    private = root / "private-alex" / "finished.txt"
    private.parent.mkdir(exist_ok=True); private.write_bytes(b"owner approved report")
    assert present(product, context, [private])["sent"]
    uploaded = upload(client, chat="group-alex-maya", name="raw.txt", mime="text/plain", data=b"private raw input").json()
    rewrite = send(client, chat="group-alex-maya", body="Rewrite privately", ids=[uploaded["id"]], reply_for_me=True).json()
    assert rewrite["request"]
    with pytest.raises(HTTPException, match="Private owner-input"):
        present(product, context, [root / "group-alex-maya" / "uploads" / uploaded["id"] / "raw.txt"])
    with product.transaction() as db:
        db.get(Message, result["message_id"]).state = "withdrawn"
    assert client.get(item["url"], headers={"x-test-owner": "owner-maya"}).status_code == 404


def test_publication_rechecks_source_scope_symlinks_and_limits(files, tmp_path):
    product, client, root = files
    path, context = prepare(files)
    outside = tmp_path / "outside.txt"; outside.write_text("outside")
    with pytest.raises(HTTPException):
        present(product, context, [outside])
    link = path.parent / "link.txt"; link.symlink_to(path)
    with pytest.raises(HTTPException):
        present(product, context, [link])
    with pytest.raises(HTTPException):
        present(product, context, [path] * 5)
    with pytest.raises(HTTPException):
        present(product, context, [path, path])
    uploaded = upload(client, name="old.txt", mime="text/plain", data=b"old").json()
    original = send(client, body="Original", ids=[uploaded["id"]]).json()["message"]
    with product.transaction() as db:
        db.get(Message, original["id"]).state = "withdrawn"
    with pytest.raises(HTTPException):
        present(product, context, [root / "private-alex" / "uploads" / uploaded["id"] / "old.txt"])


def test_rollback_retry_reuses_only_the_protected_original_snapshot(files, monkeypatch):
    product, client, root = files
    path, context = prepare(files)
    operation = uuid4().hex
    route = product.route_committed_message
    def fail_before_commit(*args):
        raise RuntimeError("Synthetic known rollback after capture")
    monkeypatch.setattr(product, "route_committed_message", fail_before_commit)
    with pytest.raises(RuntimeError, match="known rollback"):
        present(product, context, [path], operation=operation)
    with product.transaction() as db:
        assert db.get(ProductOperation, operation) is None
        assert db.scalar(select(func.count()).select_from(ChatAttachment)) == 0
    with pytest.raises(HTTPException) as hidden:
        product.workspace("owner-alex", "private-alex", "uploads")
    assert hidden.value.status_code == 404
    path.write_text("Later working copy")
    monkeypatch.setattr(product, "route_committed_message", route)
    result = present(product, context, [path], operation=operation)
    row = result["message"]["attachments"][0]
    assert client.get(row["url"]).content == b"# Completed report\n"
    assert len(list((root / "private-alex" / "uploads").glob("*/result.md"))) == 1
