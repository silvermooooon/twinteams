"""Real SDK runs, tools and Git Memory against disposable product storage."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

from agent_runtime import AssistantMessageEventStream, assistant_message
from agent_runtime.sessions import SessionInfo
from sqlalchemy import select
from test_memory_dream import product as product  # noqa: PLC0414

from app import personal, personal_bots
from app.models import ReplyRequest
from app.runtime_bridge import RuntimeBridge, text_content


def response(model, content):
    stream = AssistantMessageEventStream()
    stream.push({"type": "done", "message": assistant_message(model, content=content,
        stopReason="toolUse" if any(item["type"] == "toolCall" for item in content) else "stop")})
    return stream


def call(tool_name, **arguments):
    return [{"type": "toolCall", "id": uuid4().hex, "name": tool_name, "arguments": arguments}]


def test_main_dispatches_real_bot_and_reviews_return_with_separate_memory(product):
    bot = personal_bots.create_bot(product, "owner-alex", "Evidence", "Verify claims and retain useful evidence.")
    peer = personal_bots.create_bot(product, "owner-alex", "Planning", "Plan next steps.")

    async def run():
        runtime = RuntimeBridge(product)
        key = "evidence-" + uuid4().hex

        def offline(model, context, options):
            messages = context.messages
            prompt = text_content(next(row for row in reversed(messages) if row["role"] == "user"))
            results = [row for row in messages if row["role"] == "toolResult"]
            if prompt == "SUPERVISE":
                if not results:
                    return response(model, call("dispatch_bot_tasks", bot_ids=[bot["id"]], body="VERIFY"))
                assert not results[-1].get("isError"), results[-1]
                return response(model, [])
            if prompt == "VERIFY":
                system = text_content(messages[0])
                assert "durable personal BOT" in system and "Verify claims" in system
                assert "front-facing Duet" not in system
                if not results:
                    return response(model, call("memory_write", key=key, content="Evidence checked", expected_version=None))
                assert not results[-1].get("isError"), results[-1]
                if not any(result.get("toolName") == "bash" for result in results):
                    return response(model, call("bash", command="pwd"))
                assert bot["chat_id"] in text_content(results[-1])
                return response(model, [{"type": "text", "text": "Verified BOT result"}])
            assert "internal personal agent returned a result" in prompt and "Verified BOT result" in prompt
            return response(model, [{"type": "text", "text": "Reviewed main Duet result"}])

        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        product.bind_runtime(runtime)
        try:
            original = product.send_message("owner-alex", "private-alex", "SUPERVISE", uuid4().hex)["request"]
            await product.execute_request(original["id"])
            assert product.get_request(original["id"])["status"] == "completed"
            with product.transaction() as db:
                work = db.scalar(select(ReplyRequest).where(ReplyRequest.source == "bot_dispatch"))
                work_id = work.id
            declarations = runtime._tools(product.get_runtime_context(work_id), SimpleNamespace(snapshot={}, resumable=False))
            names = {tool.name for tool in declarations}
            assert {"message_personal_agent", "post_bot_room"} <= names
            assert not {"reply_as_owner", "post_duet_reply", "create_chat", "send_chat_message", "list_people"} & names
            await product.execute_request(work_id)
            with product.transaction() as db:
                work = db.get(ReplyRequest, work_id)
                assert work.status == "completed", work.error
            with product.transaction() as db:
                returned = db.scalar(select(ReplyRequest).where(ReplyRequest.source == "bot_result"))
                assert returned.continuation_of_request_id == original["id"]
                assert returned.session_id == original["session_id"]
                returned_id = returned.id
            await product.execute_request(returned_id)
            assert product.get_request(returned_id)["status"] == "completed"
            main = product.chat_detail("owner-alex", "private-alex")["messages"]
            assert [row["body"] for row in main] == ["SUPERVISE", "VERIFY", "Verified BOT result", "Reviewed main Duet result"]
            assert main[2]["author_id"] == bot["id"] and main[2]["coordination"]["kind"] == "result"
            identity = SessionInfo("inspect-memory", runtime.tenant_id, "owner-alex")
            own = personal.make_bot_memory_store(identity, bot["id"])
            other = personal.make_bot_memory_store(identity, peer["id"])
            main_memory = personal.make_memory_store(identity)
            assert (await own.read(key)).content == "Evidence checked"
            assert await other.read(key) is None and await main_memory.read(key) is None
        finally:
            await runtime.close()

    asyncio.run(run())


def test_direct_bot_and_room_run_as_selected_agents(product):
    first = personal_bots.create_bot(product, "owner-alex", "Evidence", "Verify evidence.")
    second = personal_bots.create_bot(product, "owner-alex", "Planning", "Plan delivery.")
    room = personal_bots.create_room(product, "owner-alex", "Delivery", [first["id"], second["id"]])

    async def run():
        runtime = RuntimeBridge(product)
        def offline(model, context, options):
            prompt = text_content(next(row for row in reversed(context.messages) if row["role"] == "user"))
            if prompt == "Collaborate":
                system = text_content(context.messages[0])
                assert all(actor_id in system for actor_id in room["member_actor_ids"])
            return response(model, [{"type": "text", "text": "Ready for the assigned work."}])
        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        product.bind_runtime(runtime)
        try:
            request = product.send_message("owner-alex", first["chat_id"], "Hello", uuid4().hex)["request"]
            await product.execute_request(request["id"])
            assert product.get_request(request["id"])["status"] == "completed"
            detail = product.chat_detail("owner-alex", first["chat_id"])
            assert detail["messages"][-1]["author_id"] == first["id"]
            product.send_message("owner-alex", room["id"], "Collaborate", uuid4().hex,
                                 recipient_actor_ids=room["member_actor_ids"])
            with product.transaction() as db:
                ids = list(db.scalars(select(ReplyRequest.id).where(ReplyRequest.chat_id == room["id"])))
            await asyncio.gather(*(product.execute_request(request_id) for request_id in ids))
            assert all(product.get_request(request_id)["status"] == "completed" for request_id in ids)
            messages = product.chat_detail("owner-alex", room["id"])["messages"]
            assert len(messages) == 4
            assert {row["author_id"] for row in messages[1:]} == set(room["member_actor_ids"])
            assert product.activity_page("owner-alex", first["chat_id"])["items"]
            assert len(product.activity_page("owner-alex", room["id"])["items"]) == 3
        finally:
            await runtime.close()
    asyncio.run(run())


def test_bot_yields_to_temporary_worker_before_returning_reviewed_result(product):
    bot = personal_bots.create_bot(product, "owner-alex", "Evidence", "Verify evidence.")

    async def run():
        runtime = RuntimeBridge(product)
        release = asyncio.Event()
        producers = []

        def offline(model, context, options):
            prompt = text_content(next(row for row in reversed(context.messages) if row["role"] == "user"))
            results = [row for row in context.messages if row["role"] == "toolResult"]
            if prompt == "BOT-WORK":
                if not results:
                    return response(model, call("spawn_agent", name="worker", task="TEMPORARY-WORK"))
                assert not results[-1].get("isError"), results[-1]
                return response(model, [])
            if prompt == "TEMPORARY-WORK":
                stream = AssistantMessageEventStream()
                async def finish():
                    await release.wait()
                    stream.push({"type": "done", "message": assistant_message(model,
                        content=[{"type": "text", "text": "Worker evidence"}], stopReason="stop")})
                producers.append(asyncio.create_task(finish()))
                return stream
            assert "Worker evidence" in prompt
            return response(model, [{"type": "text", "text": "BOT reviewed worker evidence"}])

        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        product.bind_runtime(runtime)
        try:
            accepted = personal_bots.dispatch_tasks(product, "owner-alex", [bot["id"]], "BOT-WORK", uuid4().hex)
            await product.execute_request(accepted["request_ids"][0])
            with product.transaction() as db:
                request = db.get(ReplyRequest, accepted["request_ids"][0])
                assert request.status == "completed", request.error
            main = product.chat_detail("owner-alex", "private-alex")["messages"]
            assert not any((row.get("coordination") or {}).get("kind") == "result" for row in main)
            release.set()
            for _ in range(200):
                await runtime.drain_child_results()
                product.process_pending_child_results()
                with product.transaction() as db:
                    continuation = db.scalar(select(ReplyRequest).where(ReplyRequest.source == "subagent_result"))
                    continuation_id = continuation.id if continuation else None
                if continuation_id:
                    break
                await asyncio.sleep(.02)
            assert continuation_id
            await product.execute_request(continuation_id)
            with product.transaction() as db:
                continuation = db.get(ReplyRequest, continuation_id)
                assert continuation.status == "completed", continuation.error
            returned = [row for row in product.chat_detail("owner-alex", "private-alex")["messages"]
                        if (row.get("coordination") or {}).get("kind") == "result"]
            assert [row["body"] for row in returned] == ["BOT reviewed worker evidence"]
            assert len(personal_bots.list_bots(product, "owner-alex")) == 1
        finally:
            release.set()
            await asyncio.gather(*producers)
            await runtime.close()
    asyncio.run(run())
