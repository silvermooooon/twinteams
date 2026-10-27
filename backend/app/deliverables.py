"""Explicit workspace-file delivery using ordinary chat attachments and messages."""
import mimetypes
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from .attachments import MAX_ATTACHMENT_BYTES, MAX_ATTACHMENTS, inspect_upload
from .models import Chat, ChatAttachment


def present_files(service, db, args, context):
    from .service import digest, fail, settings
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
        source_attachment = db.scalar(select(ChatAttachment).where(ChatAttachment.relative_path == str(path.relative_to(settings.workspace_root))))
        if source_attachment and source_attachment.private_owner_input and not (target.kind == "private" and target.private_owner_id == twin.owner_id):
            fail(403, "Private owner-input attachments cannot be published to another audience")
        identifier = uuid5(NAMESPACE_URL, "twinteams:present:" + context["operation_id"] + ":" + str(position)).hex
        destination = settings.workspace_root / target.id / "uploads" / identifier / path.name
        if any(part.is_symlink() for part in [destination, *destination.parents] if part.is_relative_to(settings.workspace_root)):
            fail(403, "Delivery snapshots cannot use symlinks")
        # An unknown DB commit can leave this exact operation's protected snapshot.
        # Reuse it rather than replacing the bytes with a later source-file edit.
        capture = destination if destination.exists() else path
        with capture.open("rb") as stream:
            data = stream.read(MAX_ATTACHMENT_BYTES + 1)
        total += len(data)
        if total > MAX_ATTACHMENT_BYTES:
            fail(413, "Message attachments are limited to 20 MiB in total")
        info = inspect_upload(path.name, mimetypes.guess_type(path.name)[0] or "application/octet-stream", data)
        selected.append((identifier, destination, data, info))
    attachments = []
    for identifier, destination, data, info in selected:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            with destination.open("xb") as stream:
                stream.write(data)
        attachment = ChatAttachment(id=identifier, chat_id=target.id, owner_id=twin.owner_id,
            name=destination.name, relative_path=str(destination.relative_to(settings.workspace_root)),
            request_id=request.id, **info)
        db.add(attachment)
        attachments.append(attachment)
    # Current-chat publication keeps ordinary output source visibility. Explicit
    # cross-chat delivery follows prepared-send semantics without granting access
    # to the original private records or to the mutable source file.
    provenance = inherited if target.id == origin.id else []
    source_ids = [source["id"] for source in provenance if source.get("kind") == "message"]
    message = service.append_message(db, target, author_id=twin.id, kind="twin", body=caption,
        attachment_ids=[row.id for row in attachments], request_id=request.id,
        causal_chain_id=service.request_chain(db, request).id,
        source_provenance=provenance, source_message_ids=source_ids,
        source_floor_seq=min([service.visible_message(db, member, sid).seq for sid in source_ids], default=target.next_message_seq),
        sdk_output_key="tool:" + digest(context["operation_id"]))
    for attachment in attachments:
        attachment.message_id = message.id
    db.flush()
    service.route_committed_message(db, target, message)
    return {"status": "sent", "sent": True, "chat_id": target.id, "message_id": message.id,
            "message": service.message_json(db, message, target_member)}
