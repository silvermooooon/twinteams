"""New topic membership does not expand an earlier approved file audience."""
from dataclasses import replace
from uuid import uuid4

import pytest
from duet_test_support import approved_chat
from fastapi import HTTPException
from test_deliverables import present
from test_duet_message_delivery import origin, tool
from test_file_workspace_acceptance import file_id
from test_memory_dream import product as product  # noqa: PLC0414

from app import service as service_module
from app.models import Approval, ChatAttachment, DuetContactGrant


def test_standing_invitation_keeps_old_file_private_until_explicit_reshare(product, tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    monkeypatch.setattr(service_module, "settings", replace(service_module.settings, workspace_root=root))
    chat = approved_chat(product, "duet-file-invitation")
    _, context = origin(product)
    context = {**context, "runtime_session_id": "offline-file-session", "runtime_run_id": "offline-file-run"}
    path = root / "private-alex" / "report.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"# File approved for Maya only\n")

    first = present(product, context, [path], chat_id=chat, caption="Published report")
    assert first["status"] == "approval_required"
    with product.transaction() as db:
        db.get(Approval, first["approval"]["id"]).status = "approved"
    published = present(product, context, [path], chat_id=chat, caption="Published report")
    attachment = published["message"]["attachments"][0]
    with product.transaction() as db:
        old = db.get(ChatAttachment, attachment["id"])
        assert old.granted_owner_ids == ["owner-alex", "owner-maya"]
        relative = old.relative_path.removeprefix(chat + "/")
        for owner in ("owner-alex", "owner-maya", "owner-jordan"):
            for peer in ("owner-alex", "owner-maya", "owner-jordan"):
                if owner != peer:
                    db.add(DuetContactGrant(owner_id=owner, peer_owner_id=peer))
    assert product.attachment_content("owner-maya", chat, attachment["id"])[0] == path.read_bytes()

    invitation = tool(product, context, "invite_duet_participants", {"chat_id": chat,
        "participant_owner_ids": ["owner-jordan"], "requirements": "Review the published discussion",
        "mention_owner_ids": []}, operation=uuid4().hex)
    assert invitation["status"] == "active"
    jordan = product.chat_detail("owner-jordan", chat)
    report = next(message for message in jordan["messages"] if message["id"] == published["message_id"])
    assert report["body"] == "Published report" and report["attachments"] == []
    assert product.attachment_content("owner-maya", chat, attachment["id"])[0] == path.read_bytes()
    with pytest.raises(HTTPException):
        product.attachment_content("owner-jordan", chat, attachment["id"])
    with pytest.raises(HTTPException):
        product.workspace_file("owner-jordan", chat, file_id(relative))
    _, jordan_context = origin(product, "owner-jordan")
    read = tool(product, jordan_context, "read_duet_topic", {"chat_id": chat})
    assert next(message for message in read["messages"] if message["id"] == report["id"])["attachments"] == []

    expanded = present(product, context, [path], chat_id=chat)
    assert expanded["status"] == "approval_required"
    assert expanded["approval"]["id"] != first["approval"]["id"]
    with product.transaction() as db:
        decision = db.get(Approval, expanded["approval"]["id"])
        assert decision.sdk_reference["file_shares"][0]["peer_owner_ids"] == ["owner-jordan", "owner-maya"]
        decision.status = "approved"
    reshared = present(product, context, [path], chat_id=chat)
    new_attachment = reshared["message"]["attachments"][0]
    assert product.attachment_content("owner-jordan", chat, new_attachment["id"])[0] == path.read_bytes()
    with product.transaction() as db:
        assert db.get(ChatAttachment, new_attachment["id"]).granted_owner_ids == ["owner-alex", "owner-jordan", "owner-maya"]
        assert db.get(ChatAttachment, attachment["id"]).granted_owner_ids == ["owner-alex", "owner-maya"]
    with pytest.raises(HTTPException):
        product.attachment_content("owner-jordan", chat, attachment["id"])
