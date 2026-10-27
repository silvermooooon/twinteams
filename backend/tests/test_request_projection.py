"""Latest retry projections on disposable PostgreSQL, without model calls."""
from datetime import timedelta
from uuid import uuid4

import pytest
from test_memory_dream import accepted
from test_memory_dream import product as product  # noqa: PLC0414

from app.models import ReplyLane, ReplyRequest, now


def visible_requests(product):
    return {row["id"]: row for row in product.chat_detail("owner-alex", "private-alex")["requests"]}


@pytest.mark.parametrize("terminal", ["completed", "cancelled", "failed"])
def test_retry_lifecycle_never_revives_an_older_failure(product, terminal):
    old = accepted(product)
    product.fail_request(old, "Synthetic original failure")
    unrelated = accepted(product)
    product.fail_request(unrelated, "Synthetic independent failure")
    assert set(visible_requests(product)) == {old, unrelated}

    retry = product.retry_request("owner-alex", old, uuid4().hex)["id"]
    rows = visible_requests(product)
    assert set(rows) == {retry, unrelated} and rows[retry]["status"] == "queued"
    product.bind_runtime_run(retry, product.get_request(retry)["session_id"], "synthetic-run")
    rows = visible_requests(product)
    assert set(rows) == {retry, unrelated} and rows[retry]["status"] == "running"

    if terminal == "completed":
        product.complete_request(retry)
    elif terminal == "cancelled":
        product._cancel_intent("owner-alex", retry)
        product.finish_request(retry, "cancelled")
    else:
        product.fail_request(retry, "Synthetic retry failure")
    rows = visible_requests(product)
    assert set(rows) == ({retry, unrelated} if terminal == "failed" else {unrelated})
    with product.transaction() as db:
        assert db.get(ReplyRequest, old).status == "failed"
        assert db.get(ReplyRequest, retry).status == terminal
    if terminal == "failed":
        assert rows[retry]["can_retry"] is True
        next_retry = product.retry_request("owner-alex", retry, uuid4().hex)["id"]
        assert set(visible_requests(product)) == {next_retry, unrelated}


@pytest.mark.parametrize("latest_status", ["completed", "failed"])
def test_superseded_generations_do_not_fill_the_200_request_limit(product, latest_status):
    unrelated = accepted(product)
    product.fail_request(unrelated, "Synthetic independent failure")
    old = accepted(product)
    product.fail_request(old, "Synthetic old failure")
    # Retain a large retry history without running dispatch or a provider. The
    # separate older failure must remain visible after obsolete attempts drop out.
    with product.transaction() as db:
        original = db.get(ReplyRequest, old)
        lane = db.get(ReplyLane, ("private-alex", "twin-alex"))
        start = now()
        for generation in range(1, 206):
            row = ReplyRequest(id=uuid4().hex, chat_id=original.chat_id,
                target_twin_id=original.target_twin_id, requester_id=original.requester_id,
                source="retry", source_message_id=original.source_message_id,
                intent_key=original.intent_key, generation=generation,
                client_request_key=uuid4().hex, accepted_seq=lane.next_accept_seq,
                accepted_at=start + timedelta(seconds=generation),
                private_generation=original.private_generation,
                status=latest_status if generation == 205 else "failed")
            db.add(row)
            lane.next_accept_seq += 1
            latest_id = row.id
    assert set(visible_requests(product)) == (
        {unrelated, latest_id} if latest_status == "failed" else {unrelated})
