"""File consent uses the existing SDK approval and attachment publication paths."""
import asyncio
import json
from uuid import uuid4

import pytest
from agent_runtime import AssistantMessageEventStream, assistant_message
from duet_test_support import seed_collaborations
from fastapi import HTTPException
from sqlalchemy import func, select
from test_chat_attachments_ui_acceptance import attachments as attachments  # noqa: PLC0414
from test_chat_attachments_ui_acceptance import send, upload
from test_deliverables import prepare, present
from test_file_workspace_acceptance import files as files  # noqa: PLC0414

from app.models import Approval, ChatAttachment, ReplyRequest
from app.runtime_bridge import RuntimeBridge


def test_upload_requires_file_consent_and_sdk_continuation_delivers_to_both_humans(files):
    product, client, root = files
    seed_collaborations(product)
    item = upload(client, name="owner-report.md", mime="text/markdown", data=b"# Owner approved report\n").json()
    request = send(client, body="Share this file with Maya", ids=[item["id"]]).json()["request"]
    path = root / "private-alex" / "uploads" / item["id"] / item["name"]
    action = {"paths": [str(path)], "chat_id": "duet-alex-maya", "caption": "Reviewed report"}
    results, phase, approval_id, shared_id = [], "request", None, None

    async def run():
        nonlocal phase, approval_id, shared_id
        runtime = RuntimeBridge(product)
        await runtime.start()
        product.bind_runtime(runtime)

        def provider(model, context, options):
            nonlocal phase
            results.append(json.loads(json.dumps(context.messages)))
            tool = ("present_files", action) if phase == "request" else (
                ("read_workspace_file", {"chat_id": "group-alex-maya", "id": "shared:" + shared_id})
                    if phase == "read" else (("list_workspace", {"chat_id": "group-alex-maya"})
                        if phase == "list" else None))
            # Each scripted model run makes one real SDK tool call and then finishes.
            if tool:
                name, arguments = tool
                content = [{"type": "toolCall", "id": uuid4().hex, "name": name, "arguments": arguments}]
            else:
                content = [{"type": "text", "text": "File task complete."}]
            stream = AssistantMessageEventStream()
            stream.push({"type": "done", "message": assistant_message(model, content=content,
                stopReason="toolUse" if tool else "stop")})
            phase = "read" if phase == "list" else "finish"
            return stream

        runtime.models.register_api("offline-regression", provider)
        try:
            await product.execute_request(request["id"])
            assert product.get_request(request["id"])["status"] == "completed"
            inbox = product.chat_detail("owner-alex", "private-alex")["approvals"]
            approval_id = next(row["id"] for row in inbox if row["action"] == {"tool": "present_files", "arguments": action})
            assert client.get("/api/chats/group-alex-maya/shared-files").json() == {"files": []}
            assert client.get("/api/chats/group-alex-maya/shared-files", headers={"x-test-owner": "owner-maya"}).json() == {"files": []}
            with product.transaction() as db:
                assert db.scalar(select(func.count()).select_from(ChatAttachment)) == 1
                assert db.get(Approval, approval_id).status == "pending"
            # Repeating an unapproved request keeps the pending consent and publishes nothing.
            context = product.get_runtime_context(request["id"], include_completed=True)
            repeated = await product.runtime_tool("present_files", action, {**context,
                "operation_id": uuid4().hex})
            assert repeated["status"] == "approval_required" and repeated["approval"]["id"] == approval_id
            decision = await product.decide_approval("owner-alex", approval_id, "approved")
            assert decision["accepted"] == "business_persisted"
            await product.reconcile_approvals()
            with product.transaction() as db:
                continued = db.get(Approval, approval_id).continuation_request_id
                assert db.get(ReplyRequest, continued).continuation_of_request_id == request["id"]
            phase = "finish"
            await product.execute_request(continued)
            assert product.get_request(continued)["status"] == "completed"
            assert not any(part.get("name") == "execute_approved_action" for call in results
                for message in call for part in message.get("content", []) if isinstance(part, dict))
            shared = client.get("/api/chats/group-alex-maya/shared-files").json()["files"]
            assert len(shared) == 1
            shared_id = shared[0]["id"]
            for owner in ("owner-alex", "owner-maya"):
                rows = client.get("/api/chats/group-alex-maya/shared-files", headers={"x-test-owner": owner}).json()["files"]
                assert rows[0]["id"] == shared_id
                assert client.get(rows[0]["preview_url"], headers={"x-test-owner": owner}).text == "# Owner approved report\n"
                assert client.get(rows[0]["url"], headers={"x-test-owner": owner}).content == path.read_bytes()
            assert client.get(shared[0]["url"], headers={"x-test-owner": "owner-jordan"}).status_code in {403, 404}
            # A later share of identical bytes and recipients reuses the approved file grant.
            again = await product.runtime_tool("present_files", action, {**context, "operation_id": uuid4().hex})
            assert again["sent"]
            with product.transaction() as db:
                assert db.scalar(select(func.count()).select_from(Approval).where(Approval.approver_id == "owner-alex",
                    Approval.sdk_reference["action"]["tool"].as_string() == "present_files")) == 1
            maya = send(client, chat="private-maya", body="Read the shared report", owner="owner-maya").json()["request"]
            phase = "list"
            await product.execute_request(maya["id"])
            assert product.get_request(maya["id"])["status"] == "completed"
            assert "# Owner approved report" in json.dumps(results[-1])
        finally:
            await runtime.close()
    asyncio.run(run())


def test_approval_is_bound_to_file_version_and_recipients_and_rejection_shares_nothing(files):
    product, client, _root = files
    seed_collaborations(product)
    path, context = prepare(files)
    context = {**context, "runtime_session_id": "offline-file-session", "runtime_run_id": "offline-file-run"}
    first = present(product, context, [path], chat_id="duet-alex-maya")
    assert first["status"] == "approval_required"
    with product.transaction() as db:
        db.get(Approval, first["approval"]["id"]).status = "rejected"
    assert client.get("/api/chats/group-alex-maya/shared-files").json() == {"files": []}
    retry = present(product, context, [path], chat_id="duet-alex-maya")
    assert retry["status"] == "approval_required" and retry["approval"]["id"] != first["approval"]["id"]
    with product.transaction() as db:
        db.get(Approval, retry["approval"]["id"]).status = "approved"
    assert present(product, context, [path], chat_id="duet-alex-maya")["sent"]
    assert present(product, context, [path], chat_id="duet-team")["status"] == "approval_required"
    path.write_text("Changed file version")
    with pytest.raises(HTTPException, match="changed since owner approval"):
        present(product, {**context, "approved_approval_id": retry["approval"]["id"]}, [path], chat_id="duet-alex-maya")
    assert present(product, context, [path], chat_id="duet-alex-maya")["status"] == "approval_required"
    # Jordan belongs to group-team, but A -> B consent cannot expose the file there.
    assert client.get("/api/chats/group-team/shared-files", headers={"x-test-owner": "owner-jordan"}).json() == {"files": []}
