"""Independent attachment acceptance: disposable PostgreSQL + offline SDK provider.

No application database, saved credential, or network model is used. The tiny PNG
is generated here so the test never depends on a user's file.
"""

import asyncio
import json
import struct
import zlib
from dataclasses import replace
from uuid import uuid4

import pytest
from agent_runtime import AssistantMessageEventStream, assistant_message
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app import api as api_module
from app import service as service_module
from app.models import Message, ReplyRequest
from app.runtime_bridge import RuntimeBridge
from checks.postgres import TestDatabase


def png_bytes(rgb=(210, 35, 45), width=32, height=24):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    pixels = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


@pytest.fixture
def attachments(tmp_path, monkeypatch):
    database = TestDatabase()
    engine = create_engine(database.url.replace("postgresql://", "postgresql+psycopg://"))
    monkeypatch.setattr(service_module, "engine", engine)
    monkeypatch.setattr(service_module, "SessionLocal", sessionmaker(engine, expire_on_commit=False))
    monkeypatch.setattr(service_module, "settings", replace(
        service_module.settings, database_url=database.url, workspace_root=tmp_path / "workspace"))
    monkeypatch.setenv("AGENT_DB_URL", database.url)
    monkeypatch.setenv("AGENT_MEMORY_DIR", str(tmp_path / "memory"))
    product = service_module.ProductService()
    product.initialize()
    product._presence_cache = {owner: {"expires_at": 0, "focused_chats": []}
                               for owner in ("owner-alex", "owner-maya", "owner-jordan")}
    monkeypatch.setattr(api_module, "service", product)
    app = FastAPI()
    app.include_router(api_module.router)

    def test_identity(request: Request):
        return request.headers.get("x-test-owner", "owner-alex")

    app.dependency_overrides[api_module.identity] = test_identity
    client = TestClient(app)
    yield product, client
    client.close()
    engine.dispose()
    database.close()


def upload(client, chat="private-alex", data=None, name="sample.png", mime="image/png", owner="owner-alex"):
    return client.post(f"/api/chats/{chat}/attachments", params={"name": name},
                       content=png_bytes() if data is None else data,
                       headers={"content-type": mime, "x-test-owner": owner})


def send(client, chat="private-alex", body="", ids=(), key=None, owner="owner-alex", **extra):
    return client.post(f"/api/chats/{chat}/messages", headers={"x-test-owner": owner}, json={
        "body": body, "attachment_ids": list(ids), "client_request_key": key or uuid4().hex, **extra})


def count(product, model):
    with product.transaction() as db:
        return db.scalar(select(func.count()).select_from(model))


async def run_offline(product, request, history=False, first_tools=()):
    runtime = RuntimeBridge(product)
    await runtime.start()
    product.bind_runtime(runtime)
    calls = []

    def provider(model, context, options):
        # Copy the actual SDK model boundary, including system and tool messages.
        calls.append(json.loads(json.dumps(context.messages)))
        stream = AssistantMessageEventStream()
        tools = ([("history", {})] if history else first_tools) if len(calls) == 1 else []
        content = ([{"type": "toolCall", "id": f"attachment-{name}", "name": name, "arguments": args}
                    for name, args in tools] or [{"type": "text", "text": "Offline attachment accepted."}])
        stream.push({"type": "done", "message": assistant_message(
            model, content=content, stopReason="toolUse" if tools else "stop")})
        return stream

    runtime.models.register_api("offline-regression", provider)
    try:
        await product.execute_request(request["id"])
        assert product.get_request(request["id"])["status"] == "completed", product.get_request(request["id"]).get("error")
        return calls
    finally:
        await runtime.close()


def test_http_image_upload_source_acl_and_published_persistence(attachments):
    _product, client = attachments
    raw = png_bytes()
    response = upload(client, data=raw)
    assert response.status_code == 200, response.text
    image = response.json()
    assert image["kind"] == "image"
    assert image["mime_type"] == "image/png"
    assert image["size"] == len(raw)
    assert image["width"] == 32 and image["height"] == 24
    assert client.get(image["url"]).content == raw
    assert client.get(image["url"], headers={"x-test-owner": "owner-maya"}).status_code in (403, 404)
    result = send(client, ids=[image["id"]])
    assert result.status_code == 200, result.text
    message = result.json()["message"]
    assert message["attachments"][0]["id"] == image["id"]
    # A fresh service facade uses the same canonical DB/files, not browser memory.
    reopened = service_module.ProductService()
    detail = reopened.chat_detail("owner-alex", "private-alex")
    saved = next(item for item in detail["messages"] if item["id"] == message["id"])
    assert saved["attachments"] == message["attachments"]
    assert client.get(image["url"]).content == raw


def test_preview_unsupported_images_are_accepted_as_files(attachments):
    _, client = attachments
    for data in (b"not an image", png_bytes()[:30]):
        response = upload(client, data=data, name="misleading.png", mime="image/png")
        assert response.status_code == 200, response.text
        assert response.json()["kind"] == "file"
        assert client.get(response.json()["url"]).content == data
    assert upload(client, data=b"hello", name="../outside.txt", mime="text/plain").status_code in (400, 422)


def test_attachment_limits_and_file_only_reply_for_me(attachments):
    product, client = attachments
    too_large = upload(client, data=b"x" * (20 * 1024 * 1024 + 1), name="large.txt", mime="text/plain")
    assert too_large.status_code == 413
    item = upload(client, data=b"unsupported", name="note.txt", mime="text/plain").json()
    before = count(product, ReplyRequest), count(product, Message)
    response = send(client, ids=[item["id"]], reply_for_me=True)
    assert response.status_code == 403
    assert (count(product, ReplyRequest), count(product, Message)) == before
    assert send(client, ids=[item["id"]] * 5).status_code == 422
    assert send(client).status_code == 400


def test_uncommitted_upload_cannot_be_attached_by_other_owner_or_chat(attachments):
    product, client = attachments
    group = product.create_chat("owner-alex", ["owner-maya"], "attachment ACL")["id"]
    image = upload(client, chat=group).json()
    assert client.get(image["url"], headers={"x-test-owner": "owner-maya"}).status_code in (403, 404)
    assert send(client, chat=group, ids=[image["id"]], owner="owner-maya").status_code in (403, 404)
    assert send(client, ids=[image["id"]]).status_code in (400, 403, 404)
    assert send(client, chat=group, ids=[image["id"]]).status_code == 200
    assert client.get(image["url"], headers={"x-test-owner": "owner-maya"}).status_code == 200


def test_no_prior_member_cannot_fetch_old_attachment_original(attachments):
    product, client = attachments
    group = product.create_chat("owner-alex", ["owner-maya"], "attachment history")["id"]
    image = upload(client, chat=group).json()
    message = send(client, chat=group, ids=[image["id"]]).json()["message"]
    product.add_members("owner-alex", group, ["owner-jordan"], "no_prior")
    detail = client.get(f"/api/chats/{group}", headers={"x-test-owner": "owner-jordan"}).json()
    assert message["id"] not in {item["id"] for item in detail["messages"]}
    assert client.get(image["url"], headers={"x-test-owner": "owner-jordan"}).status_code in (403, 404)


@pytest.mark.parametrize("group_chat", [False, True])
def test_file_only_messages_are_model_input_in_private_and_group_chats(attachments, group_chat):
    product, client = attachments
    chat = product.create_chat("owner-alex", ["owner-maya"], "unsupported only")["id"] if group_chat else "private-alex"
    item = upload(client, chat=chat, data=b"FILE_CONTENT_MUST_NEVER_REACH_MODEL", name="DO_NOT_PROMPT_THIS_FILE.txt", mime="text/plain").json()
    assert item["kind"] == "file"
    before = count(product, ReplyRequest)
    response = send(client, chat=chat, ids=[item["id"]])
    assert response.status_code == 200, response.text
    result = response.json()
    assert (result["request"] is None) == group_chat
    assert result["message"]["attachments"][0]["kind"] == "file"
    assert count(product, ReplyRequest) == before + (0 if group_chat else 1)


def test_text_with_file_supplies_references_without_content_to_sdk(attachments):
    product, client = attachments
    filename = "FILENAME_SENTINEL_DO_NOT_INJECT.txt"
    content = "FILE_SENTINEL_DO_NOT_INJECT_93721"
    item = upload(client, data=content.encode(), name=filename, mime="text/plain").json()
    response = send(client, body="Reply only to this visible text.", ids=[item["id"]])
    assert response.status_code == 200, response.text
    calls = asyncio.run(run_offline(product, response.json()["request"], history=True))
    assert len(calls) == 2
    wire = json.dumps(calls)
    assert "Reply only to this visible text." in wire
    assert filename in wire and item["workspace_path"] in wire and content not in wire
    assert '"type": "image"' not in wire
    assert any(message.get("role") == "toolResult" for message in calls[-1])


def test_image_only_initial_sdk_input_is_paths_without_image_bytes(attachments):
    product, client = attachments
    previous = upload(client, data=png_bytes((0, 200, 20)), name="old.png").json()
    send(client, ids=[previous["id"]])
    raw = png_bytes((200, 10, 30))
    current = upload(client, data=raw, name="current.png").json()
    response = send(client, ids=[current["id"]])
    assert response.status_code == 200, response.text
    calls = asyncio.run(run_offline(product, response.json()["request"]))
    assert len(calls) == 1
    images = [part for message in calls[0] if isinstance(message.get("content"), list)
              for part in message["content"] if part.get("type") == "image"]
    assert images == []
    assert current["workspace_path"] in json.dumps(calls)


def test_message_idempotency_includes_attachments(attachments):
    product, client = attachments
    first = upload(client, name="first.png").json()
    second = upload(client, name="second.png", data=png_bytes((0, 30, 190))).json()
    key = uuid4().hex
    original = send(client, body="same text", ids=[first["id"]], key=key)
    repeated = send(client, body="same text", ids=[first["id"]], key=key)
    changed = send(client, body="same text", ids=[second["id"]], key=key)
    assert original.status_code == repeated.status_code == 200
    assert repeated.json()["message"]["id"] == original.json()["message"]["id"]
    assert changed.status_code == 409
    with product.transaction() as db:
        assert db.scalar(select(func.count()).select_from(Message).where(Message.client_request_key == key)) == 1


def test_reply_for_me_cannot_publish_raw_attachments_in_human_chat(attachments):
    product, client = attachments
    group = product.create_chat("owner-alex", ["owner-maya"], "private raw attachment")["id"]
    image = upload(client, chat=group).json()
    before = count(product, ReplyRequest), count(product, Message)
    response = send(client, chat=group, body="Rewrite this image", ids=[image["id"]], reply_for_me=True)
    assert response.status_code == 403
    assert (count(product, ReplyRequest), count(product, Message)) == before
    assert client.get(image["url"], headers={"x-test-owner": "owner-maya"}).status_code in (403, 404)
    assert client.get(image["url"]).status_code == 200


def test_owner_references_human_review_and_reads_original_files(attachments):
    product, client = attachments
    group = product.create_chat("owner-alex", ["owner-maya"], "Review team")["id"]
    send(client, chat=group, owner="owner-maya", body="UNRELATED_GROUP_DISCUSSION")
    note = upload(client, group, b"actual review text", "review.md", "text/markdown", "owner-maya").json()
    image = upload(client, group, png_bytes(), "chart.png", "image/png", "owner-maya").json()
    original = send(client, chat=group, owner="owner-maya", body="Please review these files.",
                    ids=[note["id"], image["id"]]).json()["message"]
    original_chat_ids = [row["id"] for row in product.chat_detail("owner-maya", group)["messages"]]
    key = uuid4().hex
    response = send(client, body="Analyze privately.", key=key, reference_message_id=original["id"])
    assert response.status_code == 200, response.text
    private, request = response.json()["message"], response.json()["request"]
    reference = private["message_reference"]
    assert (reference["chat_id"], reference["message_id"], reference["body"]) == (group, original["id"], original["body"])
    assert private["attachments"] == []
    assert [file["id"] for file in reference["attachments"]] == [note["id"], image["id"]]
    assert send(client, body="Analyze privately.", key=key, reference_message_id=original["id"]).json()["message"]["id"] == private["id"]
    assert send(client, body="Analyze privately.", key=key).status_code == 409
    calls = asyncio.run(run_offline(product, request, first_tools=[
        ("read_workspace_file", {"chat_id": group, "id": reference["attachments"][0]["workspace_id"]}),
        ("read", {"path": reference["attachments"][1]["workspace_path"]}),
    ]))
    wire = json.dumps(calls[0])
    assert private["id"] in wire and original["id"] in wire and original["body"] in wire
    assert "UNRELATED_GROUP_DISCUSSION" not in wire and "actual review text" not in wire
    results = [message for message in calls[-1] if message["role"] == "toolResult"]
    assert any("actual review text" in json.dumps(result["content"]) for result in results)
    assert any(part["type"] == "image" for result in results for part in result["content"])
    assert not any(result.get("isError") for result in results)
    follow_up = send(client, body="What were the review files?").json()["request"]
    follow_context = product.get_runtime_context(follow_up["id"])
    assert next(row for row in follow_context["messages"] if row["id"] == private["id"])["message_reference"] == reference
    assert [row["id"] for row in product.chat_detail("owner-maya", group)["messages"]] == original_chat_ids
    with product.transaction() as db:
        db.get(Message, original["id"]).state = "withdrawn"
    retained = next(row for row in product.chat_detail("owner-alex", "private-alex")["messages"] if row["id"] == private["id"])
    assert retained["body"] == private["body"] and retained["message_reference"]["unavailable"]
    assert retained["message_reference"]["attachments"] == []
    new_request = send(client, body="Summarize the already delivered review.").json()["request"]
    new_context = product.get_runtime_context(new_request["id"])
    assert product.authorize_runtime_generation(new_context)
    assert original["body"] not in json.dumps(new_context["messages"])
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        product.authorize_runtime_generation(follow_context)
    assert client.get(reference["attachments"][0]["url"]).status_code == 404


def test_reference_requires_incoming_visible_human_message_and_private_destination(attachments):
    product, client = attachments
    group = product.create_chat("owner-alex", ["owner-maya"], "Review")["id"]
    source = send(client, chat=group, body="Received.", owner="owner-maya").json()["message"]["id"]
    assert send(client, chat=group, body="Review", reference_message_id=source).status_code == 403
    assert send(client, chat="private-maya", owner="owner-maya", body="Review", reference_message_id=source).status_code == 422
    assert send(client, chat="private-jordan", owner="owner-jordan", body="Review", reference_message_id=source).status_code == 403
    product.add_members("owner-alex", group, ["owner-jordan"], "no_prior")
    assert send(client, chat="private-jordan", owner="owner-jordan", body="Review", reference_message_id=source).status_code == 404
    private_source = send(client, body="Maya only.", chat="private-maya", owner="owner-maya").json()["message"]["id"]
    assert send(client, body="Review", reference_message_id=private_source).status_code == 403


def test_compacted_reference_still_depends_on_quoted_source(attachments, monkeypatch):
    product, client = attachments
    monkeypatch.setenv("TWINTEAMS_SUMMARY_AFTER_MESSAGES", "1")
    monkeypatch.setenv("TWINTEAMS_SUMMARY_KEEP_MESSAGES", "1")
    group = product.create_chat("owner-alex", ["owner-maya", "owner-jordan"], "Review")["id"]
    original = send(client, chat=group, owner="owner-maya", body="Review tomorrow's proposal").json()["message"]
    private = send(client, body="Help me review.", reference_message_id=original["id"]).json()["message"]
    send(client, body="Keep the proposed edits private.")
    scope = {"chat_id": "private-alex", "owner_id": "owner-alex"}
    prepared = product.prepare_conversation_summary(scope)
    assert next(row for row in prepared["messages"] if row["id"] == private["id"])["message_reference"]["message_id"] == original["id"]
    product.save_conversation_summary(prepared["scope"], "Maya requested a review tomorrow.")
    assert product.get_conversation_context(scope)["summary"]["body"] == "Maya requested a review tomorrow."
    with product.transaction() as db:
        db.get(Message, original["id"]).body = "A changed source."
    fresh = product.get_conversation_context(scope)
    assert fresh["summary"] is None
    retained = next(row for row in fresh["messages"] if row["id"] == private["id"])
    assert retained["message_reference"]["body"] == "A changed source."


def test_history_tool_records_original_reference_read_outside_initial_window(attachments):
    from fastapi import HTTPException

    from app.models import Chat, ContextManifest

    product, client = attachments
    group = product.create_chat("owner-alex", ["owner-maya"], "Referenced review")["id"]
    original = send(client, chat=group, owner="owner-maya", body="Original document review").json()["message"]
    anchor = send(client, body="Review this later.", reference_message_id=original["id"]).json()["message"]
    with product.transaction() as db:
        private = db.get(Chat, "private-alex")
        for index in range(90):
            product.append_message(db, private, author_id="owner-alex", kind="human", body=f"Later fact {index}")
    request = send(client, body="Find the earlier document review.").json()["request"]
    context = product.get_runtime_context(request["id"])
    assert anchor["id"] not in {row["id"] for row in context["messages"]}
    history = asyncio.run(product.runtime_tool("history", {"before_seq": anchor["seq"] + 1, "limit": 1},
        {**context, "operation_id": uuid4().hex}))
    assert history["messages"][0]["message_reference"]["message_id"] == original["id"]
    with product.transaction() as db:
        manifest = db.get(ContextManifest, context["context_manifest_id"])
        assert any(source.get("chat_id") == group and any(item.get("id") == original["id"]
            for item in source.get("sources", [])) for source in manifest.sources)
        db.get(Message, original["id"]).state = "withdrawn"
    with pytest.raises(HTTPException):
        product.authorize_runtime_generation(context)
