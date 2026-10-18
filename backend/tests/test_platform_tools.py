"""Owner platform operations use existing transactions, grants and causal budget."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from test_memory_dream import product as product  # noqa: PLC0414

from app import service as service_module
from app.models import (
    AllowanceGrant,
    Approval,
    CausalChain,
    Chat,
    ContextManifest,
    Membership,
    Message,
    ProductOperation,
    WorkspaceNode,
)
from app.notifications import notifications


def origin(product, chat_id="group-alex-maya", requester="owner-alex"):
    with product.transaction() as db:
        message = product.append_message(db, db.get(Chat, chat_id), author_id=requester, kind="human", body="Coordinate this task")
        message_id = message.id
    request = product.manual_request(requester, chat_id, message_id, "twin-alex", uuid4().hex)
    context = product.get_runtime_context(request["id"])
    return request, {**context, "runtime_session_id": "offline-session", "runtime_run_id": "offline-run"}


def call(product, context, name, args, key=None):
    return asyncio.run(product.runtime_tool(name, args, {**context, "operation_id": key or uuid4().hex}))


def approval(product, context, action):
    return call(product, context, "create_approval", {"title": "Review action", "description": "Exact action",
        "target": "Platform", "impact": "One change", "action": action})


def test_typed_platform_helpers_and_new_membership_notification(product, monkeypatch):
    _, context = origin(product)
    emitted = []
    monkeypatch.setattr(notifications, "emit_personal", lambda owner, section: emitted.append((owner, section)))
    people = call(product, context, "list_people", {"limit": 2})["people"]
    assert len(people) == 2 and all(row["kind"] == "human" for row in people)
    pair = call(product, context, "create_chat", {"owner_ids": ["owner-jordan"], "history_policy": "no_prior"}, "new-pair")
    assert set(emitted) == {("owner-alex", "chats"), ("owner-jordan", "chats")}
    emitted.clear()
    assert call(product, context, "create_chat", {"owner_ids": ["owner-jordan"]})["id"] == pair["id"]
    assert not emitted
    with product.transaction() as db:
        grant = db.scalar(select(Membership).where(Membership.chat_id == pair["id"], Membership.owner_id == "owner-jordan"))
        assert grant.history_policy == "no_prior"
    call(product, context, "add_chat_members", {"chat_id": pair["id"], "owner_ids": ["owner-maya"], "history_policy": "all"})
    assert emitted == [("owner-maya", "chats")]
    renamed = call(product, context, "rename_chat", {"chat_id": pair["id"], "title": "  Coordinated group  "})
    assert renamed["title"] == "Coordinated group"
    assert call(product, context, "get_chat", {"chat_id": pair["id"]})["chat"]["title"] == renamed["title"]
    reminder = call(product, context, "create_reminder", {"chat_id": pair["id"], "title": "Follow through"})
    assert call(product, context, "list_reminders", {})["reminders"][0]["id"] == reminder["id"]
    assert call(product, context, "update_reminder", {"id": reminder["id"], "done": True})["done"]
    assert call(product, context, "list_reminders", {"include_done": False})["reminders"] == []
    status = call(product, context, "get_twin_status", {})
    assert status["twin"]["id"] == "twin-alex" and "worker" in status["runtime"]
    assert not {"soul", "execution_config", "endpoint", "key_present"} & status.keys()


def test_cross_chat_tracking_workspace_and_bounded_utf8_reads(product, monkeypatch, tmp_path):
    monkeypatch.setattr(service_module, "settings", replace(service_module.settings, workspace_root=tmp_path))
    _, context = origin(product)
    with product.transaction() as db:
        private = db.get(Chat, "private-alex")
        source = product.append_message(db, private, author_id="owner-alex", kind="human", body="Owner-only source")
        source_id = source.id
        node = WorkspaceNode(chat_id=private.id, relative_path="note.txt", source_message_ids=[source_id], granted_owner_ids=["owner-alex"])
        db.add(node); db.flush(); node_id = node.id
    path = tmp_path / "private-alex" / "note.txt"
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text("Owner workspace reference", encoding="utf-8")
    assert call(product, context, "list_workspace", {"chat_id": "private-alex"})["entries"][0]["id"] == node_id
    assert call(product, context, "read_workspace_file", {"chat_id": "private-alex", "id": node_id})["content"] == "Owner workspace reference"
    topic = call(product, context, "create_tracking", {"chat_id": "private-alex", "title": "Private topic", "description": "Owner subject", "source_message_ids": [source_id]})
    assert call(product, context, "get_tracking", {"chat_id": "private-alex", "id": topic["id"]})["id"] == topic["id"]
    with product.transaction() as db:
        sources = db.get(ContextManifest, context["context_manifest_id"]).sources
        assert any(s["kind"] == "chat_context" and s["chat_id"] == "private-alex" for s in sources)
        assert not product.provenance_allowed(db, product.membership(db, "owner-maya", "group-alex-maya")[1], sources)
    path.write_bytes(b"x" * 65537)
    with pytest.raises(HTTPException) as exc:
        call(product, context, "read_workspace_file", {"chat_id": "private-alex", "id": node_id})
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException):
        call(product, context, "save_summary", {"chat_id": "private-alex", "body": "Foreign", "source_message_ids": [source_id]})


def test_pending_approval_has_durable_private_inbox_and_cross_origin_status(product):
    request, context = origin(product, requester="owner-maya")
    action = {"tool": "create_reminder", "arguments": {"title": "Approved reminder", "chat_id": "group-alex-maya"}}
    row = approval(product, context, action)
    detail = product.chat_detail("owner-alex", "private-alex")
    assert [item["id"] for item in detail["approvals"]] == [row["id"]]
    notice = next(m for m in detail["messages"] if m["body"] == "Approval requested: Review action")
    assert notice["author_id"] == "twin-alex" and notice["causal_chain_id"] == request["causal_chain_id"]
    _, private_context = origin(product, "private-alex")
    assert call(product, private_context, "get_approval", {"id": row["id"]})["status"] == "pending"
    with pytest.raises(HTTPException):
        product.approval_for_execution("owner-alex", "private-alex", row["id"])
    with product.transaction() as db:
        stored = db.get(Approval, row["id"])
        assert stored.chat_id == "group-alex-maya" and stored.sdk_reference["action"] == action
        assert db.scalar(select(func.count()).select_from(Message).where(Message.sdk_output_key == "approval-notice:" + row["id"])) == 1
    with pytest.raises(HTTPException):
        call(product, context, "create_reminder", {"title": "No approval"})
    for malformed in [
        {"tool": "continue_automatic_conversation", "arguments": {}},
        {"tool": "update_tracking", "arguments": {"id": "x", "source_message_ids": []}},
        {"tool": "send_chat_message", "arguments": {"chat_id": "private-alex", "body": "Missing sources"}},
    ]:
        with pytest.raises(HTTPException):
            approval(product, context, malformed)


def test_reverse_cross_chat_mutations_share_sorted_locks(product):
    _first, a = origin(product)
    _second, b = origin(product, "group-team")
    # Both targets have three owners and can be renamed.
    product.add_members("owner-alex", "group-alex-maya", ["owner-jordan"], "all")
    barrier = Barrier(2)
    def rename(context, target):
        barrier.wait()
        return call(product, context, "rename_chat", {"chat_id": target, "title": "Updated " + target})
    with ThreadPoolExecutor(max_workers=2) as pool:
        left = pool.submit(rename, a, "group-team")
        right = pool.submit(rename, b, "group-alex-maya")
        assert left.result(timeout=10)["title"] == "Updated group-team"
        assert right.result(timeout=10)["title"] == "Updated group-alex-maya"


def test_retry_grant_consumed_once_only_when_full_and_safe_status_projection(product):
    old, _ = origin(product)
    product.fail_request(old["id"], "Offline failure")
    controller, context = origin(product)
    with product.transaction() as db:
        chain = db.get(CausalChain, controller["causal_chain_id"])
        chain.spent = chain.allowance
    consent = approval(product, context, {"tool": "retry_reply_request", "arguments": {"id": old["id"]}})
    with product.transaction() as db:
        db.get(Approval, consent["id"]).status = "approved"
    result = call(product, {**context, "approved_approval_id": consent["id"]}, "retry_reply_request", {"id": old["id"]}, "approved-effect")
    assert result["source"] == "tool_retry" and result["causal_chain_id"] == controller["causal_chain_id"]
    assert call(product, context, "get_reply_request", {"id": result["id"]})["id"] == result["id"]
    assert "execution_config" not in str(call(product, context, "get_reply_request", {"id": result["id"]}))
    assert call(product, {**context, "approved_approval_id": consent["id"]}, "retry_reply_request", {"id": old["id"]}, "approved-effect") == result
    with product.transaction() as db:
        chain = db.get(CausalChain, controller["causal_chain_id"])
        assert chain.spent == chain.allowance == 7
        assert db.scalar(select(func.count()).select_from(AllowanceGrant).where(AllowanceGrant.client_request_key == "approval:" + consent["id"])) == 1
        assert db.scalar(select(func.count()).select_from(ProductOperation).where(ProductOperation.operation_id == "approved-effect")) == 1
    old2, _ = origin(product); product.fail_request(old2["id"], "Offline failure")
    controller2, context2 = origin(product)
    consent2 = approval(product, context2, {"tool": "retry_reply_request", "arguments": {"id": old2["id"]}})
    with product.transaction() as db:
        db.get(Approval, consent2["id"]).status = "approved"
    call(product, {**context2, "approved_approval_id": consent2["id"]}, "retry_reply_request", {"id": old2["id"]})
    with product.transaction() as db:
        chain = db.get(CausalChain, controller2["causal_chain_id"])
        assert chain.spent == 1 and chain.allowance == 6
        assert not db.scalar(select(AllowanceGrant.id).where(AllowanceGrant.client_request_key == "approval:" + consent2["id"]))


def test_group_private_retry_requires_exact_consent_and_retains_frozen_environment(product):
    old, _ = origin(product, "private-alex")
    frozen = {"version": 1, "skills": [{"name": "frozen", "description": "Old skill"}],
              "credentials": [], "execution_enabled": True, "profile": {"soul": "Old persona"}}
    with product.transaction() as db:
        from app.models import ReplyRequest
        db.get(ReplyRequest, old["id"]).execution_config = frozen
    product.fail_request(old["id"], "Offline failure")
    controller, context = origin(product)
    with product.transaction() as db:
        spent = db.get(CausalChain, controller["causal_chain_id"]).spent
    with pytest.raises(HTTPException) as error:
        call(product, context, "retry_reply_request", {"id": old["id"]})
    assert error.value.status_code == 403
    with product.transaction() as db:
        assert db.get(CausalChain, controller["causal_chain_id"]).spent == spent
    consent = approval(product, context, {"tool": "retry_reply_request", "arguments": {"id": old["id"]}})
    assert "private execution tools" in consent["impact"] and consent["target"] == "Private task " + old["id"]
    with product.transaction() as db:
        db.get(Approval, consent["id"]).status = "approved"
    result = call(product, {**context, "approved_approval_id": consent["id"]}, "retry_reply_request", {"id": old["id"]})
    with product.transaction() as db:
        from app.models import ReplyRequest
        retried = db.get(ReplyRequest, result["id"])
        assert retried.execution_config == frozen
        assert retried.private_generation == db.get(ReplyRequest, old["id"]).private_generation == 0
    # A pre-feature task cannot acquire today's private tool catalog via retry.
    legacy, _ = origin(product, "private-alex")
    product.fail_request(legacy["id"], "Legacy failure")
    consent2 = approval(product, context, {"tool": "retry_reply_request", "arguments": {"id": legacy["id"]}})
    with product.transaction() as db:
        db.get(Approval, consent2["id"]).status = "approved"
    result2 = call(product, {**context, "approved_approval_id": consent2["id"]}, "retry_reply_request", {"id": legacy["id"]})
    with product.transaction() as db:
        assert db.get(ReplyRequest, result2["id"]).execution_config == {
            "version": 0, "skills": [], "credentials": [], "execution_enabled": False}


def test_report_cites_actual_authorized_foreign_chat_without_sharing_sources(product):
    _, context = origin(product)
    with product.transaction() as db:
        source_id = product.append_message(db, db.get(Chat, "private-alex"), author_id="owner-alex", kind="human", body="Private reference").id
    target = product.create_chat("owner-alex", ["owner-jordan"])["id"]
    result = call(product, context, "send_chat_message", {"chat_id": target, "body": "Prepared owner statement", "source_message_ids": [source_id]})
    assert any(row["id"] == result["message_id"] for row in product.chat_detail("owner-jordan", target)["messages"])
    with product.transaction() as db:
        message = db.get(Message, result["message_id"])
        assert not message.source_message_ids and not message.source_provenance
        sources = db.get(ContextManifest, context["context_manifest_id"]).sources
        assert any(row.get("chat_id") == "private-alex" and any(s["id"] == source_id for s in row["sources"]) for row in sources if row["kind"] == "chat_context")
        db.get(Message, source_id).state = "withdrawn"
    with pytest.raises(HTTPException):
        call(product, context, "send_chat_message", {"chat_id": target, "body": "Late statement", "source_message_ids": [source_id]})
