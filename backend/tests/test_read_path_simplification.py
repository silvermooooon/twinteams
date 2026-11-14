"""Source-heavy reads and authorization boundaries on disposable PostgreSQL."""
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import inspect
from test_context_acceptance import sql_reads
from test_memory_dream import product as product  # noqa: PLC0414

from app import scheduled_tasks
from app.conversation_context import message_source
from app.models import (
    Approval,
    Chat,
    Message,
    ReplyRequest,
    ScheduledTask,
    TrackingTopic,
    TrackingUpdate,
    now,
)


@pytest.mark.parametrize("count", [1, 40])
def test_source_heavy_tracking_and_approvals_read_message_batches(product, count):
    with product.transaction() as db:
        chat = db.get(Chat, "private-alex")
        sources = [message_source(db, product.append_message(db, chat, author_id="owner-alex",
            kind="human", body=f"Synthetic evidence {index}")) for index in range(count)]
        request = product.accept_request(db, chat, "twin-alex", "owner-alex", "private", uuid4().hex,
            source_message_id=sources[-1]["id"])
        topics = []
        for index, source in enumerate(sources):
            topic = TrackingTopic(chat_id=chat.id, title=f"Topic {index}", description="Synthetic topic",
                subject_key=f"topic-{index}", source_message_ids=[source["id"]], source_floor_seq=source["seq"],
                source_provenance=[source])
            db.add(topic)
            db.add(Approval(id=uuid4().hex, chat_id=chat.id, approver_id="owner-alex", title=f"Approval {index}",
                description="Synthetic decision", target="fixture", impact="fixture",
                sdk_reference={"request_id": request.id}, source_message_ids=[source["id"]],
                source_provenance=[source]))
            topics.append(topic)
        db.flush()
        first_id = topics[0].id
        topics[0].source_message_ids = [source["id"] for source in sources]
        topics[0].source_provenance = sources
        for source in sources:
            db.add(TrackingUpdate(topic_id=first_id, body="Synthetic update", source_message_ids=[source["id"]],
                source_floor_seq=source["seq"], source_provenance=[source]))

    with sql_reads() as reads:
        index = product.tracking_index("owner-alex", "private-alex")
    assert len(index["tracking"]) == count
    assert sum(" from tt_messages" in row["sql"] for row in reads) == 1

    with sql_reads() as reads, product.transaction() as db:
        chat, member = product.membership(db, "owner-alex", "private-alex")
        detail = product._tool_dispatch(db, "get_tracking", {"id": first_id}, {}, chat, member)
    assert len(detail["updates"]) == count
    assert sum(" from tt_messages" in row["sql"] for row in reads) == 1

    with sql_reads() as reads, product.transaction() as db:
        chat, member = product.membership(db, "owner-alex", "private-alex")
        approvals = product._approval_inbox(db, chat, member)
    assert len(approvals) == count
    assert sum(" from tt_messages" in row["sql"] for row in reads) == 1

    with product.transaction() as db:
        db.get(Message, sources[0]["id"]).state = "withdrawn"
    assert len(product.tracking_index("owner-alex", "private-alex")["tracking"]) == count - 1
    with product.transaction() as db:
        chat, member = product.membership(db, "owner-alex", "private-alex")
        assert len(product._approval_inbox(db, chat, member)) == count - 1
        with pytest.raises(HTTPException) as denied:
            product._tool_dispatch(db, "get_tracking", {"id": first_id}, {}, chat, member)
        assert denied.value.status_code == 404


def test_hash_batch_fills_partial_rows_without_overwriting_dirty_values(product):
    with product.transaction() as db:
        chat = db.get(Chat, "private-alex")
        sources = [message_source(db, product.append_message(db, chat, author_id="owner-alex",
            kind="human", body=f"Synthetic evidence {index}")) for index in range(3)]
    with product.transaction() as db:
        _, member = product.membership(db, "owner-alex", "private-alex")
        cache = {}
        assert product.sources_allowed(db, member, [source["id"] for source in sources], cache=cache)
        message = product._message_metadata(db, sources[0]["id"], cache)
        message.body = "Unflushed owner correction"
        with db.no_autoflush, sql_reads() as reads:
            assert not product.provenance_allowed(db, member, sources, cache=cache)
        assert message.body == "Unflushed owner correction"
        assert inspect(message).attrs.body.history.has_changes()
        assert sum(" from tt_messages" in row["sql"] for row in reads) == 1


def test_request_read_only_locks_when_legacy_lineage_needs_repair(product):
    request = product.send_message("owner-alex", "private-alex", "Synthetic task", uuid4().hex)["request"]
    with sql_reads() as reads:
        current = product.get_request(request["id"])
    assert current["causal_chain_id"]
    assert not any(" for update" in row["sql"] for row in reads)

    with product.transaction() as db:
        db.get(ReplyRequest, request["id"]).causal_chain_id = None
        db.get(Message, request["source_message_id"]).causal_chain_id = None
    with sql_reads() as reads:
        repaired = product.get_request(request["id"])
    assert repaired["causal_chain_id"]
    assert any(" for update" in row["sql"] for row in reads)
    with product.transaction() as db:
        assert db.get(Message, request["source_message_id"]).causal_chain_id == repaired["causal_chain_id"]


def test_scheduled_authorization_checks_sources_once_and_keeps_owner_boundary(product):
    task = scheduled_tasks.write(product, "owner-alex", values={"title": "Synthetic review",
        "instructions": "Review synthetic evidence", "schedule": {"kind": "interval", "seconds": 60},
        "timezone": "Asia/Shanghai"})
    with product.transaction() as db:
        chat = db.get(Chat, "private-alex")
        source = product.append_message(db, chat, author_id="owner-alex", kind="human", body="Synthetic evidence")
        source_id = source.id
        db.get(ScheduledTask, task["id"]).next_run_at = now() - timedelta(seconds=1)
    request_id, = scheduled_tasks.process_due(product)
    context = product.get_runtime_context(request_id)
    with sql_reads() as reads:
        assert product.authorize_runtime_generation(context)
    assert sum(" from tt_memberships" in row["sql"] for row in reads) == 1
    assert sum(" from tt_context_manifests" in row["sql"] for row in reads) == 1
    with pytest.raises(HTTPException) as denied:
        product.authorize_runtime_generation({**context, "owner_id": "owner-maya"})
    assert denied.value.status_code == 403
    with product.transaction() as db:
        db.get(Message, source_id).body = "Corrected evidence"
    with pytest.raises(HTTPException) as denied:
        product.authorize_runtime_generation(context)
    assert denied.value.status_code == 409
