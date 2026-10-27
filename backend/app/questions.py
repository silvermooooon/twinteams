"""One focused, nonblocking owner question using existing messages and FIFO."""
import json
from datetime import UTC

from sqlalchemy import or_, select

from .credentials import runtime_origin
from .models import Actor, Chat, ContextManifest, Message, ProductOperation, ReplyRequest, now
from .question_models import QuestionRequest


def dto(service, db, row):
    return {key: getattr(row, key) for key in ("id", "message_id", "origin_chat_id", "question", "options", "status", "answer", "answer_message_id")} | {
        "origin_chat_title": service.chat_title(db, db.get(Chat, row.origin_chat_id), row.owner_id),
        "created_at": row.created_at.astimezone(UTC).isoformat(), "answered_at": row.answered_at.astimezone(UTC).isoformat() if row.answered_at else None}


def origin(service, db, row):
    from .service import fail
    request, chat, member = runtime_origin(service, db, row.owner_id, row.origin_request_id, lock=False)
    inbox_member = service.membership(db, row.owner_id, row.chat_id)[1]
    service.visible_message(db, inbox_member, row.message_id)
    if not service.provenance_allowed(db, member, row.source_provenance):
        fail(409, "The question's original context is no longer authorized")
    return request, chat, member


def for_chat(service, db, chat, member, message_ids):
    if chat.kind != "private" or chat.private_owner_id != member.owner_id or not message_ids:
        return []
    rows = db.scalars(select(QuestionRequest).where(QuestionRequest.owner_id == member.owner_id,
        QuestionRequest.chat_id == chat.id, QuestionRequest.message_id.in_(message_ids)))
    result = []
    for row in rows:
        try:
            origin(service, db, row)
        except Exception as error:
            from fastapi import HTTPException
            if not isinstance(error, HTTPException) or error.status_code not in {403, 404, 409}:
                raise
            continue
        result.append(dto(service, db, row))
    return result


def create(service, args, context):
    from .service import digest, fail
    question, options = args.get("question"), args.get("options", [])
    if (not isinstance(question, str) or not question.strip() or len(question) > 4000
            or not isinstance(options, list) or len(options) not in {0, 2, 3}
            or any(not isinstance(value, str) or not value.strip() or len(value) > 500 for value in options)
            or len(set(options)) != len(options)):
        fail(422, "Ask one focused question with zero or two to three suggested answers")
    if not all(context.get(key) for key in ("runtime_session_id", "runtime_run_id", "runtime_command_id")):
        fail(409, "Questions require a durable originating run")
    with service.transaction() as db:
        private = db.scalar(select(Chat).where(Chat.private_owner_id == context["owner_id"], Chat.kind == "private"))
        for chat_id in sorted({context["chat"]["id"], private.id}):
            db.scalar(select(Chat.id).where(Chat.id == chat_id).with_for_update())
        db.refresh(private)
        if private.reset_pending:
            fail(409, "The owner's private reset is still pending")
        request, _, chat, member, _ = service._message_tool_origin(db, context)
        fingerprint = digest(args)
        prior = db.get(ProductOperation, context["operation_id"])
        if prior:
            if (prior.owner_id, prior.name, prior.arguments_hash) != (context["owner_id"], "ask_question", fingerprint):
                fail(409, "Question operation identity conflicts with its original action")
            origin(service, db, db.get(QuestionRequest, prior.result["id"]))
            return prior.result
        existing = db.scalar(select(QuestionRequest).where(QuestionRequest.runtime_session_id == context["runtime_session_id"],
            QuestionRequest.runtime_run_id == context["runtime_run_id"], QuestionRequest.status == "pending").limit(1))
        if existing:
            fail(409, "This turn already has a pending focused question")
        sources = service.manifest_provenance(db, request.id)
        if not service.provenance_allowed(db, member, sources):
            fail(409, "Original question sources are no longer authorized")
        twin = db.get(Actor, request.target_twin_id)
        message = service.append_message(db, private, author_id=twin.id, kind="twin", body=question,
            request_id=request.id, causal_chain_id=service.request_chain(db, request).id,
            sdk_output_key="question:" + digest(context["operation_id"]), source_provenance=sources if private.id == chat.id else [
                {"kind": "chat_context", "chat_id": chat.id, "sources": sources}])
        row = QuestionRequest(owner_id=twin.owner_id, chat_id=private.id, origin_chat_id=chat.id,
            origin_request_id=request.id, message_id=message.id, question=question, options=options,
            runtime_session_id=context["runtime_session_id"], runtime_run_id=context["runtime_run_id"],
            runtime_command_id=context["runtime_command_id"], source_provenance=sources)
        db.add(row);db.flush()
        result = dto(service, db, row)
        db.add(ProductOperation(operation_id=context["operation_id"], owner_id=twin.owner_id, chat_id=chat.id,
            name="ask_question", arguments_hash=fingerprint, result=result))
        return result


def consume_reply(service, db, message):
    """Only an exact human reply-to binds automatically; other text stays a task."""
    from .service import fail
    row = db.scalar(select(QuestionRequest).where(QuestionRequest.message_id == message.reply_to_message_id).with_for_update()) if message.reply_to_message_id else None
    if not row:
        return None
    if message.kind != "human" or message.author_id != row.owner_id or message.chat_id != row.chat_id:
        fail(403, "Only the owner can answer this question")
    if row.status != "pending":
        fail(409, "This question is no longer pending")
    if not message.body.strip() or message.created_at < row.created_at:
        fail(422, "Answer with a new, nonempty owner message")
    return enqueue(service, db, row, message)


def enqueue(service, db, row, message=None):
    from .service import digest
    request, chat, member = origin(service, db, row)
    key = "question-answer:" + row.id
    payload = {"question": row.question, "answer": message.body if message else None,
               "owner_declined": message is None}
    continuation = service.accept_request(db, chat, request.target_twin_id, request.requester_id,
        "question_result", key, source_message_id=request.source_message_id, intent_key=key, continuation_of_request_id=request.id,
        causal_chain_id=request.causal_chain_id, source_owner_id=request.source_owner_id,
        input_body="The owner answered or explicitly declined the focused question below. This is task input, not new action approval. "
                   "Continue remaining authorized work without repeating completed actions. If declined, do not ask the same question again; "
                   "use an authorized alternative or report the precise blocker.\n" + json.dumps(payload, ensure_ascii=False))
    continuation.session_id = row.runtime_session_id
    continuation.execution_config = request.execution_config
    row.status, row.answer, row.answer_message_id, row.answered_at = ("answered" if message else "cancelled"), (message.body if message else None), (message.id if message else None), now()
    row.continuation_request_id = continuation.id
    db.add(ContextManifest(request_id=continuation.id, owner_id=row.owner_id,
        grant_version=member.grant_version, high_water_seq=chat.next_message_seq - 1,
        sources=row.source_provenance, source_digest=digest(row.source_provenance)))
    return continuation


def answer(service, owner_id, question_id, body):
    from .service import fail
    if not isinstance(body, str) or not body.strip():
        fail(422, "Provide a nonempty answer")
    with service.transaction() as db:
        row = db.get(QuestionRequest, question_id)
        if not row or row.owner_id != owner_id:
            fail(404, "Question not found")
        origin(service, db, row)
        if row.status == "answered":
            if row.answer != body:
                fail(409, "This question already has a different answer")
            return dto(service, db, row)
        chat_id, message_id = row.chat_id, row.message_id
    service.send_message(owner_id, chat_id, body, "question-answer:" + question_id, reply_to=message_id)
    with service.transaction() as db:
        return dto(service, db, db.get(QuestionRequest, question_id))


def dismiss(service, owner_id, question_id):
    from .service import fail
    with service.transaction() as db:
        row = db.get(QuestionRequest, question_id)
        if not row or row.owner_id != owner_id:
            fail(404, "Question not found")
        lock_scopes(db, row)
        origin(service, db, row)
        if row.status == "pending":
            enqueue(service, db, row)
        return dto(service, db, row)


def answer_from_message(service, args, context):
    from .service import fail
    with service.transaction() as db:
        row = db.get(QuestionRequest, args.get("question_id"))
        if row and row.owner_id == context["owner_id"]:
            lock_scopes(db, row)
        request, _, _ = runtime_origin(service, db, context["owner_id"], context["request_id"])
        message = db.get(Message, args.get("message_id"))
        if (not row or row.owner_id != context["owner_id"] or not message
                or request.source != "private" or request.requester_id != row.owner_id
                or request.source_message_id != message.id or message.author_id != row.owner_id
                or message.kind != "human" or message.state != "published" or message.chat_id != row.chat_id
                or message.created_at <= row.created_at):
            fail(403, "Associate only this turn's new owner message with a pending question")
        if message.reply_to_message_id not in {None, row.message_id} or db.scalar(select(QuestionRequest.id).where(
                QuestionRequest.answer_message_id == message.id, QuestionRequest.id != row.id).limit(1)):
            fail(409, "This owner message already answers or quotes something else")
        origin(service, db, row)
        # The model selects identity, never supplies or rewrites the owner's answer.
        if row.status == "answered" and row.answer_message_id == message.id:
            return dto(service, db, row)
        if row.status != "pending":
            fail(409, "This question is no longer pending")
        # The separate answer relation must not rewrite a published human
        # message's original reply target or its send idempotency contract.
        enqueue(service, db, row, message)
        return dto(service, db, row)


def pending_for_run(service, session_id, run_id):
    with service.transaction() as db:
        return bool(db.scalar(select(QuestionRequest.id).outerjoin(ReplyRequest,
            QuestionRequest.continuation_request_id == ReplyRequest.id).where(QuestionRequest.runtime_session_id == session_id,
            QuestionRequest.runtime_run_id == run_id, or_(QuestionRequest.status == "pending",
                ReplyRequest.status.in_(["queued", "running"]) & ReplyRequest.cancel_requested.is_(False))).limit(1)))


def lock_scopes(db, row):
    for chat_id in sorted({row.chat_id, row.origin_chat_id}):
        db.scalar(select(Chat.id).where(Chat.id == chat_id).with_for_update())
    db.refresh(row, with_for_update=True)


def cancel_origin(db, request_id):
    for row in db.scalars(select(QuestionRequest).where(QuestionRequest.origin_request_id == request_id,
            QuestionRequest.status == "pending")):
        row.status = "cancelled"


def reset_owner(service, db, owner_id):
    """Hard reset is different from the owner's explicit decline operation."""
    from .models import DispatchCommand
    for row in db.scalars(select(QuestionRequest).where(QuestionRequest.owner_id == owner_id)):
        carried = db.get(ReplyRequest, row.continuation_request_id) if row.continuation_request_id else None
        if carried and carried.status in {"queued", "running"}:
            carried.cancel_requested = True
            if carried.status == "queued":
                carried.status = "cancelled"
            else:
                service.withdraw_outputs(db, carried)
            command = db.get(DispatchCommand, carried.id)
            if command:
                command.kind, command.state = "cancel", "pending"
        row.status, row.question, row.options, row.answer = "cancelled", "", [], None
        row.source_provenance, row.continuation_request_id = [], None


def reset_scope_ids(db, owner_id):
    return set(db.scalars(select(QuestionRequest.origin_chat_id).outerjoin(ReplyRequest,
        QuestionRequest.continuation_request_id == ReplyRequest.id).where(QuestionRequest.owner_id == owner_id,
        or_(QuestionRequest.status == "pending", ReplyRequest.status.in_(["queued", "running"]))).distinct()))


def continuation(service, db, request):
    """Accepted answers are frozen task input, not access to private inbox history.

    Deleting its human message later does not cancel already accepted work;
    original task cancellation, reset and source authorization still apply.
    """
    from .service import fail
    original = ReplyRequest.__table__.alias("question_continuation")
    row = db.scalar(select(QuestionRequest).join(original, QuestionRequest.continuation_request_id == original.c.id).where(
        original.c.intent_key == request.intent_key, original.c.chat_id == request.chat_id))
    if not row or row.status not in {"answered", "cancelled"} or not row.continuation_request_id:
        fail(409, "Question continuation has no retained owner answer")
    parent, _, _ = origin(service, db, row)
    return {"id": row.id, "owner_id": row.owner_id, "origin_request_id": parent.id,
        "session_id": row.runtime_session_id, "run_id": row.runtime_run_id,
        "command_id": row.runtime_command_id, "main_session_id": parent.session_id,
        "main_run_id": parent.run_id}


def continuation_context(service, request_id):
    with service.transaction() as db:
        request = db.get(ReplyRequest, request_id)
        return continuation(service, db, request) if request and request.source == "question_result" else None


def pending_questions(service, db, request, owner_id, visible_ids=None):
    rows = db.scalars(select(QuestionRequest).where(QuestionRequest.owner_id == owner_id,
        QuestionRequest.chat_id == request.chat_id, QuestionRequest.status == "pending",
        *([QuestionRequest.message_id.in_(visible_ids)] if visible_ids is not None else [])
        ).order_by(QuestionRequest.created_at).limit(50))
    result = []
    for row in rows:
        try:
            origin(service, db, row)
        except Exception as error:
            from fastapi import HTTPException
            if not isinstance(error, HTTPException) or error.status_code not in {403, 404, 409}:
                raise
            continue
        result.append(dto(service, db, row))
    return result
