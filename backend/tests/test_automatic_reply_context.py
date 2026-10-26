"""Final group admission evidence survives retries without becoming live presence."""
import time
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_group_trigger_matrix import GROUP, candidates, clock_after, route
from test_twin_followup_acceptance import product as product  # noqa: PLC0414

from app import service as service_module
from app.models import Chat, ReplyRequest, TriggerCandidate
from app.service import iso

OWNER = "owner-maya"
TWIN = "twin-maya"


def key():
    return uuid4().hex


def presence(product, online):
    product._presence_cache[OWNER] = {"expires_at": time.time() + 3600 if online else 0,
                                    "focused_chats": []}


def question(product):
    message = product.send_message("owner-alex", GROUP, "Can you handle this? " + key(), key())["message"]
    route(product, message["id"], [OWNER])
    return message


def candidate(product, message):
    return candidates(product, message["id"])[OWNER]


def context(product, message):
    return product.get_runtime_context(candidate(product, message)["request_id"])["automatic_reply"]


@pytest.mark.parametrize("online", [False, True])
def test_final_admission_observation_exposed_as_historical_context(product, monkeypatch, online):
    presence(product, online)
    message = question(product)
    clock_after(product, monkeypatch, message["id"], 11 if online else 1)
    observed_at = iso(service_module.now())
    product.process_candidates({GROUP})
    evidence = context(product, message)
    assert evidence == {"reason": "owner_online_unread" if online else "owner_offline",
                        "owner_id": OWNER, "observed_at": observed_at}
    presence(product, not online)
    with product.transaction() as db:
        row = db.get(TriggerCandidate, candidate(product, message)["id"])
        row.evidence = {**row.evidence, "online": not online, "observed_at": "unrelated creation evidence"}
    assert context(product, message) == evidence
    stored = candidate(product, message)["evidence"]
    assert stored["automatic_reply"] == evidence
    assert stored["automatic_reply_request_id"] == candidate(product, message)["request_id"]


@pytest.mark.parametrize("initial_online", [False, True])
def test_presence_flip_uses_final_observation_and_online_grace(product, monkeypatch, initial_online):
    presence(product, initial_online)
    message = question(product)
    presence(product, not initial_online)
    clock_after(product, monkeypatch, message["id"], 1)
    product.process_candidates({GROUP})
    assert candidate(product, message)["state"] == "waiting"
    assert "automatic_reply" not in candidate(product, message)["evidence"]
    clock_after(product, monkeypatch, message["id"], 11)
    product.process_candidates({GROUP})
    assert context(product, message) == {
        "reason": "owner_offline" if initial_online else "owner_online_unread",
        "owner_id": OWNER, "observed_at": iso(service_module.now())}


def test_read_suppresses_without_admission_evidence(product, monkeypatch):
    presence(product, True)
    message = question(product)
    product.read(OWNER, GROUP, [message["id"]])
    clock_after(product, monkeypatch, message["id"], 11)
    product.process_candidates({GROUP})
    row = candidate(product, message)
    assert row["state"] == "suppressed" and row["request_id"] is None
    assert "automatic_reply" not in row["evidence"]


def test_unknown_presence_never_becomes_offline_evidence(product, monkeypatch):
    product._presence_cache.pop(OWNER)
    message = question(product)
    clock_after(product, monkeypatch, message["id"], 60)
    product.process_candidates({GROUP})
    row = candidate(product, message)
    assert row["state"] == "waiting" and row["request_id"] is None
    assert "automatic_reply" not in row["evidence"]
    presence(product, False)
    product.process_candidates({GROUP})
    assert context(product, message)["reason"] == "owner_offline"


def test_manual_and_tool_retries_preserve_original_observation(product, monkeypatch):
    message = question(product)
    product.process_candidates({GROUP})
    original_id = candidate(product, message)["request_id"]
    original = context(product, message)
    presence(product, True)
    product.read(OWNER, GROUP, [message["id"]])
    clock_after(product, monkeypatch, message["id"], 60)
    with product.transaction() as db:
        db.get(ReplyRequest, original_id).status = "failed"
    first = product.retry_request(OWNER, original_id, key())
    assert candidate(product, message)["request_id"] == first["id"]
    assert product.get_runtime_context(first["id"])["automatic_reply"] == original
    assert product.get_runtime_context(original_id, include_completed=True)["automatic_reply"] == original
    with product.transaction() as db:
        db.get(ReplyRequest, first["id"]).status = "failed"
        second = product._retry_request_db(db, OWNER, first["id"], key(), automatic=True)
    assert product.get_runtime_context(second["id"])["automatic_reply"] == original
    assert candidate(product, message)["evidence"]["automatic_reply_request_id"] == original_id


def test_legacy_creation_observation_is_not_admission_evidence_even_on_retry(product):
    message = question(product)
    product.process_candidates({GROUP})
    request_id = candidate(product, message)["request_id"]
    with product.transaction() as db:
        row = db.scalar(select(TriggerCandidate).where(TriggerCandidate.message_id == message["id"],
                                                       TriggerCandidate.target_twin_id == TWIN))
        row.evidence = {"online": False, "read": False, "observed_at": iso(service_module.now())}
        db.get(ReplyRequest, request_id).status = "failed"
    assert product.get_runtime_context(request_id, include_completed=True)["automatic_reply"] is None
    retry = product.retry_request(OWNER, request_id, key())
    assert product.get_runtime_context(retry["id"])["automatic_reply"] is None


@pytest.mark.parametrize("source", ["manual", "reply_for_me", "subagent_result", "retry", "tool_retry"])
def test_other_request_sources_do_not_inherit_candidate_evidence(product, source):
    message = question(product)
    product.process_candidates({GROUP})
    with product.transaction() as db:
        accepted = product.accept_request(db, db.get(Chat, GROUP), TWIN, OWNER, source,
            key(), source_message_id=message["id"], intent_key="unrelated:" + key(), input_body="Separate delivery " + key(),
            raw="Please draft my reply" if source == "reply_for_me" else None)
        request_id = accepted.id
    assert product.get_runtime_context(request_id)["automatic_reply"] is None


def test_private_request_and_retry_have_no_group_admission_evidence(product):
    sent = product.send_message(OWNER, "private-maya", "Direct private task", key())
    request_id = sent["request"]["id"]
    assert product.get_runtime_context(request_id)["automatic_reply"] is None
    with product.transaction() as db:
        db.get(ReplyRequest, request_id).status = "failed"
    retry = product.retry_request(OWNER, request_id, key())
    assert product.get_runtime_context(retry["id"])["automatic_reply"] is None
