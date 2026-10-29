"""The explicit breaking upgrade cleans retired state once, on disposable PG."""
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select, text
from test_memory_dream import product as product  # noqa: PLC0414

from app import service as service_module
from app.duet_message_schema import (
    RETIRED_TABLES,
    check_duet_message_schema,
    upgrade_duet_message_schema,
)
from app.models import (
    AllowanceGrant,
    CausalChain,
    DispatchCommand,
    Message,
    PendingChildResult,
    ReplyRequest,
)


def request(product, body):
    return product.send_message("owner-alex", "private-alex", body, uuid4().hex)["request"]


def test_fresh_schema_checks_and_repeat_upgrade_preserve_current_send_accounting(product):
    row = request(product, "Current owner intent")
    with product.transaction() as db:
        chain = db.get(CausalChain, row["causal_chain_id"])
        chain.spent, chain.allowance = 7, 12
        db.add(AllowanceGrant(causal_chain_id=chain.id, chat_id="private-alex", owner_id="owner-alex",
            client_request_key="current-grant", amount=6, base_allowance=6))
    for _ in range(2):
        upgrade_duet_message_schema(service_module.engine)
        check_duet_message_schema(inspect(service_module.engine))
    with product.transaction() as db:
        chain = db.get(CausalChain, row["causal_chain_id"])
        assert (chain.spent, chain.allowance) == (7, 12)
        assert db.scalar(select(AllowanceGrant).where(AllowanceGrant.client_request_key == "current-grant"))
    with service_module.engine.begin() as connection:
        connection.execute(text("DROP INDEX tt_pending_duet_sends"))
    with pytest.raises(RuntimeError, match="explicit Duet message schema"):
        check_duet_message_schema(inspect(service_module.engine))
    upgrade_duet_message_schema(service_module.engine)
    check_duet_message_schema(inspect(service_module.engine))


@pytest.mark.parametrize("pause_reason", ["budget_exhausted", "repeated_content"])
def test_old_upgrade_cancels_only_retired_work_preserves_messages_and_releases_budget_paused_children(product, pause_reason):
    old_auto, old_followup, completed, current = [request(product, body) for body in (
        "Old peer work", "Old waiting continuation", "Completed old task", "Current owner work")]
    child_id = uuid4().hex
    with product.transaction() as db:
        db.get(ReplyRequest, old_auto["id"]).source = "auto"
        old = db.get(ReplyRequest, old_followup["id"])
        old.source, old.status = "followup_result", "running"
        old = db.get(ReplyRequest, completed["id"])
        old.source, old.status = "auto", "completed"
        chain = db.get(CausalChain, current["causal_chain_id"])
        chain.spent, chain.allowance = 7, 12
        db.add(AllowanceGrant(causal_chain_id=chain.id, chat_id="private-alex", owner_id="owner-alex",
            client_request_key="retired-grant", amount=6, base_allowance=6))
        db.add(PendingChildResult(id=child_id, causal_chain_id=chain.id, parent_request_id=current["id"],
            chat_id="private-alex", owner_id="owner-alex", requester_id="owner-alex", target_twin_id="twin-alex",
            body="Durable child work must remain available", notification={}, state="paused", pause_reason=pause_reason))
        before = [(m.id, m.body, m.seq, m.state) for m in db.scalars(select(Message).order_by(Message.id))]
    with service_module.engine.begin() as connection:
        for table in RETIRED_TABLES:
            connection.execute(text(f"CREATE TABLE {table} (id text PRIMARY KEY)"))
        connection.execute(text("ALTER TABLE tt_reply_requests ADD COLUMN automatic_reservation_id varchar(80)"))
        connection.execute(text("ALTER TABLE tt_messages ALTER COLUMN seq SET NOT NULL"))
        connection.execute(text("ALTER TABLE tt_messages DROP CONSTRAINT tt_message_state"))
        connection.execute(text("ALTER TABLE tt_messages ADD CONSTRAINT tt_message_state CHECK (state IN ('published','withdrawn'))"))
    with pytest.raises(RuntimeError):
        check_duet_message_schema(inspect(service_module.engine))
    upgrade_duet_message_schema(service_module.engine)
    check_duet_message_schema(inspect(service_module.engine))
    with product.transaction() as db:
        for previous in (old_auto, old_followup):
            row = db.get(ReplyRequest, previous["id"])
            assert row.status == "cancelled" and row.cancel_requested
            assert db.get(DispatchCommand, row.id).state == "delivered"
        assert db.get(ReplyRequest, completed["id"]).status == "completed"
        assert db.get(ReplyRequest, current["id"]).status == "queued"
        assert db.scalar(select(AllowanceGrant).where(AllowanceGrant.client_request_key == "retired-grant")) is None
        assert (db.get(CausalChain, current["causal_chain_id"]).spent, db.get(CausalChain, current["causal_chain_id"]).allowance) == (0, 6)
        child = db.get(PendingChildResult, child_id)
        assert child.state == "pending" and child.pause_reason is None
        assert child.body == "Durable child work must remain available"
        assert [(m.id, m.body, m.seq, m.state) for m in db.scalars(select(Message).order_by(Message.id))] == before
        db.get(CausalChain, current["causal_chain_id"]).spent = 1
    upgrade_duet_message_schema(service_module.engine)
    with product.transaction() as db:
        assert db.get(CausalChain, current["causal_chain_id"]).spent == 1
