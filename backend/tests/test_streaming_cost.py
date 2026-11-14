"""Discarded live previews must not create per-token product DB round trips."""
import asyncio
import time
from threading import local
from uuid import uuid4

import pytest
from agent_runtime import AgentTool, AgentToolResult, AssistantMessageEventStream, assistant_message
from sqlalchemy import event
from test_memory_dream import product as product  # noqa: PLC0414

from app import service as service_module
from app.models import Message
from app.runtime_bridge import RuntimeBridge, text_content


async def measure_stream(product, bridge_type=RuntimeBridge, *, deltas=100, owner="alex", tool_updates=None, worker=False):
    metrics = {"gates": 0, "gate_sql": 0, "product_sql": 0, "tool_updates": 0}
    class ProgressRuntime(bridge_type):
        def _tools(self, context, session, execution_tools=()):
            tools = super()._tools(context, session, execution_tools)
            if tool_updates is not None:
                async def progress(call_id, args, signal, on_update):
                    for index in range(tool_updates):
                        metrics["tool_updates"] += 1
                        on_update(AgentToolResult(content=[{"type": "text", "text": str(index)}]))
                    return AgentToolResult(content=[{"type": "text", "text": "Synthetic tool completed"}])
                tools.append(AgentTool(name="synthetic_progress", description="Emit synthetic test progress",
                    parameters={"type": "object", "properties": {}, "additionalProperties": False}, execute=progress))
            return tools
    runtime = ProgressRuntime(product)
    gate_thread = local()
    original = product.authorize_runtime_generation

    def statement(*args):
        metrics["product_sql"] += 1
        if getattr(gate_thread, "active", False):
            metrics["gate_sql"] += 1

    def counted(context):
        metrics["gates"] += 1
        gate_thread.active = True
        try:
            return original(context)
        finally:
            gate_thread.active = False

    product.authorize_runtime_generation = counted

    def offline(model, context, options):
        user = text_content(next(message for message in reversed(context.messages) if message["role"] == "user"))
        if not any(message.get("role") == "toolResult" for message in context.messages) and tool_updates is not None:
            if worker and user != "Synthetic progress child":
                tool, args = "spawn_agent", {"name": "worker", "task": "Synthetic progress child"}
            else:
                tool, args = "synthetic_progress", {}
            message = assistant_message(model, content=[{"type": "toolCall", "id": uuid4().hex,
                "name": tool, "arguments": args}], stopReason="toolUse")
        else:
            message = assistant_message(model, content=[{"type": "text", "text": "Synthetic verified answer"}], stopReason="stop")
        stream = AssistantMessageEventStream()
        stream.push({"type": "start", "partial": assistant_message(model)})
        for _ in range(deltas):
            stream.push({"type": "text_delta", "delta": "x", "partial": message})
        stream.push({"type": "done", "message": message})
        return stream

    runtime.models.register_api("offline-regression", offline)
    await runtime.start()
    try:
        request = product.send_message("owner-" + owner, "private-" + owner, "Synthetic stream request", uuid4().hex)["request"]
        event.listen(service_module.engine, "before_cursor_execute", statement)
        started = time.perf_counter()
        result = await runtime.execute(product.get_request(request["id"]), product.get_runtime_context(request["id"]))
        product.complete_request(request["id"], result)
        if worker:
            manager = runtime.managers[result["session_id"], False]
            children = await manager.aget()
            assert len(children) == 1
            child = await manager.wait(children[0]["task_id"], timeout=10)
            assert child["status"] == "completed"
        assert product.get_request(request["id"])["status"] == "completed"
        return {**metrics, "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)}
    finally:
        if event.contains(service_module.engine, "before_cursor_execute", statement):
            event.remove(service_module.engine, "before_cursor_execute", statement)
        product.authorize_runtime_generation = original
        await runtime.close()


def test_live_delta_count_does_not_increase_product_authorization_queries(product):
    async def run():
        empty = await measure_stream(product, deltas=0)
        many = await measure_stream(product, deltas=100, owner="maya")
        assert many["gates"] == empty["gates"]
        assert many["gate_sql"] == empty["gate_sql"]
        assert empty["gate_sql"] > 0  # Request/tool/commit boundaries still run.
    asyncio.run(run())


@pytest.mark.parametrize("worker", [False, True])
def test_tool_progress_does_not_increase_product_authorization_queries(product, worker):
    async def run():
        empty = await measure_stream(product, deltas=0, tool_updates=0, worker=worker)
        many = await measure_stream(product, deltas=0, tool_updates=50, worker=worker, owner="maya")
        assert many["tool_updates"] == 50
        assert many["gates"] == empty["gates"]
        assert many["gate_sql"] == empty["gate_sql"]
        assert empty["gate_sql"] > 0
    asyncio.run(run())


@pytest.mark.parametrize("action", ["stop", "reset"])
def test_stream_can_be_cancelled_without_waiting_for_the_model_to_finish(product, action):
    async def run():
        runtime = RuntimeBridge(product)
        streaming = asyncio.Event()

        def offline(model, context, options):
            stream = AssistantMessageEventStream()

            async def produce():
                stream.push({"type": "start", "partial": assistant_message(model)})
                stream.push({"type": "text_delta", "delta": "preview", "partial": assistant_message(model)})
                streaming.set()
                await options["signal"].wait()
                stream.push({"type": "error", "error": assistant_message(model,
                    content=[], stopReason="aborted", errorMessage="Synthetic provider aborted")})

            stream.task = asyncio.create_task(produce())
            return stream

        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        product.bind_runtime(runtime)
        request = product.send_message("owner-alex", "private-alex", "Synthetic cancellable work", uuid4().hex)["request"]
        task = asyncio.create_task(product.execute_request(request["id"]))
        try:
            await asyncio.wait_for(streaming.wait(), 3)
            if action == "reset":
                product.personal_reset("owner-alex", uuid4().hex, 0)
            else:
                product._cancel_intent("owner-alex", request["id"])
            # Existing worker stop delivery, independent of stream previews.
            await runtime.signal_stop(product.get_request(request["id"]))
            await asyncio.wait_for(task, 3)
            with product.transaction() as db:
                from sqlalchemy import select
                assert db.scalar(select(Message.id).where(Message.request_id == request["id"])) is None
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.close()
    asyncio.run(run())
