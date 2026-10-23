"""One private first meeting; owner facts stay in the existing Memory store."""
from sqlalchemy import select

from .config import settings
from .models import Actor, Chat, OnboardingState, now

TERMINAL = {"completed", "skipped"}
TOOL = "finish_onboarding"


def _chat(service, db, owner_id, *, lock=False):
    query = select(Chat).where(Chat.kind == "private", Chat.private_owner_id == owner_id)
    chat = db.scalar(query.with_for_update() if lock else query)
    if chat is None:
        from .service import fail
        fail(404, "Owner private conversation is unavailable")
    service.membership(db, owner_id, chat.id)
    return chat


def _state(db, chat):
    if not settings.onboarding_enabled:
        return {"enabled": False, "status": "disabled", "chat_id": chat.id, "welcome_message_id": None}
    row = db.get(OnboardingState, chat.private_owner_id)
    status = row.status if row else "new"
    if ((status not in TERMINAL and (chat.reset_generation or chat.reset_pending))
          or (row is None and chat.next_message_seq != 1)):
        status = "unavailable"
    return {"enabled": settings.onboarding_enabled, "status": status, "chat_id": chat.id,
            "welcome_message_id": row.welcome_message_id if row else None}


def get_state(service, owner_id):
    with service.transaction() as db:
        return _state(db, _chat(service, db, owner_id))


def start(service, owner_id):
    with service.transaction() as db:
        chat = _chat(service, db, owner_id, lock=True)
        state = _state(db, chat)
        if state["status"] != "new":
            return state
        twin = service.twin(db, owner_id)
        owner = db.get(Actor, owner_id)
        row = OnboardingState(owner_id=owner_id, chat_id=chat.id, status="active")
        db.add(row)
        welcome = service.append_message(db, chat, consume_followup=False,
            author_id=twin.id, kind="system", body=(
                f"Hi {owner.name}, I'm {twin.name}. I can help you think things through, "
                "draft messages, work with shared files, and coordinate work with people here. "
                "Tell me the outcome, and I'll work out the steps and ask when your input is needed. "
                "What would you like help with first?"))
        row.welcome_message_id = welcome.id
        result = _state(db, chat)
    return result


def skip(service, owner_id):
    with service.transaction() as db:
        chat = _chat(service, db, owner_id, lock=True)
        if not settings.onboarding_enabled:
            return _state(db, chat)
        row = db.get(OnboardingState, owner_id)
        if row is None:
            row = OnboardingState(owner_id=owner_id, chat_id=chat.id, status="skipped", finished_at=now())
            db.add(row)
        elif row.status not in TERMINAL:
            row.status, row.finished_at = "skipped", now()
        result = _state(db, chat)
    return result


def owner_message(db, chat, message):
    """Called under the send transaction's existing Chat lock, after idempotency checks."""
    if not settings.onboarding_enabled or chat.kind != "private" or message.author_id != chat.private_owner_id:
        return
    row = db.get(OnboardingState, chat.private_owner_id)
    if row and row.status == "active":
        if row.first_message_id is None:
            row.first_message_id = message.id
        elif row.first_message_id != message.id:
            row.status, row.finished_at = "completed", now()


def context_for_request(db, request, chat):
    if (not settings.onboarding_enabled or chat.kind != "private" or chat.reset_generation
            or request.source != "private" or request.generation != 0 or request.raw_owner_input is not None
            or request.requester_id != chat.private_owner_id):
        return None
    row = db.get(OnboardingState, chat.private_owner_id)
    if row and row.first_message_id == request.source_message_id:
        return {"status": row.status, "first_message_id": row.first_message_id}
    return None


def temporary_prompt(context, *, worker=False):
    if (worker or context.get("request_source") != "private"
            or context.get("request_requester_id") != context.get("owner_id")
            or context.get("raw_owner_input") is not None
            or (context.get("onboarding") or {}).get("status") != "active"):
        return ""
    return (
        "\nFirst meeting, only for this owner's first private input: the platform has already "
        "introduced you and asked what they want help with. Use their existing display name; "
        "a preferred form of address is optional. Be warm, concise and concrete about relevant "
        "capabilities, without feature jargon or repeated naming questions. If they give a task, "
        "finish onboarding and handle it immediately, without an introductory interview. Honor "
        "an explicit skip. Otherwise acknowledge what they share and finish this brief welcome; "
        "ask no further onboarding question. Save only explicitly stated durable preferences "
        "through the normal Memory tools, not inferred traits. Use finish_onboarding with "
        "completed or skipped; it does not complete their actual task.\n"
    )


def tool_schema():
    return (TOOL, "Finish this brief first meeting after responding to the owner, or honor their explicit skip. This changes only onboarding progress, not the owner's task or Memory.",
            {"status": {"type": "string", "enum": ["completed", "skipped"]}}, ["status"])


def finish(service, db, args, context, chat):
    from .service import fail
    if not isinstance(args, dict) or set(args) != {"status"} or args["status"] not in TERMINAL:
        fail(400, "Onboarding status must be completed or skipped")
    request, twin, origin, _, _ = service._message_tool_origin(db, context)
    if not settings.onboarding_enabled:
        return _state(db, chat)
    if (context.get("runtime_session_id") != context.get("main_session_id")
            or origin.id != chat.id or twin.owner_id != chat.private_owner_id
            or context_for_request(db, request, chat) is None):
        fail(403, "Onboarding belongs to the first owner-private input")
    row = db.get(OnboardingState, twin.owner_id)
    if row.status not in TERMINAL:
        row.status, row.finished_at = args["status"], now()
    return _state(db, chat)
