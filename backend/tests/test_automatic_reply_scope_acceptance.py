"""Independent automatic reply boundaries; disposable PG, no worker or model calls."""
import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from test_memory_dream import product as product  # noqa: PLC0414
from test_send_ack import send_clients as send_clients  # noqa: PLC0414
from test_send_ack import sender_socket

from app import service as service_module
from app.models import CausalChain, Chat, Message, ReplyRequest, RoutingDecision, TriggerCandidate
from app.service import aware


def route(product, chat_id="group-alex-maya", *, settle=True):
    sent = product.send_message("owner-alex", chat_id, "Synthetic question for Maya", uuid4().hex)
    with product.transaction() as db:
        decision = db.scalar(select(RoutingDecision).where(RoutingDecision.message_id == sent["message"]["id"]))
        decision_id = decision.id
    if settle:
        product._settle_route(decision_id, ["owner-maya"], True, "Offline synthetic recipient decision", None)
    return sent["message"]["id"], decision_id


def candidate(product, message_id):
    with product.transaction() as db:
        row = db.scalar(select(TriggerCandidate).where(TriggerCandidate.message_id == message_id,
            TriggerCandidate.target_twin_id == "twin-maya"))
        return {"id": row.id, "state": row.state, "due_at": aware(row.due_at),
            "request_id": row.accepted_request_id, "chain_id": row.causal_chain_id}


def clock_after_source(product, monkeypatch, message_id, seconds):
    with product.transaction() as db:
        timestamp = aware(db.get(Message, message_id).created_at) + timedelta(seconds=seconds)
    monkeypatch.setattr(service_module, "now", lambda: timestamp)


@pytest.mark.parametrize("chat_id", ["group-alex-maya", "group-team"])
def test_known_offline_accepts_immediately_but_unknown_presence_does_not(send_clients, chat_id):
    product, _, _ = send_clients
    product._presence_cache["owner-maya"] = {"expires_at": 0, "focused_chats": []}
    message_id, _ = route(product, chat_id)
    assert candidate(product, message_id)["state"] == "waiting"
    product.process_candidates({chat_id})
    row = candidate(product, message_id)
    assert row["state"] == "accepted" and row["request_id"]
    with product.transaction() as db:
        request = db.get(ReplyRequest, row["request_id"])
        assert request.source == "auto" and request.target_twin_id == "twin-maya"
        assert db.get(CausalChain, row["chain_id"]).spent == 1
    product._presence_cache.pop("owner-maya")
    unknown, _ = route(product, chat_id)
    product.process_candidates({chat_id})
    assert candidate(product, unknown)["request_id"] is None



@pytest.mark.parametrize("online", [False, True])
def test_stale_offline_cache_cannot_bypass_fresh_presence_sample(send_clients, monkeypatch, online):
    product, _, _ = send_clients
    chat_id = "group-alex-maya"
    product._presence_cache["owner-maya"] = {"expires_at": 0, "focused_chats": []}
    message_id, _ = route(product, chat_id)
    assert candidate(product, message_id)["request_id"] is None
    # Use the worker's existing Redis refresh with a fresh read-only sample,
    # after routing created the waiting candidate. No presence value is rewritten.
    redis = SimpleNamespace(
        smembers=AsyncMock(return_value={"live-device"} if online else set()),
        mget=AsyncMock(return_value=[json.dumps({"chat_id": chat_id, "visible": True})]),
    )
    monkeypatch.setattr(service_module.notifications, "redis", redis)
    asyncio.run(product.refresh_presence({chat_id}))
    redis.smembers.assert_awaited_once()
    product.process_candidates({chat_id})
    if online:
        first = candidate(product, message_id)
        assert first["state"] == "waiting" and first["request_id"] is None
        with product.transaction() as db:
            assert first["due_at"] == aware(db.get(Message, message_id).created_at) + timedelta(seconds=10)
        clock_after_source(product, monkeypatch, message_id, 9)
        product.process_candidates({chat_id})
        assert candidate(product, message_id)["request_id"] is None
        clock_after_source(product, monkeypatch, message_id, 11)
        product.process_candidates({chat_id})
    else:
        request_id = candidate(product, message_id)["request_id"]
        with product.transaction() as db:
            request = db.get(ReplyRequest, request_id)
            assert aware(request.accepted_at) - aware(db.get(Message, message_id).created_at) < timedelta(seconds=10)
    assert candidate(product, message_id)["state"] == "accepted"
    product.process_candidates({chat_id})
    with product.transaction() as db:
        assert db.scalar(select(func.count()).select_from(ReplyRequest).where(
            ReplyRequest.source_message_id == message_id, ReplyRequest.target_twin_id == "twin-maya")) == 1


@pytest.mark.parametrize("chat_id", ["group-alex-maya", "group-team"])
def test_online_read_and_focus_are_not_replies_and_do_not_cancel_ten_second_wait(send_clients, monkeypatch, chat_id):
    product, _, _ = send_clients
    product.presence("owner-maya", "focused-device", chat_id, True)
    message_id, _ = route(product, chat_id)
    first = candidate(product, message_id)
    assert first["state"] == "waiting" and first["request_id"] is None
    with product.transaction() as db:
        assert first["due_at"] == aware(db.get(Message, message_id).created_at) + timedelta(seconds=10)
    product.read("owner-maya", chat_id, [message_id])
    product.presence("owner-maya", "second-focused-device", chat_id, True)
    product.process_candidates({chat_id})
    assert candidate(product, message_id)["state"] == "waiting"
    clock_after_source(product, monkeypatch, message_id, 9)
    product.process_candidates({chat_id})
    assert candidate(product, message_id)["request_id"] is None
    clock_after_source(product, monkeypatch, message_id, 11)
    product.process_candidates({chat_id})
    accepted = candidate(product, message_id)
    assert accepted["state"] == "accepted" and accepted["request_id"]
    product.process_candidates({chat_id})
    with product.transaction() as db:
        assert db.scalar(select(func.count()).select_from(ReplyRequest).where(
            ReplyRequest.source_message_id == message_id, ReplyRequest.target_twin_id == "twin-maya")) == 1


@pytest.mark.parametrize("reply_for_me", [False, True])
@pytest.mark.parametrize("router_late", [False, True])
def test_owner_actual_input_cancels_automatic_reply_even_when_router_finishes_late(send_clients, monkeypatch, reply_for_me, router_late):
    product, _, _ = send_clients
    chat_id = "group-alex-maya"
    product.presence("owner-maya", "online-device", None, False)
    message_id, decision_id = route(product, chat_id, settle=not router_late)
    submitted = product.send_message("owner-maya", chat_id, "Synthetic owner response", uuid4().hex,
        reply_for_me=reply_for_me)
    if reply_for_me:
        assert submitted["message"] is None and submitted["request"]["source"] == "reply_for_me"
        assert submitted["request"]["target_twin_id"] == "twin-maya"
    if router_late:
        product._settle_route(decision_id, ["owner-maya"], True, "Offline delayed routing", None)
    clock_after_source(product, monkeypatch, message_id, 11)
    product.process_candidates({chat_id})
    assert candidate(product, message_id)["state"] == "suppressed"
    assert candidate(product, message_id)["request_id"] is None


def test_http_and_websocket_cannot_manually_call_or_retry_another_owners_twin(send_clients):
    product, sender, peer = send_clients
    product.presence("owner-maya", "online-device", None, False)
    message_id, _ = route(product)
    with product.transaction() as db:
        before = db.scalar(select(func.count()).select_from(ReplyRequest))
    response = sender.post("/api/chats/group-alex-maya/reply-requests", json={
        "message_id": message_id, "target_twin_id": "twin-maya", "client_request_key": uuid4().hex})
    assert response.status_code == 403
    with sender_socket(sender) as socket:
        command_id = uuid4().hex
        socket.send_json({"v": 1, "type": "command", "command_id": command_id,
            "chat_id": "group-alex-maya", "command": "reply",
            "payload": {"message_id": message_id, "target_twin_id": "twin-maya"}})
        error = socket.receive_json()
        assert error["type"] == "error" and error["status"] == 403 and error["command_id"] == command_id
    with product.transaction() as db:
        assert db.scalar(select(func.count()).select_from(ReplyRequest)) == before
        old = product.accept_request(db, db.get(Chat, "group-alex-maya"), "twin-maya",
            "owner-alex", "auto", uuid4().hex, source_message_id=message_id)
        old.status = "failed"
        old_id = old.id
        assert product.request_json(db, old, "owner-alex")["can_retry"] is False
        assert product.request_json(db, old, "owner-maya")["can_retry"] is True
    denied = sender.post(f"/api/reply-requests/{old_id}/retry", json={"client_request_key": uuid4().hex})
    assert denied.status_code == 403
    retried = peer.post(f"/api/reply-requests/{old_id}/retry", json={"client_request_key": uuid4().hex})
    assert retried.status_code == 200 and retried.json()["target_twin_id"] == "twin-maya"
    own = sender.post("/api/chats/group-alex-maya/reply-requests", json={
        "message_id": message_id, "target_twin_id": "twin-alex", "client_request_key": uuid4().hex})
    assert own.status_code == 200 and own.json()["target_twin_id"] == "twin-alex"


def test_continue_retains_budget_and_does_not_advance_online_response_deadline(send_clients, monkeypatch):
    product, _, _ = send_clients
    product.presence("owner-maya", "online-device", None, False)
    message_id, _ = route(product)
    original = candidate(product, message_id)
    with product.transaction() as db:
        row = db.get(TriggerCandidate, original["id"])
        row.state, row.evidence = "paused", {**row.evidence, "pause_reason": "budget_exhausted"}
        chain = db.get(CausalChain, original["chain_id"])
        chain.spent = chain.allowance
        allowance = chain.allowance
    key = uuid4().hex
    continued = product.continue_automatic_conversation("owner-alex", "group-alex-maya",
        original["chain_id"], key, allowance, allowance)
    assert continued["allowance"] == allowance * 2 and continued["spent"] == allowance
    resumed = candidate(product, message_id)
    assert resumed["state"] == "waiting" and resumed["due_at"] == original["due_at"]
    assert resumed["request_id"] is None
    replay = product.continue_automatic_conversation("owner-alex", "group-alex-maya",
        original["chain_id"], key, allowance, allowance)
    assert replay["already_applied"] and replay["allowance"] == allowance * 2
    clock_after_source(product, monkeypatch, message_id, 11)
    product.process_candidates({"group-alex-maya"})
    assert candidate(product, message_id)["request_id"]
    with product.transaction() as db:
        assert db.get(CausalChain, original["chain_id"]).spent == allowance + 1
