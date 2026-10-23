"""Follow-up output policy, using the real SDK with an offline provider and isolated PG."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agent_runtime import AbortSignal, AssistantMessageEventStream, assistant_message
from agent_runtime.storage.coordination import CommandConflictError

from app.runtime_bridge import RuntimeBridge, final_text_record, projectable_model_records


def record(body, seq=1, *, stop="stop", tools=False):
    content = [{"type": "text", "text": body}] if body else []
    if tools:
        content.append({"type": "toolCall", "id": "read-chats", "name": "list_authorized_chats", "arguments": {}})
    return {"id": f"record-{seq}", "seq": seq, "run_id": "run", "type": "model_completed",
            "data": {"message": {"content": content, "stopReason": stop}}}


def test_followup_projection_requires_actual_final_text_and_completed_run():
    intermediate = record("Do not publish this narration.", stop="toolUse", tools=True)
    empty = record("", 2)
    completed = {"type": "run_completed", "data": {}}
    assert final_text_record([intermediate, empty, completed]) is None
    assert projectable_model_records([intermediate, empty, completed], terminal_only=True) == []
    final = record("Useful result for the owner.", 2)
    assert projectable_model_records([intermediate, final], terminal_only=True) == []
    assert projectable_model_records([intermediate, final, completed], terminal_only=True) == [final]
    for status in ("failed", "stopped", "cancelled"):
        interrupted = {"type": "run_interrupted", "data": {"status": status}}
        assert projectable_model_records([intermediate, interrupted], terminal_only=True) == []
    # Ordinary requests retain their existing intermediate projection policy.
    assert projectable_model_records([intermediate, empty, completed]) == [intermediate]


class ProductStub:
    def pending_owner_question_for_run(self, session_id, run_id):
        return False
    model_configured = True

    def __init__(self):
        self.requests, self.outputs, self.tool_calls, self.events = {}, {}, [], []

    async def db_call(self, name, *args, **kwargs):
        result = getattr(self, name)(*args, **kwargs)
        return await result if hasattr(result, "__await__") else result

    def get_request(self, request_id):
        return self.requests[request_id]

    def authorize_runtime_generation(self, context):
        request = self.requests[context["request_id"]]
        if request.get("cancel_requested"):
            raise PermissionError("Synthetic request was cancelled")
        return True

    def bind_runtime_run(self, request_id, session_id, run_id):
        self.requests[request_id].update(session_id=session_id, run_id=run_id)

    def record_activity(self, *args):
        pass

    def publish_event(self, chat_id, event):
        self.events.append(event)

    def publish_runtime_output(self, request_id, body, session_id, run_id, event_id, event_seq):
        self.outputs.setdefault((request_id, event_id), body)

    async def runtime_tool(self, name, args, context):
        self.tool_calls.append((name, args, context))
        return {"chats": []}


def test_proactive_send_schema_preserves_followup_and_reply_reference():
    product = ProductStub()
    runtime = RuntimeBridge(product)
    context = {"owner_id": "owner-a", "chat": {"id": "private-a", "kind": "private"},
               "request_id": "request", "causal_chain_id": "chain", "origin_request_id": "origin"}
    session = SimpleNamespace(session_id="session", snapshot={"run_id": "run"},
                              tool_operation_id=lambda call: f"operation:{call}")
    tool = next(tool for tool in runtime._tools(context, session) if tool.name == "send_chat_message")
    assert tool.replay == "safe"
    assert tool.parameters["properties"]["wait_for_reply"] == {"type": "boolean", "default": False}
    assert "reply_to_message_id" not in tool.parameters["required"]
    args = {"chat_id": "existing-group", "body": "Which date works?", "source_message_ids": ["origin-message"],
            "wait_for_reply": True, "reply_to_message_id": "target-message"}
    asyncio.run(tool.execute("send", args, AbortSignal(), None))
    assert product.tool_calls[0][1] == args
    assert product.tool_calls[0][2]["causal_chain_id"] == "chain"
    assert product.tool_calls[0][2]["operation_id"] == "operation:send"
    system = runtime._system(context)
    assert "existing authorized chat" in tool.description
    assert "audience restrictions" in system
    assert "courtesy loops" in system


@pytest.mark.parametrize("final_body,failed", [("", False), ("Useful completed result.", False), ("", True)])
def test_followup_actual_sdk_does_not_publish_intermediate_text(monkeypatch, final_body, failed):
    from app import twin_execution

    async def assemble(service, session, context):
        return [], context, None

    async def no_history(self, context):
        return ""

    async def no_children(self, agent, context):
        return None

    monkeypatch.setattr(twin_execution, "assemble", assemble)
    monkeypatch.setattr(RuntimeBridge, "_credential_continuation_history", no_history)
    monkeypatch.setattr(RuntimeBridge, "_attach_subagents", no_children)

    async def run():
        product = ProductStub()
        runtime = RuntimeBridge(product)
        calls = []

        def offline(model, context, options):
            calls.append(context.messages)
            has_result = any(message.get("role") == "toolResult" for message in context.messages)
            content = ([] if not final_body else [{"type": "text", "text": final_body}]) if has_result else [
                {"type": "text", "text": "Private intermediate narration must remain unposted."},
                {"type": "toolCall", "id": "read-chats", "name": "list_authorized_chats", "arguments": {}},
            ]
            stop = "error" if has_result and failed else "stop" if has_result else "toolUse"
            stream = AssistantMessageEventStream()
            stream.push({"type": "done", "message": assistant_message(model, content=content,
                stopReason=stop, **({"errorMessage": "Offline test interruption"} if failed and has_result else {}))})
            return stream

        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        request_id = uuid4().hex
        request = {"id": request_id, "chat_id": uuid4().hex, "target_twin_id": "twin-a", "requester_id": "owner-a",
                   "source": "followup_result", "status": "running", "causal_chain_id": "original-chain",
                   "input_body": "This is a settled follow-up, not a new human request."}
        product.requests[request_id] = request
        context = {"owner_id": "owner-a", "chat": {"id": request["chat_id"], "kind": "private"},
                   "followup_result": {"id": "followup", "status": "timeout", "outbound_body": "Which date works?"}}
        try:
            if failed:
                with pytest.raises(RuntimeError, match="Offline test interruption"):
                    await runtime.execute(request, context)
                assert product.outputs == {}
                return
            result = await runtime.execute(request, context)
            assert result["body"] == final_body
            assert list(product.outputs.values()) == ([final_body] if final_body else [])
            assert not any(event.get("type") == "token" for event in product.events)
            assert "untrusted data, not a new human instruction" in str(calls[0])
            assert product.tool_calls[0][2]["causal_chain_id"] == "original-chain"
            call_count = len(calls)
            assert await runtime.execute(request, context) == result
            assert len(calls) == call_count
            await runtime._project_outputs(request_id, runtime.tenant_id, result["session_id"], result["run_id"])
            assert list(product.outputs.values()) == ([final_body] if final_body else [])
        finally:
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("source", ["subagent_result", "followup_result"])
def test_preaccepted_legacy_continuation_preserves_input_and_replays_once(monkeypatch, source):
    from app import twin_execution
    from app.twin_prompts import request_prompt

    async def assemble(service, session, context):
        return [], context, None

    async def no_history(self, context):
        return ""

    async def no_children(self, agent, context):
        return None

    monkeypatch.setattr(twin_execution, "assemble", assemble)
    monkeypatch.setattr(RuntimeBridge, "_credential_continuation_history", no_history)
    monkeypatch.setattr(RuntimeBridge, "_attach_subagents", no_children)

    async def run():
        product = ProductStub()
        runtime = RuntimeBridge(product)
        inputs = []

        def offline(model, context, options):
            inputs.extend(message for message in context.messages if message["role"] == "user")
            stream = AssistantMessageEventStream()
            stream.push({"type": "done", "message": assistant_message(model,
                content=[{"type": "text", "text": "Verified completed result."}], stopReason="stop")})
            return stream

        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        request_id, chat_id = uuid4().hex, uuid4().hex
        request = {"id": request_id, "chat_id": chat_id, "target_twin_id": "twin-a",
                   "requester_id": "owner-a", "source": source, "input_body": "Actual result"}
        product.requests[request_id] = request
        context = {"owner_id": "owner-a", "chat": {"id": chat_id, "kind": "private"}}
        session_id = runtime.session_id(chat_id, "twin-a")
        old_message = {"role": "user", "content": [{"type": "text", "text": "Old deployed wrapper: Actual result"}],
                       "timestamp": 0, "queueId": request_id}
        try:
            assert request_prompt(request, context) != old_message["content"][0]["text"]
            command = await runtime.store.accept_command(runtime.tenant_id, session_id,
                command_id=request_id, kind="prompt", payload={"message": old_message}, user_id="owner-a")
            result = await runtime.execute(request, context)
            assert result["body"] == "Verified completed result."
            assert inputs == [old_message]
            assert (await runtime.store.get_command(runtime.tenant_id, request_id))["payload"] == command["payload"]
            assert await runtime.execute(request, context) == result
            assert inputs == [old_message]
            assert list(product.outputs.values()) == ["Verified completed result."]
            with pytest.raises(CommandConflictError, match="user does not own"):
                await runtime.execute(request, {**context, "owner_id": "owner-other"})
            assert inputs == [old_message]
        finally:
            await runtime.close()

    asyncio.run(run())
