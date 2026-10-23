"""Independent contexts share actual owner capabilities; all execution is offline."""
import asyncio
from uuid import uuid4

import pytest
from agent_runtime import AssistantMessageEventStream, assistant_message
from agent_runtime.memory import MemoryConflict
from agent_runtime.sessions import SessionInfo
from fastapi import HTTPException
from sqlalchemy import select
from test_credentials import product as product  # noqa: PLC0414

from app import personal
from app.models import Actor, Message, ReplyRequest
from app.runtime_bridge import RuntimeBridge


def text(message):
    content = message.get("content", "")
    return content if isinstance(content, str) else "\n".join(p.get("text", "") for p in content)


def response(model, content):
    stream = AssistantMessageEventStream()
    stream.push({"type": "done", "message": assistant_message(model, content=content,
        stopReason="toolUse" if any(p["type"] == "toolCall" for p in content) else "stop")})
    return stream


def call(tool_name, **args):
    return [{"type": "toolCall", "id": uuid4().hex, "name": tool_name, "arguments": args}]


@pytest.mark.parametrize("restart", [False, True, "stop"])
def test_nested_result_resumes_actual_parent_with_shared_git_memory_and_recovery(product, monkeypatch, restart):
    async def run():
        runtime = RuntimeBridge(product)
        key = "nested-" + uuid4().hex
        captured = []
        worker_blocked, release_grandchild = asyncio.Event(), asyncio.Event()
        def delayed(model, options, *, abort=False):
            stream = AssistantMessageEventStream()
            async def produce():
                await (options["signal"].wait() if abort else release_grandchild.wait())
                if abort:
                    stream.push({"type": "error", "error": assistant_message(model, content=[], stopReason="aborted", errorMessage="Synthetic provider aborted")})
                else:
                    stream.push({"type": "done", "message": assistant_message(model, content=[{"type": "text", "text": "GRAND-RESULT"}], stopReason="stop")})
            stream.task = asyncio.create_task(produce())
            return stream
        def offline(model, context, options):
            messages = context.messages
            user = text(next(m for m in reversed(messages) if m["role"] == "user"))
            results = [m for m in messages if m["role"] == "toolResult"]
            captured.append((user, context))
            if "GRAND-TASK" == user:
                if not results:
                    return response(model, call("memory_write", key=key, content="Owner prefers concise factual updates.", expected_version=None))
                assert not results[-1].get("isError"), results[-1]
                if restart == "stop":
                    return delayed(model, options)
                return response(model, [{"type": "text", "text": "GRAND-RESULT"}])
            if user == "WORKER-TASK":
                if not results:
                    return response(model, call("spawn_agent", name="worker", task="GRAND-TASK"))
                if restart == "stop":
                    worker_blocked.set()
                    return delayed(model, options, abort=True)
                return response(model, [])
            if user.startswith("A delegated child returned"):
                assert "GRAND-RESULT" in user
                assert any(m["role"] == "user" and text(m) == "WORKER-TASK" for m in messages)
                assert any(m.get("toolName") == "spawn_agent" for m in results)
                assert "ROOT-TASK:" not in text(messages[0])
                if not any(m.get("toolName") == "memory_read" for m in results):
                    return response(model, call("memory_read", key=key))
                assert "concise factual" in text(results[-1])
                return response(model, [{"type": "text", "text": "WORKER-REVIEWED-RESULT"}])
            if "WORKER-REVIEWED-RESULT" in user:
                if not any(m.get("toolName") == "memory_read" for m in results):
                    return response(model, call("memory_read", key=key))
                assert "concise factual" in text(results[-1])
                return response(model, [{"type": "text", "text": "ROOT-REVIEWED-RESULT"}])
            if not results:
                return response(model, call("spawn_agent", name="worker", task="WORKER-TASK"))
            return response(model, [])
        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        product.bind_runtime(runtime)
        try:
            if restart:
                async def retain_only(*args, **kwargs):
                    return
                monkeypatch.setattr(runtime, "_deliver_child_result", retain_only)
            request = product.send_message("owner-alex", "private-alex", "ROOT-TASK: delegate and save my stated preference.", uuid4().hex)["request"]
            await product.execute_request(request["id"])
            original = product.get_request(request["id"])
            with product.transaction() as db:
                error = db.get(ReplyRequest, request["id"]).error
            assert original["status"] == "completed", error
            if restart:
                if restart == "stop":
                    await asyncio.wait_for(worker_blocked.wait(), 10)
                    manager = runtime.managers[original["session_id"]]
                    children = await manager.aget()
                    await asyncio.wait_for(manager.stop(children[0]["task_id"]), 5)
                    release_grandchild.set()
                async def retained():
                    for _ in range(300):
                        notes = await runtime.store.pending_child_results()
                        if len(notes) >= 2:
                            return
                        await asyncio.sleep(.02)
                    raise AssertionError("child terminal notices were not retained")
                await asyncio.wait_for(retained(), 15)
                assert await runtime.has_unresolved_scheduled_work(original)
                if restart == "stop":
                    notes = await runtime.store.pending_child_results()
                    root_note = next(n for n in notes if n["payload"]["parent_session_id"] == original["session_id"])
                    worker_id = root_note["payload"]["child_session_id"]
                    grand_note = next(n for n in notes if n["payload"]["parent_session_id"] == worker_id)
                    before = len(captured)
                    async def unread(*args):
                        raise AssertionError("stopped-parent result must retire before reading output")
                    monkeypatch.setattr(runtime, "_run_records", unread)
                    await RuntimeBridge._deliver_child_result(runtime, grand_note)
                    assert (await runtime.store.get_command("local", grand_note["command_id"]))["state"] == "completed"
                    commands = await runtime.store.list_commands("local", worker_id, kind="prompt")
                    assert len(commands) == 1 and len(captured) == before
                    return
                await runtime.close()
                runtime = RuntimeBridge(product)
                runtime.models.register_api("offline-regression", offline)
                await runtime.start()
                product.bind_runtime(runtime)
            async def settle():
                for _ in range(300):
                    await runtime.recover_background()
                    with product.transaction() as db:
                        rows = db.scalars(select(ReplyRequest).where(ReplyRequest.source == "subagent_result", ReplyRequest.causal_chain_id == request["causal_chain_id"])).all()
                        ids = [row.id for row in rows]
                    if ids:
                        return ids
                    await asyncio.sleep(.02)
                raise AssertionError("nested result never reached root FIFO")
            ids = await asyncio.wait_for(settle(), 20)
            assert len(ids) == 1
            await product.execute_request(ids[0])
            with product.transaction() as db:
                outputs = db.scalars(select(Message).where(Message.request_id == ids[0])).all()
                assert [row.body for row in outputs] == ["ROOT-REVIEWED-RESULT"]
                root_continuation = db.get(ReplyRequest, ids[0])
                assert root_continuation.causal_chain_id == request["causal_chain_id"]
            owner_store = personal.make_memory_store(SessionInfo("parent-reader", "local", "owner-alex"))
            entry = await owner_store.read(key)
            assert entry.content == "Owner prefers concise factual updates."
            source = entry.sources[0]
            from agent_runtime.sessions import DatabaseSession
            grand_session = await DatabaseSession.open(runtime.store, tenant_id="local", session_id=source.session_id)
            assert grand_session.info.user_id == "owner-alex"
            assert source.event_id
            assert await personal.make_memory_store(SessionInfo("other", "local", "owner-maya")).read(key) is None
            # Two independent stores retain native same-key CAS; no parent-only cache.
            sibling = personal.make_memory_store(SessionInfo("sibling", "local", "owner-alex"))
            stale = await sibling.read(key)
            await owner_store.write(key, "Reconciled owner preference", sources=entry.sources, expected_version=entry.version, operation_id=uuid4().hex)
            with pytest.raises(MemoryConflict):
                await sibling.write(key, "Stale overwrite", sources=entry.sources, expected_version=stale.version, operation_id=uuid4().hex)
            assert (await sibling.read(key)).content == "Reconciled owner preference"
            worker_calls = [ctx for user, ctx in captured if user == "WORKER-TASK"]
            assert worker_calls and all("ROOT-TASK:" not in text(ctx.messages[0]) for ctx in worker_calls)
            assert len([u for u, _ in captured if u.startswith("A delegated child returned")]) == 2
        finally:
            await runtime.close()
    asyncio.run(run())


@pytest.mark.parametrize("revocation", ["cancel", "withdraw"])
def test_group_child_memory_write_checks_current_root_authorization(product, revocation):
    async def run():
        runtime = RuntimeBridge(product)
        await runtime.start()
        seen = []
        def offline(model, context, options):
            return response(model, [])
        runtime.models.register_api("offline-regression", offline)
        try:
            with product.transaction() as db:
                chat, _ = product.membership(db, "owner-alex", "group-alex-maya")
                source = product.append_message(db, chat, author_id=db.scalar(select(Actor.id).where(Actor.owner_id == "owner-maya", Actor.kind == "human")), kind="human", body="Authorized group task")
                request = product.accept_request(db, chat, product.twin(db, "owner-alex").id, "owner-maya", "automatic", uuid4().hex, source_message_id=source.id)
                request_id, source_id = request.id, source.id
            result = await runtime.execute(product.get_request(request_id), product.get_runtime_context(request_id))
            product.complete_request(request_id, result)
            from agent_runtime.sessions import DatabaseSession
            child = DatabaseSession(runtime.store, tenant_id="local", user_id="owner-alex", session_id=uuid4().hex)
            await child.commit("subagent_created", parent_session_id=result["session_id"], parent_run_id=result["run_id"], root_run_id=result["session_id"] + "/" + result["run_id"], name="worker", task="Authorized same-owner work", operation_id=uuid4().hex)
            agent = await runtime._child_factory(child)
            assert {"memory_write", "bash", "spawn_agent"} <= {t.name for t in agent.state.tools}
            with product.transaction() as db:
                if revocation == "cancel":
                    db.get(ReplyRequest, request_id).cancel_requested = True
                else:
                    db.get(Message, source_id).state = "withdrawn"
            with pytest.raises((HTTPException, PermissionError)):
                await runtime._check_generation(await runtime._background_context(result["session_id"], result["run_id"]))
            runtime.models.register_api("offline-regression", lambda *args: seen.append(True))
            # The model boundary fails before any memory operation/provider call.
            with pytest.raises(HTTPException):
                await agent.prompt("Save an unauthorized preference")
            assert not seen
            assert await personal.make_memory_store(child.info).read("unauthorized") is None
        finally:
            await runtime.close()
    asyncio.run(run())
