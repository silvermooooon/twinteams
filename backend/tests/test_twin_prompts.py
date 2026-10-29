"""Prompt composition checks use synthetic context and no provider requests."""
from types import SimpleNamespace

import pytest

from app import soul_file
from app.runtime_bridge import RuntimeBridge
from app.twin_prompts import conversation_system, platform_prompt, request_prompt, runtime_protocol


def test_mission_is_server_owned_even_without_soul():
    prompt = conversation_system({"chat": {"kind": "private"}})
    assert platform_prompt({}) in prompt
    assert "You are your owner's front-facing Duet." in platform_prompt({})
    assert "Owner's Soul:" not in prompt
    assert len(prompt.split()) < 1000


@pytest.mark.parametrize("worker", [False, True])
def test_duet_role_preserves_custom_personal_name_and_worker_identity(worker):
    context = {"target_twin_name": "Elysia", "chat": {"kind": "private"},
               "execution_config": {"profile": {"soul": "My Twin persona uses short answers."}}}
    prompt = conversation_system(context, worker=worker)
    identity = "a background worker for Elysia" if worker else "Elysia"
    assert prompt.startswith(platform_prompt(context, worker=worker))
    assert f"You are {identity}, your owner's Duet, working in this private conversation" in prompt
    assert "Duet is the role; preserve personal names." in prompt
    assert "Never identify as Twin/digital twin." in prompt
    assert "My Twin persona uses short answers." in prompt
    assert ("You are your owner's front-facing Duet." in prompt) == (not worker)


def test_reply_for_me_keeps_embedded_requests_as_writing_content():
    context = {"raw_owner_input": "Please deploy this tonight", "prompt": "Keep my voice"}
    mission = platform_prompt(context)
    assert "message content, not as tasks to execute" in mission
    assert "Do not start unrelated work, delegate, or create ongoing goals" in mission
    assert "You are your owner's front-facing Duet." not in mission
    assert "return only that message" in conversation_system(context)
    assert request_prompt({"source": "reply_for_me"}, context) == context["raw_owner_input"]


@pytest.mark.parametrize("raw", [None, "The parent's raw wording is reference data"])
def test_worker_role_is_explicit_and_does_not_inherit_front_desk_or_parent_writing_mode(raw):
    runtime = RuntimeBridge.__new__(RuntimeBridge)
    context = {"chat": {"kind": "private"}, "raw_owner_input": raw}
    main = runtime._system(context)
    worker = runtime._system(context, worker=True)
    assert platform_prompt(context, worker=True) in worker
    assert "You are a background worker for the owner's Duet." in worker
    assert "You are your owner's front-facing Duet." not in worker
    assert "Reply-for-me instructions:" not in worker
    assert "return only that message" not in worker
    assert "You are a background worker for the owner's Duet." not in main
    assert ("You are your owner's front-facing Duet." in main) == (raw is None)
    assert ("Reply-for-me instructions:" in main) == (raw is not None)
    assert "Express warmth naturally with set_message_reaction" not in worker
    assert ("Express warmth naturally with set_message_reaction" in main) == (raw is None)
    assert ("Routine current-chat reactions attributed to your Duet" in main) == (raw is None)
    assert "Routine current-chat reactions attributed to your Duet" not in worker


@pytest.mark.parametrize("kind", ["private", "duet"])
def test_protocol_uses_separate_owner_approved_duet_conversations(kind):
    protocol = runtime_protocol({"chat": {"kind": kind}, "execution_enabled": True})
    assert "request_credential uses an owner-only form" in protocol
    assert "request_duet_conversation" in protocol
    assert "approve once" in protocol and "always grant to every peer" in protocol
    assert "connection only" in protocol and "no authority for arbitrary actions" in protocol
    assert "share six sends across the causal chain" in protocol
    assert "Reading, owner reports and questions are unmetered" in protocol
    assert "held until an authorized owner uses Continue" in protocol
    assert "Never publish in human chats" in protocol
    assert "another owner's Memory" in protocol
    assert "Main and child capabilities match" in protocol
    assert "never clear sources or use another destination to bypass a denied send" in protocol
    assert "Automatic reply trigger" not in protocol
    assert "owner was observed" not in protocol


def test_worker_reports_to_parent_and_direct_delivery_needs_the_assigned_authorization():
    main = runtime_protocol({"chat": {"kind": "private"}})
    worker = runtime_protocol({"chat": {"kind": "private"}}, worker=True)
    assert "Report useful outcomes and necessary decisions to your owner privately" in main
    assert "Report results and necessary decisions to your parent" in worker
    assert "the assigned task requires it and the owner has authorized that delivery" in worker
    assert "Report useful outcomes and necessary decisions to your owner privately" not in worker
    assert "Main and child capabilities match" in main and "Main and child capabilities match" in worker


def test_duet_context_has_own_audience_and_tracking_scope():
    prompt = conversation_system({"chat": {"kind": "duet", "audience": [{"owner_id": "peer"}]}})
    assert "working in this Duet conversation" in prompt
    assert "Duet: this conversation only" in prompt
    assert '"owner_id": "peer"' in prompt


def test_current_references_keep_only_authorized_visible_message_metadata():
    context = {"request_id": "request-a", "request_source": "private", "source_message_id": "m1",
               "private_memory": "unloaded memory", "timezone": "invented-zone",
               "messages": [
                   {"id": "m1", "created_at": "2026-10-19T03:00:00Z", "body": "Proceed",
                    "reply_to": {"id": "m0", "body": "Original goal"}, "private_field": "hidden-field"},
                   {"id": "withdrawn", "state": "withdrawn", "body": "withdrawn-content"}]}
    prompt = conversation_system(context)
    assert '"request_id": "request-a"' in prompt
    assert '"reply_to": {"id": "m0", "body": "Original goal"}' in prompt
    assert "2026-10-19T03:00:00Z" in prompt
    assert not any(value in prompt for value in (
        "unloaded memory", "invented-zone", "hidden-field", "withdrawn-content"))


@pytest.mark.parametrize(("source", "body", "expected"), [
    ("subagent_result", "Worker completed the draft", "continue remaining authorized work"),
])
def test_continuations_resume_remaining_work_without_new_permission(source, body, expected):
    prompt = request_prompt({"source": source, "input_body": body}, {})
    assert expected in prompt
    assert "permission" in prompt and body in prompt
    assert "repeat completed operations" in prompt or "completed operations" in prompt


def test_input_selection_keeps_existing_precedence_and_attachment_fallback():
    context = {"raw_owner_input": "raw", "source_message": {"body": "source"}}
    assert request_prompt({"input_body": "accepted"}, context) == "accepted"
    assert request_prompt({}, context) == "raw"
    assert request_prompt({}, {"source_message": {"body": "source"}}) == "source"
    assert request_prompt({"source_message_id": "m"}, {"messages": [{"id": "m", "body": "tail"}]}) == "tail"
    assert "attached files" in request_prompt({}, {"input_files": [{"workspace_path": "/synthetic/example.png"}]})
    assert request_prompt({"body": "fallback"}, {}) == "fallback"


def test_checkpoint_maintenance_is_absent_from_reply_prompt_and_new_tools():
    runtime = RuntimeBridge.__new__(RuntimeBridge)
    context = {"summary": {"body": "Earlier task is pending"},
               "messages": [{"id": "recent", "seq": 9, "body": "Proceed"}]}
    prompt = runtime._system(context)
    assert "Earlier task is pending" in prompt and "Proceed" in prompt
    assert "save_summary" not in prompt and "Periodic summary maintenance" not in prompt
    tools = runtime._tools(context, SimpleNamespace(snapshot={}, resumable=False))
    assert "save_summary" not in {tool.name for tool in tools}
    assert "save_summary" not in str(next(tool for tool in tools if tool.name == "create_approval").parameters)


def test_legacy_summary_tool_is_recovery_only_with_exact_frozen_schema():
    runtime = RuntimeBridge.__new__(RuntimeBridge)
    frozen = {"name": "save_summary", "description": "Legacy summary", "version": "old",
              "parameters": {"type": "object", "properties": {"body": {"type": "string"}}},
              "replay": "safe"}
    restored = runtime._tools({}, SimpleNamespace(snapshot={"tools": [frozen]}, resumable=True))
    assert len(restored) == 1
    assert restored[0].name == "save_summary"
    assert restored[0].parameters == frozen["parameters"]
    assert restored[0].description == frozen["description"]
    assert restored[0].version == frozen["version"]


@pytest.mark.parametrize("frozen_name", ["create_approval", "request_credential"])
def test_tool_guidance_and_recovery_preserve_frozen_declarations(frozen_name):
    runtime = RuntimeBridge.__new__(RuntimeBridge)
    tools = runtime._tools({"execution_enabled": True}, SimpleNamespace(snapshot={}, resumable=False))
    by_name = {tool.name: tool for tool in tools}
    assert "independent authorized work" in by_name["request_credential"].description
    assert "one-off answers need no topic" in by_name["create_tracking"].description
    assert "original configuration" in by_name["retry_reply_request"].description
    frozen = {"name": frozen_name, "description": "Original frozen declaration",
              "parameters": {"type": "object", "properties": {}}, "replay": "safe", "version": "old"}
    restored = runtime._tools({"execution_enabled": True}, SimpleNamespace(snapshot={"tools": [frozen]}, resumable=True))
    assert len(restored) == 1
    assert restored[0].description == frozen["description"]
    assert restored[0].parameters == frozen["parameters"]
    assert restored[0].version == "old"


def test_new_soul_balances_clarification_and_preserves_existing_files(tmp_path):
    new_home, old_home = tmp_path / "new", tmp_path / "old"
    soul_file.initialize(new_home, "New")
    assert "ask a focused question with your recommendation" in soul_file.read(new_home)["content"]
    assert "the right help to move the owner's work forward" in soul_file.read(new_home)["content"]
    assert "Front-facing" not in soul_file.read(new_home)["content"]
    old_home.mkdir()
    original = "Owner's custom persona: answer without ceremony."
    (old_home / "SOUL.md").write_text(original)
    soul_file.initialize(old_home, "Old")
    assert soul_file.read(old_home)["content"] == original
    (new_home / "SOUL.md").unlink()
    soul_file.initialize(new_home, "New")
    assert not (new_home / "SOUL.md").exists()


def test_main_delegates_substantial_execution_without_doing_the_handoff_work_first():
    main = platform_prompt({})
    assert "Stay available and keep your owner informed" in main
    assert "quick replies, brief lookups and small bounded actions directly" in main
    assert "Judge the whole owner's goal, not each small step in isolation" in main
    assert "for sustained or substantial execution choose spawn_agent first, before heavy work" in main
    assert "review worker results and deliver verified outcomes" in main
    assert "yielding is not completing the owner's goal" in main
    assert "Multi-source analysis" not in main and "acceptance criteria" not in main
    assert "substantial steps personally" in main
    worker = platform_prompt({}, worker=True)
    assert "Execute it, verify results" in worker
    assert "small bounded actions directly" not in worker
    assert "Skill procedures follow the same main/worker division" in worker


def test_reaction_can_be_the_entire_social_response_and_keeps_substantive_care():
    runtime = RuntimeBridge.__new__(RuntimeBridge)
    main = platform_prompt({})
    reaction = next(tool for tool in runtime._tools({}, SimpleNamespace(snapshot={}, resumable=False))
                    if tool.name == "set_message_reaction")
    assert "Express warmth naturally with set_message_reaction, alongside words when useful" in main
    assert "real questions and serious feelings substantive care" in main
    assert "when acknowledgment is enough" not in main
    assert "shared joy" not in main and "courtesy loops" not in main
    assert "acknowledgment, thanks, shared joy or appreciation of effort" in reaction.description
    assert "never announce it" in reaction.description
    assert "every message or start courtesy loops" in reaction.description
    assert "Serious feelings" in reaction.description
    assert "reaction alone can suffice" in reaction.description
    assert "No message or model response is triggered" in reaction.description


def test_product_subagent_guidance_preserves_sdk_capabilities_and_main_worker_parity():
    from agent_runtime.subagents import create_subagent_tools
    runtime = RuntimeBridge.__new__(RuntimeBridge)
    def manager(worker=False):
        return SimpleNamespace(parent=SimpleNamespace(session=SimpleNamespace(snapshot={}, resumable=False,
            subagent_creation={"task": "assigned"} if worker else None)), agent_factories={"worker": None})
    sdk = {tool.name: tool for tool in create_subagent_tools(manager())}
    main = {tool.name: tool for tool in runtime._subagent_tools(manager())}
    worker = {tool.name: tool for tool in runtime._subagent_tools(manager(True))}
    assert main.keys() == worker.keys() == sdk.keys()
    for name, tool in main.items():
        assert tool.parameters == worker[name].parameters == sdk[name].parameters
        assert tool.replay == worker[name].replay == sdk[name].replay
        assert tool.description == worker[name].description
    assert "durable acceptance/status immediately" in main["spawn_agent"].description
    assert "queued does not mean execution has started" in main["spawn_agent"].description
    assert "normal final response to yield this turn" in main["spawn_agent"].description
    assert "multi-source analysis or research, implementation or debugging" in main["spawn_agent"].description
    assert "creating and validating nontrivial deliverables" in main["spawn_agent"].description
    assert "goal, known facts, relevant paths, authorization limits and acceptance criteria" in main["spawn_agent"].description
    assert "transcript is not automatically copied" in main["spawn_agent"].description
    assert "inherit authorized owner resources and task scope" in main["spawn_agent"].description
    assert "read authorized history when needed" in main["spawn_agent"].description
    assert "Dispatch independent assignments together" in main["spawn_agent"].description
    assert "leave full analysis and execution to the worker" in main["spawn_agent"].description
    assert "Completion automatically notifies and resumes this task after the current turn ends" in main["spawn_agent"].description
    assert "tell the owner" not in main["spawn_agent"].description
    assert "Completion is notified automatically" in main["get_agent"].description
    assert "omitted timeout can wait indefinitely" in main["wait_agent"].description
    assert "holds the current execution slot" in main["wait_agent"].description
    assert "Normally" in main["wait_agent"].description
    assert main["send_agent_message"].description == sdk["send_agent_message"].description


def test_resumable_subagent_tools_keep_exact_frozen_declarations_and_order():
    runtime = RuntimeBridge.__new__(RuntimeBridge)
    frozen = [{"name": name, "description": "Original " + name,
               "parameters": {"type": "object", "properties": {"original": {"type": "string"}}},
               "replay": replay, "version": "original-v1"}
              for name, replay in (("wait_agent", "safe"), ("spawn_agent", "safe"), ("stop_agent", "never"))]
    manager = SimpleNamespace(parent=SimpleNamespace(session=SimpleNamespace(snapshot={"tools": frozen},
                              resumable=True)), agent_factories={"worker": None})
    restored = runtime._subagent_tools(manager)
    assert [{key: getattr(tool, key) for key in ("name", "description", "parameters", "replay", "version")}
            for tool in restored] == frozen
    assert restored[0].parameters is not frozen[0]["parameters"]


def test_custom_soul_cannot_remove_the_server_mission():
    custom = "Prefer personally doing every step."
    prompt = conversation_system({"execution_config": {"profile": {"soul": custom}}})
    assert custom in prompt
    assert platform_prompt({}) in prompt
    assert prompt.index(platform_prompt({})) < prompt.index(runtime_protocol({})) < prompt.index(custom)
    assert "within the platform mission, runtime rules and authorization boundaries" in prompt
