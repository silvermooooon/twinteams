"""Internal agent tools and their product permission boundary."""
from .models import Actor, ReplyRequest
from .service import fail

INTERNAL_TOOLS = {
    "list_personal_bots", "dispatch_bot_tasks", "message_personal_agent",
    "create_bot_room", "post_bot_room", "inspect_bot_history",
    "history", "list_tracking", "get_tracking", "create_tracking", "update_tracking",
    "delete_tracking", "add_tracking_update", "list_workspace", "read_workspace_file", "present_files",
}


def tool_schemas():
    text = {"type": "string", "minLength": 1}
    bots = {"type": "array", "items": text, "minItems": 1, "maxItems": 100, "uniqueItems": True}
    return [
        ("list_personal_bots", "List your owner's main Duet identity and durable personal BOTs with their assignments and actor IDs for messaging. BOTs have their own conversations and Memory. Use a suitable BOT for its ongoing responsibility; temporary background workers remain separate.", {}, []),
        ("create_personal_bot", "Create a durable personal BOT for your owner when requested in the primary private Duet conversation. Save its name and responsibility assignment and create its own owner-only conversation, available under BOTs in the sidebar. Preserve the owner's chosen name; otherwise choose a concise name fitting the requested responsibility. Returns the created BOT's id, chat_id, name and assignment. Report creation only after success, explain how to open it, and use its returned id with dispatch_bot_tasks for authorized work. Do not ask the owner to create it elsewhere or supply an ID. Creation alone needs no Tracking item. BOTs cannot join human groups, Duet topics, or contact another owner's agents.",
         {"name": {**text, "maxLength": 128}, "assignment": text}, ["name", "assignment"]),
        ("dispatch_bot_tasks", "The main Duet assigns a task to selected personal BOTs. Assignments are visible in both conversations. BOTs run independently; their final results resume this task automatically. Continue independent coordination and end your turn; never poll or wait. Review results before reporting or preparing external messages for owner confirmation. Primary-private topic publications require create_approval for the exact topic action; execute only after owner approval.",
         {"bot_ids": bots, "body": text}, ["bot_ids", "body"]),
        ("message_personal_agent", "Send a visible internal message to your owner's main Duet or another personal BOT using its actor ID. The recipient runs independently and its answer returns to you. This cannot address another owner or an external topic. Your current agent is always the sender.",
         {"recipient_actor_id": text, "body": text}, ["recipient_actor_id", "body"]),
        ("create_bot_room", "Create an owner-only collaboration room containing the main Duet and selected personal BOTs. Everyone shares one thread. It is neither a human group nor a Duet forum topic.",
         {"title": text, "bot_ids": bots}, ["title", "bot_ids"]),
        ("post_bot_room", "Post in your owner's internal collaboration room. Select recipient actor IDs to ask them to act; an empty list posts without waking anyone. Responses appear in the same thread without automatically waking all members.",
         {"chat_id": text, "body": text, "recipient_actor_ids": {**bots, "minItems": 0}}, ["chat_id", "body", "recipient_actor_ids"]),
        ("inspect_bot_history", "The main Duet explicitly inspects one of its owner's BOT conversations to review understanding or drift. Reads a bounded page of published messages; use before_seq for earlier history. This does not mirror the transcript into the primary chat or grant cross-owner access.",
         {"bot_id": text, "limit": {"type": "integer", "minimum": 1, "maximum": 100}, "before_seq": {"type": "integer", "minimum": 1}}, ["bot_id"]),
    ]


def authorize(service, db, name, args, context, chat):
    request_id = context.get("request_id") or context.get("origin_request_id")
    request = db.get(ReplyRequest, request_id) if request_id else None
    actor = db.get(Actor, request.target_twin_id) if request else None
    if not (actor and actor.kind == "bot" or chat.kind == "internal"):
        return
    if name not in INTERNAL_TOOLS:
        fail(403, "Personal BOTs and internal rooms coordinate only inside their owner; use the primary Duet for external actions")
    target_id = args.get("chat_id")
    if target_id:
        target, _ = service.membership(db, context["owner_id"], target_id)
        if target.kind not in {"bot", "internal"}:
            fail(403, "Use internal agent messaging to contact the main Duet; human groups and Duet topics are unavailable here")
