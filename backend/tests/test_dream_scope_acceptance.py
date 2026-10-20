"""Independent Dream scope acceptance; synthetic messages and disposable PostgreSQL only."""
import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from agent_runtime import AssistantMessageEventStream, assistant_message
from fastapi import HTTPException
from sqlalchemy import select, text
from test_memory_dream import product as product  # noqa: PLC0414

from app.models import Chat, MemoryDreamState, Message, ReplyRequest, now
from checks.legacy_requests import legacy_request


def append(product, body, *, chat_id="private-alex", author="twin-alex", **values):
    with product.transaction() as db:
        chat = db.get(Chat, chat_id)
        message = product.append_message(db, chat, consume_followup=False,
            author_id=author, kind="human" if author.startswith("owner-") else "twin",
            body=body, **values)
        return message.id


def next_due(product):
    with product.transaction() as db:
        state = db.get(MemoryDreamState, "owner-alex")
        assert state is not None
        state.due_at = now() - timedelta(seconds=1)
    product.process_memory_dreams()
    with product.transaction() as db:
        return db.get(MemoryDreamState, "owner-alex").queued_request_id


def confirm(product, dream_id, payload):
    product.complete_request(dream_id, memory_dream={
        "published": True, "version": "synthetic-verified-" + uuid4().hex,
        "cursor_before": payload["cursor_before"], "through_seq": payload["through_seq"]})


def test_group_report_without_owner_reply_or_successful_origin_is_dream_evidence(product):
    source = product.send_message("owner-maya", "group-alex-maya", "RAW GROUP MUST STAY OUT", uuid4().hex)["message"]
    origin = legacy_request(product, "owner-maya", "group-alex-maya", source["id"], "twin-alex", uuid4().hex)
    context = {**product.get_runtime_context(origin["id"]), "operation_id": uuid4().hex}
    report = asyncio.run(product.runtime_tool("send_chat_message", {
        "chat_id": "private-alex", "body": "Delivered private progress from group work.",
        "source_message_ids": [source["id"]]}, context))["message"]
    product.fail_request(origin["id"], "Synthetic failure after committed report")
    queued = asyncio.run(product.request_memory_dream("owner-alex"))
    assert queued["status"] == "queued"  # Empty Memory and no owner-human private message.
    dream = product.get_runtime_context(queued["request_id"])
    payload, evidence = dream["memory_dream"], dream["memory_dream_evidence"]
    assert payload["scope"] == "private_messages" and "experiences" not in payload
    assert [ref["id"] for ref in payload["messages"]] == [report["id"]]
    assert evidence[0]["source"] == {
        "session_id": "twin-chat:private-alex:generation:0", "event_id": report["id"]}
    assert evidence[0]["role"] == "assistant"
    assert "Delivered private progress" in json.dumps(evidence)
    assert "RAW GROUP MUST STAY OUT" not in json.dumps(dream)
    product._cancel_intent("owner-alex", queued["request_id"])
    automatic = product.get_runtime_context(next_due(product))["memory_dream"]
    assert [ref["id"] for ref in automatic["messages"]] == [report["id"]]


def test_bounded_message_windows_skip_hidden_gaps_without_losing_later_reports(product):
    hidden = [append(product, "Withdrawn", state="withdrawn") for _ in range(105)]
    expected = []
    for index in range(47):
        expected.append(append(product, f"Published report {index}"))
        if index % 7 == 0:
            hidden.append(append(product, "Unavailable by provenance", source_provenance=[{"kind": "unknown"}]))
    seen = []
    for _ in range(12):
        dream_id = next_due(product)
        if not dream_id:
            continue  # One bounded all-hidden scan is allowed to make cursor-only progress.
        context = product.get_runtime_context(dream_id)
        payload = context["memory_dream"]
        ids = [ref["id"] for ref in payload["messages"]]
        assert 0 < len(ids) <= 20 and not set(ids) & set(hidden)
        assert not set(seen) & set(ids)
        with product.transaction() as db:
            assert db.get(MemoryDreamState, "owner-alex").message_cursor_seq == payload["cursor_before"]
        seen.extend(ids)
        confirm(product, dream_id, payload)
        if len(seen) == len(expected):
            break
    assert seen == expected
    assert next_due(product) is None


@pytest.mark.parametrize("change", ["body", "withdraw", "provenance", "reset"])
def test_queued_private_message_evidence_is_revalidated_before_use(product, change):
    other = append(product, "OTHER OWNER PRIVATE", chat_id="private-maya", author="twin-maya")
    report = append(product, "Own delivered report")
    dream_id = next_due(product)
    context = product.get_runtime_context(dream_id)
    assert [ref["id"] for ref in context["memory_dream"]["messages"]] == [report]
    assert other not in json.dumps(context)
    with product.transaction() as db:
        message = db.get(Message, report)
        if change == "body":
            message.body = "Changed after the frozen evidence snapshot"
        elif change == "withdraw":
            message.state = "withdrawn"
        elif change == "provenance":
            message.source_provenance = [{"kind": "unknown"}]
        else:
            chat = db.get(Chat, "private-alex")
            chat.reset_generation += 1
    with pytest.raises(HTTPException):
        product.get_runtime_context(dream_id)


def test_legacy_recovery_cursor_is_not_reinterpreted_as_private_message_sequence(product):
    report = append(product, "Report predates migration but was never old-run evidence")
    with product.transaction() as db:
        state = db.get(MemoryDreamState, "owner-alex")
        state.cursor_seq, state.message_cursor_seq = 900, None
        state.last_status, state.due_at = "recovery_required", None
    with pytest.raises(HTTPException, match="409"):
        asyncio.run(product.request_memory_dream("owner-alex"))
    product.process_memory_dreams()
    with product.transaction() as db:
        state = db.get(MemoryDreamState, "owner-alex")
        assert state.cursor_seq == 900 and state.message_cursor_seq is None
        assert state.queued_request_id is None and state.last_status == "recovery_required"
        state.last_status = "published"  # Simulate the existing explicit reconciliation boundary.
    queued = asyncio.run(product.request_memory_dream("owner-alex"))
    payload = product.get_runtime_context(queued["request_id"])["memory_dream"]
    assert payload["cursor_before"] == 0 and [ref["id"] for ref in payload["messages"]] == [report]
    confirm(product, queued["request_id"], payload)
    with product.transaction() as db:
        state = db.get(MemoryDreamState, "owner-alex")
        assert state.cursor_seq == 900
        assert state.message_cursor_seq == db.scalar(select(Message.seq).where(Message.id == report))
        assert db.get(ReplyRequest, queued["request_id"]).status == "completed"


def test_manual_dream_advances_empty_scan_and_schedules_later_new_report(product):
    for _ in range(105):
        append(product, "Withdrawn before a new report", state="withdrawn")
    report = append(product, "Eligible report beyond the first scan window")
    result = asyncio.run(product.request_memory_dream("owner-alex"))
    assert result == {"status": "scheduled", "request_id": None}
    with product.transaction() as db:
        state = db.get(MemoryDreamState, "owner-alex")
        assert state.message_cursor_seq == 100 and state.queued_request_id is None
        assert state.due_at <= now()
    product.process_memory_dreams()
    with product.transaction() as db:
        dream_id = db.get(MemoryDreamState, "owner-alex").queued_request_id
    payload = product.get_runtime_context(dream_id)["memory_dream"]
    assert [ref["id"] for ref in payload["messages"]] == [report]


def test_additive_migration_preserves_legacy_queued_and_unknown_intents(product):
    from app import service as service_module
    from app.dream_scope_schema import upgrade

    report = append(product, "Private report missed by old SDK-run eligibility")
    append(product, "Maya private evidence", chat_id="private-maya", author="twin-maya")
    append(product, "Jordan private evidence", chat_id="private-jordan", author="twin-jordan")
    with product.transaction() as db:
        origin = product.accept_request(db, db.get(Chat, "private-alex"), "twin-alex",
            "owner-alex", "manual", uuid4().hex, raw="Legacy human experience")
        origin.status, origin.session_id, origin.run_id = "completed", "legacy-session", "legacy-run"
        payload = {"owner_id": "owner-alex", "cursor_before": 0, "through_seq": origin.accepted_seq,
            "experiences": [{"request_id": origin.id, "session_id": origin.session_id, "run_id": origin.run_id}],
            "manual_edits": []}
        legacy = product.accept_request(db, db.get(Chat, "private-alex"), "twin-alex", "owner-alex",
            "memory_dream", uuid4().hex, input_body=json.dumps(payload))
        legacy_id = legacy.id
        state = db.get(MemoryDreamState, "owner-alex")
        state.cursor_seq, state.message_cursor_seq = 0, None
        state.queued_request_id, state.last_status, state.due_at = legacy.id, "queued", None
        unknown = db.get(MemoryDreamState, "owner-maya")
        unknown.cursor_seq, unknown.last_status, unknown.due_at = 777, "recovery_required", None
        db.delete(db.get(MemoryDreamState, "owner-jordan"))
    # Recreate the precise pre-migration column shape in this disposable schema only.
    with service_module.engine.begin() as connection:
        connection.execute(text("ALTER TABLE tt_memory_dream_state DROP COLUMN message_cursor_seq"))
        upgrade(connection)
    with product.transaction() as db:
        state = db.get(MemoryDreamState, "owner-alex")
        assert state.queued_request_id == legacy_id and state.cursor_seq == 0
        assert state.message_cursor_seq is None and state.due_at is None
        assert json.loads(db.get(ReplyRequest, legacy_id).input_body) == payload
        unknown = db.get(MemoryDreamState, "owner-maya")
        assert unknown.cursor_seq == 777 and unknown.last_status == "recovery_required"
        assert unknown.message_cursor_seq is None and unknown.due_at is None
        backfill = db.get(MemoryDreamState, "owner-jordan")
        assert backfill.message_cursor_seq == 0 and backfill.due_at > now()
        deadline = backfill.due_at
    with service_module.engine.begin() as connection:
        upgrade(connection)
    with product.transaction() as db:
        assert db.get(MemoryDreamState, "owner-jordan").due_at == deadline
    assert product.get_runtime_context(legacy_id)["memory_dream"] == payload
    assert asyncio.run(product.request_memory_dream("owner-alex")) == {
        "status": "already_queued", "request_id": legacy_id}
    confirm(product, legacy_id, payload)
    current = product.get_runtime_context(next_due(product))["memory_dream"]
    assert current["scope"] == "private_messages" and current["cursor_before"] == 0
    assert [ref["id"] for ref in current["messages"]] == [report]


def test_new_message_source_is_readable_by_real_sdk_and_preserved_in_git_memory(product):
    from app import personal
    from app.runtime_bridge import RuntimeBridge

    body = "The owner's verified preferred document format is Markdown."
    report = append(product, body)
    dream_id = next_due(product)
    reference = {"session_id": "twin-chat:private-alex:generation:0", "event_id": report}
    calls = []

    async def run():
        runtime = RuntimeBridge(product)

        def offline(model, context, options):
            calls.append(context)
            results = [row for row in context.messages if row.get("role") == "toolResult"]
            if not results:
                name, args = "memory_read", {"key": "MEMORY"}
            elif len(results) == 1:
                name, args = "read_source", reference
            elif len(results) == 2:
                source = json.loads(results[-1]["content"][0]["text"])
                assert source["content"]["body"] == body
                assert source["content"]["source"] == reference
                assert source["content"]["author_owner_id"] == "owner-alex"
                name, args = "memory_write", {"key": "MEMORY", "content": body, "expected_version": None}
            else:
                name, args = "memory_publish", {}
            stream = AssistantMessageEventStream()
            stream.push({"type": "done", "message": assistant_message(model, content=[{
                "type": "toolCall", "id": f"step-{len(calls)}", "name": name, "arguments": args}],
                stopReason="toolUse")})
            return stream

        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        try:
            result = await runtime.execute(product.get_request(dream_id), product.get_runtime_context(dream_id))
            assert result["memory_dream"]["published"] and result["body"] == ""
            product.complete_request(dream_id, result)
            store = personal.own_memory_store("owner-alex")
            entry = await store.read("MEMORY")
            assert entry.content == body
            assert list(entry.to_dict()["sources"]) == [reference]
            assert len(calls) == 4
        finally:
            await runtime.close()

    asyncio.run(run())


def test_normal_maximum_multibyte_private_message_keeps_its_tail(product):
    body = "文" * 29985 + "尾部事实TAILFACT"
    assert len(body) <= 30000 and len(body.encode("utf-8")) > 88000
    report = append(product, body)
    context = product.get_runtime_context(next_due(product))
    assert [ref["id"] for ref in context["memory_dream"]["messages"]] == [report]
    assert context["memory_dream_evidence"][0]["body"] == body
    assert "truncated_fields" not in context["memory_dream_evidence"][0]


def test_true_oversize_is_explicit_and_full_content_hash_detects_an_unseen_tail_change(product):
    report = append(product, "文" * 30000, raw_owner_input="原" * 30000)
    dream_id = next_due(product)
    value = product.get_runtime_context(dream_id)["memory_dream_evidence"][0]
    assert len(value["body"].encode("utf-8")) + len(value["raw_owner_input"].encode("utf-8")) <= 90 * 1024
    assert set(value["truncated_fields"]) == {"body", "raw_owner_input"}
    assert value["truncated_fields"]["body"]["original_bytes"] == 90000
    with product.transaction() as db:
        db.get(Message, report).body += "Unseen tail changed"
    with pytest.raises(HTTPException):
        product.get_runtime_context(dream_id)


@pytest.mark.parametrize("terminal", ["completed", "failed"])
def test_mutable_reply_projection_cannot_be_consumed_before_its_final_state(product, terminal):
    with product.transaction() as db:
        request = product.accept_request(db, db.get(Chat, "private-alex"), "twin-alex", "owner-alex",
            "reply_for_me", uuid4().hex, raw="Original private owner input")
        request_id = request.id
    provisional = product.publish_runtime_output(request_id, "Provisional draft", "projection-session",
        "projection-run", "provisional-event", 0)
    later = append(product, "Independent published report after the mutable projection")
    assert next_due(product) is None
    with product.transaction() as db:
        assert db.get(MemoryDreamState, "owner-alex").message_cursor_seq == 0
    if terminal == "completed":
        product.publish_runtime_output(request_id, "Stable final projection", "projection-session",
            "projection-run", "final-event", 1)
        product.complete_request(request_id)
    else:
        product.fail_request(request_id, "Unconfirmed generated draft")
    context = product.get_runtime_context(next_due(product))
    ids = [ref["id"] for ref in context["memory_dream"]["messages"]]
    assert ids == ([provisional, later] if terminal == "completed" else [later])
    evidence = json.dumps(context["memory_dream_evidence"])
    assert "Provisional draft" not in evidence
    assert ("Stable final projection" in evidence) == (terminal == "completed")
