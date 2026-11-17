"""Durable personal agents use the existing chats, requests and result projection."""
from sqlalchemy import or_, select

from .models import (
    Actor,
    Chat,
    ContextManifest,
    Membership,
    Message,
    ProductOperation,
    ReplyRequest,
    uid,
)
from .service import digest, fail

TOOLS = {"list_personal_bots", "create_personal_bot", "dispatch_bot_tasks", "message_personal_agent",
         "create_bot_room", "post_bot_room", "inspect_bot_history"}
WRITE_TOOLS = {"create_personal_bot", "dispatch_bot_tasks", "message_personal_agent", "create_bot_room", "post_bot_room"}


def bot_rows(db, owner_id, bot_ids=None):
    query = select(Actor, Chat, Membership).join(Chat, Chat.bot_id == Actor.id).join(
        Membership, Membership.chat_id == Chat.id).where(
            Actor.kind == "bot", Actor.owner_id == owner_id,
            Chat.kind == "bot", Chat.internal_owner_id == owner_id,
            Membership.owner_id == owner_id, Membership.left_at.is_(None))
    if bot_ids is not None:
        query = query.where(Actor.id.in_(bot_ids))
    return list(db.execute(query.order_by(Actor.name, Actor.id)))


def bot_json(service, db, actor, chat, member, *, summaries=None):
    return {"id": actor.id, "chat_id": chat.id, "name": actor.name,
            "assignment": actor.assignment, "actor": service.actor_json(actor),
            "chat": service.chat_json(db, chat, member, [actor.owner_id], summaries=summaries)}


def list_bots_db(service, db, owner_id, *, summaries=None):
    rows = bot_rows(db, owner_id)
    if summaries is None:
        summaries = service._chat_summaries(db, owner_id, [chat.id for _, chat, _ in rows])
    return [bot_json(service, db, actor, chat, member, summaries=summaries)
            for actor, chat, member in rows]


def list_bots(service, owner_id):
    with service.transaction() as db:
        return list_bots_db(service, db, owner_id)


def create_bot(service, owner_id, name, assignment):
    with service.transaction() as db:
        return create_bot_db(service, db, owner_id, name, assignment)


def create_bot_db(service, db, owner_id, name, assignment):
    name, assignment = bot_profile(name, assignment)
    service.twin(db, owner_id)
    actor = Actor(id=uid(), owner_id=owner_id, kind="bot", name=name,
                  assignment=assignment, initials=name[:2].upper(), color="#595c63")
    db.add(actor)
    db.flush()
    chat = Chat(kind="bot", title=name, internal_owner_id=owner_id,
                bot_id=actor.id, member_actor_ids=[actor.id])
    db.add(chat)
    db.flush()
    member = Membership(chat_id=chat.id, owner_id=owner_id, history_policy="all", lower_seq=1)
    db.add(member)
    db.flush()
    return bot_json(service, db, actor, chat, member, summaries=({}, {}))


def edit_bot(service, owner_id, bot_id, name, assignment):
    name, assignment = bot_profile(name, assignment)
    with service.transaction() as db:
        actor, chat, member = require_bot(db, owner_id, bot_id)
        db.refresh(chat, with_for_update=True)
        actor.name, actor.assignment = name, assignment
        actor.initials, actor.profile_revision = name[:2].upper(), actor.profile_revision + 1
        chat.title = name
        result = bot_json(service, db, actor, chat, member)
    from .notifications import notifications
    notifications.emit_personal(owner_id, "bots")
    return result


def bot_history(service, owner_id, bot_id, limit=30, before_seq=None):
    with service.transaction() as db:
        _, chat, _ = require_bot(db, owner_id, bot_id)
        chat_id = chat.id
    return service.chat_detail(owner_id, chat_id, limit, before_seq)


def create_room(service, owner_id, title, bot_ids):
    with service.transaction() as db:
        return create_room_db(service, db, owner_id, title, bot_ids)


def create_room_db(service, db, owner_id, title, bot_ids):
    title = title.strip()
    if not title or len(title) > 256:
        fail(400, "Room name must contain 1–256 characters")
    rows = require_bots(db, owner_id, bot_ids)
    main = service.twin(db, owner_id)
    chat = Chat(kind="internal", title=title, internal_owner_id=owner_id,
                member_actor_ids=[main.id, *[actor.id for actor, _, _ in rows]])
    db.add(chat)
    db.flush()
    member = Membership(chat_id=chat.id, owner_id=owner_id, history_policy="all", lower_seq=1)
    db.add(member)
    db.flush()
    return service.chat_json(db, chat, member, [owner_id], summaries=({}, {}))


def dispatch_tasks(service, owner_id, bot_ids, body, key):
    with service.transaction() as db:
        main = service.twin(db, owner_id)
        origin = home_chats(db, owner_id, [main.id])[main.id]
        args = {"bot_ids": bot_ids, "body": body}
        context = {"owner_id": owner_id, "target_twin_id": main.id, "chat_id": origin.id}
        lock_chats(db, scope_ids(db, "dispatch_bot_tasks", args, context))
        return command(service, db, origin, owner_id, "dispatch_bot_tasks", args, key,
                       lambda: dispatch_tasks_db(service, db, origin, main, bot_ids, body))


def dispatch_tasks_db(service, db, origin, sender, bot_ids, body, origin_request_id=None):
    if sender.kind != "twin" or origin.kind not in {"private", "internal"}:
        fail(403, "Only your main Duet can dispatch tasks to personal BOTs")
    body = message_body(body)
    rows = require_bots(db, sender.owner_id, bot_ids)
    message_ids, deliveries = [], []
    for actor, destination, _ in rows:
        sent, received, handoff = deliver(service, db, origin, destination, sender, actor, body,
                                          "assignment", origin_request_id)
        message_ids.append(sent.id)
        deliveries.append((destination, actor, received, "bot_dispatch", handoff))
    requests = service.accept_internal_requests(db, deliveries)
    return {"chat_id": origin.id, "message_ids": message_ids,
            "request_ids": [request.id for request in requests]}


def message_agent(service, owner_id, sender_actor_id, recipient_actor_id, body, key):
    with service.transaction() as db:
        agents = require_agents(db, owner_id, [sender_actor_id, recipient_actor_id])
        homes = home_chats(db, owner_id, agents)
        origin = homes[sender_actor_id]
        lock_chats(db, {chat.id for chat in homes.values()})
        args = {"sender_actor_id": sender_actor_id, "recipient_actor_id": recipient_actor_id, "body": body}
        return command(service, db, origin, owner_id, "message_personal_agent", args, key,
            lambda: message_agent_db(service, db, origin, agents[sender_actor_id],
                                     agents[recipient_actor_id], homes[recipient_actor_id], body))


def message_agent_db(service, db, origin, sender, recipient, destination, body, origin_request_id=None):
    if sender.id == recipient.id:
        fail(400, "Choose another personal agent to message")
    sent, received, handoff = deliver(service, db, origin, destination, sender, recipient,
                                      message_body(body), "message", origin_request_id)
    request, = service.accept_internal_requests(db, [(destination, recipient, received, "bot_message", handoff)])
    return {"chat_id": destination.id, "source_chat_id": origin.id,
            "message_id": sent.id, "request_id": request.id}


def deliver(service, db, origin, destination, sender, recipient, body, kind, origin_request_id):
    if destination.kind == "private" and destination.reset_pending:
        fail(409, "Private reset cleanup is pending")
    chain = service.new_causal_chain(db, sender.owner_id)
    metadata = {"kind": kind, "source_chat_id": origin.id, "target_chat_id": destination.id,
                "actor_ids": [sender.id, recipient.id]}
    sent = service.append_message(db, origin, author_id=sender.id, kind="twin", body=body,
                                  causal_chain_id=chain.id, coordination=metadata)
    received = service.append_message(db, destination, author_id=sender.id, kind="twin", body=body,
                                      causal_chain_id=chain.id, coordination=metadata)
    chain.root_message_id = sent.id
    handoff = {"chat_id": origin.id, "actor_id": sender.id,
               "request_id": origin_request_id,
               "session_id": db.get(ReplyRequest, origin_request_id).session_id if origin_request_id else None,
               "private_generation": origin.reset_generation if origin.kind == "private" else None}
    return sent, received, handoff


def queue_room_message(service, db, chat, message, recipient_actor_ids):
    ids = actor_ids(recipient_actor_ids, allow_empty=True)
    if not set(ids).issubset(chat.member_actor_ids):
        fail(403, "Only this room's main Duet and personal BOTs can receive a room message")
    recipients = require_agents(db, chat.internal_owner_id, ids) if ids else {}
    message.coordination = {"kind": "message", "actor_ids": ids}
    return service.accept_internal_requests(db, [
        (chat, actor, message, "bot_room", None) for actor in recipients.values()])


def finish_handoff(service, db, request, status):
    route = request.bot_handoff
    if not route:
        return
    if status == "completed" and db.scalar(select(ReplyRequest.id).where(
            ReplyRequest.requester_id == request.requester_id,
            ReplyRequest.status.in_(["queued", "running"]),
            or_(ReplyRequest.bot_handoff["session_id"].as_string() == request.session_id,
                ReplyRequest.session_id == request.session_id)).limit(1)):
        return
    destination = db.scalar(select(Chat).where(Chat.id == route["chat_id"]).with_for_update())
    if destination.kind == "private" and (destination.reset_pending
            or destination.reset_generation != route["private_generation"]):
        return
    actor = db.get(Actor, request.target_twin_id)
    if status == "completed":
        output = db.scalar(select(Message).where(Message.request_id == request.id,
            Message.chat_id == request.chat_id, Message.state == "published",
            Message.sdk_output_key.is_not(None), ~Message.sdk_output_key.like("tool:%"))
            .order_by(Message.seq.desc()).limit(1))
        body = output.body if output else ""
    else:
        body = ""
    message = service.append_message(db, destination, author_id=actor.id, kind="twin", body=body,
        request_id=request.id, causal_chain_id=request.causal_chain_id,
        sdk_output_key=f"bot-handoff:{request.id}", coordination={"kind": "result",
            "source_chat_id": request.chat_id, "target_chat_id": destination.id,
            "actor_ids": [actor.id, route["actor_id"]], "status": status, "has_result": bool(body)})
    original = db.get(ReplyRequest, route["request_id"]) if route.get("request_id") else None
    if original and (original.cancel_requested or original.status in {"cancelled", "failed"}):
        return
    service.accept_request(db, destination, route["actor_id"], actor.owner_id,
        "bot_result", f"bot-result:{request.id}", source_message_id=message.id,
        causal_chain_id=request.causal_chain_id,
        input_body=f"Personal agent {actor.name} returned status {status}.\n{body}",
        continuation_of_request_id=original.id if original else None)


def dispatch(service, db, name, args, context, actor, origin):
    owner_id = context["owner_id"]
    if origin.kind not in {"private", "bot", "internal"}:
        fail(403, "Personal BOT coordination is available only inside your owner's private workspace")
    if name == "list_personal_bots":
        main = service.twin(db, owner_id)
        return {"main_duet": {"id": main.id, "name": main.name},
                "bots": [{"id": bot.id, "chat_id": chat.id, "name": bot.name, "assignment": bot.assignment}
                         for bot, chat, _ in bot_rows(db, owner_id)]}
    if name == "create_personal_bot":
        if origin.kind != "private" or actor.kind != "twin":
            fail(403, "Create personal BOTs from your primary private Duet conversation")
        return create_bot_db(service, db, owner_id, args["name"], args["assignment"])
    if name == "dispatch_bot_tasks":
        return dispatch_tasks_db(service, db, origin, actor, args["bot_ids"], args["body"], context.get("request_id"))
    if name == "message_personal_agent":
        recipient = require_agents(db, owner_id, [args["recipient_actor_id"]])[args["recipient_actor_id"]]
        destination = home_chats(db, owner_id, [recipient.id])[recipient.id]
        return message_agent_db(service, db, origin, actor, recipient, destination,
                                args["body"], context.get("request_id"))
    if name in {"create_bot_room", "inspect_bot_history"} and actor.kind != "twin":
        fail(403, "Only your main Duet can create rooms or inspect a BOT transcript")
    if name == "create_bot_room":
        return create_room_db(service, db, owner_id, args["title"], args["bot_ids"])
    if name == "inspect_bot_history":
        return inspect_history(service, db, owner_id, args, context)
    if name == "post_bot_room":
        room, _ = service.membership(db, owner_id, args["chat_id"])
        if room.kind != "internal" or room.internal_owner_id != owner_id or actor.id not in room.member_actor_ids:
            fail(403, "Only your main Duet and the selected personal BOTs can post in this room")
        chain = service.new_causal_chain(db, owner_id)
        message = service.append_message(db, room, author_id=actor.id, kind="twin",
            body=message_body(args["body"]), causal_chain_id=chain.id)
        chain.root_message_id = message.id
        requests = queue_room_message(service, db, room, message, args["recipient_actor_ids"])
        return {"chat_id": room.id, "message_id": message.id, "request_ids": [row.id for row in requests]}


def inspect_history(service, db, owner_id, args, context):
    from sqlalchemy.orm import load_only

    from .conversation_context import read_sources
    from .platform_tools import bounded_limit
    from .service import DISPLAY_MESSAGE_FIELDS
    actor, chat, member = require_bot(db, owner_id, args["bot_id"])
    limit = bounded_limit(args, 100)
    before = args.get("before_seq")
    if before is not None and (type(before) is not int or before < 1):
        fail(400, "before_seq must be a positive message sequence")
    query = select(Message).options(load_only(*DISPLAY_MESSAGE_FIELDS)).where(
        Message.chat_id == chat.id, Message.state == "published", Message.seq >= member.lower_seq)
    if before is not None:
        query = query.where(Message.seq < before)
    rows = list(reversed(list(db.scalars(query.order_by(Message.seq.desc()).limit(limit + 1)))))
    has_more, rows = len(rows) > limit, rows[-limit:]
    cache = {}
    messages = service.messages_json(db, rows, member, cache=cache)
    if context.get("context_manifest_id"):
        manifest = db.get(ContextManifest, context["context_manifest_id"])
        sources = [source for row, projection in zip(rows, messages)
                   for source in read_sources(db, row, projection)]
        manifest.sources = [*manifest.sources, {"kind": "chat_context", "chat_id": chat.id, "sources": sources}]
        manifest.source_digest = digest(manifest.sources)
    return {"bot_id": actor.id, "chat_id": chat.id, "messages": messages,
            "has_more": has_more, "before_seq": rows[0].seq if rows else None}


def scope_ids(db, name, args, context):
    owner_id = context["owner_id"]
    ids = {context.get("chat_id") or context.get("chat", {}).get("id")}
    if name == "dispatch_bot_tasks":
        ids.update(db.scalars(select(Chat.id).where(Chat.internal_owner_id == owner_id,
            Chat.kind == "bot", Chat.bot_id.in_(args.get("bot_ids", [])))))
    if name == "message_personal_agent":
        ids.update(chat.id for chat in home_chats(db, owner_id, [args["recipient_actor_id"]]).values())
    if name == "post_bot_room":
        ids.add(args["chat_id"])
    return ids - {None}


def lock_chats(db, chat_ids):
    list(db.scalars(select(Chat).where(Chat.id.in_(chat_ids)).order_by(Chat.id).with_for_update()
                   .execution_options(populate_existing=True)))


def command(service, db, origin, owner_id, name, args, key, action):
    if origin.kind == "private" and origin.reset_pending:
        fail(409, "Private reset cleanup is pending")
    operation_id = f"bot-command:{owner_id}:{digest(key)}"
    arguments_hash = digest(args)
    prior = db.get(ProductOperation, operation_id)
    if prior:
        if (prior.chat_id, prior.owner_id, prior.name, prior.arguments_hash) != (origin.id, owner_id, name, arguments_hash):
            fail(409, "Command key was already used for a different BOT action")
        return prior.result
    result = action()
    db.add(ProductOperation(operation_id=operation_id, chat_id=origin.id, owner_id=owner_id,
                            name=name, arguments_hash=arguments_hash, result=result))
    return result


def bot_profile(name, assignment):
    name, assignment = name.strip(), assignment.strip()
    if not name or len(name) > 128:
        fail(400, "BOT name must contain 1–128 characters")
    if not assignment:
        fail(400, "Describe what this BOT is responsible for")
    return name, assignment


def message_body(body):
    if not body.strip():
        fail(400, "Message cannot be blank")
    return body


def actor_ids(values, *, allow_empty=False):
    if not isinstance(values, list) or len(values) > 100 or any(not isinstance(value, str) or not value for value in values):
        fail(400, "Choose up to 100 personal agents")
    ids = list(dict.fromkeys(values))
    if not ids and not allow_empty:
        fail(400, "Choose at least one personal BOT")
    return ids


def require_bots(db, owner_id, bot_ids):
    ids = actor_ids(bot_ids)
    rows = bot_rows(db, owner_id, ids)
    if len(rows) != len(ids):
        fail(404, "A BOT was not found or belongs to another owner")
    return rows


def require_bot(db, owner_id, bot_id):
    return require_bots(db, owner_id, [bot_id])[0]


def require_agents(db, owner_id, ids):
    ids = actor_ids(list(ids))
    actors = {actor.id: actor for actor in db.scalars(select(Actor).where(
        Actor.id.in_(ids), Actor.owner_id == owner_id, Actor.kind.in_(["twin", "bot"])))}
    if len(actors) != len(ids):
        fail(404, "Personal agents must belong to the same owner")
    return actors


def home_chats(db, owner_id, ids):
    rows = db.execute(select(Actor.id, Chat).join(Chat,
        ((Actor.kind == "bot") & (Chat.bot_id == Actor.id) & (Chat.internal_owner_id == owner_id))
        | ((Actor.kind == "twin") & (Chat.kind == "private") & (Chat.private_owner_id == owner_id)))
        .where(Actor.owner_id == owner_id, Actor.id.in_(ids)))
    return dict(rows.all())
