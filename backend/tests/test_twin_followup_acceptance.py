"""Independent follow-up acceptance: real disposable PG and no provider network."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from duet_test_support import send_message
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app import service as service_module
from app.models import (
    CausalChain,
    ChangeOutbox,
    DispatchCommand,
    Message,
    Reminder,
    ReplyRequest,
    RoutingDecision,
)
from checks.postgres import TestDatabase


def key():
    return uuid4().hex


@pytest.fixture
def product(tmp_path, monkeypatch):
    database = TestDatabase()
    engine = create_engine(database.url.replace("postgresql://", "postgresql+psycopg://"))
    monkeypatch.setattr(service_module, "engine", engine)
    monkeypatch.setattr(service_module, "SessionLocal", sessionmaker(engine, expire_on_commit=False))
    monkeypatch.setattr(service_module, "settings", replace(
        service_module.settings, database_url=database.url, workspace_root=tmp_path / "workspaces"))
    monkeypatch.setenv("AGENT_DB_URL", database.url)
    monkeypatch.setenv("AGENT_MEMORY_DIR", str(tmp_path / "memory"))
    product = service_module.ProductService()
    product.initialize(create_schema=True)
    from duet_test_support import seed_collaborations
    seed_collaborations(product)
    product._presence_cache = {owner: {"expires_at": 0, "focused_chats": []}
                               for owner in ("owner-alex", "owner-maya", "owner-jordan")}
    yield product
    product.db_pool.shutdown()
    engine.dispose()
    database.close()


def send_question(product, *, waiting=True):
    secret = "PRIVATE_HISTORY_ONLY_" + key()
    send_message(product, "owner-maya", "private-maya", secret, key())
    origin = send_message(product, "owner-maya", "private-maya", "Ask AAA whether Friday works", key())
    context = product.get_runtime_context(origin["request"]["id"])
    context["operation_id"] = key()
    arguments = {"chat_id": "duet-alex-maya", "body": "AAA, does Friday work?",
                 "source_message_ids": [origin["message"]["id"]], "wait_for_reply": waiting}
    sent = asyncio.run(product.runtime_tool("send_chat_message", arguments, context))
    return origin, context, arguments, sent, secret


def followup(product, message_id):
    from app.models import TwinFollowup
    with product.transaction() as db:
        row = db.scalar(select(TwinFollowup).where(TwinFollowup.sent_message_id == message_id))
        assert row is not None
        return {column.name: getattr(row, column.name) for column in TwinFollowup.__table__.columns}


def continuations(product, chain_id):
    with product.transaction() as db:
        return list(db.scalars(select(ReplyRequest).where(
            ReplyRequest.source == "followup_result", ReplyRequest.causal_chain_id == chain_id)))


def test_private_send_public_body_is_visible_without_private_history(product):
    before = service_module.now()
    origin, context, arguments, sent, secret = send_question(product)
    assert sent["sent"] and sent["chat_id"] == "duet-alex-maya"
    detail = product.chat_detail("owner-alex", "duet-alex-maya")
    visible = next(message for message in detail["messages"] if message["id"] == sent["message_id"])
    assert visible["body"] == arguments["body"] and visible["author_id"] == "twin-maya"
    assert secret not in str(detail)
    with pytest.raises(HTTPException) as denied:
        product.chat_detail("owner-alex", "private-maya")
    assert denied.value.status_code in {403, 404}
    first = followup(product, sent["message_id"])
    with product.transaction() as db:
        message = db.get(Message, sent["message_id"])
        assert message.source_message_ids == [] and message.source_provenance == []
        assert message.causal_chain_id == origin["request"]["causal_chain_id"]
    replay = asyncio.run(product.runtime_tool("send_chat_message", arguments, context))
    assert replay["message_id"] == sent["message_id"]
    again = followup(product, sent["message_id"])
    assert again["id"] == first["id"] and again["due_at"] == first["due_at"]
    assert 1795 <= (first["due_at"] - before).total_seconds() <= 1805


def test_only_explicit_other_owner_reply_settles_original_chain(product):
    origin, _, _, sent, _ = send_question(product)
    initial = followup(product, sent["message_id"])
    unrelated = send_message(product, "owner-alex", "duet-alex-maya", "Unrelated status update", key())
    send_message(product, "owner-alex", "duet-alex-maya", "Reply to another topic", key(), reply_to=unrelated["message"]["id"])
    send_message(product, "owner-maya", "duet-alex-maya", "My own follow-up", key(), reply_to=sent["message_id"])
    assert followup(product, sent["message_id"])["status"] == "waiting"
    replied = send_message(product, "owner-alex", "duet-alex-maya", "Friday works", key(), reply_to=sent["message_id"])
    product.process_followups({"private-maya", "duet-alex-maya"})
    row = followup(product, sent["message_id"])
    assert row["status"] == "replied" and row["reply_message_id"] == replied["message"]["id"]
    assert row["due_at"] == initial["due_at"]
    queued = continuations(product, origin["request"]["causal_chain_id"])
    assert len(queued) == 1
    assert queued[0].chat_id == "private-maya" and queued[0].target_twin_id == "twin-maya"
    assert queued[0].automatic_reservation_id is not None
    assert product.tasks == {}  # Waiting does not start or retain an executor.
    for _ in range(2):
        product.process_followups()
    assert len(continuations(product, origin["request"]["causal_chain_id"])) == 1


def test_reply_and_timeout_compete_once_across_pg_transactions(product):
    from app.models import TwinFollowup
    origin, _, _, sent, _ = send_question(product)
    with product.transaction() as db:
        row = db.scalar(select(TwinFollowup).where(TwinFollowup.sent_message_id == sent["message_id"]))
        row.due_at = service_module.now() - timedelta(milliseconds=1)
    def reply():
        return send_message(product, "owner-alex", "duet-alex-maya", "Late reply", key(), reply_to=sent["message_id"])
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(product.process_followups), executor.submit(reply),
                   executor.submit(product.process_followups)]
        for future in futures:
            future.result(timeout=10)
    row = followup(product, sent["message_id"])
    assert row["status"] in {"replied", "timed_out"}
    queued = continuations(product, origin["request"]["causal_chain_id"])
    assert len(queued) == 1
    with product.transaction() as db:
        assert db.scalar(select(func.count()).select_from(DispatchCommand).where(
            DispatchCommand.request_id == queued[0].id)) == 1
    product.process_followups()
    assert followup(product, sent["message_id"])["continuation_request_id"] == queued[0].id


def test_exhausted_chain_does_not_call_router_provider(product):
    message = send_message(product, "owner-maya", "duet-team", "AAA, question under exhausted budget", key())["message"]
    with product.transaction() as db:
        chain = db.get(CausalChain, message["causal_chain_id"])
        chain.spent = chain.allowance
        decision_id = db.scalar(select(RoutingDecision.id).where(RoutingDecision.message_id == message["id"]))
    route = AsyncMock(side_effect=AssertionError("Exhausted budget must never invoke the router model"))
    product.bind_runtime(SimpleNamespace(configured=True, startup_error=None, route_targets=route))
    asyncio.run(product.route_pending({decision_id}))
    assert route.await_count == 0
    with product.transaction() as db:
        assert db.scalar(select(func.count()).select_from(ReplyRequest).where(
            ReplyRequest.source_message_id == message["id"])) == 0


def test_reply_already_consumed_by_followup_has_no_duplicate_router_call(product):
    _, _, _, sent, _ = send_question(product)
    reply = send_message(product, "owner-alex", "duet-alex-maya", "Friday works", key(), reply_to=sent["message_id"])
    with product.transaction() as db:
        decision_id = db.scalar(select(RoutingDecision.id).where(
            RoutingDecision.message_id == reply["message"]["id"]))
    route = AsyncMock(side_effect=AssertionError("Followup handled the only eligible recipient"))
    product.bind_runtime(SimpleNamespace(configured=True, startup_error=None, route_targets=route))
    asyncio.run(product.route_pending({decision_id}))
    assert route.await_count == 0
    with product.transaction() as db:
        assert db.get(RoutingDecision, decision_id).status == "completed"


def test_restart_keeps_deadline_and_event_waits_behind_busy_private_lane(product):
    from app.models import TwinFollowup
    origin, _, _, sent, _ = send_question(product)
    saved = followup(product, sent["message_id"])
    with product.transaction() as db:
        head = db.scalar(select(ReplyRequest).where(ReplyRequest.chat_id == "private-maya")
                         .order_by(ReplyRequest.accepted_seq))
        head_id = head.id
    product.get_runtime_context(head_id)
    product.bind_runtime_run(head_id, "isolated-busy-session", "isolated-busy-run")
    restarted = service_module.ProductService()
    try:
        assert restarted.next_deadlines()["private-maya"] == saved["due_at"]
        restarted.process_followups({"private-maya"})
        assert followup(product, sent["message_id"])["due_at"] == saved["due_at"]
        assert continuations(product, origin["request"]["causal_chain_id"]) == []
        with product.transaction() as db:
            row = db.get(TwinFollowup, saved["id"])
            row.due_at = service_module.now() - timedelta(seconds=1)
            persisted_deadline = row.due_at
        restarted.process_followups()
        queued = continuations(product, origin["request"]["causal_chain_id"])
        assert len(queued) == 1
        event = queued[0]
        assert event.id not in restarted.claim_dispatches()
        assert event.causal_chain_id == origin["request"]["causal_chain_id"]
        assert event.status == "queued" and restarted.tasks == {}
        with product.transaction() as db:
            assert db.scalar(select(ChangeOutbox.id).where(
                ChangeOutbox.chat_id == "private-maya", ChangeOutbox.kind == "work"))
        # Settling the earlier accepted work releases the same FIFO; it does not
        # require a waiting follow-up Agent or a second private session.
        restarted.finish_request(head_id, "completed")
        restarted.finish_request(origin["request"]["id"], "completed")
        assert event.id in restarted.claim_dispatches()
        restarted.process_followups()
        assert followup(product, sent["message_id"])["due_at"] == persisted_deadline
        assert len(continuations(product, origin["request"]["causal_chain_id"])) == 1
    finally:
        restarted.db_pool.shutdown()


def test_exhausted_followup_stays_on_original_chain_until_explicit_allowance(product):
    from app.models import TwinFollowup
    origin, _, _, sent, _ = send_question(product)
    chain_id = origin["request"]["causal_chain_id"]
    with product.transaction() as db:
        chain = db.get(CausalChain, chain_id)
        chain.spent = chain.allowance
        allowance = chain.allowance
        row = db.scalar(select(TwinFollowup).where(TwinFollowup.sent_message_id == sent["message_id"]))
        row.due_at = service_module.now() - timedelta(seconds=1)
    product.process_followups()
    row = followup(product, sent["message_id"])
    assert row["status"] == "timed_out" and row["notification_state"] == "paused"
    assert row["pause_reason"] == "budget_exhausted"
    assert continuations(product, chain_id) == []
    restarted = service_module.ProductService()
    try:
        restarted.process_followups()
        assert continuations(product, chain_id) == []
        with product.transaction() as db:
            chain = db.get(CausalChain, chain_id)
            assert chain.spent == chain.allowance == allowance
        # Only the existing explicit Continue API may increase the original cap.
        result = restarted.continue_automatic_conversation(
            "owner-maya", "private-maya", chain_id, key(), 6, allowance)
        assert result["causal_chain_id"] == chain_id and result["allowance"] == allowance + 6
        queued = continuations(product, chain_id)
        assert len(queued) == 1 and queued[0].automatic_reservation_id is not None
        with product.transaction() as db:
            chain = db.get(CausalChain, chain_id)
            assert chain.spent == allowance + 1 and chain.allowance == allowance + 6
    finally:
        restarted.db_pool.shutdown()


def test_intermediate_model_projection_does_not_finish_question_followup(product):
    _, _, _, sent, _ = send_question(product)
    from duet_test_support import request_duet_reply
    request = request_duet_reply(product, "owner-alex", "duet-alex-maya", sent["message_id"], "twin-alex", key())
    product.get_runtime_context(request["id"])
    session, run = "isolated-reply-session", "isolated-reply-run"
    product.publish_runtime_output(request["id"], "I will check first", session, run, "intermediate", 1)
    assert followup(product, sent["message_id"])["status"] == "waiting"
    final_id = product.publish_runtime_output(request["id"], "Friday works", session, run, "final", 2)
    assert followup(product, sent["message_id"])["status"] == "waiting"
    product.complete_request(request["id"], {"body": "Friday works", "session_id": session,
        "run_id": run, "event_id": "final", "event_seq": 2})
    row = followup(product, sent["message_id"])
    assert row["status"] == "replied" and row["reply_message_id"] == final_id


@pytest.mark.parametrize("change", ["cancel", "outbound_withdrawn", "target_access_revoked", "private_source_withdrawn"])
def test_cancel_withdrawal_and_revocation_do_not_deliver_followup(product, change):
    from app.models import Membership, TwinFollowup
    origin, _, _, sent, _ = send_question(product)
    if change == "cancel":
        product._cancel_intent("owner-maya", origin["request"]["id"])
    with product.transaction() as db:
        if change == "outbound_withdrawn":
            db.get(Message, sent["message_id"]).state = "withdrawn"
        elif change == "target_access_revoked":
            member = db.scalar(select(Membership).where(Membership.chat_id == "duet-alex-maya",
                                                      Membership.owner_id == "owner-maya"))
            member.left_at = service_module.now()
        elif change == "private_source_withdrawn":
            db.get(Message, origin["message"]["id"]).state = "withdrawn"
        row = db.scalar(select(TwinFollowup).where(TwinFollowup.sent_message_id == sent["message_id"]))
        row.due_at = service_module.now() - timedelta(seconds=1)
    product.process_followups()
    row = followup(product, sent["message_id"])
    assert row["notification_state"] == "blocked"
    assert continuations(product, origin["request"]["causal_chain_id"]) == []
    if change == "cancel":
        assert row["status"] == "cancelled"
        # Cancelling outstanding work does not pretend to retract an already
        # committed outward statement.
        assert any(message["id"] == sent["message_id"] for message in
                   product.chat_detail("owner-alex", "duet-alex-maya")["messages"])


@pytest.mark.parametrize("generations", [1, 2])
def test_failed_followup_retry_keeps_followup_policy_and_rechecks_reply_access(product, generations):
    from app.models import Membership
    origin, _, _, sent, _ = send_question(product)
    send_message(product, "owner-alex", "duet-alex-maya", "Friday works", key(), reply_to=sent["message_id"])
    original = continuations(product, origin["request"]["causal_chain_id"])[0]
    current_id = original.id
    for _ in range(generations):
        product.fail_request(current_id, "Isolated provider interruption")
        retried = product.retry_request("owner-maya", current_id, key())
        assert product.get_request(retried["id"])["source"] == "followup_result"
        current_id = retried["id"]
    context = product.get_runtime_context(retried["id"])
    assert context["followup_result"] is not None
    assert context["followup_result"]["reply_body"] == "Friday works"
    assert retried["causal_chain_id"] == original.causal_chain_id
    assert retried["automatic_reservation_id"] == original.automatic_reservation_id
    with product.transaction() as db:
        membership = db.scalar(select(Membership).where(Membership.chat_id == "duet-alex-maya",
                                                       Membership.owner_id == "owner-maya"))
        membership.left_at = service_module.now()
        reminders_before = db.scalar(select(func.count()).select_from(Reminder))
        requests_before = db.scalar(select(func.count()).select_from(ReplyRequest))
    with pytest.raises(HTTPException) as tool_denied:
        asyncio.run(product.runtime_tool("create_reminder", {"title": "Must not use revoked reply"},
            {**context, "operation_id": key()}))
    assert tool_denied.value.status_code == 403
    with product.transaction() as db:
        assert db.scalar(select(func.count()).select_from(Reminder)) == reminders_before
        assert db.scalar(select(func.count()).select_from(ReplyRequest)) == requests_before
    with pytest.raises(HTTPException):
        product.get_runtime_context(retried["id"])


def test_second_followup_retry_is_silent_with_real_sdk_and_offline_model(product, monkeypatch):
    from agent_runtime import AssistantMessageEventStream, assistant_message

    from app.runtime_bridge import RuntimeBridge
    origin, _, _, sent, _ = send_question(product)
    send_message(product, "owner-alex", "duet-alex-maya", "Friday works", key(), reply_to=sent["message_id"])
    original = continuations(product, origin["request"]["causal_chain_id"])[0]
    current_id = original.id
    for _ in range(2):
        product.fail_request(current_id, "Isolated interruption before SDK acceptance")
        current_id = product.retry_request("owner-maya", current_id, key())["id"]
    async def run():
        runtime = RuntimeBridge(product)
        calls, events = [], []
        def offline(model, context, options):
            calls.append(context.messages)
            has_result = any(message.get("role") == "toolResult" for message in context.messages)
            content = [] if has_result else [
                {"type": "text", "text": "RETRY_INTERMEDIATE_MUST_STAY_HIDDEN"},
                {"type": "toolCall", "id": "retry-read", "name": "list_authorized_chats", "arguments": {}}]
            stream = AssistantMessageEventStream()
            stream.push({"type": "done", "message": assistant_message(model, content=content,
                stopReason="stop" if has_result else "toolUse")})
            return stream
        async def capture(chat_id, **event):
            events.append(event)
        runtime.models.register_api("offline-regression", offline)
        monkeypatch.setattr(runtime, "emit", capture)
        await runtime.start()
        product.bind_runtime(runtime)
        try:
            await product.execute_request(current_id)
            assert product.get_request(current_id)["status"] == "completed"
            assert len(calls) == 2
            assert "Friday works" in str(calls[0]) and "untrusted data" in str(calls[0])
            assert not any(event.get("type") == "token" for event in events)
            with product.transaction() as db:
                assert db.scalar(select(func.count()).select_from(Message).where(Message.request_id == current_id)) == 0
                chain = db.get(CausalChain, original.causal_chain_id)
                assert chain.spent == 2  # Initial owner run + one follow-up, never per retry.
        finally:
            await runtime.close()
    asyncio.run(run())
