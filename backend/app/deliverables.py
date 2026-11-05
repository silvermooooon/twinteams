"""Explicit workspace-file delivery using ordinary chat attachments and messages."""
import mimetypes
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import or_, select

from .attachments import MAX_ATTACHMENT_BYTES, MAX_ATTACHMENTS, inspect_upload
from .models import Actor, Approval, Chat, ChatAttachment, Membership, Message, ProductOperation


def snapshot_destination(chat_id, operation_id, position, name):
    from .service import fail, settings
    identifier = uuid5(NAMESPACE_URL, "twinteams:present:" + operation_id + ":" + str(position)).hex
    destination = settings.workspace_root / chat_id / "uploads" / identifier / name
    if any(part.is_symlink() for part in [destination, *destination.parents] if part.is_relative_to(settings.workspace_root)):
        fail(403, "Delivery snapshots cannot use symlinks")
    return identifier, destination


def save_snapshots(db, request, owner_id, target, selected):
    from .service import settings
    attachments = []
    for identifier, destination, data, info, _source in selected:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            with destination.open("xb") as stream:
                stream.write(data)
        attachment = ChatAttachment(id=identifier, chat_id=target.id, owner_id=owner_id,
            name=destination.name, relative_path=str(destination.relative_to(settings.workspace_root)),
            request_id=request.id, **info)
        db.add(attachment)
        attachments.append(attachment)
    return attachments


def prepare_files(service, db, args, context):
    from .service import fail, settings
    request, twin, origin, member, inherited = service._message_tool_origin(db, context)
    target, target_member = service.membership(db, twin.owner_id, args.get("chat_id") or origin.id, True)
    from .duet_conversations import require_active
    require_active(db, origin)
    require_active(db, target)
    if target.kind == "private" and target.private_owner_id != twin.owner_id:
        fail(403, "Deliverables require your own private chat or an approved Duet conversation")
    if target.kind == "private" and target.reset_pending:
        fail(409, "Private chat reset is pending")
    paths, caption = args.get("paths"), args.get("caption", "")
    if (not isinstance(paths, list) or not 1 <= len(paths) <= MAX_ATTACHMENTS
            or any(not isinstance(path, str) or not path.strip() for path in paths)
            or not isinstance(caption, str)):
        fail(400, "Present one to four file paths and an optional text caption")
    private_id = db.scalar(select(Chat.id).where(Chat.private_owner_id == twin.owner_id, Chat.kind == "private"))
    owner_root = settings.workspace_root / private_id
    roots = [(private_id, owner_root)]
    if origin.id != private_id:
        roots.append((origin.id, settings.workspace_root / origin.id))
    selected, seen, total = [], set(), 0
    for position, value in enumerate(paths):
        raw = Path(value)
        if ".." in raw.parts or "\\" in value or "\x00" in value:
            fail(403, "Invalid delivery path")
        absolute = raw if raw.is_absolute() else owner_root / raw
        source = next(((chat_id, root, absolute.relative_to(root)) for chat_id, root in roots if absolute.is_relative_to(root)), None)
        if source is None:
            fail(403, "Delivery files must belong to your workspace or the current chat workspace")
        source_chat, _root, relative = source
        path = service._workspace_path_db(db, twin.owner_id, source_chat, relative)
        if path in seen:
            fail(400, "Present distinct files")
        seen.add(path)
        identifier, destination = snapshot_destination(target.id, context["operation_id"], position, path.name)
        # An unknown DB commit can leave this exact operation's protected snapshot.
        # Reuse it rather than replacing the bytes with a later source-file edit.
        capture = destination if destination.exists() else path
        with capture.open("rb") as stream:
            data = stream.read(MAX_ATTACHMENT_BYTES + 1)
        total += len(data)
        if total > MAX_ATTACHMENT_BYTES:
            fail(413, "Message attachments are limited to 20 MiB in total")
        info = inspect_upload(path.name, mimetypes.guess_type(path.name)[0] or "application/octet-stream", data)
        selected.append((identifier, destination, data, info, path))
    return request, twin, origin, member, inherited, target, target_member, selected, caption


def file_scope(service, db, target, twin, selected):
    peers = sorted(member.owner_id for member in service.members(db, target.id) if member.owner_id != twin.owner_id)
    return [{"path": str(source), "sha256": info["sha256"], "peer_owner_ids": peers}
            for _identifier, _destination, _data, info, source in selected]


def present_files(service, db, args, context):
    from .service import digest
    request, twin, origin, member, inherited, target, target_member, selected, caption = prepare_files(service, db, args, context)
    sharing = file_scope(service, db, target, twin, selected) if target.kind == "duet" else []
    if sharing:
        approvals = list(db.scalars(select(Approval).where(Approval.approver_id == twin.owner_id,
            Approval.status.in_(["approved", "pending"]))))
        granted = [file for approval in approvals if approval.status == "approved"
                   for file in approval.sdk_reference.get("file_shares", [])]
        authorized = all(all(any(file["path"] == prior["path"] and file["sha256"] == prior["sha256"]
            and peer in prior["peer_owner_ids"] for prior in granted) for peer in file["peer_owner_ids"]) for file in sharing)
        if not authorized:
            if context.get("approved_approval_id"):
                from .service import fail
                fail(409, "File content or recipients changed since owner approval; request new approval")
            action = {"tool": "present_files", "arguments": args}
            pending = next((approval for approval in approvals if approval.status == "pending"
                and approval.sdk_reference.get("action") == action
                and approval.sdk_reference.get("file_shares") == sharing), None)
            if pending:
                approval = service.approval_json(pending)
            else:
                names = ", ".join(destination.name for _, destination, _, _, _ in selected)
                approval = service._tool_dispatch(db, "create_approval", {
                    "title": "Share files: " + names,
                    "description": "Allow your Duet to share these file versions: " + names,
                    "target": ", ".join(service.twin(db, peer).name for peer in sharing[0]["peer_owner_ids"]),
                    "impact": "Recipients can read and download these files. This authorization is reused for the same files and recipients.",
                    "action": action,
                }, context, origin, member)
            return {"status": "approval_required", "sent": False, "chat_id": target.id, "approval": approval}
    attachments = save_snapshots(db, request, twin.owner_id, target, selected)
    payload = {"author_id": twin.id, "kind": "twin", "body": caption,
        "attachment_ids": [row.id for row in attachments], "sdk_output_key": "tool:" + digest(context["operation_id"])}
    if target.kind == "duet" or target.id != origin.id:
        message = service.append_prepared_message(db, target, request, **payload)
    else:
        source_ids = [source["id"] for source in inherited if source.get("kind") == "message"]
        message = service.append_message(db, target, request_id=request.id,
            causal_chain_id=service.request_chain(db, request).id,
            source_provenance=inherited, source_message_ids=source_ids,
            source_floor_seq=min([service.visible_message(db, member, sid).seq for sid in source_ids], default=target.next_message_seq),
            **payload)
    for attachment in attachments:
        attachment.message_id = message.id
    db.flush()
    result = {"status": "sent" if message.state == "published" else "pending_budget",
        "sent": message.state == "published", "chat_id": target.id,
        "message_id": message.id, "state": message.state,
        "shared_files": [{**scope, "attachment_id": row.id} for scope, row in zip(sharing, attachments)]}
    if message.state == "published":
        result["message"] = service.message_json(db, message, target_member)
    return result


def shared_files(service, db, owner_id, human_chat_id):
    """Project delivered file snapshots without widening their approved audience."""
    from .service import iso
    chat, _ = service.membership(db, owner_id, human_chat_id)
    if chat.kind != "group":
        return []
    humans = {member.owner_id for member in service.members(db, chat.id)}
    operations = db.scalars(select(ProductOperation).where(ProductOperation.name == "present_files",
        ProductOperation.owner_id.in_(humans),
        ProductOperation.result["shared_files"][0]["attachment_id"].as_string().is_not(None)))
    attachment_ids = {share["attachment_id"] for operation in operations
        for share in operation.result.get("shared_files", [])
        if owner_id in {operation.owner_id, *share["peer_owner_ids"]}
        and set(share["peer_owner_ids"]) & (humans - {operation.owner_id})}
    rows = db.execute(select(ChatAttachment, Message.id, Actor.name).join(Message,
        Message.id == ChatAttachment.message_id).join(Chat, Chat.id == Message.chat_id)
        .join(Membership, Membership.chat_id == Chat.id).join(Actor,
            (Actor.owner_id == ChatAttachment.owner_id) & (Actor.kind == "twin"))
        .where(ChatAttachment.id.in_(attachment_ids), Membership.owner_id == owner_id,
            Membership.left_at.is_(None), Message.state == "published", Message.seq >= Membership.lower_seq,
            or_(Chat.kind != "group", Message.kind == "human"),
            or_(Chat.kind != "private", Chat.private_owner_id == owner_id))
        .order_by(ChatAttachment.created_at.desc(), ChatAttachment.id))
    return [service.attachment_json(attachment) | {
        "owner_id": attachment.owner_id, "shared_by_name": name, "created_at": iso(attachment.created_at),
        "chat_id": attachment.chat_id, "message_id": message_id,
    } for attachment, message_id, name in rows]
