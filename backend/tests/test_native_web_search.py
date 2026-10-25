"""Actual Duet factories and SDK HTTP payloads, with local journals and no network."""
import asyncio
import json
from copy import deepcopy

import httpx
import pytest
from agent_runtime import LocalSession, Model, Models, ProviderConfig, RuntimeConfig
from agent_runtime.sessions.base import tool_records
from agent_runtime.tools import create_web_search_tool

from app.runtime_bridge import RuntimeBridge, output_text_content, text_content

URL = "https://example.com/docs"
ANNOTATION = {"type": "url_citation", "start_index": 0, "end_index": 6,
              "url": URL, "title": "Source documentation"}


def response(api, *, body="Answer from a source.", searched=True):
    if api == "openai-completions":
        events = [{"choices": [{"index": 0, "delta": {"content": body}, "finish_reason": "stop"}]}]
    else:
        events = [{"type": "response.completed", "response": {
            "id": "response", "status": "completed", "output": [
                *([{"type": "web_search_call", "id": "search", "status": "completed",
                    "action": {"type": "search", "queries": ["documentation"]}}] if searched else []),
                {"type": "message", "id": "answer", "role": "assistant", "status": "completed",
                 "content": [{"type": "output_text", "text": body, "annotations": [ANNOTATION] if searched else []}]},
            ], "usage": {"input_tokens": 3, "output_tokens": 4}}}]
    return httpx.Response(200, text="".join("data: " + json.dumps(event) + "\n\n" for event in events))


class ProcessLost(BaseException):
    pass


class Product:
    def __init__(self):
        self.request = {"id": "request", "chat_id": "chat", "target_twin_id": "duet",
                        "requester_id": "owner", "source": "private", "input_body": "Search the web."}
        self.outputs = []

    async def db_call(self, name, *args):
        if name == "get_request":
            return self.request
        if name == "publish_runtime_output":
            self.outputs.append(args[1])

    def publish_event(self, *args):
        pass


class FactoryHarness:
    """Replace only durable command hosting; the factories, Agent and journal remain real."""
    def __init__(self, monkeypatch, tmp_path, client, api):
        from agent_runtime import execution, subagents

        from app import twin_execution

        self.directory, self.sessions, self.agents = tmp_path, {}, []
        self.product = Product()
        self.runtime = RuntimeBridge(self.product)
        self.context = {"owner_id": "owner", "chat": {"id": "chat", "kind": "private"}}
        self.runtime.config = RuntimeConfig(provider="openai", model="test", api=api,
            providers={"openai": ProviderConfig("openai", api, "https://provider.invalid/v1", "offline")})
        self.runtime.models = Models(config=self.runtime.config, client=client)
        self.runtime.store = self
        self.before_run = None

        async def assemble(service, session, context):
            return [], context, None

        async def no_history(*args):
            return ""

        async def background(*args):
            return self.context

        class Manager:
            def __init__(self, parent, *, agent_factories, **kwargs):
                self.parent, self.agent_factories = parent, agent_factories

            def subscribe_completion(self, observer):
                pass

            def subscribe(self, observer):
                pass

        monkeypatch.setattr(twin_execution, "assemble", assemble)
        monkeypatch.setattr(self.runtime, "_require", lambda: None)
        monkeypatch.setattr(self.runtime, "_credential_continuation_history", no_history)
        monkeypatch.setattr(subagents, "DurableSubagentManager", Manager)
        monkeypatch.setattr(self.runtime, "_background_context", background)
        harness = self

        class Executor:
            def __init__(self, store, factory, **kwargs):
                self.factory = factory

            async def execute(self, tenant, session_id, *, user_id, command_id, message):
                session = harness.open(session_id)
                agent = await self.factory(session)
                harness.agents.append(agent)
                if harness.before_run:
                    harness.before_run(agent)
                await agent.resume() if session.resumable else await agent.prompt(message)
                assert agent.state.error_message is None
                return {"status": "completed", "snapshot": session.snapshot, "run_id": session.snapshot["run_id"]}

        monkeypatch.setattr(execution, "DurableExecutor", Executor)

    def open(self, session_id):
        session = LocalSession(session_id, self.directory, tenant_id="local", user_id="owner")
        self.sessions[session_id] = session
        return session

    async def get_command(self, *args):
        return None

    async def read_run_records(self, tenant, session_id, run_id, *, after_seq, limit):
        return [row for row in self.sessions[session_id].read_records(after_seq)
                if row.get("run_id") == run_id][:limit]

    async def agent(self, role):
        if role == "main":
            await self.runtime.execute(self.product.request, self.context)
            return self.agents[-1]
        session = self.open("child")
        if not session.subagent_creation:
            await session.commit("subagent_created", parent_session_id="parent", parent_run_id="parent-run",
                root_run_id="parent/parent-run", name="worker", task="Search the web.", operation_id="spawn")
        agent = await self.runtime._child_factory(session)
        self.agents.append(agent)
        if self.before_run:
            self.before_run(agent)
        await agent.resume() if session.resumable else await agent.prompt("Search the web.")
        assert agent.state.error_message is None
        return agent


@pytest.mark.parametrize("role", ["main", "child"])
@pytest.mark.parametrize("api", ["openai-responses", "openai-completions"])
def test_new_factories_default_native_search_only_for_responses(monkeypatch, tmp_path, role, api):
    async def run():
        payloads = []

        def handler(request):
            payloads.append(json.loads(request.content))
            return response(api)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            harness = FactoryHarness(monkeypatch, tmp_path, client, api)
            agent = await harness.agent(role)
            native = [tool for tool in payloads[0].get("tools", []) if tool["type"] == "web_search"]
            assert native == ([{"type": "web_search"}] if api == "openai-responses" else [])
            assert payloads[0]["model"] == "test"
            if api == "openai-responses":
                message = agent.session.snapshot["messages"][-1]
                raw = deepcopy(message)
                assert output_text_content(message) == "Answer from a source.\n\nSources: [Source documentation](<" + URL + ">)"
                assert message == raw
                restored = harness.open(agent.session.session_id)
                assert restored.snapshot["messages"][-1] == raw
                assert text_content(raw) == "Answer from a source."
                if role == "main":
                    assert harness.product.outputs and all(URL in output for output in harness.product.outputs)
    asyncio.run(run())


@pytest.mark.parametrize("role", ["main", "child"])
@pytest.mark.parametrize("native", [False, True])
def test_recovery_preserves_frozen_tools_and_resolved_api(monkeypatch, tmp_path, role, native):
    async def run():
        payloads = []

        def handler(request):
            payloads.append(json.loads(request.content))
            return response("openai-responses")

        def crash(*args):
            raise ProcessLost()

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            harness = FactoryHarness(monkeypatch, tmp_path, client, "openai-responses")
            frozen_tool = create_web_search_tool(search_context_size="low", external_web_access=False,
                filters={"allowed_domains": ["example.com"]}, user_location={"type": "approximate", "country": "CN"})
            # Model a deployment's exact accepted contract, including legacy runs without search.
            monkeypatch.setattr(harness.runtime, "_add_web_search",
                lambda agent: agent.state.tools.extend([frozen_tool] if native else []))
            harness.before_run = lambda agent: setattr(agent, "stream_function", crash)
            with pytest.raises(ProcessLost):
                await harness.agent(role)
            await harness.agents[-1].session.commit("run_interrupted", status="failed", reason="Offline model request failed")
            frozen = harness.agents[-1].session.snapshot
            assert frozen["phase"] == "model" and frozen["request"] and frozen["status"] == "failed"
            assert payloads == []
            # A later deployment selects another API. The accepted model and tools still win.
            harness.runtime.config.api = "openai-completions"
            harness.before_run = None
            monkeypatch.setattr(harness.runtime, "_add_web_search", RuntimeBridge._add_web_search)
            recovered = await harness.agent(role)
            assert recovered.state.model.api == "openai-responses"
            assert tool_records(recovered.state.tools) == frozen["tools"]
            search = [tool for tool in payloads[0].get("tools", []) if tool["type"] == "web_search"]
            assert search == ([frozen_tool.options] if native else [])
            assert len(payloads) == 1
    asyncio.run(run())


def test_new_main_uses_restored_idle_model_and_child_uses_resolved_model(monkeypatch, tmp_path):
    async def run():
        payloads = []

        def handler(request):
            payloads.append(json.loads(request.content))
            return response("openai-responses")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            harness = FactoryHarness(monkeypatch, tmp_path, client, "openai-responses")
            await harness.agent("main")
            harness.runtime.config.api = "openai-completions"
            # A completed session still restores its accepted model, as Agent normally does.
            await harness.agent("main")
            # Explicit registered metadata overrides the configured API for fresh child runs.
            harness.runtime.models.register_model(Model("test", "openai", "openai-responses", "https://provider.invalid/v1"))
            await harness.agent("child")
            assert len(payloads) == 3
            assert all({"type": "web_search"} in payload["tools"] for payload in payloads)
    asyncio.run(run())


def test_citation_projection_deduplicates_and_leaves_input_and_existing_links_unchanged():
    message = {"content": [{"type": "text", "text": "Answer.", "annotations": [ANNOTATION, ANNOTATION,
        {"type": "url_citation", "url": "https://[invalid", "title": "malformed"},
        {"type": "url_citation", "url": "javascript:alert(1)", "title": "invalid"}]}]}
    original = deepcopy(message)
    assert output_text_content(message).count(URL) == 1
    assert message == original
    message["content"][0]["text"] = f"Answer with [existing source]({URL})."
    assert output_text_content(message) == text_content(message)
    assert text_content(original) == "Answer."
    assert output_text_content(None) == ""


def test_routing_classifier_keeps_responses_payload_tool_free(monkeypatch, tmp_path):
    async def run():
        payloads = []

        def handler(request):
            payloads.append(json.loads(request.content))
            return response("openai-responses", searched=False,
                body=json.dumps({"needs_response": True, "target_owner_ids": ["owner"], "reason": "Question"}))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            harness = FactoryHarness(monkeypatch, tmp_path, client, "openai-responses")
            result = await harness.runtime.route_targets("group", {"id": "route", "author_owner_id": "sender"}, [], ["owner"])
            assert result["target_owner_ids"] == ["owner"]
            assert "tools" not in payloads[0]
    asyncio.run(run())
