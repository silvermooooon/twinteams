"""Independent generation fences: real SDK/PG, controlled offline model responses."""
import asyncio
from uuid import uuid4

import pytest
from agent_runtime import AssistantMessageEventStream, assistant_message

from app.runtime_bridge import RuntimeBridge


class Product:
    model_configured = True

    def __init__(self):
        self.generation, self.requests, self.outputs, self.events, self.tools = 0, {}, [], [], []

    async def db_call(self, name, *args):
        assert isinstance(name, str), "Test must not fall back to the real product database"
        return getattr(self, name)(*args)

    def authorize_runtime_generation(self, context):
        if context["private_generation"] != self.generation:
            raise PermissionError("Private conversation was reset")

    def get_request(self, request_id):
        return self.requests[request_id]

    def bind_runtime_run(self, request_id, session_id, run_id):
        self.requests[request_id].update(session_id=session_id, run_id=run_id)

    def record_activity(self, *args):
        assert args[-1].get("private_generation") == self.generation

    def publish_runtime_output(self, request_id, body, *args):
        if self.requests[request_id]["private_generation"] != self.generation:
            raise PermissionError("Private conversation was reset")
        self.outputs.append(body)

    def publish_event(self, chat_id, event):
        self.events.append(event)

    async def runtime_tool(self, name, args, context):
        self.tools.append(name)
        return {"messages": []}


def stub_assembly(monkeypatch):
    from app import twin_execution
    async def assemble(service, session, context, **kwargs):
        return [], context, None
    async def no_history(self, context):
        return ""
    async def no_children(self, agent, context):
        return None
    monkeypatch.setattr(twin_execution, "assemble", assemble)
    monkeypatch.setattr(RuntimeBridge, "_credential_continuation_history", no_history)
    monkeypatch.setattr(RuntimeBridge, "_attach_subagents", no_children)


def accepted(product, chat_id, generation, body):
    request = {"id": uuid4().hex, "chat_id": chat_id, "target_twin_id": "twin-owner", "source": "private",
               "status": "running", "private_generation": generation, "input_body": body}
    product.requests[request["id"]] = request
    context = {"owner_id": "owner", "chat": {"id": chat_id, "kind": "private"},
               "private_generation": generation, "messages": []}
    return request, context


def test_private_session_generation_preserves_legacy_zero_and_separates_resets():
    old = RuntimeBridge.session_id("chat", "twin")
    assert old == RuntimeBridge.session_id("chat", "twin", 0)
    assert old != RuntimeBridge.session_id("chat", "twin", 1)
    assert RuntimeBridge.session_id("chat", "twin", 1) != RuntimeBridge.session_id("chat", "twin", 2)


@pytest.mark.parametrize("late_tool", [False, True])
def test_reset_blocks_late_model_output_and_next_private_run_has_fresh_journal(monkeypatch, late_tool):
    stub_assembly(monkeypatch)
    async def run():
        product = Product()
        runtime = RuntimeBridge(product)
        captured = []
        def offline(model, context, options):
            captured.append(context.messages)
            if len(captured) == 1:
                product.generation = 1  # reset wins after the provider was invoked
                content = ([{"type": "toolCall", "id": "late", "name": "history", "arguments": {}}]
                           if late_tool else [{"type": "text", "text": "Old run must not appear after reset"}])
            else:
                content = [{"type": "text", "text": "Fresh generation response"}]
            stream = AssistantMessageEventStream()
            stream.push({"type": "done", "message": assistant_message(model, content=content,
                stopReason="toolUse" if len(captured) == 1 and late_tool else "stop")})
            return stream
        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        old, old_context = accepted(product, uuid4().hex, 0, "Old private context marker")
        try:
            with pytest.raises((PermissionError, RuntimeError)):
                await runtime.execute(old, old_context)
            assert product.outputs == [] and product.tools == [] and len(captured) == 1
            assert not any(event.get("type") == "token" for event in product.events)
            fresh, context = accepted(product, old["chat_id"], 1, "Fresh owner request")
            result = await runtime.execute(fresh, context)
            assert result["body"] == "Fresh generation response"
            assert result["session_id"] != RuntimeBridge.session_id(old["chat_id"], old["target_twin_id"])
            assert "Old private context marker" not in str(captured[1])
            calls = len(captured)
            with pytest.raises(PermissionError, match="was reset"):
                await runtime.execute(old, old_context)
            assert len(captured) == calls
        finally:
            await runtime.close()
    asyncio.run(run())


def test_runtime_status_does_not_resolve_a_synthetic_memory_owner(monkeypatch):
    from app import personal
    def forbidden(*args):
        raise AssertionError("Configuration must not create/lookup a fake owner store")
    monkeypatch.setattr(personal, "make_memory_store", forbidden)
    status = RuntimeBridge(Product()).configuration_status
    assert status["memory"] == {"enabled": True, "mode": "git"}
