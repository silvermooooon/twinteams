"""Owner reset is destructive only inside disposable PG schemas and temporary directories."""
import asyncio
import shutil
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from agent_runtime.memory import MemorySource
from agent_runtime.memory.git import GitMemoryError
from fastapi import HTTPException
from sqlalchemy import select
from test_memory_dream import product as product  # noqa: PLC0414

from app import config, personal, twin_reset
from app import service as service_module
from app.models import Chat, ChatAttachment, Message, WorkspaceNode


@pytest.fixture(autouse=True)
def roots(product, monkeypatch, tmp_path):
    configured = replace(service_module.settings, workspace_root=tmp_path / "workspace")
    for module in (service_module, personal, twin_reset, config):
        monkeypatch.setattr(module, "settings", configured)


def key():
    return uuid4().hex


def test_reset_erases_private_and_git_legacy_preserves_configuration_files_group_and_owner(product):
    async def run():
        request = product.send_message("owner-alex", "private-alex", "Private message", key())["request"]
        context = product.get_runtime_context(request["id"])
        with product.transaction() as db:
            group = db.scalar(select(Chat).where(Chat.kind == "group"))
            group_id = group.id
        public = product.send_message("owner-maya", group_id, "Keep group text", key())["message"]
        workspace = personal.resolve_twin_workspace("owner-alex")
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "code.py").write_text("User code stays")
        (workspace / "skills/report").mkdir(parents=True)
        (workspace / "skills/report/SKILL.md").write_text("description: keep")
        with product.transaction() as db:
            node = WorkspaceNode(chat_id="private-alex", relative_path="code.py", source_message_ids=[request["source_message_id"]], granted_owner_ids=["owner-alex"])
            db.add(node)
            db.flush()
            node_id = node.id
        soul = personal.get_soul("owner-alex")
        personal.write_soul("owner-alex", "Owner custom persona", soul["version"])
        actor = personal.get_twin("owner-alex")
        store, other = personal.own_memory_store("owner-alex"), personal.own_memory_store("owner-maya")
        refs = [MemorySource("real-session", "real-event")]
        await store.write("MEMORY", "Private Git fact", sources=refs, expected_version=None)
        await store.legacy.write("old", "Legacy private fact", sources=refs, expected_version=None)
        await other.write("MEMORY", "Other owner's fact", sources=refs, expected_version=None)
        async with store.draft() as draft:
            await draft.write("late", "Old draft", sources=refs, expected_version=None)
            result = product.personal_reset("owner-alex", key(), 0)
            assert result["status"] == "completed" and result["generation"] == 1
            with pytest.raises((HTTPException, GitMemoryError)):
                await draft.publish("old-operation")
        assert not store.scope.exists()
        with pytest.raises(HTTPException):
            await store.read("MEMORY")
        assert not store.scope.exists()  # Old factory cannot recreate a purged repository.
        fresh = personal.own_memory_store("owner-alex")
        assert await fresh.read("MEMORY") is None
        assert (await other.read("MEMORY")).content == "Other owner's fact"
        assert personal.get_soul("owner-alex")["content"] == "Owner custom persona"
        assert personal.get_twin("owner-alex") == actor
        assert (workspace / "code.py").read_text() == "User code stays"
        assert (workspace / "skills/report/SKILL.md").exists()
        assert product.workspace_file("owner-alex", "private-alex", node_id).read_text() == "User code stays"
        assert product.chat_detail("owner-alex", "private-alex")["messages"] == []
        with product.transaction() as db:
            assert db.get(WorkspaceNode, node_id).source_message_ids == []
            assert db.get(Message, public["id"]).body == "Keep group text"
            assert db.get(Message, request["source_message_id"]).body == ""
        with pytest.raises(HTTPException):
            product.authorize_runtime_generation(context)
        with pytest.raises(HTTPException):
            product.get_runtime_context(request["id"], include_completed=True)
    asyncio.run(run())


def test_pending_cleanup_retry_and_old_idempotency_key_cannot_clear_new_content(product, monkeypatch):
    store = personal.own_memory_store("owner-alex")
    asyncio.run(store.read("MEMORY"))
    original = shutil.rmtree
    def unavailable(path, *args, **kwargs):
        if Path(path) == store.scope:
            raise OSError("Synthetic cleanup failure")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(twin_reset.shutil, "rmtree", unavailable)
    operation = key()
    pending = product.personal_reset("owner-alex", operation, 0)
    assert pending["status"] == "pending"
    assert product.reset_status("owner-alex")["idempotency_key"] == operation
    with pytest.raises(HTTPException):
        product.send_message("owner-alex", "private-alex", "Blocked during pending cleanup", key())
    monkeypatch.setattr(twin_reset.shutil, "rmtree", original)
    result = product.personal_reset("owner-alex", operation, 0)
    assert result["status"] == "completed"
    fresh = product.send_message("owner-alex", "private-alex", "New private content", key())["message"]
    assert product.personal_reset("owner-alex", operation, 0) == result
    assert product.chat_detail("owner-alex", "private-alex")["messages"][0]["id"] == fresh["id"]
    with pytest.raises(HTTPException):
        product.personal_reset("owner-alex", key(), 0)


def test_owner_scope_symlink_is_rejected_without_touching_another_owner(product):
    own, other = personal.own_memory_store("owner-alex"), personal.own_memory_store("owner-maya")
    asyncio.run(other.write("MEMORY", "Keep", sources=[MemorySource("s", "e")], expected_version=None))
    own.scope.parent.mkdir(parents=True, exist_ok=True)
    own.scope.symlink_to(other.scope, target_is_directory=True)
    result = product.personal_reset("owner-alex", key(), 0)
    assert result["status"] == "pending" and own.scope.is_symlink()
    assert asyncio.run(other.read("MEMORY")).content == "Keep"


def test_two_resets_with_attachment_shell_are_repeatable_and_keep_group_attachment(product):
    data = b"private attachment"
    # Build the same immutable on-disk attachment shape as the product uploader.
    attachment = product.upload_attachment("owner-alex", "private-alex", "note.txt", data)
    product.send_message("owner-alex", "private-alex", "File", key(), attachment_ids=[attachment["id"]])
    first_key = key()
    assert product.personal_reset("owner-alex", first_key, 0)["status"] == "completed"
    with product.transaction() as db:
        assert db.get(ChatAttachment, attachment["id"]).relative_path == "reset-removed"
    product.send_message("owner-alex", "private-alex", "After first reset", key())
    second = product.personal_reset("owner-alex", key(), 1)
    assert second["status"] == "completed" and second["generation"] == 2
    latest = product.send_message("owner-alex", "private-alex", "After second reset", key())["message"]
    assert product.personal_reset("owner-alex", first_key, 0)["generation"] == 1
    assert product.chat_detail("owner-alex", "private-alex")["messages"][0]["id"] == latest["id"]


def test_missing_private_attachment_bytes_are_idempotent_and_shared_group_blob_is_retained(product):
    missing = product.upload_attachment("owner-alex", "private-alex", "missing.txt", b"Already gone")
    shared = product.upload_attachment("owner-alex", "private-alex", "shared.txt", b"Shared group bytes")
    with product.transaction() as db:
        absent_row, shared_row = db.get(ChatAttachment, missing["id"]), db.get(ChatAttachment, shared["id"])
        product._attachment_path(absent_row).unlink()
        shared_path = product._attachment_path(shared_row)
        group = db.scalar(select(Chat).where(Chat.kind == "group"))
        message = product.append_message(db, group, author_id="owner-maya", kind="human", body="Keep group attachment",
            attachment_ids=[shared["id"]])
        group_message_id = message.id
    assert product.personal_reset("owner-alex", key(), 0)["status"] == "completed"
    assert shared_path.read_bytes() == b"Shared group bytes"
    with product.transaction() as db:
        assert db.get(ChatAttachment, missing["id"]).relative_path == "reset-removed"
        assert db.get(ChatAttachment, shared["id"]).relative_path != "reset-removed"
        assert db.get(Message, group_message_id).attachment_ids == [shared["id"]]
