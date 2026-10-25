"""Checkpoint writes validate their server-owned batch and authorization scope."""
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from test_memory_dream import product as product  # noqa: PLC0414

from app.models import Chat, Membership, Message, Summary


def history(product, count=60):
    with product.transaction() as db:
        chat = db.get(Chat, "group-team")
        rows = [Message(id=uuid4().hex, chat_id=chat.id, seq=chat.next_message_seq + index,
                        author_id="owner-alex", kind="human", body=f"Fact {index}") for index in range(count)]
        db.add_all(rows)
        chat.next_message_seq += count
        db.flush()
        return [row.id for row in rows]


def scope(product):
    return product.get_conversation_context({"chat_id": "group-team", "owner_id": "owner-alex"})["_summary_scope"]


@pytest.mark.parametrize("field,value", [("coverage_end_seq", 100000), ("batch_digest", "invented"),
                                          ("previous_summary_id", "not-a-checkpoint")])
def test_checkpoint_coverage_cannot_be_chosen_by_summary_result(product, field, value):
    ids = history(product)
    prepared = product.prepare_conversation_summary(scope(product))
    with pytest.raises(HTTPException) as error:
        product.save_conversation_summary({**prepared["scope"], field: value}, "Untrusted proposed summary")
    assert error.value.status_code == 409
    with product.transaction() as db:
        assert db.scalar(select(func.count()).select_from(Summary)) == 0
        assert db.scalar(select(func.count()).select_from(Message).where(Message.id.in_(ids))) == len(ids)


def test_grant_change_while_model_summarizes_leaves_originals_and_no_checkpoint(product):
    ids = history(product)
    prepared = product.prepare_conversation_summary(scope(product))
    with product.transaction() as db:
        member = db.scalar(select(Membership).where(Membership.chat_id == "group-team", Membership.owner_id == "owner-alex"))
        member.lower_seq = 25
        member.grant_version += 1
    with pytest.raises(HTTPException) as error:
        product.save_conversation_summary(prepared["scope"], "Old history must not be published")
    assert error.value.status_code == 409
    fresh = product.get_conversation_context({"chat_id": "group-team", "owner_id": "owner-alex"})
    assert fresh["summary"] is None
    assert all(row["seq"] >= 25 for row in fresh["messages"])
    with product.transaction() as db:
        assert db.scalar(select(func.count()).select_from(Message).where(Message.id.in_(ids))) == len(ids)


def test_source_provenance_change_during_summary_is_not_only_a_body_hash(product):
    ids = history(product)
    prepared = product.prepare_conversation_summary(scope(product))
    with product.transaction() as db:
        row = db.get(Message, ids[0])
        row.source_floor_seq = 1  # Body is identical, but the snapshot changed.
    with pytest.raises(HTTPException) as error:
        product.save_conversation_summary(prepared["scope"], "Snapshot changed")
    assert error.value.status_code == 409


def test_current_request_partial_output_cannot_enter_or_be_crossed_by_checkpoint(product):
    ids = history(product)
    request = product.manual_request("owner-alex", "group-team", ids[-1], "twin-alex", uuid4().hex)
    with product.transaction() as db:
        chat = db.get(Chat, "group-team")
        own = product.append_message(db, chat, author_id="twin-alex", kind="twin", body="Partial output",
                                     request_id=request["id"])
        own_seq = own.seq
        product.append_message(db, chat, author_id="owner-alex", kind="human", body="Later fact")
    context = product.get_runtime_context(request["id"])
    assert all(row["request_id"] != request["id"] for row in context["messages"])
    prepared = product.prepare_conversation_summary(context["_summary_scope"])
    assert prepared["scope"]["coverage_end_seq"] < own_seq
    assert all(row.get("request_id") != request["id"] for row in prepared["messages"])


def test_additive_schema_is_repeatable_and_startup_rejects_missing_columns(product):
    from sqlalchemy import text

    from app import service as service_module
    from app.conversation_context import upgrade_schema
    ids = history(product)
    prepared = product.prepare_conversation_summary(scope(product))
    saved = product.save_conversation_summary(prepared["scope"], "Retained checkpoint")
    # The fixture owns a disposable PostgreSQL schema; no application tables
    # outside that schema are mutated by this legacy-schema simulation.
    with service_module.engine.begin() as connection:
        connection.execute(text("ALTER TABLE tt_summaries DROP COLUMN private_generation"))
    with pytest.raises(RuntimeError, match="init_db"):
        product.check_initialized()
    upgrade_schema(service_module.engine)
    upgrade_schema(service_module.engine)
    product.check_initialized()
    with product.transaction() as db:
        assert db.get(Summary, saved["id"]).body == "Retained checkpoint"
        assert db.scalar(select(func.count()).select_from(Message).where(Message.id.in_(ids))) == len(ids)


def test_repeated_context_validates_frozen_tail_after_batched_body_load(product):
    from test_context_acceptance import sql_reads
    ids = history(product)
    prepared = product.prepare_conversation_summary(scope(product))
    product.save_conversation_summary(prepared["scope"], "Checkpoint facts")
    request = product.manual_request("owner-alex", "group-team", ids[-1], "twin-alex", uuid4().hex)
    for _ in range(2):
        with sql_reads() as reads:
            context = product.get_runtime_context(request["id"])
        assert len(context["messages"]) == 20
        assert len(reads) < 50, "Frozen tail hashes must reuse the bounded bulk body read"
        summary_reads = [row["sql"].split(" from ", 1)[0] for row in reads if " from tt_summaries" in row["sql"]]
        assert all("tt_summaries.source_message_ids" not in query for query in summary_reads)


@pytest.mark.parametrize("legacy", [True, False])
def test_current_and_legacy_source_hashes_accept_unchanged_then_reject_body_edit(product, legacy):
    from app.conversation_context import message_source
    from app.service import digest
    ids = history(product, 1)
    with product.transaction() as db:
        member = product.membership(db, "owner-alex", "group-team")[1]
        message = db.get(Message, ids[0])
        source = ({"kind": "message", "id": message.id, "hash": digest(message.body)}
                  if legacy else message_source(db, message))
        assert product.provenance_allowed(db, member, [source])
        message.body = "Corrected fact"
        assert not product.provenance_allowed(db, member, [source])


def test_rebuilt_checkpoint_does_not_reauthorize_a_stale_hashed_derived_tail(product):
    from app.service import digest
    ids = history(product)
    with product.transaction() as db:
        source = db.get(Message, ids[0])
        old_hash = digest(source.body)
        output = product.append_message(db, db.get(Chat, "group-team"), author_id="twin-alex", kind="twin",
            body="Finding based on the old fact", source_message_ids=[source.id], source_floor_seq=source.seq,
            source_provenance=[{"kind": "message", "id": source.id, "hash": old_hash}])
        output_id = output.id
    prepared = product.prepare_conversation_summary(scope(product))
    product.save_conversation_summary(prepared["scope"], "Original facts")
    with product.transaction() as db:
        db.get(Message, ids[0]).body = "Owner corrected the original fact"
    fresh = scope(product)
    prepared = product.prepare_conversation_summary(fresh)
    product.save_conversation_summary(prepared["scope"], "Corrected facts")
    assert output_id not in {row["id"] for row in product.get_conversation_context(fresh)["messages"]}


def test_overlapping_preparations_discard_obsolete_result_after_valid_progress(product):
    history(product, 40)
    first = product.prepare_conversation_summary(scope(product))
    history(product, 1)
    second = product.prepare_conversation_summary(scope(product))
    assert first["scope"]["coverage_end_seq"] < second["scope"]["coverage_end_seq"]
    saved = product.save_conversation_summary(first["scope"], "First completed checkpoint")
    superseded = product.save_conversation_summary(second["scope"], "Obsolete overlapping result")
    assert superseded["id"] == saved["id"]
    assert product.prepare_conversation_summary(second["scope"]) is None
    current = product.get_conversation_context(second["scope"])
    assert current["summary"]["id"] == saved["id"]
    assert not current["context_incomplete"]
