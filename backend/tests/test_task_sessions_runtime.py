"""Concurrent chat tasks use real SDK leases and disposable PostgreSQL, offline."""
import asyncio
from uuid import uuid4

import pytest
from agent_runtime import AssistantMessageEventStream, assistant_message
from agent_runtime.memory import MemoryConflict, MemorySource
from agent_runtime.sessions import SessionInfo
from sqlalchemy import select
from test_memory_dream import product as product  # noqa: PLC0414

from app import personal, questions
from app.models import ReplyRequest
from app.question_models import QuestionRequest
from app.runtime_bridge import RuntimeBridge, RuntimeBusy, text_content


@pytest.mark.parametrize("cancel_first", [False, True])
def test_same_private_tasks_run_independently_and_stop_is_request_scoped(product, cancel_first):
    async def run():
        runtime = RuntimeBridge(product)
        first_started, release = asyncio.Event(), asyncio.Event()
        contexts, producers = {}, []

        def offline(model, context, options):
            task = text_content(next(message for message in reversed(context.messages) if message["role"] == "user"))
            contexts[task] = context.messages
            stream = AssistantMessageEventStream()

            async def finish():
                if task == "OLD-TASK":
                    first_started.set()
                    await release.wait()
                stream.push({"type": "done", "message": assistant_message(model,
                    content=[{"type": "text", "text": task + "-RESULT"}], stopReason="stop")})
            producers.append(asyncio.create_task(finish()))
            return stream

        runtime.models.register_api("offline-regression", offline)
        await runtime.start(); product.bind_runtime(runtime)
        first = product.send_message("owner-alex", "private-alex", "OLD-TASK", uuid4().hex)["request"]
        pending = asyncio.create_task(product.execute_request(first["id"]))
        try:
            await asyncio.wait_for(first_started.wait(), 10)
            second = product.send_message("owner-alex", "private-alex", "NEW-TASK", uuid4().hex)["request"]
            await asyncio.wait_for(product.execute_request(second["id"]), 15)
            second = product.get_request(second["id"])
            first = product.get_request(first["id"])
            assert second["status"] == "completed" and first["status"] == "running"
            assert first["session_id"] != second["session_id"]
            assert set(runtime.managers) >= {(first["session_id"], False), (second["session_id"], False)}
            assert "Other active tasks and workers" in str(contexts["NEW-TASK"])
            assert first["id"] in str(contexts["NEW-TASK"])
            second_records = await runtime._run_records(runtime.tenant_id, second["session_id"], second["run_id"])
            inputs = next(row["data"]["inputs"] for row in second_records if row["type"] == "run_started")
            assert [text_content(message) for message in inputs if message.get("role") == "user"] == ["NEW-TASK"]
            if cancel_first:
                await product.cancel_request("owner-alex", first["id"])
                await runtime.signal_stop(first)
            release.set()
            await asyncio.wait_for(pending, 15)
            if cancel_first:
                assert product.get_request(first["id"])["status"] == "cancelled"
            else:
                assert product.get_request(first["id"])["status"] == "completed"
            assert product.get_request(second["id"])["status"] == "completed"
            messages = product.chat_detail("owner-alex", "private-alex")["messages"]
            assert sum(message["body"] == "NEW-TASK-RESULT" for message in messages) == 1
            assert sum(message["body"] == "OLD-TASK-RESULT" for message in messages) == (not cancel_first)
        finally:
            release.set()
            await asyncio.gather(pending, *producers, return_exceptions=True)
            await runtime.close()
    asyncio.run(run())


def test_legacy_accepted_sdk_command_keeps_its_session_and_immutable_input(product):
    async def run():
        runtime = RuntimeBridge(product)
        calls = []
        def offline(model, context, options):
            calls.append(context.messages)
            stream = AssistantMessageEventStream()
            stream.push({"type": "done", "message": assistant_message(model,
                content=[{"type": "text", "text": "Legacy task recovered"}], stopReason="stop")})
            return stream
        runtime.models.register_api("offline-regression", offline)
        await runtime.start(); product.bind_runtime(runtime)
        request = product.send_message("owner-alex", "private-alex", "Original task", uuid4().hex)["request"]
        with product.transaction() as db:
            db.get(ReplyRequest, request["id"]).session_id = None
        sid = RuntimeBridge.session_id("private-alex", "twin-alex")
        original = {"role": "user", "content": [{"type": "text", "text": "FROZEN-ACCEPTED-INPUT"}], "timestamp": 0}
        try:
            await runtime.store.accept_command(runtime.tenant_id, sid, user_id="owner-alex",
                command_id=request["id"], kind="prompt", payload={"message": original})
            await product.execute_request(request["id"])
            restored = product.get_request(request["id"])
            assert restored["status"] == "completed" and restored["session_id"] == sid
            assert text_content(calls[0][-1]) == "FROZEN-ACCEPTED-INPUT"
            await product.execute_request(request["id"])
            assert len(calls) == 1
        finally:
            await runtime.close()
    asyncio.run(run())


def test_answer_waits_only_for_its_origin_while_unrelated_private_task_finishes(product):
    async def run():
        runtime = RuntimeBridge(product)
        settling, release = asyncio.Event(), asyncio.Event()
        producers = []
        def offline(model, context, options):
            user = text_content(next(message for message in reversed(context.messages) if message["role"] == "user"))
            stream = AssistantMessageEventStream()
            async def finish():
                if user == "ORIGINAL-QUESTION-TASK":
                    if not any(message.get("role") == "toolResult" for message in context.messages):
                        content = [{"type": "toolCall", "id": "question", "name": "ask_question",
                                    "arguments": {"question": "Which format?", "options": ["Short", "Detailed"]}}]
                        stop = "toolUse"
                    else:
                        settling.set()
                        await release.wait()
                        content, stop = [], "stop"
                else:
                    content, stop = [{"type": "text", "text": "ANSWER-CONTINUED" if user.startswith("The owner answered") else "UNRELATED-COMPLETED"}], "stop"
                stream.push({"type": "done", "message": assistant_message(model, content=content, stopReason=stop)})
            producers.append(asyncio.create_task(finish()))
            return stream
        runtime.models.register_api("offline-regression", offline)
        await runtime.start(); product.bind_runtime(runtime)
        original = product.send_message("owner-alex", "private-alex", "ORIGINAL-QUESTION-TASK", uuid4().hex)["request"]
        pending = asyncio.create_task(product.execute_request(original["id"]))
        try:
            await asyncio.wait_for(settling.wait(), 10)
            with product.transaction() as db:
                question_id = db.scalar(select(QuestionRequest.id))
            answered = questions.answer(product, "owner-alex", question_id, "Detailed")
            assert answered["status"] == "answered"
            with product.transaction() as db:
                continuation_id = db.get(QuestionRequest, question_id).continuation_request_id
            await product.execute_request(continuation_id)
            assert product.get_request(continuation_id)["status"] == "queued"
            assert await runtime.store.get_command(runtime.tenant_id, continuation_id) is None
            unrelated = product.send_message("owner-alex", "private-alex", "UNRELATED-QUICK-TASK", uuid4().hex)["request"]
            await asyncio.wait_for(product.execute_request(unrelated["id"]), 15)
            assert product.get_request(unrelated["id"])["status"] == "completed"
            assert product.get_request(original["id"])["status"] == "running"
            release.set()
            await asyncio.wait_for(pending, 15)
            product.deferred.pop(continuation_id, None)
            await product.execute_request(continuation_id)
            continued = product.get_request(continuation_id)
            assert continued["status"] == "completed"
            assert continued["session_id"] == product.get_request(original["id"])["session_id"]
            assert continued["session_id"] != product.get_request(unrelated["id"])["session_id"]
            detail = product.chat_detail("owner-alex", "private-alex")
            assert sum(message["body"] == "ANSWER-CONTINUED" for message in detail["messages"]) == 1
        finally:
            release.set()
            await asyncio.gather(pending, *producers, return_exceptions=True)
            await runtime.close()
    asyncio.run(run())


def test_distinct_task_sessions_share_only_their_owners_git_memory(product, monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_MEMORY_DIR", str(tmp_path / "memory"))
    async def run():
        first, second, outsider = [personal.make_memory_store(SessionInfo(sid, "local", owner))
            for sid, owner in (("task-one", "owner-alex"), ("task-two", "owner-alex"), ("task-other", "owner-maya"))]
        assert first.remote == second.remote and first.remote != outsider.remote
        source = [MemorySource("task-one", "verified-fact")]
        await asyncio.gather(first.write("task-one", "First verified result", sources=source, expected_version=None),
                             second.write("task-two", "Second verified result", sources=source, expected_version=None))
        assert (await first.read("task-two")).content == "Second verified result"
        assert await outsider.read("task-one") is None
        current = await first.read("task-one")
        await second.write("task-one", "Updated result", sources=source, expected_version=current.version)
        with pytest.raises(MemoryConflict):
            await first.write("task-one", "Stale overwrite", sources=source, expected_version=current.version)
        assert (await first.read("task-one")).content == "Updated result"
    asyncio.run(run())


@pytest.mark.parametrize("incomplete", [False, True])
def test_checkpoint_contention_uses_complete_authorized_context_without_waiting(monkeypatch, incomplete):
    class ContextService:
        async def db_call(self, name, scope):
            if name == "prepare_conversation_summary":
                return {"scope": scope, "messages": []}
            assert name == "get_conversation_context"
            return {"summary": {"body": "Already authorized checkpoint"}, "messages": [],
                    "context_incomplete": incomplete}
    async def busy(prepared):
        raise RuntimeBusy("Same checkpoint is active")
    async def run():
        runtime = RuntimeBridge(ContextService())
        monkeypatch.setattr(runtime, "_summarize_conversation", busy)
        context = {"_summary_scope": {"chat_id": "private"}, "context_incomplete": True, "owner_id": "owner"}
        if incomplete:
            with pytest.raises(RuntimeBusy):
                await runtime._maintain_conversation_context(context)
        else:
            result = await runtime._maintain_conversation_context(context)
            assert result["summary"]["body"] == "Already authorized checkpoint"
            assert result["owner_id"] == "owner" and not result["context_incomplete"]
    asyncio.run(run())
