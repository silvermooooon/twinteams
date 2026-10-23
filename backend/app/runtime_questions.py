"""Resume an answered worker in its own existing SDK context and FIFO."""
from agent_runtime.execution import DurableExecutor

from . import questions


async def execute_child_answer(runtime, request, context):
    from .runtime_bridge import RuntimeBusy
    source = await runtime.service.db_call(questions.continuation_context, runtime.service, request["id"])
    # Both the root turn and the actual question-asking worker must have settled.
    for command_id in {source["origin_request_id"], source["command_id"]}:
        command = await runtime.store.get_command(runtime.tenant_id, command_id)
        if not command or command.get("cancel_requested_at") or command["state"] in {"stopped", "failed", "rejected"}:
            raise PermissionError("The original question task was stopped or is unavailable")
        if command["state"] in {"accepted", "applied"}:
            raise RuntimeBusy("The original question task is still settling")
    if source["session_id"] == source["main_session_id"]:
        return None
    original = await runtime.store.get_command(runtime.tenant_id, source["command_id"])
    creation = original["payload"].get("subagent")
    if not creation:
        raise RuntimeError("The question's worker has no durable ancestry")
    accepted = await runtime.store.get_command(runtime.tenant_id, request["id"])
    if accepted:
        if accepted["session_id"] != source["session_id"] or accepted["kind"] != "prompt":
            raise RuntimeError("Question continuation has conflicting SDK identity")
    else:
        await runtime.store.accept_command(runtime.tenant_id, source["session_id"], user_id=context["owner_id"],
            command_id=request["id"], kind="prompt", payload={"message": request["input_body"],
                "subagent": creation, "question_result": request["id"]})
    executor = DurableExecutor(runtime.store, runtime._child_factory, host=runtime.host)
    runtime.executors[request["id"]] = executor
    try:
        result = await executor.execute_command(runtime.tenant_id, source["session_id"],
            user_id=context["owner_id"], command_id=request["id"])
        result = result if isinstance(result, dict) else vars(result)
        if result["status"] in {"accepted", "queued", "busy"}:
            raise RuntimeBusy("The worker's answer remains durably queued")
        if result["status"] != "completed":
            raise RuntimeError("The question's worker continuation did not complete")
        run_id = result.get("run_id") or result.get("snapshot", {}).get("run_id")
        if not run_id:
            raise RuntimeError("The question continuation has no durable run")
        # Its normal SDK child-result notice goes to its actual parent for review.
        return {"session_id": source["session_id"], "run_id": run_id, "body": ""}
    finally:
        runtime.executors.pop(request["id"], None)
