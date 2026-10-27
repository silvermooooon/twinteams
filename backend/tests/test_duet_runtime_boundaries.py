"""Runtime boundaries use isolated PostgreSQL and never invoke a provider."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from test_memory_dream import product as product  # noqa: PLC0414

from app import duet_conversations, reactions
from app import service as service_module
from app.models import Chat, ChatAttachment, Message, ProductOperation, Summary
from app.runtime_bridge import RuntimeBridge


def origin(product):
    sent = product.send_message("owner-alex", "private-alex", "Coordinate the authorized task", uuid4().hex)
    return product.get_runtime_context(sent["request"]["id"])


def call(product, context, name, args, operation=None):
    return asyncio.run(product.runtime_tool(name, args, {**context, "operation_id": operation or uuid4().hex}))


def propose(product, context):
    return call(product, context, "request_duet_conversation", {"participant_owner_ids": ["owner-maya"],
        "title": "Task coordination", "purpose": "Verify the plan", "opening_message": "Please verify the agreed plan."})


def test_new_catalog_exposes_connection_contract_and_preserves_legacy_recovery():
    runtime = RuntimeBridge.__new__(RuntimeBridge)
    tools = {tool.name: tool for tool in runtime._tools({}, SimpleNamespace(snapshot={}, resumable=False))}
    assert "request_duet_conversation" in tools
    assert {"create_chat", "rename_chat", "add_chat_members"} <= tools.keys()
    request = tools["request_duet_conversation"]
    assert set(request.parameters["required"]) == {"participant_owner_ids", "title", "purpose", "opening_message"}
    assert "connection only" in request.description
    assert all(name in str(tools["create_approval"].parameters) for name in ("create_chat", "rename_chat", "add_chat_members"))
    frozen = {"name": "create_chat", "description": "Old declaration", "version": "old",
              "parameters": {"type": "object", "properties": {}}, "replay": "safe"}
    recovered = runtime._tools({}, SimpleNamespace(snapshot={"tools": [frozen]}, resumable=True))
    assert len(recovered) == 1 and recovered[0].name == "create_chat"
    assert recovered[0].parameters == frozen["parameters"]


@pytest.mark.parametrize("tool,args", [
    ("present_files", {"chat_id": "group-team", "paths": ["missing.md"]}),
    ("send_chat_message", {"chat_id": "group-team", "body": "Agent post", "source_message_ids": []}),
])
def test_private_agent_cannot_publish_in_human_chats(product, tool, args):
    context = origin(product)
    operation = uuid4().hex
    with pytest.raises(HTTPException) as denied:
        call(product, context, tool, args, operation)
    assert denied.value.status_code == 403
    with product.transaction() as db:
        assert db.get(ProductOperation, operation) is None
        assert list(db.scalars(select(ChatAttachment))) == []


def test_agent_reactions_are_scoped_and_human_duet_reactions_are_read_only(product):
    context = origin(product)
    pending = propose(product, context)
    with product.transaction() as db:
        human = product.append_message(db, db.get(Chat, "group-team"), author_id="owner-alex", kind="human", body="Human discussion")
        message_id = human.id
    with pytest.raises(HTTPException) as denied:
        call(product, context, "set_message_reaction", {"chat_id": "group-team", "message_id": message_id,
            "emoji": "👍", "active": True})
    assert denied.value.status_code == 403
    for approval in pending["conversation"]["approvals"]:
        asyncio.run(product.decide_approval(approval["approver_id"], approval["id"], "approved"))
    message_id = product.chat_detail("owner-alex", pending["chat_id"])["messages"][0]["id"]
    with product.transaction() as db:
        with pytest.raises(HTTPException) as denied:
            reactions.set_reaction(product, db, "owner-alex", pending["chat_id"], message_id, "owner-alex", "👍", True)
        assert denied.value.status_code == 403


def test_active_duet_files_and_reactions_close_on_revocation(product, monkeypatch, tmp_path):
    monkeypatch.setattr(service_module, "settings", replace(service_module.settings, workspace_root=tmp_path))
    context = origin(product)
    pending = propose(product, context)
    for approval in pending["conversation"]["approvals"]:
        asyncio.run(product.decide_approval(approval["approver_id"], approval["id"], "approved", "always"))
    path = tmp_path / "private-alex" / "report.md"
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text("Verified report")
    args = {"chat_id": pending["chat_id"], "paths": [str(path)], "caption": "Draft ready"}
    delivered = call(product, context, "present_files", args)
    reaction = {"chat_id": pending["chat_id"], "message_id": delivered["message_id"], "emoji": "👍", "active": True}
    assert call(product, context, "set_message_reaction", reaction)["active"]
    duet_conversations.revoke(product, "owner-alex", "owner-maya")
    for tool, arguments in (("present_files", args), ("set_message_reaction", reaction)):
        with pytest.raises(HTTPException) as denied:
            call(product, context, tool, arguments)
        assert denied.value.status_code == 409


def test_legacy_group_checkpoint_and_agent_messages_never_enter_context(product):
    with product.transaction() as db:
        chat = db.get(Chat, "group-team")
        member = product.membership(db, "owner-alex", chat.id)[1]
        human = Message(id=uuid4().hex, chat_id=chat.id, seq=1, author_id="owner-alex", kind="human", body="Visible human fact")
        agent = Message(id=uuid4().hex, chat_id=chat.id, seq=2, author_id="twin-maya", kind="twin", body="Hidden old agent content")
        db.add_all([human, agent]); chat.next_message_seq = 3
        db.add(Summary(id=uuid4().hex, chat_id=chat.id, owner_id=member.owner_id,
            grant_version=member.grant_version, body="Hidden summary content", source_message_ids=[human.id, agent.id],
            source_provenance=[], coverage_start_seq=member.lower_seq, coverage_end_seq=2))
    result = product.get_conversation_context({"chat_id": "group-team", "owner_id": "owner-alex"})
    assert result["summary"] is None
    assert [message["body"] for message in result["messages"]] == ["Visible human fact"]
