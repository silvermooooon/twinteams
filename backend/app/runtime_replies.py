"""Publish an owner reply before enabling work in the same durable SDK run."""
from agent_runtime import AgentContext, AgentTool, AgentToolResult
from agent_runtime.transcript import declare_tool_changes

from .runtime_scheduled import is_scheduled_request, terminal_output_only

START_WORK = "start_work"


def reply_first(request, context, session):
    if session.resumable:
        return any(tool["name"] == START_WORK for tool in session.snapshot.get("tools", []))
    return (request.get("source") == "private" and not request.get("background")
            and not terminal_output_only(request, context)
            and context.get("chat", {}).get("kind") == "private")


def final_reply_required(request, context, session):
    if session.resumable:
        return "finish_turn" in session.snapshot.get("required_hooks", [])
    return (request.get("source") in {"private", "subagent_result", "bot_result"}
            and not request.get("background")
            and not is_scheduled_request(request, context) and not context.get("message_center")
            and context.get("chat", {}).get("kind") == "private")


def install_reply_first(agent):
    session = agent.session
    generation_gate = agent.before_tool_call
    opening_committed = False

    def started():
        nonlocal opening_committed
        if not opening_committed:
            opening_committed = any(message.get("role") == "toolResult"
                                    and message.get("toolName") == START_WORK and not message.get("isError")
                                    for message in session.snapshot.get("run_messages", []))
        return opening_committed

    async def begin(call_id, args, signal, on_update):
        return AgentToolResult(content=[{"type": "text", "text": (
            "Your opening reply is published. Continue the requested work now. "
            "Publish useful milestones and deliver the final result when the work is complete."
        )}])

    tool = AgentTool(
        name=START_WORK,
        description=("Continue working after your opening private reply. Include one or two short "
                     "sentences of visible assistant text in this same response before calling this "
                     "tool: lead with a known conclusion, otherwise state your first concrete step. "
                     "If you can answer immediately, give the complete answer without calling this tool."),
        parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        execute=begin, replay="safe", version="duet-early-reply-v1",
    )
    if session.resumable:
        saved = next(row for row in session.snapshot["tools"] if row["name"] == START_WORK)
        tool = AgentTool(**{key: saved[key] for key in (
            "name", "description", "parameters", "replay", "version")}, execute=begin)
    agent.state.tools.append(tool)

    def visible_tools(messages, signal):
        signal.throw_if_aborted()
        if started():
            return messages
        # Keep the complete execution catalog frozen in the SDK. Only the provider's
        # offered declarations change, including native tools that run server-side.
        return [*messages, *declare_tool_changes(AgentContext(messages, [tool]), [])]

    async def before_tool(state, signal):
        await generation_gate(state, signal)
        if started():
            return None
        from .runtime_bridge import text_content
        if state["toolCall"]["name"] != START_WORK or not text_content(state["assistantMessage"]):
            return {"block": True, "reason": (
                "First write a short visible reply and call start_work in that same response. "
                "Other work can begin after that reply is published."
            )}
        # The bridge awaits projection of message_end before the SDK invokes tools.
        return None

    agent.transform_context = visible_tools
    agent.before_tool_call = before_tool


def install_final_reply(agent, chat_id):
    def finish_turn(state, signal):
        from .runtime_bridge import text_content
        signal.throw_if_aborted()
        if (state["message"].get("stopReason") == "stop"
                and not text_content(state["message"])
                and not any(delivered_or_handed_off(message, chat_id)
                            for message in agent.session.snapshot["run_messages"])):
            raise RuntimeError("Duet finished without delivering a final result. Retry the request.")

    agent.finish_turn = finish_turn


def delivered_or_handed_off(message, chat_id):
    """Existing durable tool receipts allow silent yields or explicit file delivery."""
    if message.get("role") != "toolResult" or message.get("isError"):
        return False
    name, result = message.get("toolName"), message.get("details") or {}
    if name in {"ask_question", "request_credential", "create_approval",
                "create_duet_topic", "invite_duet_participants"}:
        return result.get("status") == "pending"
    if name == "reply_as_owner":
        return result.get("status") == "confirmation_required"
    if name == "answer_question":
        return result.get("status") == "answered"
    if name == "spawn_agent":
        return bool(result.get("task_id"))
    if name in {"dispatch_bot_tasks", "message_personal_agent"}:
        return bool(result.get("request_ids") or result.get("request_id"))
    if name == "present_files":
        return result.get("status") == "approval_required" or (
            result.get("sent") and result.get("state") == "published"
            and result.get("chat_id") == chat_id and bool(result.get("message_id")))
    return False
