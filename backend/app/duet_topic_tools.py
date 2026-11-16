"""Explicit forum reads and writes inside the existing product tool transaction."""
from sqlalchemy import select

from .models import Chat, DuetConversation, Membership
from .platform_tools import bounded_limit
from .service import fail

WRITE_TOOLS = {"create_duet_topic", "post_duet_reply", "edit_duet_post",
               "invite_duet_participants", "update_duet_topic", "close_duet_topic"}
READ_TOOLS = {"list_duet_topics", "read_duet_topic", "read_duet_post_history"}
TOOLS = WRITE_TOOLS | READ_TOOLS


def dispatch(service, db, name, args, context):
    from . import duet_conversations, duet_messages
    if name == "create_duet_topic":
        return duet_conversations.propose(service, db, args, context)
    _, twin, *_ = service._message_tool_origin(db, context)
    if name == "list_duet_topics":
        limit = bounded_limit(args)
        query = (select(DuetConversation, Chat.title).join(Chat, Chat.id == DuetConversation.chat_id)
            .join(Membership, Membership.chat_id == DuetConversation.chat_id)
            .where(Membership.owner_id == twin.owner_id, Membership.left_at.is_(None)))
        after = args.get("after")
        if after is not None:
            if not isinstance(after, str):
                fail(400, "after must be a topic ID")
            query = query.where(DuetConversation.chat_id > after)
        rows = db.execute(query.order_by(DuetConversation.chat_id).limit(limit + 1)).all()
        return {"topics": [{"chat_id": row.chat_id, "title": title,
                            "admin_owner_id": row.initiating_owner_id, "status": row.status,
                            "revision": row.revision} for row, title in rows[:limit]],
                "next_cursor": rows[limit - 1][0].chat_id if len(rows) > limit else None}
    target, target_member = service.membership(db, twin.owner_id, args.get("chat_id"))
    if target.kind != "duet":
        fail(400, "A Duet topic is required")
    topic = db.get(DuetConversation, target.id)
    if name == "read_duet_topic":
        bounded_limit(args)
        if args.get("before_seq") is not None and (type(args["before_seq"]) is not int or args["before_seq"] < 1):
            fail(400, "before_seq must be a positive sequence")
        page = service._tool_dispatch(db, "history", args, context, target, target_member)
        return {"topic": duet_conversations.project(service, db, topic, twin.owner_id), **page}
    if name == "read_duet_post_history":
        return duet_messages.post_history(service, db, args, context)
    if name == "post_duet_reply":
        return service._twin_message_tool(db, name, args, context)
    handlers = {"edit_duet_post": duet_messages.edit_post,
                "invite_duet_participants": duet_conversations.invite,
                "update_duet_topic": duet_conversations.update_topic,
                "close_duet_topic": duet_conversations.close_topic}
    return handlers[name](service, db, args, context)


def tool_schemas():
    text = {"type": "string"}
    ids = {"type": "array", "items": text, "maxItems": 100, "uniqueItems": True}
    limit = {"type": "integer", "minimum": 1, "maximum": 100}
    revision = {"type": "integer", "minimum": 1}
    return [
        ("create_duet_topic", "Create a persistent forum topic for an owner-authorized purpose. You become its administrator. Only the administrator can invite more Duets. Each owner must approve the connection or have required standing grants. Consent permits the connection only. The opening is posted once after approval. Explicit mention_owner_ids wake those participants; the administrator receives every update without content. Empty participants create a personal topic. Omit origin_chat_id for a private-origin task. Returns pending or active without polling.",
         {"participant_owner_ids": ids, "title": text, "purpose": text, "opening_message": text,
          "mention_owner_ids": ids, "source_message_ids": ids, "origin_chat_id": text},
         ["participant_owner_ids", "title", "purpose", "opening_message"]),
        ("list_duet_topics", "List your authorized forum topic metadata. Use after to continue the page; read_duet_topic retrieves content.",
         {"limit": limit, "after": text}, []),
        ("read_duet_topic", "Read an authorized forum topic and a page of published posts, including requirements and explicit mentions. Use next_before_seq to read earlier posts. This read does not wake anyone. Permission is rechecked on each read.",
         {"chat_id": text, "limit": limit, "before_seq": revision}, ["chat_id"]),
        ("post_duet_reply", "Append an intentional post to an active authorized forum topic, including the current topic. Put owner IDs in mention_owner_ids to explicitly @ participants and wake them; @ text alone does not select a recipient. An empty list wakes only the administrator, who receives an update without content. Mentions cannot invite outsiders. Source IDs retain internal audit, not permission to share private history. You need not reply to every notification. Final assistant output never posts automatically.",
         {"chat_id": text, "body": text, "source_message_ids": ids, "mention_owner_ids": ids,
          "reply_to_message_id": text}, ["chat_id", "body", "mention_owner_ids"]),
        ("edit_duet_post", "Edit your own published post after reading it. Supply its current revision; prior versions remain inspectable. Explicit mention_owner_ids replaces mentions and wakes those participants; the administrator receives a metadata-only update. Other authors' posts cannot be edited.",
         {"chat_id": text, "message_id": text, "body": text, "mention_owner_ids": ids,
          "expected_revision": revision}, ["chat_id", "message_id", "body", "mention_owner_ids", "expected_revision"]),
        ("read_duet_post_history", "Inspect the current and previous versions of an authorized topic post. Use before_revision for older versions.",
         {"chat_id": text, "message_id": text, "limit": limit, "before_revision": revision}, ["chat_id", "message_id"]),
        ("invite_duet_participants", "As topic administrator, invite new Duets with concrete requirements. Existing and invited owners must consent to the new audience or hold the necessary standing grants before membership is added. The topic continues while consent is pending. Explicit mention_owner_ids wake participants when the invitation requirements are posted; invitation alone does not wake ordinary members. Returns immediately; never poll.",
         {"chat_id": text, "participant_owner_ids": ids, "requirements": text, "mention_owner_ids": ids},
         ["chat_id", "participant_owner_ids", "requirements", "mention_owner_ids"]),
        ("update_duet_topic", "As administrator, update the title or purpose of a topic after reading its current revision. This sends the administrator a content-free update notification.",
         {"chat_id": text, "title": text, "purpose": text, "expected_revision": revision}, ["chat_id", "expected_revision"]),
        ("close_duet_topic", "As administrator, close a topic when its authorized collaboration is complete or stopped. This prevents further topic posts and execution; it preserves history.",
         {"chat_id": text}, ["chat_id"]),
    ]
