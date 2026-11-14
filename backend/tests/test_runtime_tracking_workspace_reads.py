"""SDK read manifests and request-local workspace reuse on real PostgreSQL."""
from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import inspect, select
from test_context_acceptance import sql_reads
from test_memory_dream import product as product  # noqa: PLC0414
from test_platform_tools import call, origin

from app import service as service_module
from app.conversation_context import message_source
from app.models import (
    Chat,
    ContextManifest,
    Membership,
    Message,
    Summary,
    TrackingTopic,
    TrackingUpdate,
    WorkspaceNode,
    now,
)


@pytest.mark.parametrize("count,hashed", [(1, True), (40, True), (40, False)])
def test_sdk_tracking_batches_projection_and_manifest_sources(product, count, hashed):
    _, context = origin(product, "private-alex")
    with product.transaction() as db:
        chat = db.get(Chat, "private-alex")
        sources = [message_source(db, product.append_message(db, chat, author_id="owner-alex", kind="human",
            body=f"Synthetic source {index}")) for index in range(count)]
        topic = TrackingTopic(chat_id=chat.id, title="Detailed topic", description="Synthetic topic", subject_key="detail",
            source_message_ids=[source["id"] for source in sources], source_floor_seq=1,
            source_provenance=sources if hashed else [])
        db.add(topic)
        db.flush()
        topic_id = topic.id
        for index, source in enumerate(sources):
            db.add(TrackingUpdate(topic_id=topic.id, body="Synthetic update", source_message_ids=[source["id"]],
                source_floor_seq=1, source_provenance=[source] if hashed else []))
            db.add(TrackingTopic(chat_id=chat.id, title=f"Topic {index}", description="Synthetic topic",
                subject_key=f"topic-{index}", source_message_ids=[source["id"]], source_floor_seq=1,
                source_provenance=[source] if hashed else []))
    with sql_reads() as reads:
        detail = call(product, context, "get_tracking", {"id": topic_id})
    assert len(detail["updates"]) == count
    assert sum(" from tt_messages" in row["sql"] for row in reads) <= 7
    assert sum(" from tt_tracking_updates" in row["sql"] for row in reads) == 2
    with sql_reads() as reads:
        listed = call(product, context, "list_tracking", {})
    assert len(listed["tracking"]) == count + 1
    assert sum(" from tt_messages" in row["sql"] for row in reads) <= 8
    assert sum(" from tt_tracking_topics" in row["sql"] for row in reads) == 2
    with product.transaction() as db:
        manifest = db.get(ContextManifest, context["context_manifest_id"])
        recorded = {item["id"]: item for item in manifest.sources if item.get("kind") == "message"}
        assert all(recorded[source["id"]]["hash"] == source["hash"] for source in sources)
        db.get(Message, sources[-1]["id"]).state = "withdrawn"
    with pytest.raises(HTTPException) as denied:
        product.authorize_runtime_generation(context)
    assert denied.value.status_code == 409


def test_tracking_manifest_only_adds_returned_updates(product):
    _, context = origin(product, "private-alex")
    with product.transaction() as db:
        chat, member = product.membership(db, "owner-alex", "private-alex")
        member.history_policy, member.joined_at = "no_prior", now()
        sources = [message_source(db, product.append_message(db, chat, author_id="owner-alex", kind="human",
            body=f"Synthetic update evidence {index}")) for index in range(3)]
        foreign = message_source(db, product.append_message(db, db.get(Chat, "private-maya"), author_id="owner-maya",
            kind="human", body="Other owner's private evidence"))
        topic = TrackingTopic(chat_id=chat.id, title="Visible topic", description="Synthetic topic", subject_key="visible",
            source_message_ids=[], source_floor_seq=1, source_provenance=[])
        db.add(topic)
        db.flush()
        topic_id = topic.id
        visible = TrackingUpdate(topic_id=topic.id, body="Visible update", source_message_ids=[sources[0]["id"]],
            source_floor_seq=1, source_provenance=[sources[0]])
        db.add_all([visible,
            TrackingUpdate(topic_id=topic.id, body="Hidden foreign source", source_message_ids=[sources[1]["id"]],
                source_floor_seq=1, source_provenance=[{"kind": "chat_context", "chat_id": "private-maya",
                    "private_generation": 0, "sources": [foreign]}]),
            TrackingUpdate(topic_id=topic.id, body="Hidden old update", source_message_ids=[], source_floor_seq=1,
                source_provenance=[sources[2]], created_at=member.joined_at - timedelta(days=1))])
        db.flush()
        visible_id = visible.id
    result = call(product, context, "get_tracking", {"id": topic_id})
    assert [row["id"] for row in result["updates"]] == [visible_id]
    with product.transaction() as db:
        manifest = db.get(ContextManifest, context["context_manifest_id"])
        recorded = {item["id"] for item in manifest.sources if item.get("kind") == "message"}
        assert sources[0]["id"] in recorded
        assert sources[1]["id"] not in recorded and sources[2]["id"] not in recorded
        assert not any(item.get("chat_id") == "private-maya" for item in manifest.sources)
        db.get(Message, sources[0]["id"]).state = "withdrawn"
    with pytest.raises(HTTPException):
        product.authorize_runtime_generation(context)


def test_tracking_mutation_manifest_keeps_returned_revision(product):
    _, context = origin(product, "private-alex")
    with product.transaction() as db:
        topic = TrackingTopic(chat_id="private-alex", title="Before", description="Synthetic topic", subject_key="before",
            source_message_ids=[], source_floor_seq=1, source_provenance=[])
        db.add(topic)
        db.flush()
        topic_id = topic.id
    result = call(product, context, "update_tracking", {"id": topic_id, "title": "After", "expected_revision": 1})
    assert result["title"] == "After" and result["revision"] == 2
    with product.transaction() as db:
        manifest = db.get(ContextManifest, context["context_manifest_id"])
        markers = [item for item in manifest.sources if item.get("kind") == "tracking" and item["id"] == topic_id]
        assert [item["revision"] for item in markers] == [result["revision"]]
        assert markers[0]["source_message_ids"] == db.get(TrackingTopic, topic_id).source_message_ids


def test_tracking_tool_adds_new_foreign_scope_and_rechecks_revoked_membership(product):
    _, context = origin(product, "private-alex")
    with product.transaction() as db:
        chat = db.get(Chat, "group-alex-maya")
        source = message_source(db, product.append_message(db, chat, author_id="owner-alex", kind="human", body="New group evidence"))
        topic = TrackingTopic(chat_id=chat.id, title="New group topic", description="Synthetic topic", subject_key="new-group",
            source_message_ids=[source["id"]], source_floor_seq=source["seq"], source_provenance=[source])
        db.add(topic)
        db.flush()
        topic_id = topic.id
    result = call(product, context, "list_tracking", {})
    assert topic_id in {row["id"] for row in result["tracking"]}
    with product.transaction() as db:
        manifest = db.get(ContextManifest, context["context_manifest_id"])
        assert not any(item.get("id") in {topic_id, source["id"]} for item in manifest.sources)
        envelope, = [item for item in manifest.sources if item.get("kind") == "chat_context" and item.get("chat_id") == "group-alex-maya"]
        assert any(item.get("kind") == "tracking" and item["id"] == topic_id for item in envelope["sources"])
        assert source in envelope["sources"]
        db.scalar(select(Membership).where(Membership.chat_id == "group-alex-maya", Membership.owner_id == "owner-alex")).left_at = now()
    with pytest.raises(HTTPException) as denied:
        product.authorize_runtime_generation(context)
    assert denied.value.status_code == 409


def test_nested_workspace_reuses_request_rows_but_rechecks_later_access(product, monkeypatch, tmp_path):
    monkeypatch.setattr(service_module, "settings", replace(service_module.settings, workspace_root=tmp_path))
    path = tmp_path / "group-alex-maya" / "registered" / "nested" / "report.txt"
    path.parent.mkdir(parents=True)
    path.write_text("Synthetic report")
    with product.transaction() as db:
        message = product.append_message(db, db.get(Chat, "group-alex-maya"), author_id="owner-alex", kind="human", body="Source")
        source_id = message.id
        db.add(WorkspaceNode(chat_id="group-alex-maya", relative_path="registered", source_message_ids=[source_id],
            granted_owner_ids=["owner-alex", "owner-maya"]))
    for relative in ("", "registered/nested"):
        with sql_reads() as reads:
            assert product.workspace("owner-maya", "group-alex-maya", relative)
        for table in ("tt_memberships", "tt_workspace_nodes", "tt_chat_attachments"):
            assert sum(f" from {table}" in row["sql"] for row in reads) == 1
    with product.transaction() as db:
        db.get(Message, source_id).state = "withdrawn"
    with pytest.raises(HTTPException):
        product.workspace("owner-maya", "group-alex-maya", "registered/nested")
    with product.transaction() as db:
        db.scalar(select(Membership).where(Membership.chat_id == "group-alex-maya", Membership.owner_id == "owner-maya")).left_at = now()
    with pytest.raises(HTTPException) as denied:
        product.workspace("owner-maya", "group-alex-maya")
    assert denied.value.status_code == 403


def test_lock_scope_discovery_batches_each_frontier_and_preserves_dirty_sources(product):
    with product.transaction() as db:
        chat = db.get(Chat, "private-alex")
        message_ids = [product.append_message(db, chat, author_id="owner-alex", kind="human", body="Synthetic leaf").id for _ in range(40)]
        ancestor = product.append_message(db, db.get(Chat, "group-alex-maya"), author_id="owner-alex", kind="human", body="Ancestor")
        ancestor_id = ancestor.id
    with product.transaction() as db:
        for _ in range(2):
            with sql_reads() as reads:
                assert product._message_source_chat_ids(db, None, "private-alex", message_ids) == {"private-alex"}
            message_reads = [row for row in reads if " from tt_messages" in row["sql"]]
            assert len(message_reads) == 1
            assert "tt_messages.body" not in message_reads[0]["sql"]
            db.expire_all()
        edited = db.get(Message, message_ids[0])
        edited.source_message_ids = [ancestor_id]
        with db.no_autoflush:
            assert product._message_source_chat_ids(db, None, "private-alex", message_ids) == {"private-alex", "group-alex-maya"}
        assert inspect(edited).attrs.source_message_ids.history.has_changes()


def test_lock_scope_discovery_keeps_nested_summary_tracking_and_cycles(product):
    _, context = origin(product, "private-alex")
    with product.transaction() as db:
        first = product.append_message(db, db.get(Chat, "private-alex"), author_id="owner-alex", kind="human", body="First")
        second = product.append_message(db, db.get(Chat, "group-alex-maya"), author_id="owner-alex", kind="human", body="Second")
        third = product.append_message(db, db.get(Chat, "private-maya"), author_id="owner-maya", kind="human", body="Third")
        summary = Summary(chat_id="group-team", body="Synthetic summary", source_message_ids=[], coverage_start_seq=1, coverage_end_seq=1,
            source_provenance=[{"kind": "history_range", "chat_id": "private-jordan", "lower_seq": 1}])
        db.add(summary)
        db.flush()
        first.source_message_ids = [second.id, second.id, "missing-source"]
        second.source_provenance = [{"kind": "chat_context", "chat_id": "group-team", "sources": [
            {"kind": "tracking", "id": "synthetic-tracking", "source_message_ids": [third.id]}]}]
        third.source_message_ids = [first.id]
        third.source_provenance = [{"kind": "summary", "id": summary.id}]
        manifest = db.get(ContextManifest, context["context_manifest_id"])
        manifest.sources = [{"kind": "message", "id": first.id}]
        first_id = first.id
    with product.transaction() as db:
        from app.models import ReplyRequest
        request = db.get(ReplyRequest, context["request_id"])
        with sql_reads() as reads:
            actual = product._message_source_chat_ids(db, request, "private-alex", [first_id])
        assert actual == {"private-alex", "group-alex-maya", "private-maya", "group-team", "private-jordan"}
        assert sum(" from tt_messages" in row["sql"] for row in reads) == 3
