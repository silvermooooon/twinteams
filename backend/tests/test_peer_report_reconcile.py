"""Peer reports trigger bounded private Tracking work on the real service."""
import asyncio
from uuid import uuid4

import pytest
from agent_runtime import AssistantMessageEventStream, assistant_message
from agent_runtime.transcript import current_tools
from duet_test_support import append_duet, approved_chat
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_memory_dream import product as product  # noqa: PLC0414

from app import api
from app.models import Chat, DispatchCommand, Message, ReplyRequest, TrackingTopic
from app.runtime_bridge import RuntimeBridge


def topic(product, *, chat_id="private-alex", title="Owner's handoff", description="Wait for both reviews"):
    return asyncio.run(product.tracking_action("create_tracking", "owner-alex", chat_id, {
        "title": title, "description": description, "source_message_ids": [],
    }, explicit=True))


def peer_report(product, *, operation=None, body="The first review is complete."):
    chat_id = approved_chat(product, "duet-report", ["owner-alex", "owner-maya"])
    peer = append_duet(product, chat_id, "owner-maya", body)
    with product.transaction() as db:
        request = db.scalar(select(ReplyRequest).where(ReplyRequest.source == "duet_message",
            ReplyRequest.source_message_id == peer["id"], ReplyRequest.target_twin_id == "twin-alex"))
        peer_request_id = request.id
    context = product.get_runtime_context(peer_request_id)
    args = {"chat_id": "private-alex", "body": body, "source_message_ids": [peer["id"]]}
    context = {**context, "operation_id": operation or uuid4().hex}
    result = asyncio.run(product.runtime_tool("send_chat_message", args, context))
    return result, context, args, peer_request_id


def reconciliations(db, message_id):
    return list(db.scalars(select(ReplyRequest).where(ReplyRequest.source == "peer_report_reconcile",
        ReplyRequest.source_message_id == message_id)))


def test_peer_report_enqueues_one_private_background_request_and_replay_is_safe(product):
    handoff = topic(product)
    result, context, args, peer_request_id = peer_report(product)
    replay = asyncio.run(product.runtime_tool("send_chat_message", args, context))
    assert replay["message_id"] == result["message_id"]
    with product.transaction() as db:
        report = db.get(Message, result["message_id"])
        rows = reconciliations(db, report.id)
        assert len(rows) == 1
        request = rows[0]
        assert request.chat_id == "private-alex" and request.background
        assert request.causal_chain_id != report.causal_chain_id
        assert request.source_message_id == report.id and report.request_id == peer_request_id
        assert request.execution_config["execution_enabled"] is False
        assert db.get(DispatchCommand, request.id).state == "pending"
        assert db.get(TrackingTopic, handoff["id"]).status == "active"
        assert product._activity_request_user_visible(db, request) is False


def test_reconciliation_is_quiet_in_private_chat_api_for_active_and_failed_states(product, monkeypatch):
    topic(product)
    ordinary = product.send_message("owner-alex", "private-alex", "An ordinary request", uuid4().hex)["request"]
    report, _, _, _ = peer_report(product)
    with product.transaction() as db:
        request_id = reconciliations(db, report["message_id"])[0].id
    monkeypatch.setattr(api, "service", product)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.identity] = lambda: "owner-alex"
    with TestClient(app) as client:
        for status in ("queued", "running", "failed"):
            with product.transaction() as db:
                db.get(ReplyRequest, request_id).status = status
            response = client.get("/api/chats/private-alex")
            assert response.status_code == 200
            requests = {row["id"]: row for row in response.json()["requests"]}
            assert requests[request_id]["quiet_background"] is True
            assert requests[ordinary["id"]]["quiet_background"] is False


@pytest.mark.parametrize("scope", ["none", "completed", "other_chat"])
def test_only_open_private_tracking_triggers_reconciliation(product, scope):
    if scope == "completed":
        existing = topic(product)
        asyncio.run(product.tracking_action("update_tracking", "owner-alex", "private-alex",
            {"id": existing["id"], "status": "completed"}))
    elif scope == "other_chat":
        topic(product, chat_id="group-team")
    report, _, _, _ = peer_report(product)
    with product.transaction() as db:
        assert reconciliations(db, report["message_id"]) == []


def test_reconciliation_is_private_tracking_only_and_never_projects_output(product):
    handoff = topic(product)
    unrelated = topic(product, title="Unrelated owner task", description="Still open")
    report, _, _, _ = peer_report(product)
    with product.transaction() as db:
        request_id = reconciliations(db, report["message_id"])[0].id
    context = product.get_runtime_context(request_id)
    assert context["source_message"]["id"] == report["message_id"]
    for name, args in (("ask_question", {"question": "Repeat?"}),
                       ("send_chat_message", {"chat_id": "duet-report", "body": "Duplicate", "source_message_ids": []}),
                       ("publish_center_message", {"title": "Duplicate", "body": "No", "recipients": []})):
        with pytest.raises(HTTPException) as denied:
            asyncio.run(product.runtime_tool(name, args, {**context, "operation_id": uuid4().hex}))
        assert denied.value.status_code == 403
    with pytest.raises(HTTPException) as denied:
        asyncio.run(product.runtime_tool("update_tracking", {"chat_id": "group-team", "id": handoff["id"], "status": "completed"},
            {**context, "operation_id": uuid4().hex}))
    assert denied.value.status_code == 403
    assert product.publish_runtime_output(request_id, "Duplicate owner message", "session", "run") is None
    with product.transaction() as db:
        assert db.get(TrackingTopic, handoff["id"]).status == "active"
        assert db.get(TrackingTopic, unrelated["id"]).status == "active"
        assert db.scalar(select(func.count()).select_from(Message).where(Message.request_id == request_id)) == 0


def test_completed_tracking_update_replays_and_accepts_a_followup_note(product):
    handoff = topic(product)
    unrelated = topic(product, title="Unrelated owner task")
    report, _, _, _ = peer_report(product, body="Both requested reviews are complete.")
    with product.transaction() as db:
        request_id = reconciliations(db, report["message_id"])[0].id
    context = product.get_runtime_context(request_id)
    args = {"id": handoff["id"], "status": "completed", "description": "Both reviews are complete."}
    operation = uuid4().hex
    first = asyncio.run(product.runtime_tool("update_tracking", args, {**context, "operation_id": operation}))
    replay = asyncio.run(product.runtime_tool("update_tracking", args, {**context, "operation_id": operation}))
    assert first["revision"] == replay["revision"] and replay["status"] == "completed"
    note = asyncio.run(product.runtime_tool("add_tracking_update", {
        "id": handoff["id"], "body": "Both reviews were confirmed.", "source_message_ids": [report["message_id"]],
    }, {**context, "operation_id": uuid4().hex}))
    assert note["status"] == "completed"
    with product.transaction() as db:
        assert db.get(TrackingTopic, unrelated["id"]).status == "active"


@pytest.mark.parametrize("change", ["reset", "withdrawn"])
def test_reconciliation_rechecks_private_generation_and_report(product, change):
    topic(product)
    report, _, _, _ = peer_report(product)
    with product.transaction() as db:
        request_id = reconciliations(db, report["message_id"])[0].id
        if change == "reset":
            db.get(Chat, "private-alex").reset_generation += 1
        else:
            db.get(Message, report["message_id"]).state = "withdrawn"
    with pytest.raises(HTTPException):
        product.get_runtime_context(request_id)


def test_retry_keeps_reconciliation_source_and_bounded_execution(product):
    topic(product)
    report, _, _, _ = peer_report(product)
    with product.transaction() as db:
        original = reconciliations(db, report["message_id"])[0].id
    product.fail_request(original, "Synthetic transient failure")
    retry = product.retry_request("owner-alex", original, uuid4().hex)
    with product.transaction() as db:
        row = db.get(ReplyRequest, retry["id"])
        assert row.source == "peer_report_reconcile" and row.background
        assert row.source_message_id == report["message_id"]
        assert row.execution_config["execution_enabled"] is False


def test_real_runtime_only_offers_tracking_tools_and_suppresses_final_text(product):
    handoff = topic(product)
    unrelated = topic(product, title="Unrelated owner task")
    peer_report(product, body="Both requested reviews are complete.")

    async def run():
        runtime = RuntimeBridge(product)
        offered = []

        def offline(model, context, options):
            offered.append({tool["name"] for tool in current_tools(context.messages)})
            results = [item for item in context.messages if item.get("role") == "toolResult"]
            if not results:
                content = [{"type": "toolCall", "id": uuid4().hex, "name": "get_tracking", "arguments": {"id": handoff["id"]}}]
            elif len(results) == 1:
                content = [{"type": "toolCall", "id": uuid4().hex, "name": "update_tracking", "arguments": {
                    "id": handoff["id"], "description": "Both requested reviews are complete.", "status": "completed"}}]
            else:
                content = [{"type": "text", "text": "This must not become an owner message."}]
            stream = AssistantMessageEventStream()
            stream.push({"type": "done", "message": assistant_message(model, content=content,
                stopReason="toolUse" if content[0]["type"] == "toolCall" else "stop")})
            return stream

        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        product.bind_runtime(runtime)
        try:
            with product.transaction() as db:
                request_id = db.scalar(select(ReplyRequest.id).where(ReplyRequest.source == "peer_report_reconcile"))
            await product.execute_request(request_id)
            with product.transaction() as db:
                assert db.get(ReplyRequest, request_id).status == "completed"
                assert db.scalar(select(func.count()).select_from(Message).where(Message.request_id == request_id)) == 0
                assert db.get(TrackingTopic, handoff["id"]).status == "completed"
                assert db.get(TrackingTopic, unrelated["id"]).status == "active"
            assert offered and all(names == {"list_tracking", "get_tracking", "update_tracking", "add_tracking_update"} for names in offered)
        finally:
            await runtime.close()

    asyncio.run(run())
