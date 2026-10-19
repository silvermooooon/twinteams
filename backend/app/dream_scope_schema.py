"""One additive cursor migration; existing frozen Dream intents retain their old units."""
from sqlalchemy import inspect, text


def upgrade(connection):
    columns = {row['name'] for row in inspect(connection).get_columns('tt_memory_dream_state')}
    if 'message_cursor_seq' in columns:
        return
    connection.execute(text('ALTER TABLE tt_memory_dream_state ADD COLUMN message_cursor_seq INTEGER'))
    # New message cursors start at zero; legacy accepted-request cursors are untouched.
    # Existing active/unknown intents must settle before the new scope is scheduled.
    from .config import settings
    deadline = "CURRENT_TIMESTAMP + make_interval(secs => :seconds)"
    connection.execute(text(f"""UPDATE tt_memory_dream_state SET due_at={deadline}
        WHERE queued_request_id IS NULL AND last_status <> 'recovery_required'"""),
        {'seconds': settings.memory_dream_interval_seconds})
    connection.execute(text(f"""INSERT INTO tt_memory_dream_state
        (owner_id,chat_id,cursor_seq,message_cursor_seq,due_at,last_status)
        SELECT c.private_owner_id,c.id,0,0,{deadline},'scheduled' FROM tt_chats c
        WHERE c.kind='private' AND NOT c.reset_pending AND EXISTS
          (SELECT 1 FROM tt_messages m WHERE m.chat_id=c.id AND m.state='published'
           AND m.kind IN ('human','twin','reply_for_me') LIMIT 1)
        ON CONFLICT (owner_id) DO NOTHING"""), {'seconds': settings.memory_dream_interval_seconds})
