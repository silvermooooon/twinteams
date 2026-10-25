"""Failed classifier visibility/recovery on disposable PG; no provider network."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from agent_runtime import AssistantMessageEventStream, assistant_message
from fastapi import HTTPException
from sqlalchemy import select
from test_recovery import bridge_for
from test_send_ack import send_clients as send_clients  # noqa: PLC0414
from test_twin_followup_acceptance import product as product  # noqa: PLC0414

from app.models import CausalChain, Membership, Message, ReplyRequest, RoutingDecision, TriggerCandidate

CHAT = "group-alex-maya"


def key():
    return uuid4().hex


def source(product):
    sent = product.send_message("owner-alex", CHAT, "@Maya, can you help?", key())["message"]
    with product.transaction() as db:
        route = db.scalar(select(RoutingDecision).where(RoutingDecision.message_id == sent["id"]))
        return sent["id"], route.id


def failed(product):
    message_id, decision_id = source(product)
    product._settle_route(decision_id, [], False, None, "ConnectError: private diagnostic")
    return message_id, decision_id


def failure(product, owner="owner-alex"):
    return next(message["routing_failure"] for message in product.chat_detail(owner, CHAT)["messages"]
                if message.get("routing_failure"))


@pytest.mark.parametrize("first_outcome", ["network", "invalid_json"])
def test_explicit_retry_executes_a_fresh_durable_command_once(product, first_outcome):
    async def run():
        runtime = await bridge_for(product)
        calls = []
        def offline(model, context, options):
            calls.append(True)
            stream = AssistantMessageEventStream()
            if len(calls) == 1 and first_outcome == "network":
                stream.push({"type": "error", "error": assistant_message(model,
                    content=[], stopReason="error", errorMessage="ConnectError")})
            else:
                body = "invalid JSON" if len(calls) == 1 else json.dumps({
                    "target_owner_ids": ["owner-maya"], "needs_response": True, "reason": "Question"})
                stream.push({"type": "done", "message": assistant_message(model,
                    content=[{"type": "text", "text": body}], stopReason="stop")})
            return stream
        runtime.models.register_api("offline-regression", offline)
        try:
            message_id, decision_id = source(product)
            await product.route_pending([decision_id])
            notice = failure(product)
            assert notice["command_id"] == message_id and notice["can_retry"]
            old = await runtime.store.get_command(runtime.tenant_id, message_id)
            assert old["state"] == ("failed" if first_outcome == "network" else "completed")
            await product.route_pending()
            assert len(calls) == 1  # Failure never retries itself.
            click = key()
            result = product.retry_route("owner-alex", CHAT, decision_id, click, message_id)
            assert result["command_id"] != message_id
            assert product.retry_route("owner-alex", CHAT, decision_id, click, message_id) == result
            with pytest.raises(HTTPException) as stale:
                product.retry_route("owner-alex", CHAT, decision_id, key(), message_id)
            assert stale.value.status_code == 409
            await product.route_pending([decision_id])
            await product.route_pending([decision_id])
            product.process_candidates({CHAT})
            product.process_candidates({CHAT})
            assert len(calls) == 2
            fresh = await runtime.store.get_command(runtime.tenant_id, result["command_id"])
            assert fresh["state"] == "completed" and fresh["session_id"] != old["session_id"]
            assert (await runtime.store.get_command(runtime.tenant_id, message_id))["state"] == old["state"]
            with product.transaction() as db:
                requests = list(db.scalars(select(ReplyRequest).where(ReplyRequest.source_message_id == message_id)))
                assert len(requests) == 1
                assert db.get(CausalChain, requests[0].causal_chain_id).spent == 1
                assert db.get(RoutingDecision, decision_id).status == "completed"
            assert not any(message.get("routing_failure") for message in product.chat_detail("owner-alex", CHAT)["messages"])
        finally:
            await runtime.close()
    asyncio.run(run())


def test_retry_http_permission_idempotence_and_diagnostic_privacy(send_clients):
    product, sender, peer = send_clients
    message_id, decision_id = failed(product)
    own, other = failure(product), failure(product, "owner-maya")
    assert own["can_retry"] and not other["can_retry"]
    assert "ConnectError" not in json.dumps(own) and "private diagnostic" not in json.dumps(other)
    path = f"/api/chats/{CHAT}/routing-decisions/{decision_id}/retry"
    payload = {"client_request_key": key(), "expected_command_id": message_id}
    assert peer.post(path, json=payload).status_code == 403
    assert sender.post(path, json={"client_request_key": key()}).status_code == 422
    accepted = sender.post(path, json=payload)
    assert accepted.status_code == 200
    assert sender.post(path, json=payload).json() == accepted.json()
    assert sender.post(path, json={**payload, "client_request_key": key()}).status_code == 409
    with product.transaction() as db:
        assert db.get(RoutingDecision, decision_id).command_id == accepted.json()["command_id"]
        assert not db.scalar(select(ReplyRequest).where(ReplyRequest.source_message_id == message_id))


@pytest.mark.parametrize("restriction", ["history", "left", "withdrawn"])
def test_failure_and_retry_require_current_visible_source(product, restriction):
    message_id, decision_id = failed(product)
    with product.transaction() as db:
        member = db.scalar(select(Membership).where(Membership.chat_id == CHAT, Membership.owner_id == "owner-alex"))
        if restriction == "history":
            member.lower_seq = db.get(Message, message_id).seq + 1
        elif restriction == "left":
            from app.models import now
            member.left_at = now()
        else:
            db.get(Message, message_id).state = "withdrawn"
    with pytest.raises(HTTPException):
        product.retry_route("owner-alex", CHAT, decision_id, key(), message_id)
    if restriction != "left":
        assert not any(message.get("routing_failure") for message in product.chat_detail("owner-alex", CHAT)["messages"])


@pytest.mark.parametrize("restriction", ["read", "budget"])
def test_retry_preserves_read_and_budget_rules(product, restriction):
    message_id, decision_id = failed(product)
    if restriction == "read":
        product.read("owner-maya", CHAT, [message_id])
    else:
        with product.transaction() as db:
            chain = db.get(CausalChain, db.get(Message, message_id).causal_chain_id)
            chain.spent = chain.allowance
    product.retry_route("owner-alex", CHAT, decision_id, key(), message_id)
    classify = AsyncMock(return_value={"target_owner_ids": ["owner-maya"], "needs_response": True, "reason": "Question"})
    product.bind_runtime(SimpleNamespace(configured=True, startup_error=None, route_targets=classify))
    asyncio.run(product.route_pending([decision_id]))
    product.process_candidates({CHAT})
    assert classify.call_count == (0 if restriction == "budget" else 1)
    with product.transaction() as db:
        assert not db.scalar(select(ReplyRequest).where(ReplyRequest.source_message_id == message_id))
        if restriction == "read":
            candidate = db.scalar(select(TriggerCandidate).where(TriggerCandidate.message_id == message_id))
            assert candidate.state == "suppressed" and candidate.evidence["suppression"] == "owner_read"
        else:
            route = db.get(RoutingDecision, decision_id)
            assert route.status == "paused" and route.reason == "budget_exhausted"


def test_failed_retry_stops_until_another_explicit_click_and_old_result_is_ignored(product):
    message_id, decision_id = failed(product)
    result = product.retry_route("owner-alex", CHAT, decision_id, key(), message_id)
    product._settle_route(decision_id, ["owner-maya"], True, "Stale result", None, message_id)
    with product.transaction() as db:
        assert db.get(RoutingDecision, decision_id).status == "pending"
    classify = AsyncMock(side_effect=RuntimeError("ConnectError"))
    product.bind_runtime(SimpleNamespace(configured=True, startup_error=None, route_targets=classify))
    asyncio.run(product.route_pending([decision_id]))
    asyncio.run(product.route_pending())
    assert classify.call_count == 1
    assert failure(product)["command_id"] == result["command_id"]


@pytest.mark.parametrize("restriction", ["history", "left", "withdrawn"])
def test_queued_retry_rechecks_source_authorization_before_execution(product, restriction):
    message_id, decision_id = failed(product)
    product.retry_route("owner-alex", CHAT, decision_id, key(), message_id)
    with product.transaction() as db:
        member = db.scalar(select(Membership).where(Membership.chat_id == CHAT, Membership.owner_id == "owner-alex"))
        if restriction == "history":
            member.lower_seq = db.get(Message, message_id).seq + 1
        elif restriction == "left":
            from app.models import now
            member.left_at = now()
        else:
            db.get(Message, message_id).state = "withdrawn"
    classify = AsyncMock()
    product.bind_runtime(SimpleNamespace(configured=True, startup_error=None, route_targets=classify))
    asyncio.run(product.route_pending([decision_id]))
    assert classify.call_count == 0
    with product.transaction() as db:
        assert db.get(RoutingDecision, decision_id).status == "failed"
