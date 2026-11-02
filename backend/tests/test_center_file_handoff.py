"""Center references preserve existing file consent and readable snapshots."""
import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from duet_test_support import seed_collaborations
from fastapi import HTTPException
from sqlalchemy import select
from test_chat_attachments_ui_acceptance import attachments as attachments  # noqa: PLC0414
from test_deliverables import prepare, present
from test_file_workspace_acceptance import files as files  # noqa: PLC0414

from app import message_center as center
from app.models import Approval, ContextManifest, Membership, Message, TrackingTopic, now


def test_center_handoff_requires_existing_share_and_retains_source(files, monkeypatch):
    product, client, _ = files
    monkeypatch.setattr(center, "settings", replace(center.settings, message_center_url="http://center", message_center_token="test"))
    seed_collaborations(product)
    path, context = prepare(files, "story.md", b"# Story\nAcceptance criteria\n")
    pending = present(product, context, [path], chat_id="duet-alex-maya")
    with product.transaction() as db:
        db.get(Approval, pending["approval"]["id"]).status = "approved"
    delivered = present(product, context, [path], chat_id="duet-alex-maya")
    attachment_id = delivered["message"]["attachments"][0]["id"]
    private_id = present(product, context, [path])["message"]["attachments"][0]["id"]
    calls = []
    message = {"id": "story-center", "version": 1, "title": "Stories", "body": "Please review.",
               "status": "active", "metadata": {"twinteams_attachment_ids": [attachment_id]}}

    async def fake_call(method, endpoint, *, params=None, body=None):
        calls.append((method, endpoint, body))
        if method == "GET":
            return {**message, "id": "story-lookup", "version": 2}
        return {"id": "story-center", "version": 1, **body}

    monkeypatch.setattr(center, "call", fake_call)
    args = {"title": "Stories", "body": "Please review.", "recipients": ["owner-maya"], "attachment_ids": [attachment_id]}

    async def exercise():
        await center.tool(product, "publish_center_message", args, {**context, "operation_id": uuid4().hex})
        assert calls[-1][2]["metadata"] == {"twinteams_attachment_ids": [attachment_id]}
        assert "attachment_ids" not in calls[-1][2]
        with pytest.raises(HTTPException):
            await center.tool(product, "publish_center_message", {**args, "recipients": ["owner-jordan"]}, {**context, "operation_id": uuid4().hex})
        with pytest.raises(HTTPException):
            await center.tool(product, "publish_center_message", {**args, "recipients": []}, {**context, "operation_id": uuid4().hex})
        with pytest.raises(HTTPException):
            await center.tool(product, "publish_center_message", {**args, "attachment_ids": [private_id]}, {**context, "operation_id": uuid4().hex})
        with product.transaction() as db:
            db.get(Message, delivered["message_id"]).state = "withdrawn"
        with pytest.raises(HTTPException):
            await center.tool(product, "publish_center_message", args, {**context, "operation_id": uuid4().hex})
        with product.transaction() as db:
            db.get(Message, delivered["message_id"]).state = "published"

        request_id = center.accept(product, {"delivery_id": "story-delivery", "message_id": "story-center", "version": 1,
            "owner_id": "owner-maya", "mode": "immediate", "message": message})["request_id"]
        maya = product.get_runtime_context(request_id)
        ref = maya["message_center"]["message"]["files"][0]
        detail = await center.tool(product, "get_center_message", {"id": "story-lookup"}, maya)
        assert detail["files"] == [ref]
        jordan_id = center.accept(product, {"delivery_id": "jordan-delivery", "message_id": "story-center", "version": 1,
            "owner_id": "owner-jordan", "mode": "immediate", "message": message})["request_id"]
        jordan = product.get_runtime_context(jordan_id)
        assert (await center.tool(product, "get_center_message", {"id": "story-lookup"}, jordan))["files"] == []
        with product.transaction() as db:
            assert any(source.get("kind") == "message_center" and source["id"] == "story-lookup" and source["version"] == 2
                       for source in db.get(ContextManifest, jordan["context_manifest_id"]).sources)
        result = await product.runtime_tool("read_workspace_file", {"chat_id": ref["chat_id"], "id": ref["workspace_id"]}, maya)
        assert result["content"] == "# Story\nAcceptance criteria\n"
        assert client.get(ref["preview_url"], headers={"x-test-owner": "owner-maya"}).text == result["content"]
        assert client.get(ref["url"], headers={"x-test-owner": "owner-jordan"}).status_code in {403, 404}
        tracking = await product.runtime_tool("create_tracking", {"title": "Story review", "description": "Review the delivered story", "source_message_ids": []}, {**maya, "operation_id": uuid4().hex})
        with product.transaction() as db:
            manifest = db.get(ContextManifest, maya["context_manifest_id"])
            assert any(source.get("kind") == "message_center" and source["id"] == "story-center" and source["version"] == 1 for source in manifest.sources)
            assert any(source.get("kind") == "message_center" and source["id"] == "story-lookup" and source["version"] == 2 for source in manifest.sources)
            topic = db.get(TrackingTopic, tracking["id"])
            _, maya_member = product.membership(db, "owner-maya", "private-maya")
            _, jordan_member = product.membership(db, "owner-jordan", "private-jordan")
            assert product.tracking_json(db, topic, maya_member)
            assert not product.provenance_allowed(db, jordan_member, topic.source_provenance)
        with product.transaction() as db:
            assert center.resolved_files(product, db, "owner-jordan", message) == []
            member = db.scalar(select(Membership).where(Membership.chat_id == "duet-alex-maya", Membership.owner_id == "owner-maya"))
            member.left_at = now()
        with product.transaction() as db:
            assert center.resolved_files(product, db, "owner-maya", message) == []
        with pytest.raises(HTTPException):
            await center.tool(product, "publish_center_message", args, {**context, "operation_id": uuid4().hex})

    asyncio.run(exercise())
