"""Bounded owner platform tools within the caller's product transaction."""
from sqlalchemy import select

from .direct_chat_schema import pair_key
from .models import (
    Actor,
    Approval,
    Chat,
    Reminder,
    ReplyRequest,
)

PLATFORM_TOOLS = {"set_message_reaction", "list_people", "get_chat", "create_chat", "rename_chat", "add_chat_members",
                  "list_reminders", "update_reminder", "list_workspace", "read_workspace_file",
                  "get_twin_status", "get_reply_request", "cancel_reply_request", "retry_reply_request"}
TRACKING_TOOLS = {"list_tracking", "get_tracking", "create_tracking", "update_tracking", "delete_tracking", "add_tracking_update"}
SCOPED_TOOLS = {"history", "create_reminder"} | TRACKING_TOOLS
WRITE_TOOLS = {"request_duet_conversation", "set_message_reaction", "send_chat_message", "present_files", "create_chat", "rename_chat", "add_chat_members", "create_reminder",
               "update_reminder", "create_tracking", "update_tracking", "delete_tracking",
               "add_tracking_update", "save_summary", "cancel_reply_request", "retry_reply_request"}
# Exact arguments are frozen by the SDK; no uninstalled action may enter that path.
ACTION_FIELDS = {
    "set_message_reaction": ({"message_id", "emoji", "active"}, {"chat_id"}),
    "send_chat_message": ({"chat_id", "body", "source_message_ids"}, {"reply_to_message_id"}),
    "present_files": ({"paths"}, {"caption", "chat_id"}),
    "create_chat": ({"owner_ids"}, {"title", "history_policy"}),
    "rename_chat": ({"chat_id", "title"}, set()),
    "add_chat_members": ({"chat_id", "owner_ids", "history_policy"}, set()),
    "create_reminder": ({"title"}, {"detail", "due_at", "chat_id"}),
    "update_reminder": ({"id", "done"}, set()),
    "create_tracking": ({"title", "description", "source_message_ids"}, {"chat_id"}),
    "update_tracking": ({"id"}, {"title", "description", "status", "chat_id"}),
    "delete_tracking": ({"id"}, {"chat_id"}),
    "add_tracking_update": ({"id", "body", "source_message_ids"}, {"chat_id"}),
    "save_summary": ({"body", "source_message_ids"}, set()),
    "cancel_reply_request": ({"id"}, set()),
    "retry_reply_request": ({"id"}, set()),
}


def exact_action(action, execution_actions=None):
    from .service import fail
    execution_actions = execution_actions or {}
    if not isinstance(action, dict) or set(action) != {"tool", "arguments"}:
        fail(400, "Approval must name an installed action with exact arguments")
    args = action["arguments"]
    if action.get("tool") in execution_actions:
        from jsonschema import Draft202012Validator
        if not Draft202012Validator(execution_actions[action["tool"]]).is_valid(args):
            fail(400, "Approval arguments do not match the installed execution tool")
        from copy import deepcopy
        return {"tool": action["tool"], "arguments": deepcopy(args)}
    if action.get("tool") not in ACTION_FIELDS:
        fail(400, "Approval must name an installed action")
    required, optional = ACTION_FIELDS[action["tool"]]
    if not isinstance(args, dict) or not required.issubset(args) or set(args) - required - optional:
        fail(400, "Approval must contain exact platform action arguments")
    return {"tool": action["tool"], "arguments": dict(args)}


def scope_ids(service, db, name, args, context):
    origin_id = context.get("chat", {}).get("id") or context.get("chat_id")
    target_id = args.get("chat_id") or origin_id
    if name in {"get_reply_request", "cancel_reply_request", "retry_reply_request"}:
        row = db.get(ReplyRequest, args.get("id"))
        if row:
            target_id = row.chat_id
    if name == "get_approval":
        approval = db.get(Approval, args.get("id"))
        if approval:
            target_id = approval.chat_id
    ids = {origin_id, target_id}
    if name == "request_duet_conversation" and args.get("origin_chat_id"):
        ids.add(args["origin_chat_id"])
    request = db.get(ReplyRequest, context.get("request_id")) if context.get("request_id") else None
    source_ids = args.get("source_message_ids", [])
    if name == "set_message_reaction" and args.get("message_id"):
        source_ids = [*source_ids, args["message_id"]]
    if request or name == "set_message_reaction":
        ids.update(service._message_source_chat_ids(db, request, target_id, source_ids))
    if name == "create_chat":
        service._validate_owner_ids(args.get("owner_ids"))
        owners = list(dict.fromkeys([context["owner_id"], *args["owner_ids"]]))
        if len(owners) == 2:
            existing = db.scalar(select(Chat.id).where(Chat.direct_pair_key == pair_key(owners)))
            if existing:
                ids.add(existing)
    if name == "create_approval":
        action = args.get("action")
        if isinstance(action, dict) and action.get("tool") == "retry_reply_request" and isinstance(action.get("arguments"), dict):
            retry = db.get(ReplyRequest, action["arguments"].get("id"))
            if retry:
                ids.add(retry.chat_id)
        private = db.scalar(select(Chat.id).where(Chat.kind == "private", Chat.private_owner_id == context["owner_id"]))
        if private:
            ids.add(private)
    if name == "present_files":
        private = db.scalar(select(Chat.id).where(Chat.kind == "private", Chat.private_owner_id == context["owner_id"]))
        if private:
            ids.add(private)
    return {value for value in ids if value}


def lock_scope(service, db, name, args, context):
    from .service import fail
    ids = scope_ids(service, db, name, args, context)
    for value in sorted(ids):
        db.scalar(select(Chat).where(Chat.id == value).with_for_update())
    db.expire_all()
    if not scope_ids(service, db, name, args, context).issubset(ids):
        fail(409, "Platform action scope changed; retry the same operation")
    return ids


def bounded_limit(args, maximum=100):
    from .service import fail
    value = args.get("limit", maximum)
    if type(value) is not int or not 1 <= value <= maximum:
        fail(400, f"limit must be between 1 and {maximum}")
    return value


def request_row(service, db, owner_id, request_id):
    from .service import fail
    row = db.get(ReplyRequest, request_id)
    if not row:
        fail(404, "Reply request not found")
    chat, member = service.membership(db, owner_id, row.chat_id)
    service._check_private_request(db, row)
    if row.raw_owner_input is not None and row.requester_id != owner_id:
        fail(404, "Reply request is unavailable")
    if row.source_message_id:
        service.visible_message(db, member, row.source_message_id)
    elif row.requester_id != owner_id:
        fail(404, "Reply request is unavailable")
    if not service.provenance_allowed(db, member, service.manifest_provenance(db, row.id)):
        fail(409, "Reply request sources are no longer authorized")
    return row, chat, member


def chat_metadata(service, db, chat, member):
    ids = [row.owner_id for row in service.members(db, chat.id)]
    return {"id": chat.id, "kind": chat.kind, "title": service.chat_title(db, chat, member.owner_id, ids),
            "member_owner_ids": ids, "is_owner_private": chat.kind == "private" and chat.private_owner_id == member.owner_id,
            "revision": chat.revision, "can_rename": chat.kind == "group" and len(ids) >= 3}


def dispatch(service, db, name, args, context, chat, member, locked_ids):
    from .service import fail
    owner_id = member.owner_id
    target_id = args.get("chat_id") or chat.id
    if (name in {"create_chat", "rename_chat", "add_chat_members"}
            and context.get("request_requester_id", owner_id) != owner_id
            and not context.get("approved_approval_id")):
        fail(403, "Another participant cannot manage chats without the owner's exact approval")
    if name == "set_message_reaction":
        exact_action({"tool": name, "arguments": args})
        _request, twin, *_ = service._message_tool_origin(db, context)
        from .reactions import set_reaction
        return set_reaction(service, db, owner_id, target_id, args["message_id"], twin.id, args["emoji"], args["active"])
    if name == "list_people":
        query = select(Actor).where(Actor.kind == "human")
        if args.get("query"):
            query = query.where(Actor.name.ilike(f"%{str(args['query'])[:256]}%"))
        return {"people": [service.actor_json(row) for row in db.scalars(query.order_by(Actor.name, Actor.id).limit(bounded_limit(args)))]}
    if name == "get_chat":
        target, membership = service.membership(db, owner_id, target_id)
        rows = db.scalars(select(ReplyRequest).where(ReplyRequest.chat_id == target.id).order_by(ReplyRequest.accepted_seq.desc()).limit(20))
        visible = []
        for row in rows:
            try:
                request_row(service, db, owner_id, row.id)
            except Exception as exc:
                from fastapi import HTTPException
                if isinstance(exc, HTTPException):
                    continue
                raise
            visible.append(service.request_json(db, row, owner_id))
        return {"chat": chat_metadata(service, db, target, membership), "requests": visible}
    if name == "create_chat":
        owners = args.get("owner_ids")
        service._validate_owner_ids(owners)
        return service._create_chat_db(db, owner_id, owners, args.get("title"), args.get("history_policy", "all"), locked_chat_ids=locked_ids)[1]
    if name == "rename_chat":
        if not isinstance(args.get("title"), str):
            fail(400, "title must be a string")
        return service._rename_chat_db(db, owner_id, target_id, args["title"])[1]
    if name == "add_chat_members":
        return service._add_chat_members_db(db, owner_id, target_id, args["owner_ids"], args["history_policy"])[1]
    if name == "list_reminders":
        if args.get("chat_id"):
            service.membership(db, owner_id, target_id)
        query = select(Reminder).where(Reminder.owner_id == owner_id)
        if args.get("chat_id"):
            query = query.where(Reminder.chat_id == target_id)
        if args.get("include_done", True) is False:
            query = query.where(Reminder.done.is_(False))
        return {"reminders": [service.reminder_json(row) for row in db.scalars(query.order_by(Reminder.due_at.desc().nulls_last(), Reminder.id).limit(bounded_limit(args)))]}
    if name == "update_reminder":
        row = db.get(Reminder, args.get("id"))
        if not row or row.owner_id != owner_id:
            fail(404, "Reminder not found")
        if type(args.get("done")) is not bool:
            fail(400, "done must be a boolean")
        if row.chat_id:
            service.membership(db, owner_id, row.chat_id)
        if context.get("request_requester_id", owner_id) != owner_id and not context.get("approved_approval_id"):
            fail(403, "Another participant cannot change the owner's personal reminders")
        row.done = args["done"]
        return service.reminder_json(row)
    if name == "list_workspace":
        limit = bounded_limit(args)
        entries = sorted(service._workspace_db(db, owner_id, target_id, args.get("path", "")), key=lambda entry: entry["id"])
        if args.get("after"):
            entries = [entry for entry in entries if entry["id"] > args["after"]]
        return {"chat_id": target_id, "entries": entries[:limit], "next_cursor": entries[limit - 1]["id"] if len(entries) > limit else None}
    if name == "read_workspace_file":
        path = service._workspace_file_db(db, owner_id, target_id, args["id"])
        with path.open("rb") as stream:
            data = stream.read(65537)
        if len(data) > 65536 or b"\0" in data:
            fail(400, "Workspace text must be at most 64 KiB UTF-8")
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            fail(400, "Workspace file must be UTF-8 text")
        return {"chat_id": target_id, "id": args["id"], "name": path.name, "content": content, "size": len(data)}
    if name == "get_twin_status":
        twin = service.twin(db, owner_id)
        private = db.scalar(select(Chat).where(Chat.kind == "private", Chat.private_owner_id == owner_id))
        from .models import MemoryDreamState
        state = db.get(MemoryDreamState, owner_id)
        import time

        from .notifications import notifications
        worker = notifications.worker_status
        online = worker.get("expires_at", 0) > time.time()
        runtime = {"configured": bool(online and worker.get("configured")),
                   **{key: worker.get(key) for key in ("provider", "model", "api", "observed_at")},
                   "worker": {"online": online, "expires_at": worker.get("expires_at")}}
        requests = list(db.scalars(select(ReplyRequest).where(ReplyRequest.target_twin_id == twin.id,
            ReplyRequest.status.in_(["queued", "running", "failed"])).order_by(ReplyRequest.accepted_at.desc()).limit(20)))
        visible_requests = []
        for row in requests:
            try:
                request_row(service, db, owner_id, row.id)
                visible_requests.append(service.request_json(db, row, owner_id))
            except Exception as exc:
                from fastapi import HTTPException
                if not isinstance(exc, HTTPException):
                    raise
        return {"runtime": runtime, "requests": visible_requests,
                "twin": {"id": twin.id, "name": twin.name}, "owner_private_chat_id": private.id if private else None,
                "private_reset_pending": bool(private and private.reset_pending),
                "memory_dream": {"status": state.last_status, "queued_request_id": state.queued_request_id} if state else None}
    if name in {"get_reply_request", "cancel_reply_request", "retry_reply_request"}:
        row, target, membership = request_row(service, db, owner_id, args["id"])
        if name == "get_reply_request":
            return service.request_json(db, row, owner_id)
        target_actor = db.get(Actor, row.target_twin_id)
        if owner_id not in {row.requester_id, target_actor.owner_id}:
            fail(403, "Only the requester or target Duet owner may change this request")
        if name == "cancel_reply_request":
            return service._cancel_request_db(db, owner_id, row.id)
        if target.kind == "private" and target.id != chat.id:
            if not context.get("approved_approval_id"):
                fail(403, "Re-executing a personal task requires exact owner approval")
            from .credentials import private_origin
            private_origin(service, db, owner_id, row.id, executable=False)
        next_row = db.scalar(select(ReplyRequest).where(ReplyRequest.intent_key == row.intent_key, ReplyRequest.generation == row.generation + 1))
        if next_row:
            return service.request_json(db, next_row, owner_id)
        if row.status != "failed":
            fail(409, "Only failed requests can be retried")
        controller = db.get(ReplyRequest, context.get("request_id"))
        if not controller:
            fail(403, "Automatic retry requires a durable controller request")
        return service._retry_request_db(db, owner_id, row.id,
            "platform-retry:" + context["operation_id"], automatic=True)

    fail(400, "Unknown platform tool")


def read_provenance(service, db, name, args, result, context):
    """Return scoped references to data consumed by this call, never its contents."""
    owner_id = context["owner_id"]
    target_id = args.get("chat_id") or context["chat"]["id"]
    sources = []
    if name == "set_message_reaction":
        sources = [{"kind": "message", "id": args["message_id"]}]
    elif name in {"list_reminders", "get_twin_status"}:
        target_id = db.scalar(select(Chat.id).where(Chat.kind == "private", Chat.private_owner_id == owner_id))
    elif name in {"get_reply_request", "cancel_reply_request", "retry_reply_request"}:
        row = db.get(ReplyRequest, args["id"])
        target_id = row.chat_id
        sources = service.manifest_provenance(db, row.id)
        if row.source_message_id:
            sources = [*sources, {"kind": "message", "id": row.source_message_id}]
    elif name == "get_approval":
        approval = db.get(Approval, args["id"])
        target_id = approval.chat_id
        sources = approval.source_provenance
    elif name == "get_chat":
        target_id = args["chat_id"]
    elif name == "list_workspace":
        sources = [{"kind": "workspace", "id": row["id"]} for row in result["entries"]]
    else:
        return []
    if not target_id:
        return []
    chat = db.get(Chat, target_id)
    envelope = {"kind": "chat_context", "chat_id": target_id, "sources": sources}
    if chat and chat.kind == "private":
        envelope["private_generation"] = chat.reset_generation
    return [envelope]


def revalidate_result(service, db, name, args, result, owner_id):
    if name == "set_message_reaction":
        _, member = service.membership(db, owner_id, result["chat_id"])
        service.visible_message(db, member, args["message_id"])
    elif name in {"create_chat", "rename_chat", "add_chat_members"}:
        service.membership(db, owner_id, result["id"])
    elif name in {"cancel_reply_request", "retry_reply_request"}:
        request_row(service, db, owner_id, args["id"])
    elif name in {"create_reminder", "update_reminder"}:
        from .service import fail
        row = db.get(Reminder, result.get("id"))
        if not row or row.owner_id != owner_id:
            fail(404, "Reminder not found")
        if row.chat_id:
            service.membership(db, owner_id, row.chat_id)
