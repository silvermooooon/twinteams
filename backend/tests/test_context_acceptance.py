"""Independent context acceptance on disposable PostgreSQL; no provider calls.

The journal, summary publication, membership checks, manifests and routing input
are production paths. Summary prose is a deliberate synthetic acceptance input.
"""
import asyncio
import json
from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select
from test_memory_dream import product as product  # noqa: PLC0414

from app import service as service_module
from app.models import Chat, ContextManifest, Membership, Message, RoutingDecision, now


def seed(product, chat_id, count, *, owner="owner-alex", label="Journal fact"):
    """Insert ordinary canonical messages without scheduling a model/worker."""
    with product.transaction() as db:
        chat = db.get(Chat, chat_id)
        start = chat.next_message_seq
        rows = [Message(id=uuid4().hex, chat_id=chat_id, seq=start + index,
                        author_id=(owner.replace("owner-", "twin-") if chat.kind == "duet" else owner), kind="twin" if chat.kind == "duet" else "human", body=f"{label} {start + index}")
                for index in range(count)]
        db.add_all(rows)
        chat.next_message_seq += count
        db.flush()
        return [{"id": row.id, "seq": row.seq, "body": row.body} for row in rows]


def request(product, chat_id, source_id, *, owner="owner-alex"):
    if chat_id.startswith("duet-"):
        from duet_test_support import request_duet_reply
        return request_duet_reply(product, owner, chat_id, source_id, owner.replace("owner-", "twin-"))["id"]
    return product.manual_request(owner, chat_id, source_id, owner.replace("owner-", "twin-"), uuid4().hex)["id"]


def summarize(product, request_id, body="Synthetic checkpoint preserving the journal facts."):
    context = product.get_runtime_context(request_id)
    scope, batches, covered = context["_summary_scope"], [], []
    while prepared := product.prepare_conversation_summary(scope):
        assert prepared["messages"], "The due prefix must be available to summarize"
        batches.append(prepared)
        covered.extend(prepared["messages"])
        saved = product.save_conversation_summary(prepared["scope"], body)
        assert saved
        assert product.save_conversation_summary(prepared["scope"], body)["id"] == saved["id"]
        assert len(batches) < 20, "Summary maintenance must advance a finite journal boundary"
    assert batches, "This acceptance history must trigger summary maintenance"
    return saved, {"messages": covered, "batches": batches}


@contextmanager
def sql_reads():
    """Count rows fetched by real SQL, including hidden provenance work.

    Counting queries alone would miss a single unbounded full-history SELECT.
    Psycopg supplies SELECT rowcount after execution, before SQLAlchemy consumes
    the result, so this observes actual fetched rows without replacing the DB.
    """
    reads = []

    def after(conn, cursor, statement, parameters, context, executemany):
        normalized = " ".join(statement.casefold().split())
        if normalized.startswith("select"):
            reads.append({"sql": normalized, "rows": max(0, cursor.rowcount)})

    event.listen(service_module.engine, "after_cursor_execute", after)
    try:
        yield reads
    finally:
        event.remove(service_module.engine, "after_cursor_execute", after)


def assert_tail_reads(reads, tail_size):
    message_reads = [row for row in reads if " from tt_messages" in row["sql"]]
    body_reads = [row for row in message_reads
                  if "tt_messages.body" in row["sql"].split(" from ", 1)[0]]
    assert body_reads, "The acceptance measurement must observe the real message read"
    assert max(row["rows"] for row in body_reads) <= tail_size + 2, body_reads
    assert sum(row["rows"] for row in message_reads) <= 8 * tail_size + 40, message_reads
    assert len(reads) <= 80 + 8 * tail_size, "Summary authorization must not re-expand each covered source"


@pytest.mark.parametrize("chat_id", ["private-alex", "duet-team"])
def test_over_500_sources_keep_journal_and_bound_repeated_context_reads(product, chat_id):
    original = seed(product, chat_id, 540)
    first_id = request(product, chat_id, original[-1]["id"])
    saved, prepared = summarize(product, first_id)
    assert len(prepared["messages"]) > 500
    end = saved["coverage_end_seq"]
    added = seed(product, chat_id, 3, label="New unsummarized fact")
    next_id = request(product, chat_id, added[-1]["id"])
    expected_tail = [row for row in original + added if row["seq"] > end]

    for _ in range(2):
        with sql_reads() as reads:
            context = product.get_runtime_context(next_id)
        assert context["summary"]["id"] == saved["id"]
        assert context["summary"]["coverage_end_seq"] == end
        assert [row["id"] for row in context["messages"]] == [row["id"] for row in expected_tail]
        assert "source_message_ids" not in context["summary"], "Cumulative IDs must stay out of model input"
        assert_tail_reads(reads, len(expected_tail))
        with product.transaction() as db:
            manifest = db.get(ContextManifest, context["context_manifest_id"])
            assert len(json.dumps(manifest.sources)) < 15000
            assert any(source.get("kind") == "summary" for source in manifest.sources)

    # Context optimization never replaces the canonical journal or breaks older
    # history access. This deliberately runs outside the measured context path.
    with product.transaction() as db:
        journal = list(db.scalars(select(Message).where(Message.chat_id == chat_id)
                                  .order_by(Message.seq)))
        assert [(row.id, row.seq, row.body) for row in journal] == [
            (row["id"], row["seq"], row["body"]) for row in original + added]
    history = asyncio.run(product.runtime_tool("history", {"before_seq": 101, "limit": 100},
                                              {"owner_id": "owner-alex", "chat": {"id": chat_id}}))
    assert [row["seq"] for row in history["messages"]] == list(range(1, 101))
    product.complete_request(next_id, body="Synthetic verified modern Duet output",
                             session_id="acceptance-" + next_id, run_id="run-" + next_id)
    with product.transaction() as db:
        output = db.scalar(select(Message).where(Message.request_id == next_id))
        assert output and any(source.get("kind") == "summary" for source in output.source_provenance)
        assert len(output.source_message_ids) <= len(expected_tail)
        assert len(json.dumps(output.source_provenance)) < 15000


def test_legacy_duet_tail_dependencies_do_not_reexpand_the_summarized_prefix(product):
    rows = seed(product, "private-alex", 540)
    saved, _ = summarize(product, request(product, "private-alex", rows[-1]["id"]))
    # Existing journals contain Duet outputs made before compact provenance was
    # introduced. Their canonical source arrays must remain intact while their
    # summarized sources avoid a per-message historical read on every turn.
    with product.transaction() as db:
        legacy = product.append_message(db, db.get(Chat, "private-alex"),
            author_id="twin-alex", kind="twin", body="Legacy sourced Duet finding",
            source_message_ids=[row["id"] for row in rows], source_floor_seq=1,
            source_provenance=[{"kind": "message", "id": row["id"]} for row in rows])
        legacy_id = legacy.id
    newest = seed(product, "private-alex", 1)
    request_id = request(product, "private-alex", newest[-1]["id"])
    with sql_reads() as reads:
        context = product.get_runtime_context(request_id)
    assert context["summary"]["id"] == saved["id"]
    assert legacy_id in {row["id"] for row in context["messages"]}
    assert_tail_reads(reads, len(context["messages"]))
    with product.transaction() as db:
        legacy = db.get(Message, legacy_id)
        assert legacy.source_message_ids == [row["id"] for row in rows]
        assert len(legacy.source_provenance) == len(rows)


@pytest.mark.parametrize("mutation", ["edit", "withdraw", "delete"])
def test_covered_source_changes_revoke_frozen_resume_and_stale_summary(product, mutation):
    rows = seed(product, "duet-team", 60)
    first_id = request(product, "duet-team", rows[-1]["id"])
    saved, _ = summarize(product, first_id, "The old fact was verified.")
    tail = seed(product, "duet-team", 1)
    frozen_id = request(product, "duet-team", tail[-1]["id"])
    assert product.get_runtime_context(frozen_id)["summary"]["id"] == saved["id"]
    with product.transaction() as db:
        source = db.get(Message, rows[0]["id"])
        if mutation == "edit":
            source.body = "The owner corrected the original fact."
        elif mutation == "withdraw":
            source.state = "withdrawn"
        else:
            db.delete(source)
    with pytest.raises(HTTPException) as error:
        product.get_runtime_context(frozen_id)
    assert error.value.status_code == 409
    fresh = product.get_conversation_context({"chat_id": "duet-team", "owner_id": "owner-alex"})
    assert fresh["summary"] is None or fresh["summary"]["id"] != saved["id"]
    if mutation == "edit":
        assert any(row["body"] == "The owner corrected the original fact." for row in fresh["messages"])
    else:
        assert rows[0]["id"] not in {row["id"] for row in fresh["messages"]}


def test_history_lower_bound_does_not_reuse_earlier_summary(product):
    rows = seed(product, "duet-team", 60)
    saved, _ = summarize(product, request(product, "duet-team", rows[-1]["id"]))
    with product.transaction() as db:
        member = db.scalar(select(Membership).where(Membership.chat_id == "duet-team",
                                                    Membership.owner_id == "owner-alex"))
        member.lower_seq = rows[44]["seq"]
        member.history_policy = "no_prior"
        member.joined_at = now()
        member.grant_version += 1
    context = product.get_conversation_context({"chat_id": "duet-team", "owner_id": "owner-alex"})
    assert context["summary"] is None or context["summary"]["id"] != saved["id"]
    assert [row["id"] for row in context["messages"]] == [row["id"] for row in rows[44:]]


@pytest.mark.parametrize("revoke", ["withdraw", "private_generation"])
def test_private_provenance_is_excluded_and_revocation_reaches_summary(product, revoke):
    private = seed(product, "private-alex", 1, label="Owner private context")
    rows = seed(product, "duet-team", 60)
    with product.transaction() as db:
        derived = db.get(Message, rows[0]["id"])
        derived.source_provenance = [{"kind": "chat_context", "chat_id": "private-alex",
                                      "private_generation": 0,
                                      "sources": [{"kind": "message", "id": private[0]["id"]}]}]
    saved, _ = summarize(product, request(product, "duet-team", rows[-1]["id"]))
    maya = product.get_runtime_context(request(product, "duet-team", rows[-1]["id"], owner="owner-maya"))
    assert maya["summary"] is None or maya["summary"]["id"] != saved["id"]
    assert rows[0]["id"] not in {row["id"] for row in maya["messages"]}
    fresh_source = seed(product, "duet-team", 1)
    frozen_id = request(product, "duet-team", fresh_source[-1]["id"])
    assert product.get_runtime_context(frozen_id)["summary"]["id"] == saved["id"]
    with product.transaction() as db:
        if revoke == "withdraw":
            db.get(Message, private[0]["id"]).state = "withdrawn"
        else:
            db.get(Chat, "private-alex").reset_generation += 1
    with pytest.raises(HTTPException) as error:
        product.get_runtime_context(frozen_id)
    assert error.value.status_code == 409
    fresh = product.get_conversation_context({"chat_id": "duet-team", "owner_id": "owner-alex"})
    assert fresh["summary"] is None or fresh["summary"]["id"] != saved["id"]
    assert rows[0]["id"] not in {row["id"] for row in fresh["messages"]}


@pytest.mark.parametrize("revoke", ["leave", "history_floor"])
def test_summarized_foreign_group_retains_membership_and_history_grants(product, revoke):
    origin = seed(product, "duet-alex-maya", 1, label="Original group evidence")
    rows = seed(product, "private-alex", 60)
    with product.transaction() as db:
        db.get(Message, rows[0]["id"]).source_provenance = [{"kind": "chat_context",
            "chat_id": "duet-alex-maya", "sources": [{"kind": "message", "id": origin[0]["id"]}]}]
    saved, _ = summarize(product, request(product, "private-alex", rows[-1]["id"]))
    latest = seed(product, "private-alex", 1)
    frozen_id = request(product, "private-alex", latest[-1]["id"])
    frozen = product.get_runtime_context(frozen_id)
    assert frozen["summary"]["id"] == saved["id"]
    with product.transaction() as db:
        manifest = db.get(ContextManifest, frozen["context_manifest_id"])
        assert not any(source.get("id") == rows[0]["id"] for source in manifest.sources)
        member = db.scalar(select(Membership).where(Membership.chat_id == "duet-alex-maya",
                                                    Membership.owner_id == "owner-alex"))
        if revoke == "leave":
            member.left_at = now()
        else:
            member.lower_seq = origin[0]["seq"] + 1
            member.grant_version += 1
    with pytest.raises(HTTPException) as error:
        product.get_runtime_context(frozen_id)
    assert error.value.status_code == 409
    fresh = product.get_conversation_context({"chat_id": "private-alex", "owner_id": "owner-alex"})
    assert fresh["summary"] is None or fresh["summary"]["id"] != saved["id"]
    assert rows[0]["id"] not in {row["id"] for row in fresh["messages"]}


def test_pending_question_remains_actionable_after_its_message_is_summarized(product):
    from test_questions import pending
    _, _, question, _ = pending(product)
    rows = seed(product, "private-alex", 60)
    saved, _ = summarize(product, request(product, "private-alex", rows[-1]["id"]),
                         "The owner still needs to choose Short or Detailed report format.")
    latest = seed(product, "private-alex", 1)
    context = product.get_runtime_context(request(product, "private-alex", latest[-1]["id"]))
    assert context["summary"]["id"] == saved["id"]
    assert question["message_id"] not in {row["id"] for row in context["messages"]}
    assert question["id"] in {row["id"] for row in context["pending_questions"]}


def test_denied_prefix_longer_than_a_page_cannot_hide_authorized_later_history(product):
    private = seed(product, "private-maya", 1, owner="owner-maya", label="Foreign private evidence")
    hidden = seed(product, "duet-team", 300, owner="owner-maya", label="Foreign derived message")
    with product.transaction() as db:
        for row in db.scalars(select(Message).where(Message.chat_id == "duet-team")):
            row.source_provenance = [{"kind": "chat_context", "chat_id": "private-maya",
                "private_generation": 0, "sources": [{"kind": "message", "id": private[0]["id"]}]}]
    visible = seed(product, "duet-team", 5, label="Authorized visible fact")
    scope = {"chat_id": "duet-team", "owner_id": "owner-alex"}
    with sql_reads() as reads:
        context = product.get_conversation_context(scope)
        prepared = product.prepare_conversation_summary(scope)
    assert not context["context_incomplete"]
    assert context["summary"] is None and prepared is None
    assert [row["id"] for row in context["messages"]] == [row["id"] for row in visible]
    assert not {row["id"] for row in hidden} & {row["id"] for row in context["messages"]}
    body_reads = [row for row in reads if " from tt_messages" in row["sql"]
                  and "tt_messages.body" in row["sql"].split(" from ", 1)[0]]
    assert max(row["rows"] for row in body_reads) <= len(visible) + 2


def test_new_origin_membership_reveals_earlier_fact_previously_skipped_by_summary(product):
    origin_chat = product.create_chat("owner-maya", ["owner-jordan"], "Original restricted project")["id"]
    origin = seed(product, origin_chat, 1, owner="owner-maya", label="Newly authorized original fact")
    rows = seed(product, "duet-team", 60)
    with product.transaction() as db:
        db.get(Message, rows[0]["id"]).source_provenance = [{"kind": "chat_context",
            "chat_id": origin_chat, "sources": [{"kind": "message", "id": origin[0]["id"]}]}]
    saved, prepared = summarize(product, request(product, "duet-team", rows[-1]["id"]),
                                 "Checkpoint of facts visible before the new origin membership.")
    assert rows[0]["id"] not in {row["id"] for row in prepared["messages"]}
    assert saved["coverage_end_seq"] > rows[0]["seq"]
    product.add_members("owner-maya", origin_chat, ["owner-alex"], "all")
    context = product.get_conversation_context({"chat_id": "duet-team", "owner_id": "owner-alex"})
    assert context["summary"] is None or context["summary"]["id"] != saved["id"]
    assert rows[0]["id"] in {row["id"] for row in context["messages"]}


def test_unrelated_membership_retires_cache_without_hiding_published_output_or_looping(product):
    rows = seed(product, "duet-team", 60)
    first_id = request(product, "duet-team", rows[-1]["id"])
    saved, _ = summarize(product, first_id)
    old_scope = product.get_runtime_context(first_id)["_summary_scope"]
    newest = seed(product, "duet-team", 1)
    reply_id = request(product, "duet-team", newest[-1]["id"])
    product.get_runtime_context(reply_id)
    product.complete_request(reply_id, body="Previously published verified finding",
                             session_id="acceptance-" + reply_id, run_id="run-" + reply_id)
    with product.transaction() as db:
        output_id = db.scalar(select(Message.id).where(Message.request_id == reply_id))
    unrelated = product.create_chat("owner-maya", ["owner-jordan"], "Unrelated new membership")["id"]
    product.add_members("owner-maya", unrelated, ["owner-alex"], "all")
    assert output_id in {row["id"] for row in product.chat_detail("owner-alex", "duet-team")["messages"]}
    prepared = product.prepare_conversation_summary(old_scope)
    if prepared:
        revived = product.save_conversation_summary(prepared["scope"], saved["body"])
        assert revived["id"] == saved["id"]
        assert product.prepare_conversation_summary(old_scope) is None
    assert output_id in {row["id"] for row in product.chat_detail("owner-alex", "duet-team")["messages"]}


def test_history_tool_extends_compact_manifest_without_false_revocation(product):
    rows = seed(product, "private-alex", 540)
    summarize(product, request(product, "private-alex", rows[-1]["id"]))
    latest = seed(product, "private-alex", 1)
    request_id = request(product, "private-alex", latest[-1]["id"])
    context = product.get_runtime_context(request_id)
    history = asyncio.run(product.runtime_tool("history", {"before_seq": 6, "limit": 5}, context))
    assert [row["id"] for row in history["messages"]] == [row["id"] for row in rows[:5]]
    resumed = product.get_runtime_context(request_id)
    assert resumed["summary"]["id"] == context["summary"]["id"]
    with product.transaction() as db:
        db.get(Message, rows[0]["id"]).body = "An explicit source correction"
    with pytest.raises(HTTPException) as error:
        product.get_runtime_context(request_id)
    assert error.value.status_code == 409


def test_legacy_body_hash_manifest_can_resume_until_its_source_changes(product):
    rows = seed(product, "private-alex", 1)
    request_id = request(product, "private-alex", rows[-1]["id"])
    context = product.get_runtime_context(request_id)
    with product.transaction() as db:
        manifest = db.get(ContextManifest, context["context_manifest_id"])
        manifest.sources = [*manifest.sources, {"kind": "message", "id": rows[0]["id"],
            "seq": rows[0]["seq"], "hash": service_module.digest(rows[0]["body"])}]
        manifest.source_digest = service_module.digest(manifest.sources)
    assert product.get_runtime_context(request_id)["source_message"]["id"] == rows[0]["id"]
    with product.transaction() as db:
        db.get(Message, rows[0]["id"]).body = "The owner revised this original source"
    with pytest.raises(HTTPException) as error:
        product.get_runtime_context(request_id)
    assert error.value.status_code == 409


def test_router_has_group_summary_tail_without_private_owner_context(product):
    private = seed(product, "private-alex", 45, label="PRIVATE_SENTINEL_ALPHA")
    summarize(product, request(product, "private-alex", private[-1]["id"]),
              "PRIVATE_SUMMARY_SENTINEL_ALPHA")
    group = seed(product, "duet-team", 60, label="Authorized group fact")
    saved, _ = summarize(product, request(product, "duet-team", group[-1]["id"]),
                         "GROUP_CHECKPOINT_SENTINEL_BETA")
    from duet_test_support import append_duet
    sent = {"message": append_duet(product, "duet-team", "owner-alex", "BBB, can you answer?")}
    with product.transaction() as db:
        decision = db.scalar(select(RoutingDecision).where(RoutingDecision.message_id == sent["message"]["id"]))
        decision_id = decision.id
    with sql_reads() as reads:
        prepared = product._prepare_route(decision_id)
    assert prepared
    route_context = prepared[2]
    encoded = json.dumps(route_context)
    assert "GROUP_CHECKPOINT_SENTINEL_BETA" in encoded
    assert "PRIVATE_SENTINEL_ALPHA" not in encoded and "PRIVATE_SUMMARY_SENTINEL_ALPHA" not in encoded
    assert "private-alex" not in encoded
    assert isinstance(route_context, dict) and route_context["summary"]["id"] == saved["id"]
    assert [row["seq"] for row in route_context["messages"]] == list(
        range(saved["coverage_end_seq"] + 1, sent["message"]["seq"] + 1))
    assert_tail_reads(reads, len(route_context["messages"]))


@pytest.fixture(autouse=True)
def approved_collaboration_fixtures(product):
    from duet_test_support import seed_collaborations
    seed_collaborations(product)
