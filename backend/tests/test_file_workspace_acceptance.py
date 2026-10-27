"""Independent file acceptance: disposable PG, temporary files and scripted SDK only."""
import asyncio
import base64
import json
from pathlib import Path
from uuid import uuid4

import pytest
from agent_runtime import AssistantMessageEventStream, assistant_message
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import inspect, select, text
from test_chat_attachments_ui_acceptance import attachments as attachments  # noqa: PLC0414
from test_chat_attachments_ui_acceptance import png_bytes, send, upload

from app import personal
from app import service as service_module
from app.models import ChatAttachment, Message, RoutingDecision, WorkspaceNode
from app.runtime_bridge import RuntimeBridge


def file_id(path):
    return "path:" + base64.urlsafe_b64encode(path.encode()).decode().rstrip("=")


@pytest.fixture
def files(attachments, monkeypatch):
    product, client = attachments
    monkeypatch.setattr(personal, "service", product)
    monkeypatch.setattr(personal, "settings", service_module.settings)
    monkeypatch.setenv("AGENT_SECRETS_KEY", Fernet.generate_key().decode())
    yield product, client, service_module.settings.workspace_root
    product.db_pool.shutdown()


def test_single_copy_upload_and_legacy_path_remain_authorized(files):
    product, client, root = files
    item = upload(client, name="archive.zip", mime="application/zip", data=b"synthetic archive").json()
    path = root / "private-alex" / "uploads" / item["id"] / "archive.zip"
    assert item["kind"] == "file" and Path(item["workspace_path"]) == path
    assert path.read_bytes() == b"synthetic archive"
    assert list(root.rglob("archive.zip")) == [path]
    assert product.workspace("owner-alex", "private-alex", "uploads")[0]["name"] == item["id"]
    assert product.workspace_file("owner-alex", "private-alex", file_id(str(path.relative_to(root / "private-alex")))) == path
    # Existing immutable attachment records need no copying/migration to remain readable.
    legacy = root / "private-alex" / ".attachments" / item["id"] / "content"
    legacy.parent.mkdir(parents=True)
    path.rename(legacy)
    with product.transaction() as db:
        db.get(ChatAttachment, item["id"]).relative_path = str(legacy.relative_to(root))
    assert client.get(item["url"]).content == b"synthetic archive"
    assert product.workspace_file("owner-alex", "private-alex", file_id(str(legacy.relative_to(root / "private-alex")))) == legacy
    assert not path.exists()


def test_actual_sdk_reads_image_only_on_demand_and_generated_file_is_browsable(files):
    product, client, root = files
    raw = png_bytes()
    image = upload(client, data=raw).json()
    request = send(client, ids=[image["id"]]).json()["request"]
    calls = []

    async def run():
        runtime = RuntimeBridge(product)
        await runtime.start()
        product.bind_runtime(runtime)

        def offline(model, context, options):
            calls.append(json.loads(json.dumps(context.messages)))
            stream = AssistantMessageEventStream()
            content = ([{"type": "toolCall", "id": "choose-image-read", "name": "read", "arguments": {"path": image["workspace_path"]}},
                        {"type": "toolCall", "id": "create-agent-artifact", "name": "write", "arguments": {"path": "generated/reports/result.md", "content": "Synthetic generated result."}}]
                       if len(calls) == 1 else [{"type": "text", "text": "Offline file work complete."}])
            stream.push({"type": "done", "message": assistant_message(model, content=content, stopReason="toolUse" if len(calls) == 1 else "stop")})
            return stream

        runtime.models.register_api("offline-regression", offline)
        try:
            await product.execute_request(request["id"])
            assert product.get_request(request["id"])["status"] == "completed"
        finally:
            await runtime.close()

    asyncio.run(run())
    assert len(calls) == 2
    assert image["workspace_path"] in json.dumps(calls[0])
    assert not any(part.get("type") == "image" for msg in calls[0] if isinstance(msg.get("content"), list) for part in msg["content"])
    images = [part for msg in calls[1] if msg.get("role") == "toolResult" for part in msg["content"] if part.get("type") == "image"]
    assert len(images) == 1 and base64.b64decode(images[0]["data"]) == raw
    cwd = personal.resolve_twin_workspace("owner-alex")
    assert cwd == root / "private-alex"
    generated = cwd / "generated/reports/result.md"
    assert generated.read_text() == "Synthetic generated result."
    top = product.workspace("owner-alex", "private-alex")
    folder = next(row for row in top if row["name"] == "generated")
    assert "children" not in folder
    assert product.workspace("owner-alex", "private-alex", "generated")[0]["name"] == "reports"
    entry = product.workspace("owner-alex", "private-alex", "generated/reports")[0]
    assert product.workspace_file("owner-alex", "private-alex", entry["id"]) == generated
    assert client.get(f'/api/chats/private-alex/workspace/files/{entry["id"]}/preview').text == generated.read_text()


def test_group_router_gets_file_references_without_pixels_or_file_body(files):
    product, client, root = files
    from duet_test_support import approved_chat
    chat = approved_chat(product, "duet-alex-maya")
    image = upload(client).json()
    document = upload(client, name="instructions.bin", mime="application/octet-stream", data=b"DO_NOT_AUTO_INJECT_72").json()
    request = send(client, body="Share these authorized reference files").json()["request"]
    context = product.get_runtime_context(request["id"])
    result = product._runtime_tool_transaction("present_files", {
        "chat_id": chat, "paths": [image["workspace_path"], document["workspace_path"]]},
        {**context, "operation_id": uuid4().hex})
    delivered = result["message"]["attachments"]
    image, document = delivered
    with product.transaction() as db:
        decision = db.scalar(select(RoutingDecision).where(RoutingDecision.message_id == result["message"]["id"]))
        decision_id = decision.id
    _, payload, _, _, _, _ = product._prepare_route(decision_id)
    assert {row["id"] for row in payload["attachments"]} == {image["id"], document["id"]}
    assert "input_images" not in payload and "DO_NOT_AUTO_INJECT_72" not in json.dumps(payload)
    assert all(Path(row["workspace_path"]).is_relative_to(root / chat) for row in payload["attachments"])
    shared_output = root / chat / "generated" / "group-report.md"
    shared_output.parent.mkdir()
    shared_output.write_text("Intended shared deliverable")
    assert product.workspace("owner-maya", chat, "generated")[0]["name"] == "group-report.md"
    assert product.workspace_file("owner-maya", chat, file_id("generated/group-report.md")) == shared_output
    private = root / "private-alex" / "never-group.txt"
    private.parent.mkdir(exist_ok=True)
    private.write_text("Private execution cwd output")
    assert "never-group.txt" not in json.dumps(product.workspace("owner-alex", chat))
    with pytest.raises(HTTPException):
        product.workspace_file("owner-alex", chat, file_id("../private-alex/never-group.txt"))


@pytest.mark.parametrize("legacy", [False, True])
def test_reserved_workspace_paths_recheck_draft_private_input_withdrawal_and_history(files, legacy):
    product, client, root = files
    chat = product.create_chat("owner-alex", ["owner-maya"], "Synthetic file ACL")["id"]
    item = upload(client, chat=chat, name="private.txt", mime="text/plain", data=b"private draft").json()
    if legacy:
        old = Path(item["workspace_path"])
        path = root / chat / ".attachments" / item["id"] / "content"
        path.parent.mkdir(parents=True)
        old.rename(path)
        with product.transaction() as db:
            db.get(ChatAttachment, item["id"]).relative_path = str(path.relative_to(root))
        item["workspace_path"] = str(path)
    local = str(Path(item["workspace_path"]).relative_to(root / chat))
    assert product.workspace("owner-maya", chat) == []
    with pytest.raises(HTTPException):
        product.workspace_file("owner-maya", chat, file_id(local))
    assert send(client, chat=chat, ids=[item["id"]], reply_for_me=True).status_code == 403
    assert product.workspace_file("owner-alex", chat, file_id(local)).read_bytes() == b"private draft"
    with pytest.raises(HTTPException):
        product.workspace("owner-maya", chat, "uploads/" + item["id"])
    shared = upload(client, chat=chat, name="shared.txt", mime="text/plain", data=b"shared source").json()
    msg = send(client, chat=chat, ids=[shared["id"]]).json()["message"]
    shared_path = str(Path(shared["workspace_path"]).relative_to(root / chat))
    assert product.workspace_file("owner-maya", chat, file_id(shared_path)).read_bytes() == b"shared source"
    product.add_members("owner-alex", chat, ["owner-jordan"], "no_prior")
    with pytest.raises(HTTPException):
        product.workspace_file("owner-jordan", chat, file_id(shared_path))
    with product.transaction() as db:
        db.get(Message, msg["id"]).state = "withdrawn"
    with pytest.raises(HTTPException):
        product.workspace_file("owner-maya", chat, file_id(shared_path))
    assert product.workspace("owner-maya", chat) == []


@pytest.mark.parametrize("reply_for_me", [False, True])
def test_http_long_body_has_no_product_truncation_and_keeps_identity(files, reply_for_me):
    product, client, _ = files
    body = '中文"\\\n' * 10000
    key = uuid4().hex
    first = send(client, body=body, key=key, reply_for_me=reply_for_me)
    if reply_for_me:
        assert first.status_code == 403
        return
    assert first.status_code == 200, first.text
    repeated = send(client, body=body, key=key, reply_for_me=reply_for_me)
    assert repeated.status_code == 200
    if reply_for_me:
        assert first.json()["request"]["id"] == repeated.json()["request"]["id"]
        assert product.get_runtime_context(first.json()["request"]["id"])["raw_owner_input"] == body
    else:
        assert first.json()["message"]["id"] == repeated.json()["message"]["id"]
        assert first.json()["message"]["body"] == body


def test_preview_decoder_failure_is_a_file_and_files_only_rewrite_is_accepted(files):
    _, client, _ = files
    item = upload(client, data=b"not a decoded image", name="unsupported.png", mime="image/png").json()
    assert item["kind"] == "file"
    response = send(client, ids=[item["id"]])
    assert response.status_code == 200 and response.json()["request"]["source"] == "private"
    assert upload(client, data=b"x", name="../escape.bin").status_code == 400


def test_generated_tree_skips_symlinks_and_download_cannot_escape(files, tmp_path):
    product, _, root = files
    private = root / "private-alex"
    private.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("Synthetic outside")
    (private / "linked.txt").symlink_to(outside)
    assert "linked.txt" not in json.dumps(product.workspace("owner-alex", "private-alex"))
    for relative in ("linked.txt", "../outside.txt", "/etc/passwd"):
        with pytest.raises(HTTPException):
            product.workspace_file("owner-alex", "private-alex", file_id(relative))


def test_legacy_registered_directory_descendants_cannot_bypass_revoked_source(files):
    product, client, root = files
    chat = product.create_chat("owner-alex", ["owner-maya"], "Synthetic registered directory")["id"]
    message = send(client, chat=chat, body="Synthetic source").json()["message"]
    nested = root / chat / "legacy-shared" / "nested" / "result.txt"
    nested.parent.mkdir(parents=True)
    nested.write_text("Derived artifact")
    with product.transaction() as db:
        db.add(WorkspaceNode(id="legacy-directory", chat_id=chat, relative_path="legacy-shared",
            source_message_ids=[message["id"]], granted_owner_ids=["owner-alex", "owner-maya"]))
    identifier = file_id("legacy-shared/nested/result.txt")
    assert product.workspace_file("owner-maya", chat, identifier) == nested
    with product.transaction() as db:
        db.get(Message, message["id"]).state = "withdrawn"
    assert product.workspace("owner-maya", chat) == []
    for path in ("legacy-shared", "legacy-shared/nested"):
        with pytest.raises(HTTPException):
            product.workspace("owner-maya", chat, path)
    for entry in ("legacy-directory", identifier):
        with pytest.raises(HTTPException):
            product.workspace_file("owner-maya", chat, entry)


def test_attachment_path_upgrade_is_idempotent_and_preserves_maximum_legal_name(files):
    product, client, root = files
    from app.attachments import upgrade_attachment_schema

    prior = upload(client, name="before.bin", data=b"prior bytes", mime="application/octet-stream").json()
    with product.transaction() as db:
        before = db.get(ChatAttachment, prior["id"]).relative_path
    # Only this disposable schema is altered to emulate the previously deployed column.
    with service_module.engine.begin() as connection:
        connection.execute(text("ALTER TABLE tt_chat_attachments ALTER COLUMN relative_path TYPE VARCHAR(256)"))
    upgrade_attachment_schema(service_module.engine)
    name = "a" * 196 + ".txt"
    item = upload(client, name=name, data=b"maximum legal file name", mime="text/plain").json()
    assert item["name"] == name and len(name) == 200
    path = root / "private-alex" / "uploads" / item["id"] / name
    assert len(str(path.relative_to(root))) > 256
    assert Path(item["workspace_path"]) == path
    assert client.get(item["url"]).content == b"maximum legal file name"
    result = send(client, ids=[item["id"]]).json()
    assert product.get_runtime_context(result["request"]["id"])["input_files"][0]["workspace_path"] == str(path)
    upgrade_attachment_schema(service_module.engine)
    upgrade_attachment_schema(service_module.engine)
    with product.transaction() as db:
        assert db.get(ChatAttachment, prior["id"]).relative_path == before
        assert db.get(ChatAttachment, item["id"]).relative_path == str(path.relative_to(root))
    column = next(column for column in inspect(service_module.engine).get_columns("tt_chat_attachments") if column["name"] == "relative_path")
    assert getattr(column["type"], "length", None) is None
    assert client.get(prior["url"]).content == b"prior bytes"


@pytest.mark.parametrize("delete_before_publish", [False, True])
def test_platform_generated_file_read_retains_live_provenance_through_sdk_output(files, delete_before_publish):
    product, client, root = files
    path = root / "private-alex" / "generated.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("Synthetic generated source evidence.")
    identifier = file_id("generated.txt")
    request = send(client, body="Synthetic platform file task").json()["request"]
    calls = []

    async def run():
        runtime = RuntimeBridge(product)
        await runtime.start()
        product.bind_runtime(runtime)

        def offline(model, context, options):
            calls.append(json.loads(json.dumps(context.messages)))
            stream = AssistantMessageEventStream()
            if len(calls) == 1:
                content = [{"type": "toolCall", "id": "generated-platform-read", "name": "read_workspace_file", "arguments": {"id": identifier}}]
            else:
                if delete_before_publish:
                    path.unlink()
                content = [{"type": "text", "text": "Synthetic answer based on generated source."}]
            stream.push({"type": "done", "message": assistant_message(model, content=content,
                stopReason="toolUse" if len(calls) == 1 else "stop")})
            return stream

        runtime.models.register_api("offline-regression", offline)
        try:
            await product.execute_request(request["id"])
        finally:
            await runtime.close()

    asyncio.run(run())
    assert len(calls) == 2
    tool_result = next(message for message in calls[1] if message.get("role") == "toolResult")
    assert not tool_result.get("isError"), tool_result
    assert "Synthetic generated source evidence." in json.dumps(tool_result)
    with product.transaction() as db:
        outputs = list(db.scalars(select(Message).where(Message.request_id == request["id"])))
        if delete_before_publish:
            assert outputs == []
            assert product.get_request(request["id"])["status"] == "failed"
        else:
            assert len(outputs) == 1
            output_id = outputs[0].id
            assert {"kind": "workspace", "id": identifier} in outputs[0].source_provenance
    if not delete_before_publish:
        assert output_id in {row["id"] for row in product.chat_detail("owner-alex", "private-alex")["messages"]}
        path.unlink()
        assert output_id not in {row["id"] for row in product.chat_detail("owner-alex", "private-alex")["messages"]}
