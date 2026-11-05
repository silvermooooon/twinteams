"""Stage a reviewed reply in a private question; only an owner option can publish it."""
from sqlalchemy import select

from .attachments import MAX_ATTACHMENT_BYTES, MAX_ATTACHMENTS
from .deliverables import save_snapshots, snapshot_destination
from .message_references import referenced_human_message
from .models import Actor, Chat, ChatAttachment, Receipt, now
from .question_models import QuestionRequest


def reply_as_owner(service, db, args, context):
    from . import questions
    from .conversation_context import message_source
    from .service import digest, fail
    request, twin, private, member, _ = service._message_tool_origin(db, context)
    if (private.kind != "private" or private.private_owner_id != twin.owner_id or request.background
            or request.source != "private" or request.requester_id != twin.owner_id
            or not request.session_id or context.get("runtime_session_id") != request.session_id):
        fail(403, "Replying as the owner requires a fresh instruction to their primary private Duet")
    anchor, original, target, instruction, files = reviewed_reply(service, db, args, request, twin, private, member)
    question_id = "owner-reply:" + digest([instruction.id, anchor.id if anchor else target.id])
    members = sorted(m.owner_id for m in service.members(db, target.id) if m.owner_id != twin.owner_id)
    frozen = {"arguments": args, "chat_id": target.id, "message_id": original.id if original else None, "audience": members,
              "files": [[file.id, file.sha256, file.name] for file in files]}
    existing = db.get(QuestionRequest, question_id)
    if existing:
        if not existing.owner_reply or any(existing.owner_reply.get(key) != value for key, value in frozen.items()):
            fail(409, "This instruction already proposed a different reply")
        questions.origin(service, db, existing)
        row = existing
    else:
        preview = {"destination": service.chat_title(db, target, twin.owner_id),
                   "audience": [db.get(Actor, owner).name for owner in members],
                   "body": args["body"], "files": [file.name for file in files]}
        if original:
            visible_source_files = [db.get(ChatAttachment, identifier) for identifier in (original.attachment_ids or [])]
            preview.update(author_name=db.get(Actor, original.author_id).name, original_body=original.body,
                original_files=[file.name for file in visible_source_files if file and not file.private_owner_input])
        private_ids = {file.message_id for file in files if file.chat_id == private.id}
        if anchor:
            private_ids.add(anchor.id)
        if args.get("review_message_id"):
            private_ids.add(args["review_message_id"])
        sources = [message_source(db, service.visible_message(db, member, mid)) for mid in private_ids]
        row = questions.add_question(service, db, request, private, private, member,
            "Send this reply as you?" if original else "Send this message as you?",
            ["Confirm send", "Cancel"], context, question_id=question_id, owner_reply={**frozen, "preview": preview},
            additional_sources=sources)
    return confirmation_result(service, db, row)


def confirmation_result(service, db, row):
    from . import questions
    return {"status": {"pending": "confirmation_required", "answered": "sent", "cancelled": "cancelled"}[row.status],
            "sent": row.status == "answered", "chat_id": row.chat_id, "question": questions.dto(service, db, row)}


def reviewed_reply(service, db, args, request, twin, private, member):
    from .service import fail
    reference_id, target_id = args.get("reference_message_id"), args.get("chat_id")
    body, ids = args.get("body"), args.get("attachment_ids", [])
    if ((reference_id is not None and not isinstance(reference_id, str))
            or (target_id is not None and not isinstance(target_id, str))
            or (reference_id is None and not target_id) or not isinstance(body, str)
            or not isinstance(ids, list) or len(ids) > MAX_ATTACHMENTS
            or not all(isinstance(value, str) for value in ids) or len(set(ids)) != len(ids)
            or not (body.strip() or ids)):
        fail(400, "Provide a reference or destination chat, and message text or up to four distinct published attachments")
    anchor = original = None
    if reference_id is not None:
        anchor = service.visible_message(db, member, reference_id)
        original = referenced_human_message(db, anchor) if anchor.kind == "human" and anchor.author_id == twin.owner_id else None
        if not original or original.kind != "human" or original.author_id == twin.owner_id:
            fail(403, "Select a human message your owner referenced in their private conversation")
        if target_id is not None and target_id != original.chat_id:
            fail(400, "The destination chat must match the referenced message")
        target_id = original.chat_id
    target, target_member = service.membership(db, twin.owner_id, target_id)
    if target.kind != "group":
        fail(403, "Send as the owner only to a human conversation")
    if original:
        service.visible_message(db, target_member, original.id)
    instruction = service.visible_message(db, member, request.source_message_id)
    if (instruction.kind != "human" or instruction.command_owner_id != twin.owner_id
            or (anchor and instruction.seq <= anchor.seq)):
        fail(403, "Review privately first; a later owner instruction is required to reply")
    if body.strip() and body not in instruction.body:
        review_id = args.get("review_message_id")
        review = service.visible_message(db, member, review_id) if isinstance(review_id, str) else None
        if (not review or review.kind != "twin" or review.author_id != twin.id
                or review.seq >= instruction.seq or (anchor and review.seq <= anchor.seq) or body not in review.body):
            fail(400, "Use the exact reply text already shown in a private review or supplied by the owner")
    files = []
    for identifier in ids:
        row = db.get(ChatAttachment, identifier)
        if not row or not row.message_id:
            fail(404, "Reply attachment is unavailable")
        if row.chat_id == private.id and row.owner_id == twin.owner_id:
            shown = service.visible_message(db, member, row.message_id)
            if (shown.seq >= instruction.seq or (anchor and shown.seq <= anchor.seq) or shown.kind != "twin"
                    or shown.author_id != twin.id or row.id not in shown.attachment_ids):
                fail(400, "Show the file to your owner before asking to send it")
        elif (not original or row.chat_id != target.id or row.message_id != original.id
                or identifier not in original.attachment_ids or row.private_owner_input):
            fail(403, "Attach a file shown in your private conversation or on the referenced message")
        files.append(row)
    if sum(row.size for row in files) > MAX_ATTACHMENT_BYTES:
        fail(413, "Message attachments are limited to 20 MiB in total")
    return anchor, original, target, instruction, files


def decide(service, db, question, choice):
    from . import questions
    from .service import fail

    frozen = question.owner_reply
    for chat_id in sorted({question.chat_id, frozen["chat_id"]}):
        db.scalar(select(Chat.id).where(Chat.id == chat_id).with_for_update())
    db.refresh(question, with_for_update=True)
    request, private, member = questions.origin(service, db, question)
    if question.status != "pending":
        if question.answer != choice:
            fail(409, "This send confirmation already has a different decision")
        return questions.dto(service, db, question)
    if choice == "Confirm send":
        args = frozen["arguments"]
        twin = db.get(Actor, request.target_twin_id)
        _, original, target, _, files = reviewed_reply(service, db, args, request, twin, private, member)
        audience = sorted(m.owner_id for m in service.members(db, target.id) if m.owner_id != twin.owner_id)
        if ((original.id if original else None) != frozen["message_id"] or target.id != frozen["chat_id"] or audience != frozen["audience"]
                or [[file.id, file.sha256, file.name] for file in files] != frozen["files"]):
            fail(409, "Recipients, original message or files changed; this reply cannot be sent")
        posted = publish(service, db, question, request, twin, original, target, files, args)
        question.owner_reply = {**frozen, "sent_message_id": posted.id}
    elif choice != "Cancel":
        fail(422, "Choose Confirm send or Cancel")
    answer = service.append_message(db, private, kind="human", author_id=question.owner_id,
        body=choice, reply_to_message_id=question.message_id, causal_chain_id=request.causal_chain_id)
    db.add(Receipt(message_id=answer.id, owner_id=question.owner_id))
    question.status = "answered" if choice == "Confirm send" else "cancelled"
    question.answer, question.answer_message_id, question.answered_at = choice, answer.id, now()
    return questions.dto(service, db, question)


def publish(service, db, question, request, twin, original, target, files, args):
    snapshots = []
    for position, file in enumerate(files):
        identifier, path = snapshot_destination(target.id, question.id, position, file.name)
        info = {key: getattr(file, key) for key in ("sha256", "size", "mime_type", "kind", "width", "height")}
        data = service.verified_attachment_bytes(service._attachment_path(file), info)
        snapshots.append((identifier, path, data, info, file))
    attached = save_snapshots(db, request, twin.owner_id, target, snapshots)
    msg = service.append_prepared_message(db, target, request, author_id=twin.owner_id, kind="human",
        body=args["body"], reply_to_message_id=original.id if original else None, attachment_ids=[file.id for file in attached],
        sdk_output_key=question.id)
    for file in attached:
        file.message_id = msg.id
    db.add(Receipt(message_id=msg.id, owner_id=twin.owner_id))
    db.flush()
    return msg
