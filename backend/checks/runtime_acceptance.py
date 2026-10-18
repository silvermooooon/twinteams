"""Bounded acceptance of invisible integration contracts using an isolated PostgreSQL DB.

This is a deterministic offline adapter used only by this check. The application
never falls back to it. No provider request, API charge, or production DB is used.
Run: .venv/bin/python checks/runtime_acceptance.py
"""
from __future__ import annotations
import asyncio
import os
from pathlib import Path
import sys
import tempfile
from uuid import uuid4

TEMP = tempfile.TemporaryDirectory(prefix="twinteams-runtime-acceptance-")
os.environ["TWINTEAMS_ENV"] = "local"
os.environ["TWINTEAMS_DEVELOPMENT_MODE"] = "true"
os.environ["TWINTEAMS_WORKSPACE_ROOT"] = str(Path(TEMP.name) / "workspace")
os.environ["AGENT_MEMORY_DIR"] = str(Path(TEMP.name) / "memory")
os.environ["AGENT_API"] = "offline-acceptance"
os.environ["OPENAI_API_KEY"] = "offline-adapter-no-network-no-real-key"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from postgres import TestDatabase
DATABASE = TestDatabase().configure()

from agent_runtime import AssistantMessageEventStream, assistant_message
from app.service import service
from app.runtime_bridge import RuntimeBridge, text_content
from app.db import engine

CALLS = []

async def offline(model, context, options):
    prompt = next((text_content(message) for message in reversed(context.messages) if message.get("role") == "user"), "")
    CALLS.append(prompt)
    if prompt == "FAIL_PROVIDER":
        raise RuntimeError("Controlled acceptance provider failure")
    if prompt == "SLOW_CANCEL":
        await options["signal"].run(asyncio.sleep(20))
    tools = [message for message in context.messages if message.get("role") == "toolResult"]
    if prompt == "RAW_OWNER_FOR_REPLY_MODE" and not tools:
        content = [{"type": "text", "text": "Acceptance intermediate reformulation."},
                   {"type": "toolCall", "id": "read-for-rewrite", "name": "list_tracking", "arguments": {}}]
        stop = "toolUse"
    elif prompt == "DELEGATE_CHILD" and not tools:
        content = [{"type": "toolCall", "id": "child-start", "name": "spawn_agent", "arguments": {"name": "worker", "task": "CHILD_TASK"}}]
        stop = "toolUse"
    elif prompt == "DELEGATE_CHILD":
        content, stop = [{"type": "text", "text": "Background work accepted; the main reply is complete."}], "stop"
    elif prompt == "CHILD_TASK":
        await asyncio.sleep(.15)
        content, stop = [{"type": "text", "text": "Child completed the bounded acceptance task."}], "stop"
    elif prompt.startswith("A background worker returned"):
        content, stop = [{"type": "text", "text": "The background result has been summarized for this chat."}], "stop"
    else:
        content, stop = [{"type": "text", "text": "A complete deterministic acceptance response."}], "stop"
    stream = AssistantMessageEventStream()
    stream.push({"type": "done", "message": assistant_message(model, content=content, stopReason=stop)})
    return stream


def submit(body, *, reply_for_me=False):
    return service.send_message("owner-alex", "private-alex", body, uuid4().hex, reply_for_me=reply_for_me)["request"]["id"]


async def dispatch():
    tasks = []
    for request_id in service.claim_dispatches():
        task = asyncio.create_task(service.execute_request(request_id))
        service.tasks[request_id] = task
        tasks.append(task)
    if tasks:
        await asyncio.gather(*tasks)


async def main():
    service.initialize()
    runtime = RuntimeBridge(service)
    runtime.models.register_api("offline-acceptance", offline)
    await runtime.start()
    service.bind_runtime(runtime)
    try:
        first = submit("HELLO")
        await dispatch()
        first_request = service.get_request(first)
        assert first_request["status"] == "completed", first_request
        details = service.chat_detail("owner-alex", "private-alex")
        output = [m for m in details["messages"] if m.get("request_id") == first]
        assert len(output) == 1 and output[0]["reply_to"], output
        sid, rid = first_request["session_id"], first_request["run_id"]
        activity = await runtime.get_activity("private-alex", "owner-alex", sid, rid)
        assert any(e["type"] == "model_completed" and e.get("output") for e in activity["events"])
        assert all("thinking" not in str(e) for e in activity["events"])
        print("PASS canonical semantic output, one product projection, original quote, safe Activity")

        raw = "RAW_OWNER_FOR_REPLY_MODE"
        rewritten = submit(raw, reply_for_me=True)
        await dispatch()
        output = [m for m in service.chat_detail("owner-alex", "private-alex")["messages"] if m.get("request_id") == rewritten]
        assert len(output) == 1 and output[0]["kind"] == "reply_for_me" and output[0]["raw_owner_input"] == raw
        assert output[0]["body"] == "A complete deterministic acceptance response."
        rewritten_request = service.get_request(rewritten)
        rewritten_events = await runtime._run_records(runtime.tenant_id, rewritten_request["session_id"], rewritten_request["run_id"])
        assert len([record for record in rewritten_events if record["type"] == "model_completed"]) == 2
        assert not any(m["kind"] == "human" and m["body"] == raw for m in service.chat_detail("owner-alex", "private-alex")["messages"])
        print("PASS multi-operation reply-for-me updates one Twin bubble; both complete outputs remain in SDK")

        failed = submit("FAIL_PROVIDER")
        await dispatch()
        assert service.get_request(failed)["status"] == "failed"
        next_request = submit("AFTER_FAILURE")
        await dispatch()
        assert service.get_request(next_request)["status"] == "completed", service.get_request(next_request)
        print("PASS provider failure retains failed request and releases next FIFO safely")

        slow = submit("SLOW_CANCEL")
        queued = submit("AFTER_CANCEL")
        claims = service.claim_dispatches()
        assert claims == [slow], claims
        task = asyncio.create_task(service.execute_request(slow))
        service.tasks[slow] = task
        for _ in range(200):
            if "SLOW_CANCEL" in CALLS:
                break
            await asyncio.sleep(.01)
        await service.cancel_request("owner-alex", slow)
        assert service.get_request(slow)["cancel_requested"]
        await runtime.signal_stop(service.get_request(slow))
        await asyncio.wait_for(task, 5)
        await dispatch()
        assert service.get_request(slow)["status"] == "cancelled", service.get_request(slow)
        assert service.get_request(queued)["status"] == "completed", service.get_request(queued)
        assert not any(m.get("request_id") == slow for m in service.chat_detail("owner-alex", "private-alex")["messages"])
        print("PASS owner Stop settles SDK, withdraws only current output, runs next FIFO")

        delegated = submit("DELEGATE_CHILD")
        await dispatch()
        assert service.get_request(delegated)["status"] == "completed", service.get_request(delegated)
        for _ in range(300):
            requests = service.chat_detail("owner-alex", "private-alex")["requests"]
            if any(r["source"] == "subagent_result" for r in requests):
                break
            await asyncio.sleep(.01)
        await dispatch()
        details = service.chat_detail("owner-alex", "private-alex")
        assert any("background result has been summarized" in m["body"] for m in details["messages"]), details
        activity = await runtime.activity("private-alex", "owner-alex")
        assert any(run["source"] == "subagent" for run in activity), activity
        assert all(run["parent_run_id"] == service.get_request(delegated)["run_id"]
                   for run in activity if run["source"] == "subagent"), activity
        print("PASS child outlives main slot, durable notification enters same chat FIFO, child Activity included")

        count = len(details["messages"])
        await runtime.close()
        runtime = RuntimeBridge(service)
        runtime.models.register_api("offline-acceptance", offline)
        await runtime.start()
        service.bind_runtime(runtime)
        await runtime.recover_background()
        await dispatch()
        assert len(service.chat_detail("owner-alex", "private-alex")["messages"]) == count
        reopened = await runtime.get_activity("private-alex", "owner-alex", sid, rid)
        assert any(e.get("output") == "A complete deterministic acceptance response." for e in reopened["events"])
        print("PASS reopen restores complete output; child notification replay creates no duplicate")
        print("6 bounded Runtime↔product acceptance scenarios passed; real PostgreSQL, offline model adapter.")
    finally:
        await service.close()
        await runtime.close()
        engine.dispose()
        TEMP.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
