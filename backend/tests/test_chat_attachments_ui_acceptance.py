"""Independent attachment acceptance: disposable PostgreSQL + offline SDK provider.

No application database, saved credential, or network model is used. The tiny PNG
is generated here so the test never depends on a user's file.
"""

import asyncio
import base64
import json
import struct
import zlib
from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from agent_runtime import AssistantMessageEventStream, assistant_message
from app import api as api_module
from app import service as service_module
from app.models import Message, ReplyRequest, RoutingDecision
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


async def run_offline(product, request, history=False):
    runtime = RuntimeBridge(product)
    await runtime.start()
    product.bind_runtime(runtime)
    calls = []

    def provider(model, context, options):
        # Copy the actual SDK model boundary, including system and tool messages.
        calls.append(json.loads(json.dumps(context.messages)))
        stream = AssistantMessageEventStream()
        tool = history and len(calls) == 1
        content = ([{"type": "toolCall", "id": "attachment-history", "name": "history", "arguments": {}}]
                   if tool else [{"type": "text", "text": "Offline attachment accepted."}])
        stream.push({"type": "done", "message": assistant_message(
            model, content=content, stopReason="toolUse" if tool else "stop")})
        return stream

    runtime.models.register_api("offline-regression", provider)
    try:
        await product.execute_request(request["id"])
        assert product.get_request(request["id"])["status"] == "completed", product.get_request(request["id"]).get("error")
        return calls
    finally:
        await runtime.close()


def test_http_image_upload_source_acl_and_published_persistence(attachments):
    product, client = attachments
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


def test_bad_image_and_claimed_mime_do_not_create_uploads(attachments):
    _, client = attachments
    for data in (b"not an image", png_bytes()[:30]):
        response = upload(client, data=data, name="misleading.png", mime="image/png")
        assert response.status_code == 415, response.text
    assert upload(client, data=b"hello", name="../outside.txt", mime="text/plain").status_code in (400, 422)


def test_attachment_limits_and_unsupported_reply_for_me_do_not_schedule(attachments):
    product, client = attachments
    too_large = upload(client, data=b"x" * (20 * 1024 * 1024 + 1), name="large.txt", mime="text/plain")
    assert too_large.status_code == 413
    item = upload(client, data=b"unsupported", name="note.txt", mime="text/plain").json()
    before = count(product, ReplyRequest), count(product, Message)
    response = send(client, ids=[item["id"]], reply_for_me=True)
    assert response.status_code == 400
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
def test_unsupported_only_creates_no_reply_or_routing_and_cannot_manual_ask(attachments, group_chat):
    product, client = attachments
    chat = product.create_chat("owner-alex", ["owner-maya"], "unsupported only")["id"] if group_chat else "private-alex"
    item = upload(client, chat=chat, data=b"FILE_CONTENT_MUST_NEVER_REACH_MODEL", name="DO_NOT_PROMPT_THIS_FILE.txt", mime="text/plain").json()
    assert item["kind"] == "unsupported"
    before = count(product, ReplyRequest), count(product, RoutingDecision)
    response = send(client, chat=chat, ids=[item["id"]])
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["request"] is None
    assert result["message"]["attachments"][0]["kind"] == "unsupported"
    assert (count(product, ReplyRequest), count(product, RoutingDecision)) == before
    twin = next(actor for actor in product.chat_detail("owner-alex", chat)["members"]
                if actor["kind"] == "twin" and actor.get("owner_id") == "owner-alex")
    asked = client.post(f"/api/chats/{chat}/reply-requests", json={
        "message_id": result["message"]["id"], "target_twin_id": twin["id"], "client_request_key": uuid4().hex})
    assert asked.status_code in (400, 415, 422), asked.text
    assert (count(product, ReplyRequest), count(product, RoutingDecision)) == before


def test_text_with_unsupported_file_never_leaks_filename_or_content_to_sdk(attachments):
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
    assert filename not in wire and content not in wire
    assert '"type": "image"' not in wire
    assert any(message.get("role") == "toolResult" for message in calls[-1])


def test_image_only_reaches_actual_sdk_image_content_without_old_images(attachments):
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
    assert len(images) == 1
    assert images[0]["mimeType"] == "image/png"
    assert base64.b64decode(images[0]["data"]) == raw


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


def test_reply_for_me_raw_attachment_stays_private_after_generated_output(attachments):
    product, client = attachments
    group = product.create_chat("owner-alex", ["owner-maya"], "private raw attachment")["id"]
    image = upload(client, chat=group).json()
    response = send(client, chat=group, body="Privately use this image to write my reply.", ids=[image["id"]], reply_for_me=True)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["message"] is None
    calls = asyncio.run(run_offline(product, result["request"]))
    assert len(calls) == 1
    own = product.chat_detail("owner-alex", group)
    other = product.chat_detail("owner-maya", group)
    generated = next(message for message in own["messages"] if message.get("request_id") == result["request"]["id"])
    public = next(message for message in other["messages"] if message["id"] == generated["id"])
    assert image["id"] not in json.dumps(public)
    assert image["name"] not in json.dumps(public)
    assert client.get(image["url"], headers={"x-test-owner": "owner-maya"}).status_code in (403, 404)
    assert client.get(image["url"]).status_code == 200
