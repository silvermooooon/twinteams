"""Focused offline checks for Twin chat tools and response-aware routing.

Uses only a temporary SDK PostgreSQL journal, an in-memory product stub, and an
explicitly registered deterministic test adapter. No app database, server,
browser, credential files, or external provider is used.
Run: .venv/bin/python checks/twin_message_bridge_acceptance.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

TEMP = tempfile.TemporaryDirectory(prefix="twin-message-bridge-")
for name, value in {
    "AGENT_PROVIDER": "openai",
    "AGENT_MODEL": "twin-message-bridge-offline",
    "AGENT_API": "twin-message-bridge-offline",
    "AGENT_REASONING": "off",
    "OPENAI_API_KEY": "isolated-placeholder-never-sent",
    "ANTHROPIC_API_KEY": "isolated-placeholder-never-sent",
    "OPENAI_BASE_URL": "http://127.0.0.1:9/network-disabled",
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:9/network-disabled",
    "AGENT_MEMORY_DIR": str(Path(TEMP.name) / "memory"),
    "TWINTEAMS_TENANT_ID": "twin-message-bridge-check",
}.items():
    os.environ[name] = value
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from postgres import TestDatabase

DATABASE = TestDatabase().configure()

from agent_runtime import AbortSignal, AssistantMessageEventStream, assistant_message

from app import credentials, personal
from app.runtime_bridge import ModelNotConfigured, RuntimeBridge, final_text_record, text_content
from app.twin_execution import owner_execution_allowed

CALLS = []


async def offline(model, context, options):
    assert model.api == "twin-message-bridge-offline"
    options["signal"].throw_if_aborted()
    last_user = max((index for index, row in enumerate(context.messages)
                     if row.get("role") == "user"), default=-1)
    prompt = text_content(context.messages[last_user])
    tools = [row for row in context.messages[last_user + 1:] if row.get("role") == "toolResult"]
    CALLS.append(prompt)
    try:
        routed = json.loads(prompt)
    except ValueError:
        routed = None
    stop = "stop"
    if isinstance(routed, dict) and "message" in routed:
        mode = routed["message"]["body"]
        if mode == "legacy-list":
            response = ["owner-b"]
        elif mode == "wrong-boolean":
            response = {"target_owner_ids": ["owner-b"], "needs_response": "false", "reason": "Invalid test decision"}
        else:
            response = {"target_owner_ids": ["owner-b", "owner-b", "unlisted-owner"],
                        "needs_response": mode != "thanks", "reason": "Offline structured decision"}
        content = [{"type": "text", "text": json.dumps(response)}]
    elif prompt == "intermediate-then-empty" and not tools:
        content = [{"type": "text", "text": "Intermediate tool explanation must not become a final reply."},
                   {"type": "toolCall", "id": "read-chats", "name": "list_authorized_chats", "arguments": {}}]
        stop = "toolUse"
    elif prompt == "intermediate-then-empty":
        content = []
    elif prompt == "send-proactive" and not tools:
        content = [{"type": "toolCall", "id": "send-message", "name": "send_chat_message",
                    "arguments": {"chat_id": "chat", "body": "A useful Twin update", "source_message_ids": ["source"]}}]
        stop = "toolUse"
    else:
        content = [{"type": "text", "text": "Complete Twin reply."}]
    stream = AssistantMessageEventStream()
    stream.push({"type": "done", "message": assistant_message(model, content=content, stopReason=stop)})
    return stream


class ProductStub:
    async def db_call(self, name, *args, **kwargs):
        # Explicit in-memory test adapter for production transaction boundary.
        # Keep the real assembly path while isolating all business-store calls:
        # these synthetic frozen requests deliberately have execution/Skills disabled.
        # Never invoke a production callable against the application's database.
        if name is owner_execution_allowed:
            assert args[0] is self and args[2] in self.requests
            value = False
        elif name is personal.get_execution_config:
            assert args[0] in self.requests
            value = {"version": 1, "skills": [], "credentials": [], "execution_enabled": False}
        elif name is credentials.continuation_origin:
            assert args[0] is self and args[2] in self.requests
            value = None
        elif callable(name):
            raise AssertionError(f"Unexpected product callable in isolated check: {name.__name__}")
        else:
            value = getattr(self, name)(*args, **kwargs)
        return await value if hasattr(value, "__await__") else value

    def record_activity(self, *args, **kwargs):
        pass

    def __init__(self):
        self.requests = {}
        self.tool_calls = []
        self.outputs = []

    def get_request(self, request_id):
        return self.requests[request_id]

    def publish_event(self, *args):
        pass

    def bind_runtime_run(self, request_id, session_id, run_id):
        self.requests[request_id].update(session_id=session_id, run_id=run_id)

    def publish_runtime_output(self, *args):
        self.outputs.append(args)

    async def runtime_tool(self, name, args, context):
        self.tool_calls.append((name, args, context))
        return {"id": "sent-message", "chats": [{"id": "chat"}]}

    def get_background_context(self, parent_session_id, parent_run_id):
        return {"owner_id": "owner-a", "request_id": "original-request",
                "requester_id": "owner-b", "origin_request_id": "root-request",
                "causal_chain_id": "chain", "chat": {"id": "chat", "kind": "group"}}


def context(request_id):
    return {"owner_id": "owner-a", "request_id": request_id,
            "origin_request_id": "origin", "causal_chain_id": "chain",
            "context_manifest_id": "manifest", "target_twin_id": "twin-a",
            "chat": {"id": "chat", "kind": "group"},
            "messages": [{"id": "source", "author_id": "twin-b", "author_owner_id": "owner-b",
                          "kind": "twin", "body": "Can you help?"}]}


def record(body, *, stop="stop", tool=False):
    content = [{"type": "text", "text": body}]
    if tool:
        content.append({"type": "toolCall", "id": "call"})
    return {"type": "model_completed", "data": {"message": {"content": content, "stopReason": stop}}}


async def main():
    product = ProductStub()
    runtime = RuntimeBridge(product)
    runtime.models.register_api("twin-message-bridge-offline", offline)
    await runtime.start()
    try:
        prior = record("Intermediate explanation", stop="toolUse", tool=True)
        assert final_text_record([prior, record("")]) is None
        assert final_text_record([record("Earlier final"), prior]) is None
        assert final_text_record([record("Truncated", stop="length")]) is None
        terminal = record("Final complete reply")
        assert final_text_record([prior, terminal]) is terminal
        print("PASS terminal result selection never falls back to tool-use/empty/truncated text")

        fake_session = SimpleNamespace(session_id="child-session", snapshot={"run_id": "child-run"},
                                       tool_operation_id=lambda call: "stable-sdk-operation/" + call)
        parent = await runtime._background_context("main-session", "main-run")
        tools = {tool.name: tool for tool in runtime._tools({**context("request"), **parent}, fake_session)}
        send = tools["send_chat_message"]
        assert send.replay == "safe"
        assert send.parameters["required"] == ["chat_id", "body", "source_message_ids"]
        args = {"chat_id": "chat", "body": "Useful update", "source_message_ids": ["source"]}
        await send.execute("same-call", args, AbortSignal(), None)
        await send.execute("same-call", args, AbortSignal(), None)
        sent_context = product.tool_calls[-1][2]
        assert sent_context["operation_id"] == product.tool_calls[-2][2]["operation_id"]
        for key, expected in {"causal_chain_id": "chain", "origin_request_id": "root-request",
                              "request_id": "original-request", "context_manifest_id": "manifest",
                              "runtime_session_id": "child-session", "runtime_run_id": "child-run",
                              "main_session_id": "main-session", "main_run_id": "main-run"}.items():
            assert sent_context[key] == expected, (key, sent_context)
        system = runtime._system(context("request"))
        assert '"author_owner_id": "owner-b"' in system
        assert "source audit and audience restrictions" in system
        assert "never clear sources or use another destination to bypass a denied send" in system
        print("PASS proactive tool schema, stable SDK operation identity, causal origin and source-owner prompt")

        async def route(mode):
            return await runtime.route_targets("chat", {"id": "route-" + mode, "body": mode,
                "author_id": "twin-a", "author_owner_id": "owner-a"}, [], ["owner-a", "owner-b"], [
                    {"id": "twin-b", "kind": "twin", "owner_id": "owner-b", "name": "Beacon Duet"}])

        decision = await route("question")
        assert decision == {"target_owner_ids": ["owner-b"], "needs_response": True,
                            "reason": "Offline structured decision"}, decision
        count = len(CALLS)
        assert await route("question") == decision
        assert len(CALLS) == count, "Durable routing replay must not call the provider again"
        assert (await route("thanks"))["target_owner_ids"] == []
        assert (await route("thanks"))["needs_response"] is False
        for invalid in ("legacy-list", "wrong-boolean"):
            try:
                await route(invalid)
            except RuntimeError as exc:
                assert "valid response decision" in str(exc)
            else:
                raise AssertionError("Malformed router output was accepted: " + invalid)
        print("PASS model-backed response-aware routing, replay, safe filtering and strict malformed-output rejection")

        for request_id, prompt in (("empty-final", "intermediate-then-empty"), ("proactive", "send-proactive")):
            request = {"id": request_id, "chat_id": "chat", "target_twin_id": "twin-a",
                       "requester_id": "owner-b", "input_body": prompt, "status": "running",
                       "causal_chain_id": "chain"}
            product.requests[request_id] = request
            result = await runtime.execute(request, context(request_id))
            assert result["session_id"] and result["run_id"]
            if request_id == "empty-final":
                assert result["body"] == "" and result["event_id"] is None and result["event_seq"] is None, result
                assert any(row[1].startswith("Intermediate tool explanation") for row in product.outputs)
            else:
                assert result["body"] == "Complete Twin reply." and result["event_id"]
                sent = next(row for row in reversed(product.tool_calls) if row[0] == "send_chat_message")
                assert sent[2]["request_id"] == request_id and sent[2]["causal_chain_id"] == "chain"
                assert sent[2]["runtime_run_id"] == result["run_id"]
                before_replay = (len(CALLS), len(product.tool_calls))
                assert await runtime.execute(request, context(request_id)) == result
                assert (len(CALLS), len(product.tool_calls)) == before_replay
        print("PASS actual SDK execution preserves intermediate projection but final-empty cannot trigger; proactive context stays on originating chain")

        runtime.store, saved_store = None, runtime.store
        try:
            await route("unconfigured")
        except ModelNotConfigured:
            pass
        else:
            raise AssertionError("Unconfigured routing must never use an application fallback")
        finally:
            runtime.store = saved_store
        print("5 focused Twin message bridge checks passed; no real provider used.")
    finally:
        await runtime.close()
        TEMP.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
