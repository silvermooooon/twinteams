"""Focused causal-conversation checks, isolated PostgreSQL, no server/provider calls.
Run from backend: .venv/bin/python checks/twin_conversation_acceptance.py
"""
from __future__ import annotations
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
from pathlib import Path
import sys
import tempfile
from uuid import uuid4

TEMP = tempfile.TemporaryDirectory(prefix="twinteams-conversation-check-")
os.environ["TWINTEAMS_WORKSPACE_ROOT"] = str(Path(TEMP.name) / "workspace")
os.environ["TWINTEAMS_ENV"] = "local"
os.environ["TWINTEAMS_DEVELOPMENT_MODE"] = "true"
os.environ["TWINTEAMS_AUTOMATIC_REPLY_ALLOWANCE"] = "6"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from postgres import TestDatabase
DATABASE = TestDatabase().configure()
from fastapi import HTTPException
from sqlalchemy import func, select
from app.db import engine
from checks.legacy_requests import legacy_request
from app.models import (Actor, AllowanceGrant, AutomaticReservation, CausalChain,
                        Message, PendingChildResult, Presence, Receipt, ReplyRequest,
                        RoutingDecision, TriggerCandidate, now)
from app.service import ProductService as RealProductService


class ProductService(RealProductService):
    def __init__(self):
        super().__init__()
        # Healthy presence fixture, offline until an explicit focus transition.
        self._presence_cache = {owner: {"expires_at": 0, "focused_chats": []} for owner in ("owner-alex", "owner-maya", "owner-jordan")}

    def presence_state(self, db, owner_id, chat_id):
        # This pure business fixture supplies TTL observations from its explicit
        # lease rows. Real Redis transport/unknown behavior is independently tested.
        leases = list(db.scalars(select(Presence).where(Presence.owner_id == owner_id, Presence.expires_at > now())))
        return bool(leases), any(lease.visible and lease.focused_chat_id == chat_id for lease in leases)


def key():
    return uuid4().hex


def expect_http(code, call):
    try:
        call()
    except HTTPException as exc:
        assert exc.status_code == code, (exc.status_code, exc.detail)
    else:
        raise AssertionError(f"Expected HTTP {code}")


class Router:
    configured = True
    no_response = {"Thanks @BBB"}
    async def route_targets(self, chat_id, message, context, owners):
        assert message["author_owner_id"] == message["source_owner_id"]
        if message["body"] == "INVALID_ROUTER @BBB":
            return ["owner-maya"]
        return {"needs_response": message["body"] not in self.no_response,
                "target_owner_ids": owners, "reason": "Explicit offline fixture"}


def requests_for(service, message_id):
    with service.transaction() as db:
        return [service.request_payload(row) for row in db.scalars(select(ReplyRequest).where(ReplyRequest.source_message_id == message_id).order_by(ReplyRequest.accepted_at))]


def chain_state(service, chain_id):
    with service.transaction() as db:
        chain = db.get(CausalChain, chain_id)
        return chain.spent, chain.allowance


def output(service, request, body):
    service.get_runtime_context(request["id"])
    service.complete_request(request["id"], {"body": body, "session_id": f"s:{request['chat_id']}:{request['target_twin_id']}", "run_id": request["id"], "event_id": f"final:{request['id']}", "event_seq": 2})
    with service.transaction() as db:
        return db.scalar(select(Message.id).where(Message.request_id == request["id"], Message.chat_id == request["chat_id"], ~Message.sdk_output_key.like("tool:%")).order_by(Message.seq.desc()).limit(1))


async def expect_async_http(code, call):
    try:
        await call()
    except HTTPException as exc:
        assert exc.status_code == code, (exc.status_code, exc.detail)
    else:
        raise AssertionError(f"Expected HTTP {code}")


async def main():
    s = ProductService(); s.initialize(); router = Router(); s.bind_runtime(router)
    pair = s.create_chat("owner-alex", ["owner-maya"], "Gate")["id"]
    for body in ["Thanks @BBB", "INVALID_ROUTER @BBB"]:
        message = s.send_message("owner-alex", pair, body, key())["message"]
        await s.route_pending()
        assert not requests_for(s, message["id"])
    print("PASS strict needs_response gate: pair acknowledgments/@/malformed routing cannot force replies")

    pair = s.create_chat("owner-alex", ["owner-maya"], "Shared cap")["id"]
    root = s.send_message("owner-alex", pair, "Please investigate this question", key())["message"]
    await s.route_pending(); current = requests_for(s, root["id"])[0]
    chain_id = current["causal_chain_id"]
    assert current["source_owner_id"] == "owner-alex" and chain_state(s, chain_id) == (1, 6)
    s.get_runtime_context(current["id"])
    mid = s.publish_runtime_output(current["id"], "Intermediate wording", f"s:{pair}:twin-maya", current["id"], "mid", 1)
    with s.transaction() as db:
        assert not db.scalar(select(RoutingDecision.id).where(RoutingDecision.message_id == mid))
    accepted = [current]
    for index in range(1, 6):
        message_id = output(s, current, f"Full final response {index}")
        await s.route_pending(); replies = requests_for(s, message_id)
        assert len(replies) == 1, replies
        current = replies[0]; accepted.append(current)
        assert current["causal_chain_id"] == chain_id
        with s.transaction() as db:
            message = db.get(Message, message_id); actor = db.get(Actor, message.author_id)
            assert message.kind == "twin" and current["requester_id"] == actor.owner_id
            assert db.get(Actor, current["target_twin_id"]).owner_id != actor.owner_id
    assert chain_state(s, chain_id) == (6, 6)
    assert current["id"] in s.claim_dispatches()
    owner = "owner-alex" if current["target_twin_id"] == "twin-alex" else "owner-maya"
    s.bind_runtime_run(current["id"], f"s:{pair}:{current['target_twin_id']}", current["id"])
    s.presence(owner, "accepted-view", pair, True)
    assert s.get_request(current["id"])["status"] == "running"
    final_id = output(s, current, "Full final response 6")
    await s.route_pending(); assert not requests_for(s, final_id)
    rows = s.chat_detail("owner-alex", pair)["automatic_conversations"]
    assert len(rows) == 1 and rows[0]["reason"] == "budget_exhausted" and rows[0]["spent"] == 6 and rows[0]["can_continue"]
    assert all(s.get_request(request["id"])["status"] == "completed" for request in accepted)
    print("PASS Twin author/owner equivalence, final-only routing, six total accepts, accepted work survives presence/cap")

    context = s.get_runtime_context(current["id"], include_completed=True)
    child = {"id": "late-child", "body": "Full late child output", "parent_run_id": current["id"]}
    child_result = s.accept_child_result(child, context)
    assert child_result["status"] == "paused" and child_result["durably_retained"]
    recovered = ProductService(); recovered.bind_runtime(router)
    assert recovered.accept_child_result(child, context)["status"] == "paused"
    assert chain_state(s, chain_id) == (6, 6)
    with s.transaction() as db:
        pending = db.get(PendingChildResult, child["id"])
        assert pending.body == child["body"] and pending.notification == child
    cap_send = await s.runtime_tool("send_chat_message", {"chat_id": pair, "body": "Distinct proactive update from the already accepted run", "source_message_ids": []}, {**context, "operation_id": key()})
    assert cap_send["causal_chain_id"] == chain_id and cap_send["sent"]
    await s.route_pending()
    assert not requests_for(s, cap_send["message_id"]) and chain_state(s, chain_id) == (6, 6)
    print("PASS child payload retained before SDK ack/recovery; accepted-run proactive send shares the exhausted chain")

    # Exhausted work pauses before routing. These reused direct-chat sources all
    # precede the final human reply; that reply suppresses backfill after Continue,
    # while earlier view/read events do not cancel already accepted work.
    attention_sources = []
    accepted_attention = []
    for mode in ("view", "read", "reply"):
        attention_chat = s.create_chat("owner-alex", ["owner-maya"], f"Paused {mode}")["id"]
        with s.transaction() as db:
            for presence in db.scalars(select(Presence).where(Presence.owner_id == "owner-maya")):
                presence.expires_at = now() - timedelta(seconds=1)
            chat, _ = s.membership(db, "owner-alex", attention_chat, True)
            message = s.append_message(db, chat, author_id="twin-alex", kind="twin", body=f"Pending offscreen {mode} question", causal_chain_id=chain_id)
            source_id = message.id
            s.route_committed_message(db, chat, message)
        await s.route_pending()
        with s.transaction() as db:
            candidate = db.scalar(select(TriggerCandidate).where(TriggerCandidate.message_id == source_id, TriggerCandidate.target_twin_id == "twin-maya"))
            assert candidate is None
            decision = db.scalar(select(RoutingDecision).where(RoutingDecision.message_id == source_id))
            assert decision.status == "paused" and decision.reason == "budget_exhausted"
        accepted_request = s.manual_request("owner-alex", attention_chat, source_id, "twin-alex", key())
        expected_status = "queued"
        if mode == "view":
            assert accepted_request["id"] in s.claim_dispatches()
            expected_status = "running"
            s.bind_runtime_run(accepted_request["id"], f"s:{attention_chat}:{accepted_request['target_twin_id']}", accepted_request["id"])
            s.presence("owner-maya", "paused-offscreen", attention_chat, True)
            s.presence("owner-maya", "paused-offscreen", None, False)
            with s.transaction() as db:
                assert db.get(Receipt, (source_id, "owner-maya")) is None
        elif mode == "read":
            s.read("owner-maya", attention_chat, [source_id])
        else:
            s.send_message("owner-maya", attention_chat, "I handled this myself", key())
        with s.transaction() as db:
            candidate = db.scalar(select(TriggerCandidate).where(TriggerCandidate.message_id == source_id, TriggerCandidate.target_twin_id == "twin-maya"))
            assert candidate is None
            assert db.get(PendingChildResult, child["id"]).state == "paused"
        assert s.get_request(accepted_request["id"])["status"] == expected_status
        attention_sources.append(source_id)
        accepted_attention.append((accepted_request["id"], expected_status))

    def grant(owner_id):
        try:
            return ProductService().continue_automatic_conversation(owner_id, pair, chain_id, f"grant:{owner_id}", 6, 6)
        except HTTPException as exc:
            return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        grants = list(pool.map(grant, ["owner-alex", "owner-maya"]))
    assert sum(isinstance(value, dict) for value in grants) == 1 and 409 in grants, grants
    grant_owner = "owner-alex" if isinstance(grants[0], dict) else "owner-maya"
    result = next(value for value in grants if isinstance(value, dict))
    assert result["allowance"] == 12 and result["spent"] == 6
    replay = s.continue_automatic_conversation(grant_owner, pair, chain_id, f"grant:{grant_owner}", 6, 6)
    assert replay["already_applied"] and replay["grant_id"] == result["grant_id"]
    expect_http(409, lambda: s.continue_automatic_conversation("owner-alex", pair, chain_id, key(), 6, 6))
    with s.transaction() as db:
        assert db.scalar(select(func.count()).select_from(AllowanceGrant).where(AllowanceGrant.causal_chain_id == chain_id)) == 1
        pending = db.get(PendingChildResult, child["id"])
        assert pending.state == "accepted"
        child_request = s.request_payload(db.get(ReplyRequest, pending.accepted_request_id))
        assert child_request["source"] == "subagent_result" and child_request["causal_chain_id"] == chain_id
    await s.route_pending()
    for source_id in attention_sources:
        with s.transaction() as db:
            candidate = db.scalar(select(TriggerCandidate).where(TriggerCandidate.message_id == source_id, TriggerCandidate.target_twin_id == "twin-maya"))
            assert candidate.state == "suppressed" and candidate.accepted_request_id is None
        assert not [request for request in requests_for(s, source_id) if request["target_twin_id"] == "twin-maya"]
    for request_id, expected_status in accepted_attention:
        assert s.get_request(request_id)["status"] == expected_status
    print("PASS the later human reply prevents paused-source backfill after Continue; accepted work and child payload survive")
    before = chain_state(s, chain_id)
    s.fail_request(child_request["id"], "Offline retry")
    retry_owner = "owner-alex" if child_request["target_twin_id"] == "twin-alex" else "owner-maya"
    retry = s.retry_request(retry_owner, child_request["id"], key())
    assert chain_state(s, chain_id) == before and retry["automatic_reservation_id"] == child_request["automatic_reservation_id"]
    assert s.get_request(retry["id"])["input_body"] == child["body"]
    fresh = s.send_message("owner-alex", pair, "An independent question", key(), reply_to=root["id"])["message"]
    assert fresh["causal_chain_id"] != chain_id
    manual = legacy_request(s, "owner-alex", pair, final_id, "twin-maya", key())
    assert manual["causal_chain_id"] == chain_id and chain_state(s, chain_id) == before
    print("PASS +6 grant CAS/stale-tab rejection/replay, resumed child, reservation-preserving retry and manual reply")

    origin = s.create_chat("owner-alex", ["owner-maya"], "Proactive source")["id"]
    target = s.create_chat("owner-alex", ["owner-maya"], "Proactive destination")["id"]
    other = s.create_chat("owner-alex", ["owner-jordan"], "Unauthorized audience")["id"]
    human = s.send_message("owner-alex", origin, "Bounded shared source", key())["message"]
    request = s.manual_request("owner-alex", origin, human["id"], "twin-alex", key())
    context = s.get_runtime_context(request["id"])
    chats = await s.runtime_tool("list_authorized_chats", {}, context)
    assert [chat["id"] for chat in chats["chats"]] == [origin]
    args = {"chat_id": target, "body": "A proactive request to another owner", "source_message_ids": [human["id"]]}
    operation = key()
    sent = await s.runtime_tool("send_chat_message", args, {**context, "operation_id": operation})
    assert sent == await s.runtime_tool("send_chat_message", args, {**context, "operation_id": operation})
    assert sent["author_id"] == "twin-alex" and sent["causal_chain_id"] == request["causal_chain_id"]
    repeat = await s.runtime_tool("send_chat_message", {**args, "body": " A PROACTIVE request to another owner "}, {**context, "operation_id": key()})
    assert repeat["status"] == "held" and repeat["sent"] is False
    assert chain_state(s, request["causal_chain_id"]) == (0, 6)
    await expect_async_http(403, lambda: s.runtime_tool("send_chat_message", {**args, "chat_id": other}, {**context, "operation_id": key()}))
    await s.route_pending(); replies = requests_for(s, sent["message_id"])
    assert len(replies) == 1 and replies[0]["causal_chain_id"] == request["causal_chain_id"] and replies[0]["requester_id"] == "owner-alex"
    # An identical final is a distinct main projection, never the tool bubble.
    final = output(s, request, args["body"])
    with s.transaction() as db:
        assert final != sent["message_id"] and db.get(Message, final).chat_id == origin
        assert db.scalar(select(RoutingDecision.id).where(RoutingDecision.message_id == final))
    print("PASS proactive identity/idempotency, cross-chat lineage/audience boundary, outgoing repetition hold, same-body final")

    private = s.send_message("owner-alex", "private-alex", "Private context", key())["request"]
    context = s.get_runtime_context(private["id"])
    chats = await s.runtime_tool("list_authorized_chats", {}, context)
    assert target in {chat["id"] for chat in chats["chats"]} and "private-maya" not in {chat["id"] for chat in chats["chats"]}
    disclosed = await s.runtime_tool("send_chat_message", {"chat_id": target, "body": "A greeting on behalf of Alex", "source_message_ids": []}, {**context, "operation_id": key()})
    assert disclosed["sent"] and disclosed["causal_chain_id"] == private["causal_chain_id"]
    destination = s.chat_detail("owner-maya", target)
    assert any(message["id"] == disclosed["message_id"] for message in destination["messages"])
    assert "Private context" not in str(destination)
    with s.transaction() as db:
        disclosed_message = db.get(Message, disclosed["message_id"])
        assert disclosed_message.source_message_ids == [] and disclosed_message.source_provenance == []
    own = await s.runtime_tool("send_chat_message", {"chat_id": "private-alex", "body": "Authorized private update", "source_message_ids": []}, {**context, "operation_id": key()})
    assert own["author_id"] == "twin-alex"
    # Stop removes incomplete main projections, preserving committed tool effects.
    stop_request = legacy_request(s, "owner-alex", origin, human["id"], "twin-maya", key())
    stop_context = s.get_runtime_context(stop_request["id"])
    same_chat = await s.runtime_tool("send_chat_message", {"chat_id": origin, "body": "Committed same-chat update", "source_message_ids": []}, {**stop_context, "operation_id": key()})
    cross_chat = await s.runtime_tool("send_chat_message", {"chat_id": target, "body": "Committed cross-chat update", "source_message_ids": []}, {**stop_context, "operation_id": key()})
    projection = s.publish_runtime_output(stop_request["id"], "Incomplete main projection", f"s:{origin}:twin-maya", stop_request["id"], "stop-mid", 1)
    with s.transaction() as db:
        row = db.get(ReplyRequest, stop_request["id"]); row.cancel_requested = True; s.withdraw_outputs(db, row)
        assert db.get(Message, projection).state == "withdrawn"
        assert db.get(Message, same_chat["message_id"]).state == db.get(Message, cross_chat["message_id"]).state == "published"
    await expect_async_http(403, lambda: s.runtime_tool("send_chat_message", {"chat_id": origin, "body": "Must not send after stop", "source_message_ids": []}, {**stop_context, "operation_id": key()}))
    print("PASS private metadata discovery, owner-voice cross-chat send without private history, same-chat sends and Stop preserves committed tool messages")

    race_chat = s.create_chat("owner-alex", ["owner-maya", "owner-jordan"], "Atomic cap")["id"]
    root = s.send_message("owner-alex", race_chat, "Race root", key())["message"]
    race_chain = root["causal_chain_id"]
    def accept(index):
        service = ProductService()
        with service.transaction() as db:
            chat, _ = service.membership(db, "owner-alex", race_chat, True)
            row = service.accept_request(db, chat, "twin-maya" if index % 2 else "twin-jordan", "owner-alex", "auto", f"race:{index}", source_message_id=root["id"], intent_key=f"race-intent:{index}", input_body=f"Distinct input {index}", causal_chain_id=race_chain)
            return row.id if row else None
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(accept, range(12)))
    assert sum(value is not None for value in ids) == 6 and chain_state(s, race_chain) == (6, 6)
    with s.transaction() as db:
        assert db.scalar(select(func.count()).select_from(AutomaticReservation).where(AutomaticReservation.causal_chain_id == race_chain)) == 6
        assert all(db.get(ReplyRequest, value).status == "queued" for value in ids if value)
    print("PASS concurrent atomic cap across independent service instances, queued work remains queued")

    repeat_chat = s.create_chat("owner-alex", ["owner-maya"], "Exact repeat")["id"]
    root = s.send_message("owner-alex", repeat_chat, "Case and whitespace", key())["message"]
    with s.transaction() as db:
        chat, _ = s.membership(db, "owner-alex", repeat_chat, True)
        s.accept_request(db, chat, "twin-maya", "owner-alex", "auto", key(), source_message_id=root["id"])
        message = s.append_message(db, chat, author_id="twin-alex", kind="twin", body=" CASE   AND whitespace ", causal_chain_id=root["causal_chain_id"])
        s.create_candidates(db, chat, message, ["owner-alex", "owner-maya"])
        candidate = db.scalar(select(TriggerCandidate).where(TriggerCandidate.message_id == message.id))
        assert candidate.state == "paused" and candidate.evidence["pause_reason"] == "repeated_content"
        assert not db.scalar(select(TriggerCandidate.id).where(TriggerCandidate.message_id == message.id, TriggerCandidate.target_twin_id == "twin-alex"))
    assert chain_state(s, root["causal_chain_id"]) == (1, 6)
    print("PASS normalized repeated content uses sender owner/target and excludes own Twin")

    legacy = s.send_message("owner-maya", "private-maya", "Legacy recovery fixture", key())["request"]
    with s.transaction() as db:
        row = db.get(ReplyRequest, legacy["id"]); source = db.get(Message, row.source_message_id)
        row.causal_chain_id = row.automatic_reservation_id = row.source_owner_id = None; source.causal_chain_id = None
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda service: service.get_request(legacy["id"]), [ProductService(), ProductService()]))
    assert rows[0]["causal_chain_id"] == rows[1]["causal_chain_id"] and rows[0]["automatic_reservation_id"] == rows[1]["automatic_reservation_id"]
    assert chain_state(s, rows[0]["causal_chain_id"])[0] == 1
    print("PASS legacy null-lineage recovery is durable and concurrent callers reuse one reservation")
    # A pre-upgrade candidate must be rerouted and remain inside the NEW target
    # set if a two-person group expanded before it became eligible.
    legacy_group = s.create_chat("owner-alex", ["owner-maya", "owner-jordan"], "Legacy recipient gate")["id"]
    old_message = s.send_message("owner-alex", legacy_group, "Legacy pending question", key())["message"]
    with s.transaction() as db:
        decision = db.scalar(select(RoutingDecision).where(RoutingDecision.message_id == old_message["id"]))
        decision.needs_response, decision.status = None, "completed"
        db.add(TriggerCandidate(chat_id=legacy_group, message_id=old_message["id"], target_twin_id="twin-maya", state="waiting", due_at=now(), evidence={}))
    s.process_candidates()
    assert not requests_for(s, old_message["id"])
    with s.transaction() as db:
        decision = db.scalar(select(RoutingDecision).where(RoutingDecision.message_id == old_message["id"]))
        assert decision.status == "pending"
        decision.needs_response, decision.status = True, "completed"
        decision.inferred_owner_ids, decision.mentioned_owner_ids = ["owner-jordan"], []
    s.process_candidates()
    with s.transaction() as db:
        candidate = db.scalar(select(TriggerCandidate).where(TriggerCandidate.message_id == old_message["id"]))
        assert candidate.state == "suppressed" and candidate.evidence["suppression"] == "not_a_routed_recipient"
    print("PASS legacy unaccepted triggers require needs_response AND the newly approved recipient set")

    # Remove newly introduced fields in this disposable schema to exercise the
    # additive PostgreSQL upgrade without touching the application schema.
    preserved_id = old_message["id"]
    from sqlalchemy import text
    with engine.begin() as db:
        additions = {
            "tt_messages": {"causal_chain_id"},
            "tt_reply_requests": {"causal_chain_id", "automatic_reservation_id", "source_owner_id"},
            "tt_trigger_candidates": {"causal_chain_id"},
            "tt_routing_decisions": {"needs_response", "reason"},
        }
        for table, removed in additions.items():
            for column in removed:
                db.execute(text(f"ALTER TABLE {table} DROP COLUMN {column} CASCADE"))
        for table in ("tt_pending_child_results", "tt_automatic_allowance_grants", "tt_automatic_reservations", "tt_causal_chains"):
            db.execute(text(f"DROP TABLE {table} CASCADE"))
    upgraded = ProductService(); upgraded.initialize()
    with upgraded.transaction() as db:
        assert db.get(Message, preserved_id).body == "Legacy pending question"
    recovered_request = upgraded.get_request(legacy["id"])
    assert recovered_request["causal_chain_id"] and recovered_request["automatic_reservation_id"]
    assert upgraded.get_request(legacy["id"])["causal_chain_id"] == recovered_request["causal_chain_id"]
    print("PASS actual old-column schema upgrades additively, preserves old content and recovers one durable lineage")
    print("Twin conversation acceptance: focused PostgreSQL checks passed; real database checks passed; no provider was called.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        engine.dispose(); TEMP.cleanup()
