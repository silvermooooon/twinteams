"""Owner-selected human messages carried into the owner's private Duet conversation."""
from .conversation_context import message_source
from .models import Actor, ChatAttachment, Message


def reference_source(service, db, owner_id, destination, message_id):
    from .service import fail

    if destination.kind != "private" or destination.private_owner_id != owner_id:
        fail(403, "Only your private Duet conversation accepts a message reference")
    source = db.get(Message, message_id)
    if not source or source.kind != "human":
        fail(404, "Referenced human message is unavailable")
    origin, member = service.membership(db, owner_id, source.chat_id)
    if source.author_id == owner_id:
        fail(422, "Choose a message received from another person")
    if origin.kind != "group":
        fail(422, "Choose a message from a human conversation")
    service.visible_message(db, member, source.id)
    return {"kind": "chat_context", "context_role": "owner_message_reference",
            "chat_id": origin.id, "sources": [message_source(db, source)]}


def referenced_human_message(db, private_message):
    if private_message.kind == "human" and private_message.command_owner_id:
        for item in private_message.source_provenance or []:
            if item.get("context_role") == "owner_message_reference" and item.get("sources"):
                return db.get(Message, item["sources"][0]["id"])
    return None


def reference_json(service, db, original):
    from .attachments import workspace_path_id

    author = db.get(Actor, original.author_id)
    attachments = []
    for attachment_id in original.attachment_ids or []:
        row = db.get(ChatAttachment, attachment_id)
        if row and row.message_id == original.id and not row.private_owner_input:
            attachments.append(service.attachment_json(row) | {
                "workspace_id": workspace_path_id(row.relative_path.removeprefix(original.chat_id + "/"))})
    return {"chat_id": original.chat_id, "message_id": original.id, "body": original.body,
            "author_name": author.name if author else "Unknown", "attachments": attachments}
