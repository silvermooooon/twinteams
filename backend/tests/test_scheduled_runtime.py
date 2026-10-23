"""Scheduled wakes use the real durable SDK with a deterministic offline provider."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agent_runtime import AbortSignal, AssistantMessageEventStream, assistant_message
from jsonschema import Draft202012Validator
from test_followup_runtime import ProductStub

from app.runtime_bridge import RuntimeBridge
from app.runtime_scheduled import is_scheduled_request, terminal_output_only
from app.twin_prompts import conversation_system, request_prompt

TASK = {"id": "saved-task", "title": "External watch", "instructions": "Check the authorized external feed and report each result.",
        "schedule": {"kind": "daily", "time": "09:00"}, "timezone": "Asia/Shanghai", "revision": 3,
        "planned_at": "2026-10-23T01:00:00Z", "occurrence_id": "occurrence-a", "kind": "personal"}


def test_scheduled_input_contract_keeps_authorization_scope_and_system_review_evidence():
    request = {"source": "scheduled_task", "input_body": "Do not use an arbitrary JSON wrapper as owner prose."}
    context = {"scheduled_task": TASK, "current_time": "2026-10-23T01:00:00+00:00", "schedule_timezone": "Asia/Shanghai"}
    prompt = request_prompt(request, context)
    system = conversation_system(context)
    assert "Current server time (UTC): 2026-10-23T01:00:00+00:00" in system
    assert "Owner timezone for interpreting schedules: Asia/Shanghai" in system
    assert "not a new human message" in prompt
    assert "explicitly requested external monitoring" in prompt
    assert TASK["instructions"] in prompt
    assert "Do not use an arbitrary" not in prompt
    assert "Asia/Shanghai" in prompt and "occurrence-a" in prompt
    review = {"as_of": "2026-10-23T04:00:00Z", "cutoff": "previous-cutoff", "deadlines": [{"id": "due", "due_at": "2026-10-23T05:00:00Z"}],
              "external_watches": [{"id": "watch", "status": "unresolved"}], "previous_notice": {"body": "Already reported"}}
    prompt = request_prompt({"source": "system_review"}, {"system_review": review})
    for value in ("previous-cutoff", "Already reported", "upcoming or overdue deadlines", "known external watches", "Never create schedules"):
        assert value in prompt
    for source in ("approval", "credential_result", "subagent_result", "followup_result"):
        request = {"source": source, "execution_config": {"scheduled_task": TASK}}
        assert is_scheduled_request(request) and terminal_output_only(request)
    assert not is_scheduled_request({"source": "private", "execution_config": None})
    assert not terminal_output_only({"source": "private"})
    assert "Scheduled occurrence" in conversation_system({"scheduled_task": TASK})
    continued = conversation_system({"scheduled_task": {**TASK, "kind": "system_review"}})
    assert "omit unchanged notices" in continued and "Only a completed final response" in continued
    with pytest.raises(ValueError, match="frozen owner task instructions"):
        request_prompt({"source": "scheduled_task", "input_body": '{"instructions":"unfrozen"}'}, {})


def test_schedule_tools_have_exact_supported_schema_and_owner_context():
    product = ProductStub()
    runtime = RuntimeBridge(product)
    context = {"owner_id": "owner-a", "chat": {"id": "private-a", "kind": "private"},
               "request_id": "request", "causal_chain_id": "chain", "origin_request_id": "origin"}
    session = SimpleNamespace(session_id="session", snapshot={"run_id": "run"},
                              tool_operation_id=lambda call: f"operation:{call}")
    tools = {tool.name: tool for tool in runtime._tools(context, session)}
    create = tools["create_scheduled_task"]
    for schedule in ({"kind": "once", "at": "2026-10-24T09:00:00+08:00"}, {"kind": "daily", "time": "09:00"},
                     {"kind": "weekly", "time": "09:00", "weekdays": [0, 4]}, {"kind": "interval", "seconds": 60}):
        args = {"title": "Task", "instructions": "Check feed", "timezone": "Asia/Shanghai", "schedule": schedule}
        assert Draft202012Validator(create.parameters).is_valid(args)
    args["schedule"] = {"kind": "interval", "seconds": 59}
    assert not Draft202012Validator(create.parameters).is_valid(args)
    args["schedule"] = {"kind": "daily", "time": "09:00"}
    asyncio.run(create.execute("create", args, AbortSignal(), None))
    assert product.tool_calls[0][1] == args
    assert product.tool_calls[0][2]["operation_id"] == "operation:create"
    assert product.tool_calls[0][2]["owner_id"] == "owner-a"
    assert product.tool_calls[0][2]["causal_chain_id"] == "chain"
    assert all(tools[name].replay == "safe" for name in ("create_scheduled_task", "list_scheduled_tasks", "update_scheduled_task", "cancel_scheduled_task"))


@pytest.mark.parametrize("source", ["scheduled_task", "system_review", "credential_result", "subagent_result"])
@pytest.mark.parametrize("final_body,failed", [("", False), ("Verified requested report.", False), ("", True)])
def test_scheduled_sdk_terminal_only_silent_replay_and_owner_context(monkeypatch, source, final_body, failed):
    from app import twin_execution

    assembled = []

    async def assemble(service, session, context):
        assembled.append(context)
        return [], {**context, "execution_config": {"profile": {"soul": "Owner-specific preferences"}}}, None

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
                {"type": "text", "text": "Intermediate scheduled narration must never be posted."},
                {"type": "toolCall", "id": "read-chats", "name": "list_authorized_chats", "arguments": {}}]
            stop = "error" if has_result and failed else "stop" if has_result else "toolUse"
            stream = AssistantMessageEventStream()
            stream.push({"type": "done", "message": assistant_message(model, content=content, stopReason=stop,
                **({"errorMessage": "Offline scheduled interruption"} if has_result and failed else {}))})
            return stream

        runtime.models.register_api("offline-regression", offline)
        await runtime.start()
        request_id, chat_id = uuid4().hex, uuid4().hex
        request = {"id": request_id, "chat_id": chat_id, "target_twin_id": "twin-a", "requester_id": "owner-a",
                   "source": source, "status": "running", "causal_chain_id": "occurrence-chain",
                   "input_body": "Scheduled or continuation input", "execution_config": {"scheduled_task": TASK}}
        product.requests[request_id] = request
        context = {"owner_id": "owner-a", "chat": {"id": chat_id, "kind": "private"},
                   "execution_config": {"scheduled_task": TASK}, "system_review": {"cutoff": "review-cutoff"}}
        try:
            if failed:
                with pytest.raises(RuntimeError, match="Offline scheduled interruption"):
                    await runtime.execute(request, context)
                assert product.outputs == {}
            else:
                result = await runtime.execute(request, context)
                assert result["body"] == final_body
                assert list(product.outputs.values()) == ([final_body] if final_body else [])
                count = len(calls)
                assert await runtime.execute(request, context) == result
                assert len(calls) == count
                await runtime._project_outputs(request_id, runtime.tenant_id, result["session_id"], result["run_id"])
                assert list(product.outputs.values()) == ([final_body] if final_body else [])
            assert assembled[0]["owner_id"] == "owner-a"
            assert assembled[0]["scheduled_task"] == TASK
            assert assembled[0]["causal_chain_id"] == "occurrence-chain"
            assert product.tool_calls[0][2]["scheduled_task"] == TASK
            assert "Owner-specific preferences" in str(calls[0])
            assert not any(event.get("type") in {"status", "operation_started", "token", "output_committed"} for event in product.events)
        finally:
            await runtime.close()

    asyncio.run(run())


def test_scheduled_child_admission_reads_durable_intent_and_unacked_result():
    async def run():
        product = ProductStub()
        runtime = RuntimeBridge(product)
        await runtime.start()
        parent_session, parent_run = uuid4().hex, uuid4().hex
        request = {"session_id": parent_session, "run_id": parent_run}
        try:
            assert not await runtime.has_unresolved_scheduled_work(request)
            # Accepted child intent is enough; no in-process manager or Activity exists.
            child = await runtime.store.accept_command(runtime.tenant_id, uuid4().hex,
                command_id=uuid4().hex, kind="prompt", user_id="owner-a", payload={
                    "message": {"role": "user", "content": [{"type": "text", "text": "Authorized worker task"}]},
                    "subagent": {"parent_session_id": parent_session, "parent_run_id": parent_run}})
            assert await runtime.has_unresolved_scheduled_work(request)
            assert not await runtime.has_unresolved_scheduled_work({**request, "run_id": "another-occurrence"})
            # A rejected, never-started intent cannot orphan the recurring occurrence.
            from agent_runtime.execution import DurableExecutor
            await DurableExecutor(runtime.store, lambda session: None, host=runtime.host, child=True).abandon(
                runtime.tenant_id, child["session_id"], user_id="owner-a", command_id=child["command_id"], reason="Synthetic cancellation")
            assert not await runtime.has_unresolved_scheduled_work(request)
            # Inbox acceptance, before product delivery, keeps the occurrence pending.
            notification = await runtime.store.accept_command(runtime.tenant_id, parent_session,
                command_id=uuid4().hex, kind="child_result", user_id="owner-a", payload={
                    "parent_session_id": parent_session, "parent_run_id": parent_run, "child_session_id": child["session_id"]})
            assert await runtime.has_unresolved_scheduled_work(request)
            await runtime.store.ack_notification(runtime.tenant_id, parent_session, notification["command_id"])
            assert not await runtime.has_unresolved_scheduled_work(request)
        finally:
            await runtime.close()

    asyncio.run(run())
