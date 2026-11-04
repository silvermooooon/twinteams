"""Publish an owner reply only to the human message the owner referenced."""
from sqlalchemy import select

from .attachments import MAX_ATTACHMENT_BYTES, MAX_ATTACHMENTS
from .deliverables import save_snapshots, snapshot_destination
from .message_references import referenced_human_message
from .models import ChatAttachment, Message, Receipt


def reply_as_owner(service, db, args, context):
    from .service import digest, fail
    request, twin, private, member, _ = service._message_tool_origin(db, context)
    if (private.kind != "private" or private.private_owner_id != twin.owner_id or request.background
            or request.source != "private" or request.requester_id != twin.owner_id
            or not request.session_id or context.get("runtime_session_id") != request.session_id):
        fail(403, "Replying as the owner requires a fresh instruction to their primary private Duet")
    reference_id = args.get("reference_message_id")
    body, ids = args.get("body"), args.get("attachment_ids", [])
    if (not isinstance(reference_id, str) or not isinstance(body, str)
            or not isinstance(ids, list) or len(ids) > MAX_ATTACHMENTS
            or not all(isinstance(value, str) for value in ids) or len(set(ids)) != len(ids)
            or not (body.strip() or ids)):
        fail(400, "Provide a reference, reply text or up to four distinct published attachments")
    anchor = service.visible_message(db, member, reference_id)
    original = referenced_human_message(db, anchor) if anchor.kind == "human" and anchor.author_id == twin.owner_id else None
    if not original or original.kind != "human" or original.author_id == twin.owner_id:
        fail(403, "Select a human message your owner referenced in their private conversation")
    target, target_member = service.membership(db, twin.owner_id, original.chat_id)
    if target.kind != "group":
        fail(403, "The referenced message must belong to a human conversation")
    service.visible_message(db, target_member, original.id)
    instruction = service.visible_message(db, member, request.source_message_id)
    if instruction.kind != "human" or instruction.command_owner_id != twin.owner_id or instruction.seq <= anchor.seq:
        fail(403, "Review privately first; a later owner instruction is required to reply")
    if body.strip() and body not in instruction.body:
        review_id = args.get("review_message_id")
        review = service.visible_message(db, member, review_id) if isinstance(review_id, str) else None
        if (not review or review.kind != "twin" or review.author_id != twin.id
                or not anchor.seq < review.seq < instruction.seq or body not in review.body):
            fail(400, "Use the exact reply text already shown in a private review or supplied by the owner")
    files = []
    for identifier in ids:
        row = db.get(ChatAttachment, identifier)
        if not row or not row.message_id:
            fail(404, "Reply attachment is unavailable")
        if row.chat_id == private.id and row.owner_id == twin.owner_id:
            shown = service.visible_message(db, member, row.message_id)
            if (not anchor.seq < shown.seq < instruction.seq or shown.kind != "twin"
                    or shown.author_id != twin.id or row.id not in shown.attachment_ids):
                fail(400, "Show the file to your owner before asking to send it")
        elif (row.chat_id != target.id or row.message_id != original.id
                or identifier not in original.attachment_ids or row.private_owner_input):
            fail(403, "Attach a file shown in your private conversation or on the referenced message")
        files.append(row)
    if sum(row.size for row in files) > MAX_ATTACHMENT_BYTES:
        fail(413, "Message attachments are limited to 20 MiB in total")
    # Repeated tool calls from the same owner instruction cannot post a second reply.
    key = "owner-reply:" + digest([instruction.id, anchor.id])
    fingerprint = digest([body, [[row.id, row.sha256] for row in files]])
    existing = db.scalar(select(Message).where(Message.sdk_output_key == key))
    if existing:
        if existing.command_fingerprint != fingerprint:
            fail(409, "This instruction already sent a different reply")
        service.visible_message(db, target_member, existing.id)
        return {"sent": True, "status": "sent", "chat_id": target.id, "message_id": existing.id,
                "message": service.message_json(db, existing, target_member)}
    snapshots = []
    for position, row in enumerate(files):
        identifier, path = snapshot_destination(target.id, context["operation_id"], position, row.name)
        info = {key: getattr(row, key) for key in ("sha256", "size", "mime_type", "kind", "width", "height")}
        data = service.verified_attachment_bytes(service._attachment_path(row), info)
        snapshots.append((identifier, path, data, info, row))
    attached = save_snapshots(db, request, twin.owner_id, target, snapshots)
    msg = service.append_prepared_message(db, target, request, author_id=twin.owner_id, kind="human",
        body=args["body"], reply_to_message_id=original.id, attachment_ids=[file.id for file in attached],
        sdk_output_key=key, command_fingerprint=fingerprint)
    for file in attached:
        file.message_id = msg.id
    db.add(Receipt(message_id=msg.id, owner_id=twin.owner_id))
    db.flush()
    return {"sent": True, "status": "sent", "chat_id": target.id, "message_id": msg.id,
            "message": service.message_json(db, msg, target_member)}
