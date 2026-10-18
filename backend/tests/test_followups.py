"""Isolated PG checks for owner-attributed sends and event/deadline followups."""
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app import credentials
from app import service as service_module
from app.models import (
    CausalChain,
    Chat,
    Membership,
    Message,
    ReplyRequest,
    RoutingDecision,
    TriggerCandidate,
    TwinFollowup,
    now,
)
from app.twin_execution import private_execution_allowed
from checks.postgres import TestDatabase


def key():
    return uuid4().hex


@pytest.fixture
def product(monkeypatch):
    database = TestDatabase()
    engine = create_engine(database.url.replace("postgresql://", "postgresql+psycopg://"))
    monkeypatch.setattr(service_module, "engine", engine)
    monkeypatch.setattr(service_module, "SessionLocal", sessionmaker(engine, expire_on_commit=False))
    monkeypatch.setattr(service_module, "settings", replace(service_module.settings, database_url=database.url))
    product = service_module.ProductService()
    product.initialize(create_schema=True)
    product._presence_cache = {owner: {"expires_at": 0, "focused_chats": []}
        for owner in ("owner-alex", "owner-maya", "owner-jordan")}
    yield product
    product.db_pool.shutdown()
    engine.dispose()
    database.close()


def send(product, *, wait=True):
    request = product.send_message("owner-alex", "private-alex", "Private task and confidential preparation", key())["request"]
    context = product.get_runtime_context(request["id"])
    context["operation_id"] = key()
    with product.transaction() as db:
        target = db.scalar(select(Chat).where(Chat.kind == "group"))
        target_id = target.id
    args = {"chat_id": target_id, "body": "Please confirm the public appointment.",
        "source_message_ids": [request["source_message_id"]], "wait_for_reply": wait}
    result = product._runtime_tool_transaction("send_chat_message", args, context)
    return request, context, args, result


def test_private_send_publishes_owner_text_without_private_history_acl(product):
    request, context, args, result = send(product, wait=False)
    assert result["sent"] and result["followup"] is None
    with product.transaction() as db:
        message = db.get(Message, result["message_id"])
        assert message.request_id == request["id"]  # internal audit link remains
        assert message.source_message_ids == [] and message.source_provenance == []
        member = product.membership(db, "owner-maya", args["chat_id"])[1]
        visible = product.visible_message(db, member, message.id)
        rendered = product.message_json(db, visible, member)
        assert "confidential" not in str(rendered)
        assert rendered["request_id"] is None
        assert db.scalar(select(func.count()).select_from(TwinFollowup)) == 0
    replay = product._runtime_tool_transaction("send_chat_message", args, context)
    assert replay["message_id"] == result["message_id"]


def test_explicit_reply_is_one_private_continuation_and_excluded_from_group_route(product):
    request, context, args, result = send(product)
    replay = product._runtime_tool_transaction("send_chat_message", args, context)
    assert replay["followup"] == result["followup"]
    assert "private-alex" in product.next_deadlines()
    product.send_message("owner-maya", args["chat_id"], "Unrelated group message", key())
    with product.transaction() as db:
        assert db.get(TwinFollowup, result["followup"]["id"]).status == "waiting"
    reply = product.send_message("owner-maya", args["chat_id"], "Confirmed, thank you.", key(), reply_to=result["message_id"])["message"]
    product.process_followups()
    with product.transaction() as db:
        row = db.get(TwinFollowup, result["followup"]["id"])
        assert row.status == "replied" and row.notification_state == "accepted"
        continuation = db.get(ReplyRequest, row.continuation_request_id)
        assert continuation.chat_id == "private-alex" and continuation.source == "followup_result"
        assert continuation.causal_chain_id == request["causal_chain_id"]
        assert continuation.automatic_reservation_id
        continuation_id = continuation.id
        decision = db.scalar(select(RoutingDecision).where(RoutingDecision.message_id == reply["id"]))
        decision_id = decision.id
        assert db.scalar(select(func.count()).select_from(ReplyRequest).where(ReplyRequest.source == "followup_result")) == 1
    product._settle_route(decision_id, ["owner-alex"], True, "Explicit reply", None)
    with product.transaction() as db:
        own_twin = product.twin(db, "owner-alex")
        assert db.scalar(select(TriggerCandidate.id).where(TriggerCandidate.message_id == reply["id"], TriggerCandidate.target_twin_id == own_twin.id)) is None
    runtime_context = product.get_runtime_context(continuation_id)
    assert runtime_context["followup_result"]["reply_body"] == "Confirmed, thank you."
    assert private_execution_allowed(product, "owner-alex", continuation_id)


def test_timeout_restart_and_late_reply_keep_single_terminal_and_chain(product):
    request, _, args, result = send(product)
    with product.transaction() as db:
        row = db.get(TwinFollowup, result["followup"]["id"])
        row.due_at = now() - timedelta(seconds=1)
    restarted = service_module.ProductService()
    try:
        restarted.process_followups()
        restarted.process_followups()
    finally:
        restarted.db_pool.shutdown()
    product.send_message("owner-maya", args["chat_id"], "A late explicit answer", key(), reply_to=result["message_id"])
    with product.transaction() as db:
        row = db.get(TwinFollowup, result["followup"]["id"])
        assert row.status == "timed_out" and row.reply_message_id is None
        continuation = db.get(ReplyRequest, row.continuation_request_id)
        assert continuation.causal_chain_id == request["causal_chain_id"]
        chain = db.get(CausalChain, row.causal_chain_id)
        assert chain.spent == 2 and chain.allowance == 6
        assert db.scalar(select(func.count()).select_from(ReplyRequest).where(ReplyRequest.source == "followup_result")) == 1


def test_budget_pause_uses_existing_owner_continue_without_reset(product):
    request, _, args, result = send(product)
    with product.transaction() as db:
        chain = db.get(CausalChain, request["causal_chain_id"])
        chain.spent = chain.allowance
    product.send_message("owner-maya", args["chat_id"], "The requested answer", key(), reply_to=result["message_id"])
    with product.transaction() as db:
        row = db.get(TwinFollowup, result["followup"]["id"])
        assert row.status == "replied" and row.notification_state == "paused"
        assert row.pause_reason == "budget_exhausted" and row.continuation_request_id is None
    automatic = product.chat_detail("owner-alex", "private-alex")["automatic_conversations"]
    assert automatic[0]["can_continue"]
    grant_key = key()
    product.continue_automatic_conversation("owner-alex", "private-alex", request["causal_chain_id"], grant_key, 6, 6)
    product.continue_automatic_conversation("owner-alex", "private-alex", request["causal_chain_id"], grant_key, 6, 6)
    with product.transaction() as db:
        row = db.get(TwinFollowup, result["followup"]["id"])
        assert row.notification_state == "accepted"
        chain = db.get(CausalChain, row.causal_chain_id)
        assert chain.spent == 7 and chain.allowance == 12


def test_revoked_destination_blocks_notification_and_forged_followup_has_no_private_tools(product):
    _, _, args, result = send(product)
    with product.transaction() as db:
        member = db.scalar(select(Membership).where(Membership.chat_id == args["chat_id"], Membership.owner_id == "owner-alex"))
        member.left_at = now()
    product.send_message("owner-maya", args["chat_id"], "Answer after owner left", key(), reply_to=result["message_id"])
    with product.transaction() as db:
        row = db.get(TwinFollowup, result["followup"]["id"])
        assert row.notification_state == "blocked" and row.continuation_request_id is None
        private = db.get(Chat, "private-alex")
        forged = product.accept_request(db, private, product.twin(db, "owner-alex").id, "owner-alex",
            "followup_result", key(), input_body="forged data")
        forged_id = forged.id
    assert not private_execution_allowed(product, "owner-alex", forged_id)
    with product.transaction() as db, pytest.raises(HTTPException):
        credentials.private_origin(product, db, "owner-alex", forged_id)


def test_standalone_ddl_and_provisioning_are_repeatable_without_losing_wait(product):
    import psycopg
    from sqlalchemy import inspect

    from app.followup_schema import check_followup_schema, upgrade_followup_schema
    engine = service_module.engine
    TwinFollowup.__table__.drop(engine)
    ddl = (Path(__file__).resolve().parents[2] / "docs" / "twin-followups-schema.sql").read_text()
    connection_url = service_module.settings.database_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(connection_url, autocommit=True) as connection:
        connection.execute(ddl)
    _, _, _, sent = send(product)
    with product.transaction() as db:
        original = db.get(TwinFollowup, sent["followup"]["id"])
        due, source = original.due_at, original.origin_request_id
    with psycopg.connect(connection_url, autocommit=True) as connection:
        connection.execute(ddl)
    upgrade_followup_schema(engine)
    upgrade_followup_schema(engine)
    check_followup_schema(inspect(engine))
    with product.transaction() as db:
        preserved = db.get(TwinFollowup, sent["followup"]["id"])
        assert preserved.due_at == due and preserved.origin_request_id == source
        assert preserved.status == "waiting"


def test_consumed_only_recipient_skips_router_model(product):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    request = product.send_message("owner-maya", "private-maya", "Ask AAA to confirm", key())["request"]
    context = product.get_runtime_context(request["id"])
    context["operation_id"] = key()
    sent = product._runtime_tool_transaction("send_chat_message", {
        "chat_id": "group-alex-maya", "body": "Does Friday work?", "source_message_ids": [request["source_message_id"]],
        "wait_for_reply": True}, context)
    reply = product.send_message("owner-alex", sent["chat_id"], "Confirmed.", key(), reply_to=sent["message_id"])["message"]
    route = AsyncMock()
    product.runtime = SimpleNamespace(route_targets=route)
    with product.transaction() as db:
        decision = db.scalar(select(RoutingDecision).where(RoutingDecision.message_id == reply["id"]))
        decision_id = decision.id
    asyncio.run(product.route_pending([decision_id]))
    assert route.await_count == 0
    with product.transaction() as db:
        decision = db.get(RoutingDecision, decision_id)
        assert decision.status == "completed" and not decision.needs_response
        assert decision.reason == "handled_by_followup"


def test_followup_retries_keep_source_deadline_and_original_reservation(product):
    _, _, args, sent = send(product)
    product.send_message("owner-maya", args["chat_id"], "Confirmed for the original task", key(), reply_to=sent["message_id"])
    with product.transaction() as db:
        row = db.get(TwinFollowup, sent["followup"]["id"])
        due, canonical_id = row.due_at, row.continuation_request_id
        original = db.get(ReplyRequest, canonical_id)
        intent, reservation, chain_id = original.intent_key, original.automatic_reservation_id, original.causal_chain_id
        spent = db.get(CausalChain, chain_id).spent
    previous = canonical_id
    for generation in (1, 2):
        product.fail_request(previous, "Offline temporary provider failure")
        retried = product.retry_request("owner-alex", previous, key())
        assert retried["source"] == "followup_result"
        assert product.get_runtime_context(retried["id"])["followup_result"]["status"] == "reply"
        with product.transaction() as db:
            request = db.get(ReplyRequest, retried["id"])
            assert request.generation == generation and request.intent_key == intent
            assert request.automatic_reservation_id == reservation
            assert request.causal_chain_id == chain_id
            assert db.get(CausalChain, chain_id).spent == spent
            row = db.get(TwinFollowup, sent["followup"]["id"])
            assert row.due_at == due and row.continuation_request_id == canonical_id
            assert any(source.get("kind") == "chat_context" and source.get("chat_id") == args["chat_id"]
                       for source in product.manifest_provenance(db, retried["id"]))
        previous = retried["id"]
