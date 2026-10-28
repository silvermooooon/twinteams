"""Independent SDK payload, task delivery and silent reaction checks; no live provider."""
import asyncio
from copy import copy
from uuid import uuid4

import pytest
from agent_runtime import Agent, AssistantMessageEventStream, assistant_message
from agent_runtime.sessions import DatabaseSession
from agent_runtime.subagents import create_subagent_tools
from agent_runtime.transcript import current_tools
from duet_test_support import append_duet, approved_chat, request_duet_reply
from sqlalchemy import func, select
from test_memory_dream import product as product  # noqa: PLC0414

from app.models import Message, MessageReaction, ReplyRequest, RoutingDecision
from app.runtime_bridge import RuntimeBridge, text_content


def response(model, content=()):
    stream = AssistantMessageEventStream()
    stream.push({"type": "done", "message": assistant_message(model, content=list(content),
        stopReason="toolUse" if any(part["type"] == "toolCall" for part in content) else "stop")})
    return stream


def call(tool_name, **arguments):
    return [{"type": "toolCall", "id": uuid4().hex, "name": tool_name, "arguments": arguments}]


@pytest.mark.parametrize("kind", ["private", "duet"])
def test_actual_reaction_only_turn_is_persisted_and_does_not_create_a_conversation_loop(product, kind):
    async def run():
        if kind == "private":
            sent = product.send_message("owner-alex", "private-alex", "Thanks, that helped", uuid4().hex)
            request, message_id, chat_id = sent["request"], sent["message"]["id"], "private-alex"
        else:
            chat_id = approved_chat(product, "duet-social-integration")
            message_id = append_duet(product, chat_id, "owner-maya", "Thanks, that helped")["id"]
            request = request_duet_reply(product, "owner-maya", chat_id, message_id, "twin-alex")
        with product.transaction() as db:
            before = tuple(db.scalar(select(func.count()).select_from(table)) for table in (Message, ReplyRequest, RoutingDecision))
        runtime = RuntimeBridge(product)
        calls = []

        def offline(model, context, options):
            calls.append(context)
            tools = {tool["name"]: tool for tool in current_tools(context.messages)}
            assert tools["set_message_reaction"]["parameters"]["required"] == ["message_id", "emoji", "active"]
            return response(model, call("set_message_reaction", message_id=message_id, emoji="❤️", active=True) if len(calls) == 1 else [])

        runtime.models.register_api("offline-regression", offline)
        await runtime.start(); product.bind_runtime(runtime)
        try:
            await product.execute_request(request["id"])
            assert product.get_request(request["id"])["status"] == "completed", product.get_request(request["id"])
            assert len(calls) == 2
            with product.transaction() as db:
                reaction = db.get(MessageReaction, (message_id, "twin-alex", "❤️"))
                assert reaction is not None
                assert not db.scalar(select(Message.id).where(Message.request_id == request["id"]))
                assert tuple(db.scalar(select(func.count()).select_from(table)) for table in (Message, ReplyRequest, RoutingDecision)) == before
            await product.execute_request(request["id"])
            assert len(calls) == 2
        finally:
            await runtime.close()
    asyncio.run(run())


def test_real_spawn_payload_matches_worker_and_completion_returns_only_to_original_task(product):
    async def run():
        runtime = RuntimeBridge(product)
        worker_started, release = asyncio.Event(), asyncio.Event()
        payloads, producers = {}, []

        def offline(model, context, options):
            user = text_content(next(row for row in reversed(context.messages) if row["role"] == "user"))
            payloads.setdefault(user, context)
            if user == "WORKER-ASSIGNMENT":
                stream = AssistantMessageEventStream()
                async def finish():
                    worker_started.set()
                    await release.wait()
                    stream.push({"type": "done", "message": assistant_message(model,
                        content=[{"type": "text", "text": "VERIFIED-CHILD-RESULT"}], stopReason="stop")})
                producers.append(asyncio.create_task(finish()))
                return stream
            if user == "ORIGINAL-TASK":
                results = [row for row in context.messages if row["role"] == "toolResult"]
                return response(model, [] if results else call("spawn_agent", name="worker", task="WORKER-ASSIGNMENT"))
            if user == "UNRELATED-TASK":
                return response(model, [{"type": "text", "text": "UNRELATED-RESULT"}])
            assert "VERIFIED-CHILD-RESULT" in user
            return response(model, [{"type": "text", "text": "ORIGINAL-REVIEWED-RESULT"}])

        runtime.models.register_api("offline-regression", offline)
        await runtime.start(); product.bind_runtime(runtime)
        try:
            original = product.send_message("owner-alex", "private-alex", "ORIGINAL-TASK", uuid4().hex)["request"]
            await product.execute_request(original["id"])
            with product.transaction() as db:
                original_error = db.get(ReplyRequest, original["id"]).error
            assert product.get_request(original["id"])["status"] == "completed", original_error
            await asyncio.wait_for(worker_started.wait(), 10)
            unrelated = product.send_message("owner-alex", "private-alex", "UNRELATED-TASK", uuid4().hex)["request"]
            await product.execute_request(unrelated["id"])
            main = {tool["name"]: tool for tool in current_tools(payloads["ORIGINAL-TASK"].messages)}
            worker = {tool["name"]: tool for tool in current_tools(payloads["WORKER-ASSIGNMENT"].messages)}
            manager = runtime.managers[original["session_id"], bool(original["background"])]
            sdk_tools = {tool.name: tool for tool in create_subagent_tools(manager)}
            for name in ("spawn_agent", "get_agent", "wait_agent"):
                assert main[name] == worker[name]
                assert main[name]["parameters"] == sdk_tools[name].parameters
                assert main[name]["description"] != sdk_tools[name].description
            assert set(sdk_tools).issubset(main) and set(sdk_tools).issubset(worker)
            release.set()
            async with asyncio.timeout(15):
                while True:
                    await runtime.recover_background()
                    with product.transaction() as db:
                        rows = list(db.scalars(select(ReplyRequest).where(ReplyRequest.source == "subagent_result")))
                    if rows:
                        break
                    await asyncio.sleep(0.02)
            assert len(rows) == 1
            continuation = rows[0]
            assert continuation.continuation_of_request_id == original["id"]
            assert continuation.session_id == original["session_id"] != unrelated["session_id"]
            await product.execute_request(continuation.id)
            await runtime.recover_background()
            with product.transaction() as db:
                assert db.scalar(select(func.count()).select_from(ReplyRequest).where(ReplyRequest.source == "subagent_result")) == 1
                assert list(db.scalars(select(Message.body).where(Message.request_id == continuation.id))) == ["ORIGINAL-REVIEWED-RESULT"]
        finally:
            release.set()
            await asyncio.gather(*producers, return_exceptions=True)
            await runtime.close()
    asyncio.run(run())


def test_actual_model_payload_preserves_frozen_delegation_declarations_until_new_turn(product):
    async def run():
        runtime = RuntimeBridge(product)
        await runtime.start()
        request = product.send_message("owner-alex", "private-alex", "Contract recovery", uuid4().hex)["request"]
        context = product.get_runtime_context(request["id"])
        session = DatabaseSession(runtime.store, tenant_id=runtime.tenant_id,
            user_id="owner-alex", session_id=request["session_id"])
        entered, captured = asyncio.Event(), []
        async def blocked(model, payload, options):
            captured.append(payload)
            entered.set()
            await asyncio.Event().wait()
        def finish(model, payload, options):
            captured.append(payload)
            return response(model)
        try:
            first = Agent(config=runtime.config, models=runtime.models, session=session,
                tools=runtime._tools(context, session), stream_fn=blocked)
            await runtime._attach_subagents(first, context)
            # A genuine older declaration is frozen before the model-phase loss.
            for index, tool in enumerate(first.state.tools):
                if tool.name == "spawn_agent":
                    old = copy(tool)
                    old.description = "Earlier installed delegation contract"
                    old.version = "earlier-platform-contract"
                    first.state.tools[index] = old
            task = asyncio.create_task(first.prompt("Accepted original input"))
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            restored = await DatabaseSession.open(runtime.store, tenant_id=runtime.tenant_id, session_id=request["session_id"])
            assert restored.resumable
            resumed = Agent(config=runtime.config, models=runtime.models, session=restored,
                tools=runtime._tools(context, restored), stream_fn=finish)
            await runtime._attach_subagents(resumed, context)
            await resumed.resume()
            frozen = {tool["name"]: tool for tool in current_tools(captured[0].messages)}
            recovered = {tool["name"]: tool for tool in current_tools(captured[1].messages)}
            assert frozen == recovered
            assert recovered["spawn_agent"]["description"] == "Earlier installed delegation contract"
            fresh = Agent(config=runtime.config, models=runtime.models, session=restored,
                tools=runtime._tools(context, restored), stream_fn=finish)
            await runtime._attach_subagents(fresh, context)
            await fresh.prompt("New turn with current contract")
            current = {tool["name"]: tool for tool in current_tools(captured[2].messages)}
            assert current["spawn_agent"]["description"] != frozen["spawn_agent"]["description"]
            assert current["spawn_agent"]["parameters"] == frozen["spawn_agent"]["parameters"]
        finally:
            await runtime.close()
    asyncio.run(run())
