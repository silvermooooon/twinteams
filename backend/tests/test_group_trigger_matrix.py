"""N-to-N trigger audit: real disposable PG/service transitions, deterministic router.

No worker, provider, SDK model execution or real Redis participates. Router decisions,
presence observations and final reply text are deliberate test inputs; persistence,
eligibility, acceptance, publication, lineage and rerouting are production code.
"""
import asyncio
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_twin_followup_acceptance import product as product  # noqa: PLC0414

from app import service as service_module
from app.models import (
    Actor,
    CausalChain,
    Message,
    ReplyRequest,
    RoutingDecision,
    TriggerCandidate,
    TwinFollowup,
)
from app.service import aware

OWNERS = ("owner-alex", "owner-maya", "owner-jordan")
NAMES = dict(zip(OWNERS, ("AAA", "BBB", "CCC")))
GROUP = "group-team"


def key():
    return uuid4().hex


def decision_for(product, message_id):
    with product.transaction() as db:
        row = db.scalar(select(RoutingDecision).where(RoutingDecision.message_id == message_id))
        return None if row is None else {column.name: getattr(row, column.name)
                                        for column in RoutingDecision.__table__.columns}


def route(product, message_id, targets=(), needs_response=True, *, error=None,
          reason="Deterministic matrix decision"):
    decision = decision_for(product, message_id)
    assert decision and decision["status"] == "pending"
    classify = AsyncMock(side_effect=error, return_value={"needs_response": needs_response,
        "target_owner_ids": list(targets), "reason": reason})
    product.bind_runtime(SimpleNamespace(configured=True, startup_error=None, route_targets=classify))
    product.worker_mode = True
    asyncio.run(product.route_pending([decision["id"]]))
    return classify


def candidates(product, message_id):
    with product.transaction() as db:
        return {db.get(Actor, row.target_twin_id).owner_id: {
            "id": row.id, "state": row.state, "request_id": row.accepted_request_id,
            "due_at": aware(row.due_at), "chain_id": row.causal_chain_id,
            "evidence": row.evidence}
            for row in db.scalars(select(TriggerCandidate).where(TriggerCandidate.message_id == message_id))}


def publish(product, request_id, text):
    product.get_runtime_context(request_id)  # Freeze and validate the real audience/context grant.
    product.complete_request(request_id, body=text, session_id="matrix-" + request_id,
        run_id="matrix-run-" + request_id, event_id="matrix-final-" + request_id, event_seq=1)
    with product.transaction() as db:
        request = db.get(ReplyRequest, request_id)
        message = db.scalar(select(Message).where(Message.request_id == request_id))
        assert request.status == "completed" and message.state == "published"
        assert message.author_id == request.target_twin_id
        assert message.causal_chain_id == request.causal_chain_id
        return message.id


def clock_after(product, monkeypatch, message_id, seconds):
    with product.transaction() as db:
        timestamp = aware(db.get(Message, message_id).created_at) + timedelta(seconds=seconds)
    monkeypatch.setattr(service_module, "now", lambda: timestamp)


@pytest.mark.parametrize("source", OWNERS)
def test_three_owner_target_union_matrix_for_every_human_source(product, source):
    others = [owner for owner in OWNERS if owner != source]
    first, second = others
    cases = [
        ("unaddressed one target", [first], True, "Question", {first}),
        ("unaddressed many targets", [*OWNERS, first, "foreign-owner"], True, "Question", set(others)),
        ("unaddressed empty target", [], True, "Question", set()),
        ("source owner only", [source], True, "Question", set()),
        ("explicit union", [first], True, f"@{NAMES[second]} question", set(others)),
        ("explicit duplicate and source", [first, source], True,
            f"@{NAMES[first]} @{NAMES[source]} question", {first}),
        ("explicit owner id", [], True, f"@{second} question", {second}),
        ("explicit no response", [*OWNERS], False,
            f"@{NAMES[first]} @{NAMES[second]} thanks, complete", set()),
    ]
    for label, inferred, needed, body, expected in cases:
        sent = product.send_message(source, GROUP, body + " " + key(), key())
        assert sent["request"] is None
        message_id = sent["message"]["id"]
        classifier = route(product, message_id, inferred, needed)
        assert classifier.await_count == 1, label
        assert set(classifier.call_args.args[3]) == set(OWNERS), label
        assert set(candidates(product, message_id)) == expected, label
        product.process_candidates({GROUP})
        rows = candidates(product, message_id)
        assert all(row["state"] == "accepted" and row["request_id"] for row in rows.values()), label
        product.process_candidates({GROUP})
        route_decision = decision_for(product, message_id)
        assert route_decision["status"] == "completed" and route_decision["needs_response"] is needed
        with product.transaction() as db:
            requests = list(db.scalars(select(ReplyRequest).where(ReplyRequest.source_message_id == message_id)))
            assert len(requests) == len(expected), label
            assert {db.get(Actor, row.target_twin_id).owner_id for row in requests} == expected, label
            assert all(row.source == "auto" and row.source_owner_id == source for row in requests), label
            assert db.get(CausalChain, sent["message"]["causal_chain_id"]).spent == len(expected), label


@pytest.mark.parametrize("source", ("owner-alex", "owner-maya"))
@pytest.mark.parametrize("needed", (False, True))
def test_pair_explicit_mention_uses_response_gate_with_empty_inference(product, source, needed):
    other = next(owner for owner in ("owner-alex", "owner-maya") if owner != source)
    sent = product.send_message(source, "group-alex-maya", f"@{NAMES[other]} question", key())
    message_id = sent["message"]["id"]
    classifier = route(product, message_id, [], needed)
    assert classifier.await_count == 1
    assert decision_for(product, message_id)["strategy"] == "pair"
    product.process_candidates({"group-alex-maya"})
    assert set(candidates(product, message_id)) == ({other} if needed else set())
    assert all(row["request_id"] for row in candidates(product, message_id).values())


@pytest.mark.parametrize("source", OWNERS)
def test_private_direct_owner_input_accepts_own_duet_without_group_route(product, source):
    sent = product.send_message(source, source.replace("owner-", "private-"), "Direct task", key())
    assert sent["request"]["source"] == "private"
    assert sent["request"]["target_twin_id"] == source.replace("owner-", "twin-")
    assert decision_for(product, sent["message"]["id"]) is None
    output_id = publish(product, sent["request"]["id"], "Completed direct private task")
    assert decision_for(product, output_id) is None


@pytest.mark.parametrize("source", OWNERS)
def test_published_duets_branch_to_both_other_owners_share_six_and_continue(product, source):
    sent = product.send_message(source, GROUP, "Initial question for both owners", key())
    root_id, chain_id = sent["message"]["id"], sent["message"]["causal_chain_id"]
    route(product, root_id, OWNERS)
    product.process_candidates({GROUP})
    first = candidates(product, root_id)
    assert set(first) == set(OWNERS) - {source}
    second_requests = []
    for owner, row in first.items():
        output_id = publish(product, row["request_id"], f"Distinct question from {owner}")
        assert decision_for(product, output_id)["strategy"] == "llm"
        route(product, output_id, OWNERS)
        assert set(candidates(product, output_id)) == set(OWNERS) - {owner}
        product.process_candidates({GROUP})
        second_requests.extend(row["request_id"] for row in candidates(product, output_id).values())
    assert len(second_requests) == 4
    with product.transaction() as db:
        chain = db.get(CausalChain, chain_id)
        assert (chain.spent, chain.allowance) == (6, 6)
        requests = list(db.scalars(select(ReplyRequest).where(ReplyRequest.causal_chain_id == chain_id)))
        assert len(requests) == 6 and len({row.automatic_reservation_id for row in requests}) == 6
        assert all(row.status in {"queued", "completed"} for row in requests)
    paused_id = publish(product, second_requests[0], "A new question after the sixth acceptance")
    classifier = route(product, paused_id, OWNERS)
    assert classifier.await_count == 0
    assert decision_for(product, paused_id)["status"] == "paused"
    assert decision_for(product, paused_id)["reason"] == "budget_exhausted"
    assert candidates(product, paused_id) == {}
    continuation_key = key()
    grant = product.continue_automatic_conversation(source, GROUP, chain_id, continuation_key, 6, 6)
    replay = product.continue_automatic_conversation(source, GROUP, chain_id, continuation_key, 6, 6)
    assert (grant["spent"], grant["allowance"]) == (6, 12)
    assert replay["already_applied"] and replay["allowance"] == 12
    assert decision_for(product, paused_id)["status"] == "pending"
    route(product, paused_id, OWNERS)
    product.process_candidates({GROUP})
    resumed = candidates(product, paused_id)
    assert len(resumed) == 2 and all(row["request_id"] for row in resumed.values())
    with product.transaction() as db:
        assert db.get(CausalChain, chain_id).spent == 8
        assert all(db.get(ReplyRequest, request_id).status in {"queued", "completed"}
                   for request_id in second_requests)


@pytest.mark.parametrize("router_late", (False, True))
def test_three_owner_receipts_are_message_and_owner_scoped_with_fresh_grace(product, monkeypatch, router_late):
    for owner in OWNERS[1:]:
        product._presence_cache[owner] = {"expires_at": time.time() + 3600, "focused_chats": [GROUP]}
    first = product.send_message(OWNERS[0], GROUP, "First joint question", key())["message"]
    if not router_late:
        route(product, first["id"], OWNERS)
    product.read("owner-maya", GROUP, [first["id"]])
    if router_late:
        route(product, first["id"], OWNERS)
    assert candidates(product, first["id"])["owner-maya"]["state"] == "suppressed"
    clock_after(product, monkeypatch, first["id"], 9)
    product.process_candidates({GROUP})
    assert candidates(product, first["id"])["owner-jordan"]["state"] == "waiting"
    clock_after(product, monkeypatch, first["id"], 11)
    product.process_candidates({GROUP})
    assert candidates(product, first["id"])["owner-jordan"]["request_id"]
    second = product.send_message(OWNERS[0], GROUP, "Independent unread question", key())["message"]
    route(product, second["id"], ["owner-maya"])
    with product.transaction() as db:
        second_deadline = aware(db.get(Message, second["id"]).created_at) + timedelta(seconds=10)
    assert candidates(product, second["id"])["owner-maya"]["due_at"] == second_deadline
    clock_after(product, monkeypatch, second["id"], 9)
    product.process_candidates({GROUP})
    assert candidates(product, second["id"])["owner-maya"]["request_id"] is None
    clock_after(product, monkeypatch, second["id"], 11)
    product.process_candidates({GROUP})
    assert candidates(product, second["id"])["owner-maya"]["request_id"]
    assert candidates(product, first["id"])["owner-maya"]["state"] == "suppressed"
    assert first["causal_chain_id"] != second["causal_chain_id"]


def test_three_owner_known_offline_accepts_while_unknown_presence_waits(product, monkeypatch):
    product._presence_cache.pop("owner-jordan")
    message = product.send_message("owner-alex", GROUP, "Question for both owners", key())["message"]
    route(product, message["id"], OWNERS)
    product.process_candidates({GROUP})
    rows = candidates(product, message["id"])
    assert rows["owner-maya"]["request_id"] and rows["owner-jordan"]["request_id"] is None
    clock_after(product, monkeypatch, message["id"], 60)
    product.process_candidates({GROUP})
    assert candidates(product, message["id"])["owner-jordan"]["state"] == "waiting"
    product._presence_cache["owner-jordan"] = {"expires_at": 0, "focused_chats": []}
    product.process_candidates({GROUP})
    assert candidates(product, message["id"])["owner-jordan"]["request_id"]


def test_three_owner_followup_consumes_only_origin_owner_other_recipient_still_routes(product):
    origin = product.send_message("owner-alex", "private-alex", "Ask the group for confirmation", key())
    context = product.get_runtime_context(origin["request"]["id"])
    context["operation_id"] = key()
    sent = product._runtime_tool_transaction("send_chat_message", {"chat_id": GROUP,
        "body": "Please confirm Friday", "source_message_ids": [origin["message"]["id"]],
        "wait_for_reply": True}, context)
    reply = product.send_message("owner-maya", GROUP, "@AAA @CCC Friday confirmed; CCC can you arrange it?",
        key(), reply_to=sent["message_id"])["message"]
    product.process_followups()
    with product.transaction() as db:
        followup = db.get(TwinFollowup, sent["followup"]["id"])
        continuation_id = followup.continuation_request_id
        assert followup.status == "replied" and continuation_id
        continuation = db.get(ReplyRequest, continuation_id)
        assert continuation.chat_id == "private-alex" and continuation.source == "followup_result"
        assert continuation.causal_chain_id == origin["request"]["causal_chain_id"]
    classifier = route(product, reply["id"], OWNERS)
    assert set(classifier.call_args.args[3]) == {"owner-maya", "owner-jordan"}
    assert set(candidates(product, reply["id"])) == {"owner-jordan"}
    product.process_candidates({GROUP})
    assert candidates(product, reply["id"])["owner-jordan"]["request_id"]
    product.process_followups()
    with product.transaction() as db:
        assert db.get(TwinFollowup, sent["followup"]["id"]).continuation_request_id == continuation_id
        assert len(list(db.scalars(select(ReplyRequest).where(ReplyRequest.source_message_id == reply["id"])))) == 1


def test_router_failure_does_not_turn_explicit_mentions_into_automatic_acceptances(product):
    message = product.send_message("owner-alex", GROUP, "@BBB @CCC new question", key())["message"]
    route(product, message["id"], error=RuntimeError("Deterministic routing outage"))
    product.process_candidates({GROUP})
    assert decision_for(product, message["id"])["status"] == "failed"
    assert candidates(product, message["id"]) == {}


@pytest.mark.parametrize("inferred", [[], ["owner-alex"]])
def test_pair_own_duet_request_does_not_select_the_other_owner(product, inferred):
    message = product.send_message("owner-alex", "group-alex-maya",
        "@AAA's Duet please prepare my notes; this is not addressed to BBB", key())["message"]
    reason = "Request is for the author's own Duet, not addressed to the other owner"
    classifier = route(product, message["id"], inferred, True, reason=reason)
    assert classifier.return_value["target_owner_ids"] == inferred
    assert decision_for(product, message["id"])["reason"] == reason
    assert decision_for(product, message["id"])["inferred_owner_ids"] == []
    product.process_candidates({"group-alex-maya"})
    assert candidates(product, message["id"]) == {}


@pytest.mark.parametrize("chat_id", ["group-alex-maya", GROUP])
@pytest.mark.parametrize("needs_response", [True, False])
def test_custom_duet_mention_resolves_on_first_message_and_router_gets_roster(product, chat_id, needs_response):
    with product.transaction() as db:
        db.get(Actor, "twin-maya").name = "Harbor custom Duet"
    message = product.send_message("owner-alex", chat_id,
        "@Harbor custom Duet please explain the delivery plan", key())["message"]
    classifier = route(product, message["id"], [], needs_response)
    decision = decision_for(product, message["id"])
    assert decision["mentioned_owner_ids"] == ["owner-maya"] and decision["inferred_owner_ids"] == []
    _, payload, context, allowed_owners, participants = classifier.call_args.args
    expected_owners = set(OWNERS) if chat_id == GROUP else {"owner-alex", "owner-maya"}
    assert set(allowed_owners) == expected_owners
    assert {actor["owner_id"] for actor in participants} == expected_owners
    assert len(participants) == 2 * len(expected_owners)
    assert {"id": "twin-maya", "kind": "twin", "owner_id": "owner-maya", "name": "Harbor custom Duet"} in participants
    assert {"id": "owner-maya", "kind": "human", "owner_id": "owner-maya", "name": "BBB"} in participants
    assert payload["author_name"] == "AAA"
    assert context["summary"] is None and not context["context_incomplete"]
    assert [item["id"] for item in context["messages"]] == [message["id"]]
    assert {item["author_name"] for item in context["messages"]} == {"AAA"}
    assert "members" not in payload and "actors" not in payload
    product.process_candidates({chat_id})
    rows = candidates(product, message["id"])
    assert set(rows) == ({"owner-maya"} if needs_response else set())
    if needs_response:
        assert rows["owner-maya"]["request_id"]


@pytest.mark.parametrize("body", ["@Anna please review the plan", "(@Anna), please review", "@aNnA，请检查。"])
def test_human_mention_anna_does_not_also_match_ann(product, body):
    with product.transaction() as db:
        db.get(Actor, "owner-maya").name = "Ann"
        db.get(Actor, "owner-jordan").name = "Anna"
    message = product.send_message("owner-alex", GROUP,
        body, key())["message"]
    route(product, message["id"], [], True)
    assert decision_for(product, message["id"])["mentioned_owner_ids"] == ["owner-jordan"]
    product.process_candidates({GROUP})
    rows = candidates(product, message["id"])
    assert set(rows) == {"owner-jordan"}
    assert all(row["request_id"] for row in rows.values())


@pytest.mark.parametrize("body, expected", [
    ("@owner-maya question", {"owner-maya"}),
    ("@twin-maya question", {"owner-maya"}),
    ("@owner-maya-extra question", set()),
    ("someone@BBB question", set()),
    ("@Harbor custom Duet @BBB question", {"owner-maya"}),
    ("@Harbor custom Duet Extended question", {"owner-jordan"}),
    ("@Harbor custom Duet-extra question", set()),
])
def test_mentions_resolve_current_names_and_actor_ids_with_boundaries(product, body, expected):
    with product.transaction() as db:
        db.get(Actor, "twin-maya").name = "Harbor custom Duet"
        db.get(Actor, "twin-jordan").name = "Harbor custom Duet Extended"
    message = product.send_message("owner-alex", GROUP, body, key())["message"]
    route(product, message["id"], [], True)
    assert set(decision_for(product, message["id"])["mentioned_owner_ids"]) == expected
    assert set(candidates(product, message["id"])) == expected
