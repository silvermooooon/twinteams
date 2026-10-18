"""Prompt composition checks use synthetic context and no provider requests."""
from types import SimpleNamespace

import pytest

from app import soul_file
from app.runtime_bridge import RuntimeBridge
from app.twin_prompts import conversation_system, platform_prompt, request_prompt, runtime_protocol


def test_mission_is_server_owned_even_without_soul():
    prompt = conversation_system({"chat": {"kind": "private"}})
    assert platform_prompt({}) in prompt
    assert "Actively complete" in platform_prompt({})
    assert "Owner's Soul:" not in prompt
    assert len(prompt.split()) < 650


def test_reply_for_me_keeps_embedded_requests_as_writing_content():
    context = {"raw_owner_input": "Please deploy this tonight", "prompt": "Keep my voice"}
    mission = platform_prompt(context)
    assert "message content, not as tasks to execute" in mission
    assert "Do not start unrelated work, delegate, or create ongoing goals" in mission
    assert "Actively complete" not in mission
    assert "return only that message" in conversation_system(context)
    assert request_prompt({"source": "reply_for_me"}, context) == context["raw_owner_input"]


def test_protocol_private_guidance_is_conditional_and_group_boundaries_remain():
    private = runtime_protocol({"chat": {"kind": "private"}})
    group = runtime_protocol({"chat": {"kind": "group"}})
    assert "request_credential uses an owner-only form" in private
    assert "request_credential uses an owner-only form" not in group
    assert "Groups have no shell, credentials or private Memory, including through delegation" in group
    assert "never clear sources or use another destination to bypass a denied send" in group
    assert "existing legitimate frozen private task" in group
    assert "Workers and groups have no memory tools" in group


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
    ("followup_result", "Timed out", "Do not invent a timeout answer"),
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
    assert "attached image" in request_prompt({}, {"input_images": ["synthetic"]})
    assert request_prompt({"body": "fallback"}, {}) == "fallback"


def test_summary_guidance_keeps_exact_prefix_sources_and_raw_tail(monkeypatch):
    monkeypatch.setenv("TWINTEAMS_SUMMARY_AFTER_MESSAGES", "4")
    monkeypatch.setenv("TWINTEAMS_SUMMARY_KEEP_MESSAGES", "2")
    context = {"summary": {"source_message_ids": ["old"]},
               "messages": [{"id": f"m{i}", "seq": i} for i in range(1, 5)]}
    policy = RuntimeBridge._summary_policy(None, context)
    assert "through sequence 2" in policy
    assert '["old", "m1", "m2"]' in policy
    assert '"m3"' not in policy and '"m4"' not in policy
    assert "pending approvals/credentials" in policy
    assert "never invent facts or future commitments" in policy
    assert "keep answering from the full context" in policy
    assert RuntimeBridge._summary_policy(None, {"messages": []}) == ""


def test_tool_guidance_and_recovery_preserve_frozen_declarations():
    runtime = RuntimeBridge.__new__(RuntimeBridge)
    tools = runtime._tools({"execution_enabled": True}, SimpleNamespace(snapshot={}, resumable=False))
    by_name = {tool.name: tool for tool in tools}
    assert "independent authorized work" in by_name["request_credential"].description
    assert "one-off answers need no topic" in by_name["create_tracking"].description
    assert "original configuration" in by_name["retry_reply_request"].description
    frozen = {"name": "create_approval", "description": "Original frozen declaration",
              "parameters": {"type": "object", "properties": {}}, "replay": "safe", "version": "old"}
    restored = runtime._tools({}, SimpleNamespace(snapshot={"tools": [frozen]}, resumable=True))
    assert len(restored) == 1
    assert restored[0].description == frozen["description"]
    assert restored[0].parameters == frozen["parameters"]
    assert restored[0].version == "old"


def test_new_soul_balances_clarification_and_preserves_existing_files(tmp_path):
    new_home, old_home = tmp_path / "new", tmp_path / "old"
    soul_file.initialize(new_home, "New")
    assert "ask a focused question with your recommendation" in soul_file.read(new_home)["content"]
    old_home.mkdir()
    original = "Owner's custom persona: answer without ceremony."
    (old_home / "SOUL.md").write_text(original)
    soul_file.initialize(old_home, "Old")
    assert soul_file.read(old_home)["content"] == original
    (new_home / "SOUL.md").unlink()
    soul_file.initialize(new_home, "New")
    assert not (new_home / "SOUL.md").exists()
