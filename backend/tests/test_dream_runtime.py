"""Real SDK + isolated PostgreSQL + local Git; every model response is offline."""
import asyncio
from uuid import uuid4

import pytest
from agent_runtime import Agent, AssistantMessageEventStream, assistant_message
from agent_runtime.execution import DurableExecutor
from agent_runtime.memory.git import GitMemoryDraft, MemoryPublishUnknown

from app.git_memory import ManagedGitMemoryStore
from app.runtime_bridge import RuntimeBridge
from app.runtime_memory_dream import evidence_from_records, publication_facts
from app.twin_prompts import conversation_system, platform_prompt


def test_dream_evidence_is_only_real_user_assistant_text():
    records = [
        {"id": "real-input", "type": "run_started", "data": {"inputs": [
            {"role": "system", "content": "hidden protocol"},
            {"role": "user", "content": [{"type": "text", "text": "owner fact"}, {"type": "image", "data": "raw-image"}]}]}},
        {"id": "real-output", "type": "model_completed", "data": {"message": {"content": [
            {"type": "thinking", "thinking": "private thinking"}, {"type": "text", "text": "assistant fact"},
            {"type": "toolCall", "name": "bash", "arguments": {"secret": "raw tool value"}}]}}},
        {"id": "summary", "type": "compaction", "data": {"summary": "unverified summary"}},
        {"id": "tool", "type": "tool_completed", "data": {"result": "raw result"}},
    ]
    assert evidence_from_records("source-session", records) == {
        ("source-session", "real-input"): {"role": "user", "text": "owner fact"},
        ("source-session", "real-output"): {"role": "assistant", "text": "assistant fact"}}


def test_prompt_composes_soul_and_server_hook_without_private_memory(monkeypatch):
    from app import twin_prompts
    context = {"owner_id": "owner", "chat": {"kind": "group"},
               "execution_config": {"profile": {"soul": "Speak gently.", "soul_version": 2}},
               "private_memory": "must never appear"}
    assert platform_prompt(context) == ""
    monkeypatch.setattr(twin_prompts, "platform_prompt", lambda _: "server-only policy")
    prompt = conversation_system(context)
    assert "group conversation" in prompt and "Speak gently." in prompt
    assert "server-only policy" in prompt
    assert "must never appear" not in prompt
    assert "Workers and groups have no memory tools" in prompt
    assert "awaiting approval" in prompt and "wait_for_reply=true" in prompt


class Product:
    model_configured = True

    def __init__(self):
        self.requests, self.contexts, self.recovery, self.outputs, self.events = {}, {}, [], [], []
        self.confirmed = []

    async def db_call(self, name, *args):
        if callable(name):
            return name(*args)
        return getattr(self, name)(*args)

    def get_request(self, request_id):
        return self.requests[request_id]

    def get_runtime_context(self, request_id):
        return self.contexts[request_id]

    def bind_runtime_run(self, request_id, session_id, run_id):
        self.requests[request_id].update(session_id=session_id, run_id=run_id)

    def mark_memory_dream_recovery(self, request_id, error):
        self.recovery.append((request_id, error))

    def confirm_memory_dream_publication(self, request_id, version):
        self.confirmed.append((request_id, version))

    def publish_runtime_output(self, *args):
        self.outputs.append(args)

    def publish_event(self, *args):
        self.events.append(args)


def message(model, content=None, stop="stop"):
    stream = AssistantMessageEventStream()
    stream.push({"type": "done", "message": assistant_message(model, content=content or [], stopReason=stop)})
    return stream


async def fixture_runtime(monkeypatch, tmp_path):
    from app import personal
    product = Product()
    runtime = RuntimeBridge(product)
    monkeypatch.setattr(personal, "make_memory_store", lambda info: ManagedGitMemoryStore(info, tmp_path))
    runtime.models.register_api("offline-regression", lambda model, context, options: message(model, [{"type": "text", "text": "The owner likes tea."}]))
    await runtime.start()
    source_session = "source-" + uuid4().hex
    seed = DurableExecutor(runtime.store, lambda session: Agent(session=session, config=runtime.config, models=runtime.models), host=runtime.host)
    result = await seed.execute(runtime.tenant_id, source_session, user_id="owner", command_id=uuid4().hex, message="I like tea.")
    request_id = uuid4().hex
    request = {"id": request_id, "chat_id": uuid4().hex, "target_twin_id": "twin", "source": "memory_dream", "status": "running"}
    payload = {"owner_id": "owner", "cursor_before": 0, "through_seq": 1,
               "experiences": [{"request_id": "real-source", "session_id": source_session, "run_id": result["run_id"]}]}
    context = {"owner_id": "owner", "chat": {"id": request["chat_id"], "kind": "private"}, "memory_dream": payload}
    product.requests[request_id], product.contexts[request_id] = request, context
    return runtime, product, request, context


def install_dream_provider(runtime, calls):
    def offline(model, context, options):
        calls.append((context, options))
        tools = {tool["name"] for row in context.messages for tool in row.get("toolsAdded", [])}
        assert tools == {"memory_read", "memory_search", "memory_write", "memory_delete", "read_source", "memory_publish"}
        assert options["max_tokens"] == 4096, options
        results = [row for row in context.messages if row.get("role") == "toolResult"]
        if not results:
            call = {"type": "toolCall", "id": "read", "name": "memory_read", "arguments": {"key": "MEMORY"}}
        elif len(results) == 1:
            call = {"type": "toolCall", "id": "write", "name": "memory_write", "arguments": {
                "key": "MEMORY", "content": "Owner likes tea.", "expected_version": None}}
        else:
            call = {"type": "toolCall", "id": "publish", "name": "memory_publish", "arguments": {}}
        return message(model, [{"type": "text", "text": "Internal Dream narration."}, call], "toolUse")
    runtime.models.register_api("offline-regression", offline)


def test_actual_dream_publishes_git_once_without_chat_output(monkeypatch, tmp_path):
    async def run():
        runtime, product, request, context = await fixture_runtime(monkeypatch, tmp_path)
        calls = []
        install_dream_provider(runtime, calls)
        try:
            result = await runtime.execute(request, context)
            assert result["body"] == "" and result["memory_dream"]["published"]
            assert result["session_id"] == f"dream:{request['id']}"
            assert len(calls) == 3 and product.outputs == [] and product.events == []
            text = str(calls[0][0].messages)
            assert "I like tea." in text and "The owner likes tea." in text
            assert await runtime.execute(request, context) == result
            assert len(calls) == 3
            rows = await runtime._run_records(runtime.tenant_id, result["session_id"], result["run_id"])
            facts = publication_facts(result["session_id"], result["run_id"], rows)
            assert len(facts) == 1 and facts[0]["version"] == result["memory_dream"]["version"]
        finally:
            await runtime.close()
    asyncio.run(run())


@pytest.mark.parametrize("committed", [True, False])
def test_unknown_publication_is_reconciled_without_replaying_draft(monkeypatch, tmp_path, committed):
    original = GitMemoryDraft.publish
    async def uncertain(self, operation_id=None):
        if committed:
            await original(self, operation_id)
        raise MemoryPublishUnknown("Synthetic lost acknowledgement")
    monkeypatch.setattr(GitMemoryDraft, "publish", uncertain)
    async def run():
        runtime, product, request, context = await fixture_runtime(monkeypatch, tmp_path)
        calls = []
        install_dream_provider(runtime, calls)
        try:
            if committed:
                result = await runtime.execute(request, context)
                assert result["memory_dream"]["published"]
                assert await runtime.execute(request, context) == result
                assert product.recovery == []
            else:
                for _ in range(2):
                    with pytest.raises(RuntimeError, match="could not be confirmed"):
                        await runtime.execute(request, context)
                assert len(product.recovery) == 2
            assert len(calls) == 3 and product.outputs == [] and product.events == []
        finally:
            await runtime.close()
    asyncio.run(run())


def test_dream_preserves_concurrent_owner_edit(monkeypatch, tmp_path):
    original = GitMemoryDraft.publish
    async def owner_wins(self, operation_id=None):
        await self.store.write("MEMORY", "Owner correction wins.", expected_version=None,
            sources=self.changes["MEMORY"].sources, operation_id="owner-edit-test")
        return await original(self, operation_id)
    monkeypatch.setattr(GitMemoryDraft, "publish", owner_wins)
    async def run():
        runtime, product, request, context = await fixture_runtime(monkeypatch, tmp_path)
        calls = []
        install_dream_provider(runtime, calls)
        try:
            with pytest.raises(RuntimeError, match="not published"):
                await runtime.execute(request, context)
            from agent_runtime.sessions import SessionInfo
            store = ManagedGitMemoryStore(SessionInfo("read", runtime.tenant_id, "owner"), tmp_path)
            assert (await store.read("MEMORY")).content == "Owner correction wins."
            assert product.recovery == [] and product.outputs == []
        finally:
            await runtime.close()
    asyncio.run(run())


def test_dream_cannot_recreate_owner_deleted_key_from_same_experiences(monkeypatch, tmp_path):
    async def run():
        runtime, product, request, context = await fixture_runtime(monkeypatch, tmp_path)
        context["memory_dream"]["manual_edits"] = [{"session_id": "twin-config:owner", "event_id": "audit-ref",
            "action": "delete", "key": "MEMORY", "state": "published", "content": None}]
        calls = []
        install_dream_provider(runtime, calls)
        try:
            result = await runtime.execute(request, context)
            assert result["memory_dream"]["published"]
            assert len(calls) == 3  # blocked key is skipped; the rest of the batch may publish
            from agent_runtime.sessions import SessionInfo
            store = ManagedGitMemoryStore(SessionInfo("read", runtime.tenant_id, "owner"), tmp_path)
            assert await store.read("MEMORY") is None
            assert product.outputs == [] and product.recovery == []
        finally:
            await runtime.close()
    asyncio.run(run())


def test_dream_has_finite_timeout_and_never_projects_error_text(monkeypatch, tmp_path):
    monkeypatch.setenv("TWINTEAMS_DREAM_TIMEOUT_SECONDS", "1")
    async def run():
        runtime, product, request, context = await fixture_runtime(monkeypatch, tmp_path)
        runtime.models.register_api("offline-regression", lambda *args: AssistantMessageEventStream())
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(runtime.execute(request, context), 5)
            assert product.outputs == [] and product.events == [] and product.recovery == []
        finally:
            await runtime.close()
    asyncio.run(run())


def test_soul_freezes_per_request_and_parent_workers_share_it(monkeypatch):
    from types import SimpleNamespace

    from agent_runtime.sessions import SessionInfo

    from app import personal, twin_execution
    from app import service as service_module
    product = service_module.service
    product.initialize(create_schema=True)
    before = personal.get_soul("owner-alex")
    async def run():
        personal.write_soul("owner-alex", "First personality", before["version"])
        request = product.send_message("owner-alex", "private-alex", "Soul snapshot test", uuid4().hex)["request"]
        context = product.get_runtime_context(request["id"])
        session = SimpleNamespace(info=SessionInfo("snapshot", "local", "owner-alex"), resumable=False, subagent_creation=None)
        _, original, _ = await twin_execution.assemble(product, session, context, include_memory=False)
        assert original["execution_config"]["profile"]["soul"] == "First personality"
        latest = personal.get_soul("owner-alex")
        personal.write_soul("owner-alex", "Second personality", latest["version"])
        _, resumed, _ = await twin_execution.assemble(product, session, context, include_memory=False)
        assert resumed["execution_config"]["profile"] == original["execution_config"]["profile"]
        session.subagent_creation = {"parent_session_id": "snapshot"}
        _, child, memory = await twin_execution.assemble(product, session, context, include_memory=False)
        assert child["execution_config"]["profile"] == original["execution_config"]["profile"] and memory is None
        second = product.send_message("owner-alex", "private-alex", "New request personality", uuid4().hex)["request"]
        session.subagent_creation = None
        _, fresh, _ = await twin_execution.assemble(product, session, product.get_runtime_context(second["id"]), include_memory=False)
        assert fresh["execution_config"]["profile"]["soul"] == "Second personality"
    # Assembly uses the real authorization/configuration path; tools aren't executed.
    try:
        asyncio.run(run())
    finally:
        latest = personal.get_soul("owner-alex")
        personal.write_soul("owner-alex", before["content"], latest["version"])


@pytest.mark.parametrize("committed", [True, False])
def test_cancel_during_publish_verifies_git_before_allowing_fresh_batch(monkeypatch, tmp_path, committed):
    original = GitMemoryDraft.publish
    async def run():
        runtime, product, request, context = await fixture_runtime(monkeypatch, tmp_path)
        async def cancelled_publish(self, operation_id=None):
            if committed:
                await original(self, operation_id)
            runtime.active[request["id"]].abort()
            await asyncio.sleep(60)  # actual parent cancellation, not a self-cancelled tool
        monkeypatch.setattr(GitMemoryDraft, "publish", cancelled_publish)
        calls = []
        install_dream_provider(runtime, calls)
        try:
            with pytest.raises(asyncio.CancelledError):
                await runtime.execute(request, context)
            assert len(calls) == 3 and product.outputs == []
            if committed:
                assert len(product.confirmed) == 1 and product.recovery == []
            else:
                assert product.confirmed == [] and len(product.recovery) == 1
        finally:
            await runtime.close()
    asyncio.run(run())


def test_dream_model_turn_budget_stops_tool_loops(monkeypatch, tmp_path):
    async def run():
        runtime, product, request, context = await fixture_runtime(monkeypatch, tmp_path)
        calls = []
        def loop(model, context, options):
            calls.append(context)
            return message(model, [{"type": "toolCall", "id": "read", "name": "memory_read", "arguments": {"key": "MEMORY"}}], "toolUse")
        runtime.models.register_api("offline-regression", loop)
        try:
            with pytest.raises(RuntimeError, match="Dream did not complete"):
                await runtime.execute(request, context)
            assert len(calls) == 16 and product.outputs == [] and product.recovery == []
        finally:
            await runtime.close()
    asyncio.run(run())


def test_dream_rejects_oversize_source_without_silent_truncation():
    with pytest.raises(ValueError, match="per-run text budget"):
        evidence_from_records("source", [{"id": "real", "type": "run_started", "data": {
            "inputs": [{"role": "user", "content": "x" * 65537}]}}])
