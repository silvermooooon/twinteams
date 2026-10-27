"""Explicit, repeatable topology upgrade. Preserve old human-chat transcript rows."""
from sqlalchemy import text


def upgrade_duet_conversation_schema(engine):
    from .models import DuetContactGrant, DuetConversation
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE tt_chats DROP CONSTRAINT IF EXISTS tt_chat_kind_owner"))
        connection.execute(text("""ALTER TABLE tt_chats ADD CONSTRAINT tt_chat_kind_owner CHECK
            ((kind='private' AND private_owner_id IS NOT NULL) OR
             (kind IN ('group','duet') AND private_owner_id IS NULL))"""))
        DuetConversation.__table__.create(connection, checkfirst=True)
        DuetContactGrant.__table__.create(connection, checkfirst=True)
        # Historical accepted mixed-chat work cannot start or publish after upgrade.
        connection.execute(text("""UPDATE tt_reply_requests SET cancel_requested=TRUE,
            status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END
            WHERE chat_id IN (SELECT id FROM tt_chats WHERE kind='group')
            AND status IN ('queued','running')"""))
        connection.execute(text("""UPDATE tt_dispatch_outbox SET kind='cancel',state='pending'
            WHERE request_id IN (SELECT r.id FROM tt_reply_requests r JOIN tt_chats c ON c.id=r.chat_id
                WHERE c.kind='group' AND r.cancel_requested=TRUE AND r.status IN ('cancelled','running'))"""))
        connection.execute(text("""UPDATE tt_trigger_candidates SET state='suppressed'
            WHERE chat_id IN (SELECT id FROM tt_chats WHERE kind='group')
            AND state IN ('waiting','paused')"""))
        connection.execute(text("""UPDATE tt_routing_decisions SET status='completed',reason='human_only_chat'
            WHERE message_id IN (SELECT m.id FROM tt_messages m JOIN tt_chats c ON c.id=m.chat_id WHERE c.kind='group')
            AND status IN ('pending','paused')"""))


def check_duet_conversation_schema(inspector):
    checks = inspector.get_check_constraints("tt_chats")
    if (not inspector.has_table("tt_duet_conversations") or not inspector.has_table("tt_duet_contact_grants")
            or not any(row["name"] == "tt_chat_kind_owner" and "duet" in row["sqltext"] for row in checks)):
        raise RuntimeError("Run python -m app.init_db before starting API or worker")
