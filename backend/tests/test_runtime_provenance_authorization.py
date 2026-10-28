"""Source authorization semantics and query bounds on disposable PostgreSQL only."""
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import inspect, select
from test_context_acceptance import sql_reads
from test_memory_dream import product as product  # noqa: PLC0414

from app.conversation_context import message_source
from app.models import Chat, ChatAttachment, ContextManifest, Membership, Message, now
from app.service import digest


def append(product, *, chat_id="private-alex", dependencies=(), attachment_ids=()):
    with product.transaction() as db:
        message = product.append_message(
            db, db.get(Chat, chat_id), author_id="owner-alex", kind="human",
            body="Synthetic source " + uuid4().hex,
            source_message_ids=[source["id"] for source in dependencies],
            source_provenance=list(dependencies), attachment_ids=list(attachment_ids),
        )
        return message_source(db, message)


def accepted_context(product, sources):
    request = product.send_message(
        "owner-alex", "private-alex", "Use these authorized sources", uuid4().hex,
    )["request"]
    # Exact known context facts, without running or mocking a model/runtime guard.
    with product.transaction() as db:
        chat, member = product.membership(db, "owner-alex", "private-alex")
        db.add(ContextManifest(
            request_id=request["id"], owner_id=member.owner_id,
            grant_version=member.grant_version, high_water_seq=chat.next_message_seq - 1,
            sources=sources, source_digest=digest(sources),
        ))
        generation = chat.reset_generation
    return {"request_id": request["id"], "owner_id": "owner-alex", "private_generation": generation}


def test_shared_source_dag_authorizes_with_linear_database_reads(product):
    leaves = [append(product) for _ in range(12)]
    branches = [append(product, dependencies=leaves) for _ in range(6)]
    context = accepted_context(product, branches)
    distinct_messages = len(leaves) + len(branches)
    with sql_reads() as reads:
        assert product.authorize_runtime_generation(context) is True
    # Repeated references must not refetch each source or its hash fields per path.
    # Allows metadata + hash read per unique message and fixed authorization reads.
    assert len(reads) <= 2 * distinct_messages + 12, len(reads)
    assert sum(row["rows"] for row in reads if " from tt_messages" in row["sql"]) <= 2 * distinct_messages


@pytest.mark.parametrize("cycle", ["self", "two_messages"])
def test_cycles_fail_closed_even_after_a_shared_valid_source_is_cached(product, cycle):
    leaf, first, second = append(product), append(product), append(product)
    with product.transaction() as db:
        one, two = db.get(Message, first["id"]), db.get(Message, second["id"])
        one.source_message_ids = [leaf["id"], first["id"] if cycle == "self" else second["id"]]
        if cycle == "two_messages":
            two.source_message_ids = [leaf["id"], first["id"]]
    context = accepted_context(product, [leaf, first])
    with pytest.raises(HTTPException) as denied:
        product.authorize_runtime_generation(context)
    assert denied.value.status_code == 409


def test_hash_fields_load_together_on_demand_without_overwriting_a_dirty_edit(product):
    source = append(product)
    with product.transaction() as db:
        member = product.membership(db, "owner-alex", "private-alex")[1]
        with sql_reads() as metadata_reads:
            message = product._message_metadata(db, source["id"])
        fields = {"body", "raw_owner_input", "attachment_ids"}
        assert fields <= inspect(message).unloaded
        assert all("tt_messages.body" not in row["sql"].split(" from ", 1)[0] for row in metadata_reads)
        with sql_reads() as hash_reads:
            assert product.provenance_allowed(db, member, [source]) is True
        message_reads = [row["sql"] for row in hash_reads if " from tt_messages" in row["sql"]]
        assert len(message_reads) == 1
        assert all(f"tt_messages.{field}" in message_reads[0].split(" from ", 1)[0] for field in fields)

    # The body is loaded and edited, while the other hash attributes are deferred.
    # Validation must inspect that edit, not refresh it from the stored old value.
    with product.transaction() as db:
        member = product.membership(db, "owner-alex", "private-alex")[1]
        message = product._message_metadata(db, source["id"])
        message.body = "An unflushed owner correction"
        with db.no_autoflush:
            assert product.provenance_allowed(db, member, [source]) is False
        assert message.body == "An unflushed owner correction"
        assert inspect(message).attrs.body.history.has_changes()


@pytest.mark.parametrize("mutation", ["body", "raw_owner_input", "attachment_ids", "attachment_sha", "withdraw"])
def test_next_authorization_rejects_changed_or_withdrawn_hashed_source(product, mutation):
    attachment = product.upload_attachment("owner-alex", "private-alex", "fact.txt", b"Synthetic file fact")
    source = append(product, attachment_ids=[attachment["id"]])
    context = accepted_context(product, [source, source])
    assert product.authorize_runtime_generation(context) is True
    with product.transaction() as db:
        message = db.get(Message, source["id"])
        if mutation == "attachment_sha":
            db.get(ChatAttachment, attachment["id"]).sha256 = "f" * 64
        elif mutation == "attachment_ids":
            message.attachment_ids = []
        elif mutation == "withdraw":
            message.state = "withdrawn"
        else:
            setattr(message, mutation, "Corrected source fact")
    with pytest.raises(HTTPException) as denied:
        product.authorize_runtime_generation(context)
    assert denied.value.status_code == 409


@pytest.mark.parametrize("revocation", ["leave", "history_floor"])
def test_next_authorization_rechecks_foreign_chat_grant_after_success(product, revocation):
    foreign = product.create_chat("owner-alex", ["owner-maya"])["id"]
    source = append(product, chat_id=foreign)
    context = accepted_context(product, [{"kind": "chat_context", "chat_id": foreign, "sources": [source]}])
    assert product.authorize_runtime_generation(context) is True
    with product.transaction() as db:
        member = db.scalar(select(Membership).where(Membership.chat_id == foreign, Membership.owner_id == "owner-alex"))
        if revocation == "leave":
            member.left_at = now()
        else:
            member.lower_seq = source["seq"] + 1
            member.grant_version += 1
    with pytest.raises(HTTPException) as denied:
        product.authorize_runtime_generation(context)
    assert denied.value.status_code == 409
