"""Independent private-reset acceptance. Real PG/Git, disposable owners, no live provider."""
import asyncio
import hashlib
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from agent_runtime.memory import LocalMemoryStore, MemorySource
from fastapi import HTTPException
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app import credentials, personal
from app import service as service_module
from app.models import Actor, Chat, Message, ReplyRequest, TwinFollowup
from checks.postgres import TestDatabase


def key():
    return uuid4().hex


@pytest.fixture
def product(monkeypatch, tmp_path):
    from cryptography.fernet import Fernet
    database = TestDatabase()
    engine = create_engine(database.url.replace("postgresql://", "postgresql+psycopg://"))
    monkeypatch.setattr(service_module, "engine", engine)
    monkeypatch.setattr(service_module, "SessionLocal", sessionmaker(engine, expire_on_commit=False))
    config = replace(service_module.settings, database_url=database.url, workspace_root=tmp_path / "workspace")
    monkeypatch.setattr(service_module, "settings", config)
    monkeypatch.setattr(personal, "settings", config)
    monkeypatch.setenv("AGENT_DB_URL", database.url)
    monkeypatch.setenv("AGENT_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("AGENT_SECRETS_DIR", str(tmp_path / "credentials"))
    monkeypatch.setenv("AGENT_SECRETS_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("TWINTEAMS_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    product = service_module.ProductService()
    monkeypatch.setattr(personal, "service", product)
    product.initialize(create_schema=True)
    yield product
    product.db_pool.shutdown()
    engine.dispose()
    database.close()


def group_hash(product):
    with product.transaction() as db:
        rows = list(db.execute(text("SELECT row_to_json(t)::text FROM (SELECT * FROM tt_messages WHERE chat_id='group-team' ORDER BY seq) t")).scalars())
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


def test_reset_preserves_groups_other_owner_configuration_and_idempotent_new_work(product):
    async def memory_setup():
        own, other = personal.own_memory_store("owner-alex"), personal.own_memory_store("owner-maya")
        source = [MemorySource("isolated-acceptance-source", "preserved-source-ref")]
        await own.write("MEMORY", "Private memory to reset", expected_version=None, sources=source)
        before = await other.write("MEMORY", "Other owner must retain this", expected_version=None, sources=source)
        # Old JSON exists beside Git; reset must remove it as well, not re-import later.
        legacy = LocalMemoryStore(own.identity, Path(own.scope).parent)
        await legacy.write("legacy-old", "Do not resurrect", expected_version=None, sources=source)
        return own, other, before, legacy._path("legacy-old")
    old_store, other_store, other_memory, legacy_file = asyncio.run(memory_setup())
    workspace = personal.resolve_twin_workspace("owner-alex")
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "keep.txt").write_text("User workspace must survive")
    soul_before = personal.get_soul("owner-alex")
    personal.write_soul("owner-alex", "Custom Soul survives reset", soul_before["version"])
    credentials.manage(product, "owner-alex", "keep-credential", values={"FAKE_TOKEN": "synthetic-reset-test"})
    credentials_before = asyncio.run(credentials.list_credentials("owner-alex"))
    with product.transaction() as db:
        actor = db.get(Actor, "twin-alex")
        actor.name, actor.avatar_preset, actor.enabled_skill_names = "Keep this Twin", "orbit", ["keep-skill"]
    request = product.send_message("owner-alex", "private-alex", "Old private text", key())["request"]
    context = product.get_runtime_context(request["id"])
    sent = product._runtime_tool_transaction("send_chat_message", {"chat_id": "group-team", "body": "Public group text remains",
        "source_message_ids": [request["source_message_id"]], "wait_for_reply": True}, {**context, "operation_id": key()})
    with product.transaction() as db:
        group_request = product.accept_request(db, db.get(Chat, "group-team"), "twin-alex", "owner-alex", "manual", key(), input_body="Existing group work")
        group_request_id = group_request.id
    before_group = group_hash(product)
    reset_key = key()
    result = product.personal_reset("owner-alex", reset_key, 0)
    assert result["status"] == "completed" and result["generation"] == 1
    assert product.chat_detail("owner-alex", "private-alex")["messages"] == []
    assert not legacy_file.exists() and not Path(old_store.remote).exists()
    with pytest.raises(HTTPException):
        product.get_runtime_context(request["id"], include_completed=True)
    with product.transaction() as db:
        assert db.get(ReplyRequest, group_request_id).status == "queued"
        actor = db.get(Actor, "twin-alex")
        assert (actor.name, actor.avatar_preset, actor.enabled_skill_names) == ("Keep this Twin", "orbit", ["keep-skill"])
        assert db.get(TwinFollowup, sent["followup"]["id"]).status == "cancelled"
        message = db.get(Message, sent["message_id"])
        assert message.body == "Public group text remains" and message.state == "published"
    assert (workspace / "keep.txt").read_text() == "User workspace must survive"
    assert personal.get_soul("owner-alex")["content"] == "Custom Soul survives reset"
    assert asyncio.run(credentials.list_credentials("owner-alex")) == credentials_before
    async def after_reset():
        fresh = personal.own_memory_store("owner-alex")
        assert (await fresh.list_entries())["items"] == []
        assert await other_store.read("MEMORY") == other_memory
        with pytest.raises((HTTPException, RuntimeError)):
            await old_store.write("zombie", "Late old task", expected_version=None,
                                  sources=[MemorySource("old", "late")])
        return await fresh.write("new", "New generation fact", expected_version=None,
                                 sources=[MemorySource("new", "real-new-ref")])
    new_memory = asyncio.run(after_reset())
    fresh_request = product.send_message("owner-alex", "private-alex", "New message must survive reset replay", key())["request"]
    assert product.personal_reset("owner-alex", reset_key, 0) == result
    assert product.get_request(fresh_request["id"])["status"] == "queued"
    assert product.chat_detail("owner-alex", "private-alex")["messages"][-1]["body"] == "New message must survive reset replay"
    assert asyncio.run(personal.own_memory_store("owner-alex").read("new")) == new_memory
    assert group_hash(product) == before_group
    with pytest.raises(HTTPException, match="409"):
        product.personal_reset("owner-alex", key(), 0)


def test_old_dream_draft_cannot_publish_or_resurrect_old_repo_after_reset(product):
    async def run():
        store = personal.own_memory_store("owner-alex")
        source = [MemorySource("real-isolated-source", "source-record")]
        await store.write("MEMORY", "Old memory", expected_version=None, sources=source)
        async with store.draft() as draft:
            entry = await draft.read("MEMORY")
            await draft.write("MEMORY", "Late dream must not return", expected_version=entry.version, sources=source)
            result = await asyncio.to_thread(product.personal_reset, "owner-alex", key(), 0)
            assert result["status"] == "completed"
            with pytest.raises((HTTPException, RuntimeError)):
                await draft.publish("old-dream-operation")
            assert not Path(store.remote).exists()
        assert (await personal.own_memory_store("owner-alex").list_entries())["items"] == []
    asyncio.run(run())


def test_group_sdk_work_completes_after_owner_private_reset_with_generation_zero_activity(product):
    from agent_runtime import AssistantMessageEventStream, assistant_message

    from app.models import ActivityProjection
    from app.runtime_bridge import RuntimeBridge
    with product.transaction() as db:
        request = product.accept_request(db, db.get(Chat, "group-team"), "twin-alex", "owner-alex", "manual", key(), input_body="An unrelated group task")
        request_id = request.id
    assert product.personal_reset("owner-alex", key(), 0)["status"] == "completed"
    async def run():
        runtime = RuntimeBridge(product)
        def offline(model, context, options):
            stream = AssistantMessageEventStream()
            stream.push({"type": "done", "message": assistant_message(model,
                content=[{"type": "text", "text": "Group work still completes"}], stopReason="stop")})
            return stream
        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        try:
            context = product.get_runtime_context(request_id)
            assert context["private_generation"] is None
            result = await runtime.execute(product.get_request(request_id), context)
            product.complete_request(request_id, result)
            assert product.get_request(request_id)["status"] == "completed"
            with product.transaction() as db:
                row = db.get(ActivityProjection, (result["session_id"], result["run_id"]))
                assert row.private_generation == 0
                assert db.scalar(select(Message.id).where(Message.chat_id == "group-team", Message.body == "Group work still completes"))
        finally:
            await runtime.close()
    asyncio.run(run())


def test_manual_dream_does_not_skip_an_already_queued_human_experience(product):
    from app.models import MemoryDreamState
    request = product.send_message("owner-alex", "private-alex", "Human work not complete when Dream clicked", key())["request"]
    async def enqueue():
        store = personal.own_memory_store("owner-alex")
        await store.write("MEMORY", "Existing fact for manual consolidation", expected_version=None,
                          sources=[MemorySource("existing-source", "existing-event")])
        return await product.request_memory_dream("owner-alex")
    queued = asyncio.run(enqueue())
    assert queued["status"] == "queued"
    # The existing human task is earlier in the same FIFO and settles after enqueue.
    product.complete_request(request["id"], session_id="completed-human-session", run_id="completed-human-run")
    with product.transaction() as db:
        state = db.get(MemoryDreamState, "owner-alex")
        assert state.message_cursor_seq == 0
    from app.memory_dream import _message_batch
    with product.transaction() as db:
        batch, _ = _message_batch(product, db, db.get(MemoryDreamState, "owner-alex"))
        assert product.get_request(request["id"])["source_message_id"] in {row.id for row, _ in batch}


def test_reset_retires_child_notification_and_queued_child_without_replaying_history(product, monkeypatch):
    from agent_runtime import Agent, AssistantMessageEventStream, assistant_message
    from agent_runtime.execution import DurableExecutor

    from app.runtime_bridge import RuntimeBridge
    async def run():
        runtime = RuntimeBridge(product)
        calls = []
        def offline(model, context, options):
            calls.append(context)
            stream = AssistantMessageEventStream()
            stream.push({"type": "done", "message": assistant_message(model,
                content=[{"type": "text", "text": "Old child audit text"}], stopReason="stop")})
            return stream
        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        try:
            parent = product.send_message("owner-alex", "private-alex", "Parent task", key())["request"]
            main = await runtime.execute(product.get_request(parent["id"]), product.get_runtime_context(parent["id"]))
            product.complete_request(parent["id"], main)
            origin = {"parent_session_id": main["session_id"], "parent_run_id": main["run_id"],
                      "root_run_id": main["run_id"], "operation_id": "isolated-child-operation", "name": "worker", "task": "Child work"}
            child_session, child_command = key(), key()
            await runtime.store.accept_command(runtime.tenant_id, child_session, user_id="owner-alex", command_id=child_command,
                kind="prompt", payload={"message": "Child work", "subagent": origin})
            async def child_factory(session):
                await session.commit("subagent_created", **origin)
                return Agent(session=session, config=runtime.config, models=runtime.models)
            await DurableExecutor(runtime.store, child_factory, host=runtime.host, child=True).execute_command(
                runtime.tenant_id, child_session, user_id="owner-alex", command_id=child_command)
            pending_session, pending_command = key(), key()
            await runtime.store.accept_command(runtime.tenant_id, pending_session, user_id="owner-alex", command_id=pending_command,
                kind="prompt", payload={"message": "Not started child", "subagent": {**origin, "operation_id": "not-started"}})
            notifications = await runtime.store.pending_child_results()
            note = next(row for row in notifications if row["payload"]["child_session_id"] == child_session)
            assert len(calls) == 2
            assert product.personal_reset("owner-alex", key(), 0)["status"] == "completed"
            assert not await runtime._retire_child_result({**note, "user_id": "owner-maya"})
            assert (await runtime.store.get_command(runtime.tenant_id, note["command_id"]))["state"] == "accepted"
            async def forbidden_history(*args, **kwargs):
                raise AssertionError("Retired child notification must not read historical output")
            monkeypatch.setattr(runtime, "_run_records", forbidden_history)
            await runtime.drain_child_results()
            await runtime.drain_child_results()
            assert (await runtime.store.get_command(runtime.tenant_id, note["command_id"]))["state"] == "completed"
            await runtime.recover_pending()
            await runtime.recover_pending()
            assert (await runtime.store.get_command(runtime.tenant_id, pending_command))["state"] == "rejected"
            assert len(calls) == 2 and product.chat_detail("owner-alex", "private-alex")["messages"] == []
        finally:
            await runtime.close()
    asyncio.run(run())


def test_real_sdk_write_changes_canonical_soul_and_next_run_loads_new_file(product):
    from agent_runtime import AssistantMessageEventStream, assistant_message

    from app.runtime_bridge import RuntimeBridge
    async def run():
        runtime = RuntimeBridge(product)
        calls = []
        body = "Agent authored personality: concise and warm."
        def offline(model, context, options):
            calls.append(context)
            has_result = any(row.get("role") == "toolResult" and row.get("toolName") == "write" for row in context.messages)
            if len(calls) == 1:
                content, stop = [{"type": "toolCall", "id": "soul-write", "name": "write",
                    "arguments": {"path": "SOUL.md", "content": body}}], "toolUse"
            else:
                content, stop = [{"type": "text", "text": "I updated SOUL.md to prefer concise and warm replies." if has_result else "New run uses the updated Soul."}], "stop"
            stream = AssistantMessageEventStream()
            stream.push({"type": "done", "message": assistant_message(model, content=content, stopReason=stop)})
            return stream
        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        try:
            first = product.send_message("owner-alex", "private-alex", "Update SOUL.md", key())["request"]
            first_result = await runtime.execute(product.get_request(first["id"]), product.get_runtime_context(first["id"]))
            product.complete_request(first["id"], first_result)
            assert personal.get_soul("owner-alex")["content"] == body
            original_profile = personal.get_execution_config(first["id"])["profile"]["soul"]
            assert original_profile != body and "You're not a chatbot" in original_profile
            fresh = product.send_message("owner-alex", "private-alex", "Use the new Soul", key())["request"]
            fresh_result = await runtime.execute(product.get_request(fresh["id"]), product.get_runtime_context(fresh["id"]))
            product.complete_request(fresh["id"], fresh_result)
            assert personal.get_execution_config(fresh["id"])["profile"]["soul"] == body
            assert personal.get_execution_config(first["id"])["profile"]["soul"] == original_profile
            assert len(calls) == 3
        finally:
            await runtime.close()
    asyncio.run(run())


def test_manual_memory_only_dream_real_sdk_publishes_with_true_audit_sources(product):
    from agent_runtime import AssistantMessageEventStream, assistant_message

    from app.runtime_bridge import RuntimeBridge
    async def run():
        seed = await personal.edit_memory("owner-alex", "MEMORY", content="Existing supported fact", expected_version=None)
        queued = await product.request_memory_dream("owner-alex")
        assert (await product.request_memory_dream("owner-alex"))["request_id"] == queued["request_id"]
        context = product.get_runtime_context(queued["request_id"])
        assert context["memory_dream"]["memory_only"] and not context["memory_dream"]["messages"]
        manual = context["memory_dream"]["manual_request"]
        assert product.get_memory_edit_source("owner-alex", **manual)["action"] == "dream"
        runtime = RuntimeBridge(product)
        calls = []
        def offline(model, context, options):
            calls.append(context)
            if len(calls) == 1:
                call = {"type": "toolCall", "id": "read", "name": "memory_read", "arguments": {"key": "MEMORY"}}
            elif len(calls) == 2:
                call = {"type": "toolCall", "id": "write", "name": "memory_write", "arguments": {
                    "key": "MEMORY", "content": "Existing supported fact, reorganized.", "expected_version": seed["version"]}}
            else:
                call = {"type": "toolCall", "id": "publish", "name": "memory_publish", "arguments": {}}
            stream = AssistantMessageEventStream()
            stream.push({"type": "done", "message": assistant_message(model, content=[call], stopReason="toolUse")})
            return stream
        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        try:
            result = await runtime.execute(product.get_request(queued["request_id"]), context)
            product.complete_request(queued["request_id"], result)
            after = await personal.own_memory_store("owner-alex").read("MEMORY")
            assert after.content == "Existing supported fact, reorganized." and after.version != seed["version"]
            assert MemorySource(**manual) in after.sources and MemorySource(**seed["sources"][0]) in after.sources
            assert len(calls) == 3 and result["body"] == ""
            assert product.chat_detail("owner-alex", "private-alex")["messages"] == []
        finally:
            await runtime.close()
    asyncio.run(run())
