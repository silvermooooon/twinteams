"""Independent Tracking scope on disposable PostgreSQL; no worker/provider calls."""
import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from test_memory_dream import product as product  # noqa: PLC0414
from test_send_ack import send_clients as send_clients  # noqa: PLC0414

from app.models import Chat, ContextManifest, Membership, Message, TrackingTopic, now


def call(product, context, name, args):
    return asyncio.run(product.runtime_tool(name, args, {**context, "operation_id": uuid4().hex}))


def source(product, chat_id, owner="owner-alex"):
    with product.transaction() as db:
        row = product.append_message(db, db.get(Chat, chat_id), author_id=owner,
            kind="human", body="Isolated project evidence " + uuid4().hex)
        return row.id


def topic(product, chat_id, owner="owner-alex", *, sources=True):
    source_id = source(product, chat_id, owner)
    result = call(product, {"owner_id": owner, "chat": {"id": chat_id}}, "create_tracking", {
        "title": "Scoped project " + uuid4().hex, "description": "Verified ongoing decision",
        "source_message_ids": [source_id] if sources else [],
    })
    return result, source_id


def origin(product, chat_id="private-alex", owner="owner-alex"):
    source_id = source(product, chat_id, owner)
    with product.transaction() as db:
        row = product.accept_request(db, db.get(Chat, chat_id), product.twin(db, owner).id,
            owner, "manual", uuid4().hex, source_message_id=source_id)
        request_id = row.id
    return request_id, product.get_runtime_context(request_id)


def ids(rows):
    return {row["id"] for row in rows}


def test_private_union_matches_api_runtime_and_tool_without_cross_owner_leaks(send_clients):
    product, alex, maya = send_clients
    own, _ = topic(product, "private-alex")
    peer, _ = topic(product, "private-maya", "owner-maya")
    pair, _ = topic(product, "group-alex-maya")
    team, _ = topic(product, "group-team")
    stranger_chat = product.create_chat("owner-maya", ["owner-jordan"])["id"]
    stranger, _ = topic(product, stranger_chat, "owner-maya")
    left_chat = product.create_chat("owner-alex", ["owner-maya", "owner-jordan"], "Exited project")["id"]
    left, _ = topic(product, left_chat)
    deleted, _ = topic(product, "group-alex-maya")
    with product.transaction() as db:
        db.scalar(select(Membership).where(Membership.chat_id == left_chat,
            Membership.owner_id == "owner-alex")).left_at = now()
        db.get(TrackingTopic, deleted["id"]).deleted_at = now()
    expected = {own["id"], pair["id"], team["id"]}
    response = alex.get("/api/chats/private-alex/tracking")
    assert response.status_code == 200
    rows = response.json()["tracking"]
    assert ids(rows) == expected
    _, context = origin(product)
    assert ids(context["tracking"]) == expected
    assert ids(call(product, context, "list_tracking", {})["tracking"]) == expected
    assert ids(product.chat_detail("owner-alex", "private-alex")["tracking"]) == expected
    for row in rows:
        assert row["chat_id"] in {"private-alex", "group-alex-maya", "group-team"}
        assert row["chat_title"] and row["chat_kind"] in {"private", "group"}
    assert not {peer["id"], stranger["id"], left["id"], deleted["id"]} & ids(rows)
    peer_rows = maya.get("/api/chats/private-maya/tracking").json()["tracking"]
    assert peer["id"] in ids(peer_rows) and own["id"] not in ids(peer_rows)
    assert maya.get("/api/chats/private-alex/tracking").status_code in {403, 404}


@pytest.mark.parametrize("other", ["private-alex", "group-team"])
def test_group_index_and_explicit_tool_arguments_stay_in_its_own_group(send_clients, other):
    product, alex, _ = send_clients
    local, _ = topic(product, "group-alex-maya")
    foreign, _ = topic(product, other)
    _, context = origin(product, "group-alex-maya")
    assert ids(context["tracking"]) == {local["id"]}
    assert ids(alex.get("/api/chats/group-alex-maya/tracking").json()["tracking"]) == {local["id"]}
    assert ids(call(product, context, "list_tracking", {})["tracking"]) == {local["id"]}
    for name, args in [
        ("list_tracking", {"chat_id": other}),
        ("get_tracking", {"chat_id": other, "id": foreign["id"]}),
        ("update_tracking", {"chat_id": other, "id": foreign["id"], "status": "completed"}),
        ("add_tracking_update", {"chat_id": other, "id": foreign["id"], "body": "Denied move", "source_message_ids": []}),
    ]:
        with pytest.raises(HTTPException) as error:
            call(product, context, name, args)
        assert error.value.status_code == 403
    with product.transaction() as db:
        assert db.get(TrackingTopic, foreign["id"]).status == "active"


def test_private_reads_and_updates_original_group_topic_without_copying_it(send_clients):
    product, alex, _ = send_clients
    original, _ = topic(product, "group-team")
    _, context = origin(product)
    args = {"chat_id": "group-team", "id": original["id"]}
    details = call(product, context, "get_tracking", args)
    assert details["id"] == original["id"] and details["chat_id"] == "group-team"
    changed = call(product, context, "update_tracking", {**args, "description": "Owner reviewed the existing decision"})
    assert changed["id"] == original["id"] and changed["revision"] == original["revision"] + 1
    with product.transaction() as db:
        assert db.get(TrackingTopic, original["id"]).chat_id == "group-team"
        assert db.scalar(select(func.count()).select_from(TrackingTopic)) == 1
    response = alex.get(f"/api/chats/group-team/tracking/{original['id']}")
    assert response.status_code == 200 and response.json()["description"] == changed["description"]
    assert alex.get(f"/api/chats/private-alex/tracking/{original['id']}").status_code == 404


@pytest.mark.parametrize("policy", ["no_prior", "fixed_7d"])
@pytest.mark.parametrize("with_sources", [False, True])
def test_aggregate_obeys_original_group_history_grants(send_clients, policy, with_sources):
    product, alex, _ = send_clients
    old, old_source = topic(product, "group-team", sources=with_sources)
    with product.transaction() as db:
        cutoff = now()
        member = db.scalar(select(Membership).where(Membership.chat_id == "group-team",
            Membership.owner_id == "owner-alex"))
        member.history_policy, member.joined_at = policy, cutoff
        member.lower_seq = db.get(Message, old_source).seq + 1
        member.grant_version += 1
        db.get(Message, old_source).created_at = cutoff - timedelta(days=8)
        db.get(TrackingTopic, old["id"]).created_at = cutoff - timedelta(days=8)
    fresh, _ = topic(product, "group-team", sources=with_sources)
    rows = alex.get("/api/chats/private-alex/tracking").json()["tracking"]
    assert fresh["id"] in ids(rows) and old["id"] not in ids(rows)
    _, context = origin(product)
    assert fresh["id"] in ids(context["tracking"]) and old["id"] not in ids(context["tracking"])
    assert ids(call(product, context, "list_tracking", {})["tracking"]) == ids(rows)


def test_withdrawn_and_nested_private_sources_are_not_exposed_in_the_aggregate(send_clients):
    product, alex, maya = send_clients
    visible, _ = topic(product, "group-team")
    withdrawn, withdrawn_source = topic(product, "group-team")
    private, private_source = topic(product, "private-maya", "owner-maya")
    nested, _ = topic(product, "group-team", "owner-maya")
    with product.transaction() as db:
        db.get(Message, withdrawn_source).state = "withdrawn"
        db.get(TrackingTopic, nested["id"]).source_provenance = [{"kind": "chat_context",
            "chat_id": "private-maya", "private_generation": 0,
            "sources": [{"kind": "message", "id": private_source}]}]
    rows = alex.get("/api/chats/private-alex/tracking").json()["tracking"]
    assert ids(rows) == {visible["id"]}
    _, context = origin(product)
    assert ids(context["tracking"]) == {visible["id"]}
    peer_rows = maya.get("/api/chats/private-maya/tracking").json()["tracking"]
    assert {visible["id"], nested["id"], private["id"]} <= ids(peer_rows)
    assert withdrawn["id"] not in ids(peer_rows)


@pytest.mark.parametrize("revoke", ["membership", "source"])
def test_aggregate_keeps_foreign_envelope_and_rechecks_after_revocation(product, revoke):
    foreign, source_id = topic(product, "group-team")
    request_id, context = origin(product)
    with product.transaction() as db:
        manifest = db.get(ContextManifest, context["context_manifest_id"])
        envelope = next(row for row in manifest.sources
            if row.get("kind") == "chat_context" and row.get("chat_id") == "group-team")
        assert any(row.get("kind") == "tracking" and row["id"] == foreign["id"]
            for row in envelope["sources"])
        assert not any(row.get("kind") == "tracking" and row["id"] == foreign["id"]
            for row in manifest.sources)
        assert product.provenance_allowed(db, product.membership(db, "owner-alex", "private-alex")[1], manifest.sources)
    assert call(product, context, "get_tracking", {"chat_id": "group-team", "id": foreign["id"]})["id"] == foreign["id"]
    with product.transaction() as db:
        if revoke == "membership":
            db.scalar(select(Membership).where(Membership.chat_id == "group-team",
                Membership.owner_id == "owner-alex")).left_at = now()
        else:
            db.get(Message, source_id).state = "withdrawn"
    with pytest.raises(HTTPException) as error:
        product.get_runtime_context(request_id)
    assert error.value.status_code == 409
    with pytest.raises(HTTPException):
        call(product, context, "update_tracking", {"chat_id": "group-team", "id": foreign["id"], "status": "completed"})
    with product.transaction() as db:
        assert db.get(TrackingTopic, foreign["id"]).status == "active"


def test_soft_delete_hides_fresh_reads_and_writes_without_revoking_frozen_history(send_clients):
    product, alex, _ = send_clients
    old, _ = topic(product, "group-team")
    _, context = origin(product)
    with product.transaction() as db:
        frozen = list(db.get(ContextManifest, context["context_manifest_id"]).sources)
        db.get(TrackingTopic, old["id"]).deleted_at = now()
    assert old["id"] not in ids(alex.get("/api/chats/private-alex/tracking").json()["tracking"])
    assert alex.get(f"/api/chats/group-team/tracking/{old['id']}").status_code == 404
    for name, args in [
        ("get_tracking", {"chat_id": "group-team", "id": old["id"]}),
        ("update_tracking", {"chat_id": "group-team", "id": old["id"], "status": "completed"}),
    ]:
        with pytest.raises(HTTPException) as error:
            call(product, context, name, args)
        assert error.value.status_code == 404
    with product.transaction() as db:
        private_member = product.membership(db, "owner-alex", "private-alex")[1]
        assert product.provenance_allowed(db, private_member, frozen)
        assert db.get(TrackingTopic, old["id"]).status == "active"
