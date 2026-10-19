"""Incremental Dream scheduling on the real product FIFO, using isolated PostgreSQL."""
import asyncio
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from agent_runtime import AssistantMessageEventStream, assistant_message
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import personal
from app import service as service_module
from app.models import Chat, MemoryDreamState, ReplyLane, ReplyRequest, now
from app.runtime_bridge import RuntimeBridge
from checks.postgres import TestDatabase


@pytest.fixture
def product(monkeypatch, tmp_path):
    database = TestDatabase()
    engine = create_engine(database.url.replace("postgresql://", "postgresql+psycopg://"))
    monkeypatch.setattr(service_module, "engine", engine)
    monkeypatch.setattr(service_module, "SessionLocal", sessionmaker(engine, expire_on_commit=False))
    monkeypatch.setattr(service_module, "settings", replace(service_module.settings, database_url=database.url))
    monkeypatch.setenv("AGENT_DB_URL", database.url)
    monkeypatch.setenv("AGENT_MEMORY_DIR", str(tmp_path))
    product = service_module.ProductService()
    monkeypatch.setattr(personal, "service", product)
    product.initialize(create_schema=True)
    yield product
    product.db_pool.shutdown()
    engine.dispose()
    database.close()


def accepted(product, source="manual", *, raw="owner fact", generation=0, intent_key=None):
    with product.transaction() as db:
        chat = db.get(Chat, "private-alex")
        message = product.append_message(db, chat, author_id="owner-alex", kind="human", body=raw or "Owner fact")
        row = product.accept_request(db, chat, "twin-alex", "owner-alex",
            source, uuid4().hex, source_message_id=message.id, generation=generation, intent_key=intent_key)
        return row.id


def completed(product, source="manual", **kwargs):
    request_id = accepted(product, source, **kwargs)
    product.complete_request(request_id, session_id="source-" + request_id, run_id="run-" + request_id)
    return request_id


def due(product):
    with product.transaction() as db:
        state = db.get(MemoryDreamState, "owner-alex")
        state.due_at = now() - timedelta(seconds=1)
    product.process_memory_dreams()
    with product.transaction() as db:
        return db.get(MemoryDreamState, "owner-alex").queued_request_id


def test_incremental_due_publish_failure_cancel_and_unknown_release_lane(product):
    first = completed(product)
    with product.transaction() as db:
        state = db.get(MemoryDreamState, "owner-alex")
        cursor = state.message_cursor_seq
        assert state.due_at > now() + timedelta(hours=3)
    assert "private-alex" in product.next_deadlines()
    product.process_memory_dreams()
    with product.transaction() as db:
        assert db.get(MemoryDreamState, "owner-alex").queued_request_id is None
    dream_id = due(product)
    request, context = product.get_request(dream_id), product.get_runtime_context(dream_id)
    assert request["session_id"] == "dream:" + dream_id
    assert context["messages"] == [] and context["memory_dream"]["messages"][0]["id"] == product.get_request(first)["source_message_id"]
    product.bind_runtime_run(dream_id, request["session_id"], "real-dream-run")
    product.mark_memory_dream_recovery(dream_id, "unknown publication")
    product.fail_request(dream_id, "unknown publication")
    with product.transaction() as db:
        state = db.get(MemoryDreamState, "owner-alex")
        assert state.message_cursor_seq == cursor and state.last_status == "recovery_required" and state.due_at is None
        assert db.get(ReplyLane, ("private-alex", "twin-alex")).active_request_id is None
        assert db.get(ReplyRequest, dream_id).status == "failed"
    completed(product)  # New owner work remains admissible; cannot automatically retry unknown publication.
    product.process_memory_dreams()
    with pytest.raises(HTTPException):
        product.retry_request("owner-alex", dream_id, uuid4().hex)
    product.confirm_memory_dream_publication(dream_id, "verified-commit")
    product.confirm_memory_dream_publication(dream_id, "verified-commit")
    with product.transaction() as db:
        state = db.get(MemoryDreamState, "owner-alex")
        assert state.message_cursor_seq == context["memory_dream"]["through_seq"] and state.last_version == "verified-commit"
    next_id = due(product)
    assert product.get_runtime_context(next_id)["memory_dream"]["messages"][0]["id"] != product.get_request(first)["source_message_id"]
    product._cancel_intent("owner-alex", next_id)
    with product.transaction() as db:
        state = db.get(MemoryDreamState, "owner-alex")
        assert state.queued_request_id is None and state.last_status == "cancelled"


def test_private_human_input_and_published_continuation_reports_need_no_completed_run(product):
    first = accepted(product)
    with product.transaction() as db:
        chat = db.get(Chat, "private-alex")
        report = product.append_message(db, chat, author_id="twin-alex", kind="twin", body="Verified continuation finding")
        report_id = report.id
    dream_id = due(product)
    refs = product.get_runtime_context(dream_id)["memory_dream"]["messages"]
    assert [ref["id"] for ref in refs] == [product.get_request(first)["source_message_id"], report_id]


def test_manual_edit_invalidates_queued_attempt_and_is_next_attempt_evidence(product):
    completed(product)
    dream_id = due(product)
    asyncio.run(personal.edit_memory("owner-alex", "MEMORY", content="Owner correction", expected_version=None))
    with pytest.raises(HTTPException, match="409"):
        product.get_runtime_context(dream_id)
    product.fail_request(dream_id, "manual edits changed")
    next_id = due(product)
    edits = product.get_runtime_context(next_id)["memory_dream"]["manual_edits"]
    assert edits[-1]["content"] == "Owner correction" and edits[-1]["state"] == "published"


@pytest.mark.parametrize("unknown", [False, True])
def test_process_to_real_sdk_git_runtime_publishes_and_advances_only_once(product, monkeypatch, unknown):
    async def run():
        runtime = RuntimeBridge(product)
        calls = []
        def offline(model, context, options):
            calls.append(context)
            stream = AssistantMessageEventStream()
            tools = {tool["name"] for row in context.messages for tool in row.get("toolsAdded", [])}
            if "memory_publish" in tools:
                content = [{"type": "toolCall", "id": "publish", "name": "memory_publish", "arguments": {}}]
                stop = "toolUse"
            else:
                content, stop = [{"type": "text", "text": "The owner likes tea."}], "stop"
            stream.push({"type": "done", "message": assistant_message(model, content=content, stopReason=stop)})
            return stream
        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        try:
            request = product.send_message("owner-alex", "private-alex", "I like tea.", uuid4().hex)["request"]
            result = await runtime.execute(product.get_request(request["id"]), product.get_runtime_context(request["id"]))
            product.complete_request(request["id"], result)
            dream_id = due(product)
            dream = product.get_request(dream_id)
            if unknown:
                from agent_runtime.memory.git import GitMemoryDraft, MemoryPublishUnknown
                async def lost_ack(self, operation_id=None):
                    raise MemoryPublishUnknown("Offline synthetic lost publication")
                monkeypatch.setattr(GitMemoryDraft, "publish", lost_ack)
                product.bind_runtime(runtime)
                product.worker_mode = True
                await product.execute_request(dream_id)
                with product.transaction() as db:
                    state = db.get(MemoryDreamState, "owner-alex")
                    assert state.last_status == "recovery_required" and state.message_cursor_seq == 0 and state.due_at is None
                    assert db.get(ReplyRequest, dream_id).status == "failed"
                    assert db.get(ReplyLane, ("private-alex", "twin-alex")).active_request_id is None
                next_request = product.send_message("owner-alex", "private-alex", "Continue normal owner chat", uuid4().hex)["request"]
                await product.execute_request(next_request["id"])
                with product.transaction() as db:
                    assert db.get(ReplyRequest, next_request["id"]).status == "completed"
                assert len(calls) == 3
                product.process_memory_dreams()
                assert len(calls) == 3
                return
            result = await runtime.execute(dream, product.get_runtime_context(dream_id))
            product.complete_request(dream_id, result)
            assert result["memory_dream"]["published"] and result["body"] == ""
            with product.transaction() as db:
                state = db.get(MemoryDreamState, "owner-alex")
                assert state.message_cursor_seq == result["memory_dream"]["through_seq"]
                assert state.last_status == "published" and state.queued_request_id is None
            assert len(calls) == 2
            product.process_memory_dreams()
            assert len(calls) == 2
        finally:
            await runtime.close()
    asyncio.run(run())


def test_one_owner_manual_edit_limit_does_not_block_another_owner(product):
    from app.models import MemoryEditAudit
    completed(product)
    with product.transaction() as db:
        for _ in range(101):
            db.add(MemoryEditAudit(owner_id="owner-alex", key=uuid4().hex, action="write", state="published"))
        chat = db.get(Chat, "private-maya")
        product.append_message(db, chat, author_id="owner-maya", kind="human", body="Other owner's experience")
        row = product.accept_request(db, chat, "twin-maya", "owner-maya",
            "manual", uuid4().hex, raw="Other owner's experience")
        other_id = row.id
    product.complete_request(other_id, session_id="other-source", run_id="other-run")
    with product.transaction() as db:
        for owner in ("owner-alex", "owner-maya"):
            db.get(MemoryDreamState, owner).due_at = now() - timedelta(seconds=1)
    product.process_memory_dreams()
    with product.transaction() as db:
        bad, good = db.get(MemoryDreamState, "owner-alex"), db.get(MemoryDreamState, "owner-maya")
        assert bad.last_status == "failed" and bad.queued_request_id is None and bad.due_at > now()
        assert good.last_status == "queued" and good.queued_request_id


def test_manual_dream_now_deduplicates_and_can_use_existing_memory_with_real_audit(product):
    assert asyncio.run(product.request_memory_dream("owner-alex"))["status"] == "nothing_to_process"
    asyncio.run(personal.edit_memory("owner-alex", "MEMORY", content="Existing durable fact", expected_version=None))
    queued = asyncio.run(product.request_memory_dream("owner-alex"))
    assert queued["status"] == "queued"
    assert asyncio.run(product.request_memory_dream("owner-alex")) == {"status": "already_queued", "request_id": queued["request_id"]}
    payload = product.get_runtime_context(queued["request_id"])["memory_dream"]
    assert payload["memory_only"] and payload["messages"] == [] and payload["current_entries_sources"]
    source = payload["manual_request"]
    assert product.get_memory_edit_source("owner-alex", **source)["action"] == "dream"
    product.mark_memory_dream_recovery(queued["request_id"], "unknown")
    product.fail_request(queued["request_id"], "unknown")
    with pytest.raises(HTTPException):
        asyncio.run(product.request_memory_dream("owner-alex"))


def test_manual_memory_dream_includes_published_input_and_keeps_later_output(product):
    unfinished = accepted(product)
    queued = asyncio.run(product.request_memory_dream("owner-alex"))
    context = product.get_runtime_context(queued["request_id"])["memory_dream"]
    assert context["messages"][0]["id"] == product.get_request(unfinished)["source_message_id"]
    with product.transaction() as db:
        report = product.append_message(db, db.get(Chat, "private-alex"), author_id="twin-alex", kind="twin", body="Later verified result")
        report_id = report.id
    product.complete_request(queued["request_id"], session_id="dream:" + queued["request_id"], run_id="published-run",
        memory_dream={"published": True, "version": "verified-commit", "cursor_before": context["cursor_before"], "through_seq": context["through_seq"]})
    next_dream = due(product)
    assert product.get_runtime_context(next_dream)["memory_dream"]["messages"][0]["id"] == report_id


def test_manual_dream_includes_new_completed_private_experiences_without_waiting_due(product):
    source = completed(product)
    queued = asyncio.run(product.request_memory_dream("owner-alex"))
    payload = product.get_runtime_context(queued["request_id"])["memory_dream"]
    assert payload["messages"][0]["id"] == product.get_request(source)["source_message_id"] and not payload["memory_only"]
