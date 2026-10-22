import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from agent_runtime import AssistantMessageEventStream, assistant_message
from agent_runtime.storage.coordination import ExecutionLeaseError
from checks.postgres import TestDatabase
from app import service as module
from app.models import Approval, Message, Reminder, ReplyLane, ReplyRequest, RoutingDecision
from app.runtime_bridge import RuntimeBridge, RuntimeBusy


def key():
    return uuid4().hex


@pytest.fixture
def product(tmp_path, monkeypatch):
    database = TestDatabase()
    url = database.url
    engine = create_engine(url.replace("postgresql://", "postgresql+psycopg://"))
    monkeypatch.setattr(module, "engine", engine)
    monkeypatch.setattr(module, "SessionLocal", sessionmaker(engine, expire_on_commit=False))
    monkeypatch.setattr(module, "settings", replace(module.settings, database_url=url))
    monkeypatch.setenv("AGENT_DB_URL", url)
    service = module.ProductService()
    service.initialize()
    # These tests explicitly model a healthy presence store with offline owners.
    service._presence_cache = {owner: {"expires_at": 0, "focused_chats": []} for owner in ("owner-alex", "owner-maya", "owner-jordan")}
    yield service
    engine.dispose()
    database.close()


async def bridge_for(service):
    runtime = RuntimeBridge(service)
    await runtime.start()
    service.bind_runtime(runtime)
    return runtime


def test_lost_worker_cannot_fail_active_request(product):
    async def run():
        runtime = await bridge_for(product)
        try:
            request = product.send_message("owner-alex", "private-alex", "hello", key())["request"]
            product.claim_dispatches()
            runtime.execute = AsyncMock(side_effect=ExecutionLeaseError("new holder"))
            await product.execute_request(request["id"])
            with product.transaction() as db:
                # Platform queue inspection no longer claims execution. A lost SDK
                # lease must preserve the original accepted input for its holder.
                saved = db.get(ReplyRequest, request["id"])
                assert saved.status == "queued"
                assert saved.error is None
                assert db.get(ReplyLane, ("private-alex", request["target_twin_id"])).active_request_id is None
                assert request["id"] in product.claim_dispatches()
        finally:
            await runtime.close()
    asyncio.run(run())


def test_reopen_rebuilds_all_complete_outputs(product):
    async def run():
        runtime = await bridge_for(product)
        calls = []
        def offline(model, context, options):
            calls.append(True)
            tools = [m for m in context.messages if m["role"] == "toolResult"]
            content = [{"type": "text", "text": "Same complete text"}]
            if not tools:
                content.append({"type": "toolCall", "id": "read", "name": "list_tracking", "arguments": {}})
            stream = AssistantMessageEventStream()
            stream.push({"type": "done", "message": assistant_message(model, content=content, stopReason="stop" if tools else "toolUse")})
            return stream
        runtime.models.register_api("offline-regression", offline)
        try:
            request = product.send_message("owner-alex", "private-alex", "hello", key())["request"]
            product.claim_dispatches()
            payload = product.get_request(request["id"])
            context = product.get_runtime_context(request["id"])
            # SDK commits both operations, but the business projection never commits.
            with patch.object(product, "publish_runtime_output", return_value=None):
                await runtime.execute(payload, context)
            assert len(calls) == 2
            # Repeat the same accepted command: no model execution, both facts restored.
            await product.execute_request(request["id"])
            await runtime._project_outputs(request["id"], "local", product.get_request(request["id"])["session_id"], product.get_request(request["id"])["run_id"])
            with product.transaction() as db:
                outputs = list(db.scalars(select(Message).where(Message.request_id == request["id"])))
                assert len(outputs) == 2
                assert len({m.sdk_output_key for m in outputs}) == 2
            assert len(calls) == 2
        finally:
            await runtime.close()
    asyncio.run(run())


def test_busy_router_does_not_discard_other_workers_result(product):
    async def run():
        runtime = await bridge_for(product)
        try:
            chat = product.create_chat("owner-alex", ["owner-maya"], "route")["id"]
            message = product.send_message("owner-alex", chat, "Can you help?", key())["message"]
            runtime.route_targets = AsyncMock(side_effect=RuntimeBusy("other holder"))
            await product.route_pending()
            with product.transaction() as db:
                assert db.scalar(select(RoutingDecision).where(RoutingDecision.message_id == message["id"])).status == "pending"
            runtime.route_targets = AsyncMock(return_value={"needs_response": True, "target_owner_ids": ["owner-maya"], "reason": "question"})
            await product.route_pending()
            # Candidates pass through the existing fresh-presence processing turn.
            product.process_candidates({chat})
            with product.transaction() as db:
                assert db.scalar(select(RoutingDecision).where(RoutingDecision.message_id == message["id"])).status == "completed"
                assert db.scalar(select(ReplyRequest).where(ReplyRequest.source_message_id == message["id"]))
        finally:
            await runtime.close()
    asyncio.run(run())


def test_slow_routing_does_not_block_private_dispatch(product):
    async def run():
        runtime = await bridge_for(product)
        entered, dispatched = asyncio.Event(), asyncio.Event()
        async def route(*args):
            entered.set()
            await asyncio.Event().wait()
        async def execute(request_id):
            dispatched.set()
        runtime.route_targets = route
        product.execute_request = execute
        chat = product.create_chat("owner-alex", ["owner-maya"], "slow route")["id"]
        product.send_message("owner-alex", chat, "question", key())
        await product.start()
        try:
            await asyncio.wait_for(entered.wait(), 2)
            product.send_message("owner-alex", "private-alex", "independent", key())
            await asyncio.wait_for(dispatched.wait(), 2)
        finally:
            await product.close()
            await runtime.close()
    asyncio.run(run())


def test_deleted_tracking_cannot_return_with_new_title_or_casual_mention(product):
    async def run():
        message = product.send_message("owner-alex", "private-alex", "Project budget", key())["message"]
        topic = await product.tracking_action("create_tracking", "owner-alex", "private-alex", {"title": "Budget", "source_message_ids": [message["id"]]}, True)
        await product.tracking_action("delete_tracking", "owner-alex", "private-alex", {"id": topic["id"]})
        for title, source in [("Project costs", message), ("Budget", product.send_message("owner-alex", "private-alex", "Do not recreate Budget", key())["message"])]:
            with pytest.raises(HTTPException) as error:
                await product.tracking_action("create_tracking", "owner-alex", "private-alex", {"title": title, "source_message_ids": [source["id"]]})
            assert error.value.status_code == 409
        restored = await product.tracking_action("create_tracking", "owner-alex", "private-alex", {"title": "Budget", "source_message_ids": [message["id"]]}, True)
        assert restored["id"] != topic["id"]
    asyncio.run(run())


def test_approval_survives_decision_projection_gap_and_executes_once(product):
    async def run():
        runtime = await bridge_for(product)
        try:
            request = product.send_message("owner-alex", "private-alex", "Make a reminder after approval", key())["request"]
            context = product.get_runtime_context(request["id"])
            context.update(operation_id="request-approval", runtime_session_id="approval-origin", runtime_run_id="original-run")
            row = await product.runtime_tool("create_approval", {"title": "Reminder", "description": "Create reminder", "target": "owner", "impact": "one local reminder", "action": {"tool": "create_reminder", "arguments": {"title": "Approved reminder"}}}, context)
            with pytest.raises(PermissionError):
                await product.runtime_tool("execute_approved_action", {"id": row["id"]}, context)
            payload = product.approval_for_execution("owner-alex", "private-alex", row["id"])
            # Decision committed in the SDK; product process exits before projection.
            await runtime.decide_approval(payload, "approved")
            await product.reconcile_approvals()
            await product.reconcile_approvals()
            with product.transaction() as db:
                approved = db.get(Approval, row["id"])
                assert approved.status == "approved"
                assert approved.continuation_request_id
                continuation = approved.continuation_request_id
                assert len(list(db.scalars(select(ReplyRequest).where(ReplyRequest.source == "approval_result")))) == 1
            continued_context = product.get_runtime_context(continuation)
            result = await product.runtime_tool("execute_approved_action", {"id": row["id"]}, continued_context)
            assert result == await product.runtime_tool("execute_approved_action", {"id": row["id"]}, continued_context)
            with product.transaction() as db:
                assert len(list(db.scalars(select(Reminder).where(Reminder.title == "Approved reminder")))) == 1
            with pytest.raises(HTTPException) as error:
                await runtime.decide_approval(payload, "rejected")
            assert error.value.status_code == 409
        finally:
            await runtime.close()
    asyncio.run(run())


def test_owner_admission_precedes_global_candidate_limit(product):
    """A busy owner's old queue cannot hide a later owner's ready FIFO head."""
    from app.models import Actor, Chat, Membership
    with product.transaction() as db:
        for index in range(105):
            chat = Chat(id=f"fair-{index}", kind="group", title="Fairness fixture")
            db.add(chat)
            db.flush()
            db.add(Membership(chat_id=chat.id, owner_id="owner-alex"))
            product.accept_request(db, chat, "twin-alex", "owner-alex", "manual", key(), input_body="Explicit test")
        other = product.accept_request(db, db.get(Chat, "private-maya"), "twin-maya", "owner-maya", "manual", key(), input_body="Later other owner")
        other_id = other.id
    ready = product.claim_dispatches()
    assert other_id in ready
    with product.transaction() as db:
        owners = [db.get(Actor, db.get(ReplyRequest, request_id).target_twin_id).owner_id for request_id in ready]
    assert owners.count("owner-alex") == 2
    assert owners.count("owner-maya") == 1


def test_unread_count_remains_exact_beyond_history_window(product):
    from app.models import Chat
    with product.transaction() as db:
        chat = db.get(Chat, "group-alex-maya")
        for index in range(130):
            product.append_message(db, chat, author_id="owner-maya", kind="human", body=f"Unread {index}")
    detail = product.chat_detail("owner-alex", "group-alex-maya", limit=100)
    assert len(detail["messages"]) == 100
    assert detail["has_more"] is True
    assert detail["chat"]["unread_count"] == 130
    earlier = product.chat_detail("owner-alex", "group-alex-maya", before_seq=detail["before_seq"])
    assert len(earlier["messages"]) == 30
    assert earlier["has_more"] is False


def test_acknowledged_historical_stops_do_not_hide_new_stop(product):
    from app.models import Chat, DispatchCommand
    with product.transaction() as db:
        chat = db.get(Chat, "private-alex")
        for _ in range(105):
            request = product.accept_request(db, chat, "twin-alex", "owner-alex", "manual", key(), input_body="Past stop")
            request.status, request.cancel_requested = "cancelled", True
            command = db.get(DispatchCommand, request.id)
            command.kind, command.state = "cancel", "stop_signalled"
        current = product.accept_request(db, chat, "twin-alex", "owner-alex", "manual", key(), input_body="Current request")
        request_id = current.id
    product.bind_runtime_run(request_id, "current-stop-session", "current-stop-run")
    product._cancel_intent("owner-alex", request_id)
    assert [row["id"] for row in product.pending_stops()] == [request_id]
    product.acknowledge_stop(request_id)
    assert product.pending_stops() == []
    assert product.get_request(request_id)["cancel_requested"] is True
