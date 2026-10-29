"""Deterministic checkpoints and actual SDK tool-history compaction; no provider network."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from agent_runtime import AgentTool, AgentToolResult, Model
from test_memory_dream import product as product  # noqa: PLC0414
from test_native_web_search import FactoryHarness, ProcessLost, response

from app.conversation_summary import SummaryPolicy
from app.runtime_bridge import RuntimeBridge


def test_large_bodies_trigger_before_message_count_and_keep_exact_tail():
    rows = [{"id": f"m{i}", "seq": i, "body": "x" * 40000} for i in range(4)]
    policy = SummaryPolicy(after_messages=0)
    prefix = policy.prefix({"messages": rows})
    assert [row["id"] for row in prefix] == ["m0", "m1", "m2"]
    assert policy.prefix({"messages": rows[-1:]}) == []
    assert policy.prefix({"messages": [{"id": "short", "body": "hi"}] * 4}) == []


def test_count_is_auxiliary_and_cumulative_summary_ids_have_no_limit():
    prior = {"body": "Active task", "source_message_ids": [str(i) for i in range(750)]}
    rows = [{"id": f"m{i}", "seq": i, "body": "short"} for i in range(40)]
    prefix = SummaryPolicy().prefix({"summary": prior, "messages": rows})
    assert len(prefix) == 20
    assert prefix[-1]["id"] == "m19"


def test_failed_summary_never_publishes_or_admits_incomplete_context():
    async def run():
        runtime = RuntimeBridge.__new__(RuntimeBridge)
        prepared = {"scope": {"owner_id": "owner"}, "messages": [{"id": "old"}]}
        runtime.service = SimpleNamespace(db_call=AsyncMock(return_value=prepared))
        runtime._summarize_conversation = AsyncMock(side_effect=RuntimeError("offline summary failure"))
        with pytest.raises(RuntimeError, match="offline summary failure"):
            await runtime._maintain_conversation_context({"_summary_scope": {"chat_id": "chat"}, "context_incomplete": True})
        assert [call.args[0] for call in runtime.service.db_call.call_args_list] == ["prepare_conversation_summary"]
        runtime.service.db_call = AsyncMock(side_effect=[None, {"context_incomplete": True}])
        with pytest.raises(RuntimeError, match="incomplete"):
            await runtime._maintain_conversation_context({"_summary_scope": {"chat_id": "chat"}, "context_incomplete": True})
    asyncio.run(run())


def tool_response(index):
    event = {"type": "response.completed", "response": {"id": f"response-{index}",
        "status": "completed", "output": [{"type": "function_call", "id": f"item-{index}",
            "call_id": f"call-{index}", "name": "large_read", "arguments": "{}"}],
        "usage": {"input_tokens": 3, "output_tokens": 4}}}
    return httpx.Response(200, text="data: " + json.dumps(event) + "\n\n")


@pytest.mark.parametrize("role", ["main", "child"])
@pytest.mark.parametrize("recover", [False, True])
def test_native_sdk_compaction_shortens_real_tool_loop_and_recovery(monkeypatch, tmp_path, role, recover):
    async def run():
        payloads, calls = [], []
        normal_count, crashed = 0, False

        def handler(request):
            nonlocal normal_count
            payload = json.loads(request.content)
            payloads.append(payload)
            if "tools" not in payload:
                return response("openai-responses", body="Both reads completed; continue the original task.", searched=False)
            normal_count += 1
            return tool_response(normal_count) if normal_count <= 2 else response("openai-responses", searched=False)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            harness = FactoryHarness(monkeypatch, tmp_path, client, "openai-responses")
            harness.context.update(request_id="origin-request", request_source="manual",
                requester_id="third-party", source_owner_id="third-party", source_message_id="origin-message",
                source_message={"id": "origin-message", "author_id": "third-party-duet",
                    "author_owner_id": "third-party", "kind": "twin", "body": "Parent history only",
                    "private_field": "not-a-reference"})
            harness.runtime.models.register_model(Model("test", "openai", "openai-responses",
                "https://provider.invalid/v1", context_window=60000, max_tokens=4096))

            async def large_read(call_id, args, signal, on_update):
                calls.append(call_id)
                return AgentToolResult(content=[{"type": "text", "text": "result-" + str(len(calls)) + "x" * 100000}])

            tool = AgentTool("large_read", "Read long evidence", {"type": "object", "properties": {}}, large_read)
            monkeypatch.setattr(harness.runtime, "_add_web_search", lambda agent: None)

            def before(agent):
                nonlocal crashed
                agent.state.tools.append(tool)
                assert agent.compactor is not None
                assert agent.compactor.settings.keep_recent_tokens == 20000
                stream = agent.stream_function

                def crash_after_checkpoint(model, context, options):
                    nonlocal crashed
                    if recover and not crashed and agent.session.snapshot["compaction"] and options.get("_purpose") != "compaction":
                        crashed = True
                        raise ProcessLost()
                    return stream(model, context, options)
                agent.stream_function = crash_after_checkpoint

            harness.before_run = before
            if recover:
                with pytest.raises(ProcessLost):
                    await harness.agent(role)
                interrupted = harness.agents[-1].session
                assert interrupted.snapshot["compaction"]
                await interrupted.commit("run_interrupted", status="failed", reason="Offline process loss after checkpoint")
                # Re-inject the fake execution tool after the real factory adds
                # native background tools, matching the frozen declaration order.
                harness.before_run = lambda agent: agent.state.tools.append(tool)
            agent = await harness.agent(role)
            assert agent.state.error_message is None
            records = list(agent.session.read_records())
            assert len([row for row in records if row["type"] == "compaction"]) == 1
            assert len(calls) == 2  # Recovery never repeats completed tools.
            assert len([payload for payload in payloads if "tools" not in payload]) == 1
            final = payloads[-1]
            serialized = json.dumps(final)
            assert "Both reads completed" in serialized
            assert "result-1" not in serialized
            assert "result-2" in serialized
            # Main and worker compaction must retain identity outside the lossy summary.
            from test_frontdesk_coordination_acceptance import request_references
            system = next(message["content"] for message in agent.state.messages if message["role"] == "system")
            references = request_references(system)
            assert references["requester_id"] == references["source_owner_id"] == "third-party"
            assert references["source_message"] == {"id": "origin-message", "author_id": "third-party-duet",
                "author_owner_id": "third-party", "kind": "twin"}
            sent_system = next(row["content"] for row in final["input"] if row.get("role") == "developer")
            assert request_references(sent_system) == references
            assert "not-a-reference" not in serialized
            # Original large tool results remain in the journal, despite the shorter request.
            assert len([row for row in records if row["type"] == "tool_completed"]) == 2
            assert "result-1" in json.dumps(records)
    asyncio.run(run())


def test_dedicated_checkpoint_operation_is_tool_free_bounded_and_unprojected(monkeypatch, tmp_path):
    async def run():
        payloads = []
        def handler(request):
            payloads.append(json.loads(request.content))
            return response("openai-responses", body="Owner approved the draft; deployment is pending.", searched=False)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            harness = FactoryHarness(monkeypatch, tmp_path, client, "openai-responses")
            body = await harness.runtime._summarize_conversation({"scope": {"chat_id": "chat", "owner_id": "owner"},
                "previous_summary": None, "messages": [{"id": "m1", "body": "Approve draft only"}]})
            assert "deployment is pending" in body
            assert payloads[0]["max_output_tokens"] == 2000
            assert "tools" not in payloads[0]
            assert harness.product.outputs == []
    asyncio.run(run())


def test_native_compaction_failure_preserves_tool_journal_then_recovers(monkeypatch, tmp_path):
    async def run():
        calls, normal, fail_summary = [], 0, True
        def handler(request):
            nonlocal normal
            payload = json.loads(request.content)
            if "tools" not in payload:
                if fail_summary:
                    return httpx.Response(503, json={"error": {"message": "Offline summary outage"}})
                return response("openai-responses", body="Earlier reads completed.", searched=False)
            normal += 1
            return tool_response(normal) if normal <= 2 else response("openai-responses", searched=False)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            harness = FactoryHarness(monkeypatch, tmp_path, client, "openai-responses")
            harness.runtime.models.register_model(Model("test", "openai", "openai-responses",
                "https://provider.invalid/v1", context_window=60000, max_tokens=4096))
            async def large_read(call_id, args, signal, on_update):
                calls.append(call_id)
                return AgentToolResult(content=[{"type": "text", "text": "preserved-result" + "x" * 100000}])
            tool = AgentTool("large_read", "Read evidence", {"type": "object", "properties": {}}, large_read)
            monkeypatch.setattr(harness.runtime, "_add_web_search", lambda agent: None)
            harness.before_run = lambda agent: agent.state.tools.append(tool)
            # The harness asserts successful Agent completion; this intentionally
            # exercises the real SDK's failed compaction instead.
            with pytest.raises(AssertionError):
                await harness.agent("main")
            session = harness.agents[-1].session
            assert session.snapshot["compaction"] is None
            assert session.snapshot["status"] == "failed"
            assert any(row["type"] == "compaction_failed" for row in session.read_records())
            assert sum("preserved-result" in json.dumps(message) for message in session.snapshot["messages"]) == 2
            fail_summary = False
            recovered = await harness.agent("main")
            assert recovered.session.snapshot["compaction"]
            assert len(calls) == 2
    asyncio.run(run())


@pytest.mark.parametrize("initial_state", ["failed", "stopped"])
def test_durable_checkpoint_failure_resumes_once_and_completed_replay_ignores_manifest(product, initial_state):
    async def run():
        from copy import deepcopy

        from agent_runtime import AssistantMessageEventStream, assistant_message

        runtime = RuntimeBridge(product)
        await runtime.start()
        calls = []
        def offline(model, context, options):
            session = options["_session"]
            calls.append({"session": session.session_id,
                          "command": session.accepted_command["command_id"],
                          "messages": deepcopy(context.messages), "tools": context.tools})
            stream = AssistantMessageEventStream()
            if len(calls) == 1 and initial_state == "stopped":
                options["signal"].abort()
            result = assistant_message(model, content=[{"type": "text", "text": "Verified draft; deployment remains pending."}],
                stopReason=("aborted" if initial_state == "stopped" else "error") if len(calls) == 1 else "stop")
            if len(calls) == 1:
                result["errorMessage"] = "Offline checkpoint failure"
            stream.push({"type": "done", "message": result})
            return stream
        runtime.models.register_api("offline-regression", offline)
        prepared = {"previous_summary": None, "messages": [{"id": "old", "seq": 1, "body": "Draft approved only"}],
                    "scope": {"owner_id": "owner-alex", "chat_id": "private-alex", "grant_version": 1,
                              "private_generation": 0, "coverage_end_seq": 1, "batch_digest": "stable-prefix",
                              "context_manifest_id": "first-manifest", "exclude_request_id": "first-request", "high_water_seq": 8}}
        try:
            with pytest.raises(RuntimeError, match="did not complete"):
                await runtime._summarize_conversation(prepared)
            assert len(calls) == 1
            command = await runtime.store.get_command(runtime.tenant_id, calls[0]["command"])
            assert command["state"] == initial_state
            prepared["scope"].update(context_manifest_id="second-manifest", exclude_request_id="second-request", high_water_seq=9)
            assert "deployment remains pending" in await runtime._summarize_conversation(prepared)
            assert len(calls) == 2 and calls[0]["command"] == calls[1]["command"]
            assert calls[0]["messages"] == calls[1]["messages"]
            prepared["scope"]["context_manifest_id"] = "third-manifest"
            assert "deployment remains pending" in await runtime._summarize_conversation(prepared)
            assert len(calls) == 2
            assert all(not call["tools"] for call in calls)
            assert "first-manifest" not in json.dumps(calls[0]["messages"])
            assert "first-request" not in json.dumps(calls[0]["messages"])
        finally:
            await runtime.close()
    asyncio.run(run())


def test_dedicated_checkpoint_rejects_truncation_then_resumes_original_input(product):
    async def run():
        from agent_runtime import AssistantMessageEventStream, assistant_message

        runtime = RuntimeBridge(product)
        await runtime.start()
        calls = []
        def truncated(model, context, options):
            calls.append(options["_session"].accepted_command["command_id"])
            stream = AssistantMessageEventStream()
            stream.push({"type": "done", "message": assistant_message(model,
                content=[{"type": "text", "text": "Task remains pending"}],
                stopReason="length" if len(calls) == 1 else "stop")})
            return stream
        runtime.models.register_api("offline-regression", truncated)
        prepared = {"previous_summary": None,
                    "scope": {"owner_id": "owner-alex", "chat_id": "private-alex", "coverage_end_seq": 3},
                    "messages": [{"id": "m3", "body": "An unfinished task"}]}
        try:
            with pytest.raises(RuntimeError, match="did not complete"):
                await runtime._summarize_conversation(prepared)
            command = await runtime.store.get_command(runtime.tenant_id, calls[0])
            assert command["state"] == "failed"
            assert len(calls) == 1  # Invalid output never retries inside this call.
            records = await runtime._run_records(runtime.tenant_id, command["session_id"], command["run_id"])
            assert any(row["type"] == "model_call_finished"
                       and row["data"].get("message", {}).get("stopReason") == "length" for row in records)
            assert not any(row["type"] == "model_completed" for row in records)
            assert await runtime._summarize_conversation(prepared) == "Task remains pending"
            assert len(calls) == 2 and calls[0] == calls[1]
        finally:
            await runtime.close()
    asyncio.run(run())


def test_incomplete_history_blocks_main_before_model_admission():
    async def run():
        runtime = RuntimeBridge.__new__(RuntimeBridge)
        runtime.tenant_id = "local"
        runtime.store = SimpleNamespace(get_command=AsyncMock(return_value=None))
        runtime._require = lambda: None
        runtime._check_generation = AsyncMock()
        runtime.service = SimpleNamespace(db_call=AsyncMock(side_effect=[None, {"context_incomplete": True}]))
        context = {"_summary_scope": {"chat_id": "group"}, "context_incomplete": True}
        with pytest.raises(RuntimeError, match="incomplete"):
            await runtime.execute({"id": "request", "source": "manual"}, context)
        assert runtime.store.get_command.await_count == 1
        assert [call.args[0] for call in runtime.service.db_call.call_args_list] == [
            "prepare_conversation_summary", "get_conversation_context"]
    asyncio.run(run())


def test_short_complete_context_reuses_authorized_projection_without_database_work():
    async def run():
        runtime = RuntimeBridge.__new__(RuntimeBridge)
        runtime.service = SimpleNamespace(db_call=AsyncMock())
        context = {"_summary_scope": {"chat_id": "chat"}, "context_incomplete": False,
                   "summary": None, "messages": [{"id": "new", "seq": 1, "body": "Hello"}]}
        assert await runtime._maintain_conversation_context(context) is context
        runtime.service.db_call.assert_not_called()
    asyncio.run(run())
