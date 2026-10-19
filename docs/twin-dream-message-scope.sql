-- Additive request-cursor -> private-message scope migration (default four-hour interval).
-- Prefer python -m app.init_db when TWINTEAMS_MEMORY_DREAM_INTERVAL_SECONDS is customized.
-- No message bodies are read or changed. Run before starting the new API/worker code.
BEGIN;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
        WHERE table_schema=current_schema() AND table_name='tt_memory_dream_state'
        AND column_name='message_cursor_seq') THEN
        ALTER TABLE tt_memory_dream_state ADD COLUMN message_cursor_seq INTEGER;
        -- Preserve cursor_seq and every queued/unknown legacy intent verbatim.
        UPDATE tt_memory_dream_state SET due_at=CURRENT_TIMESTAMP + INTERVAL '4 hours'
          WHERE queued_request_id IS NULL AND last_status <> 'recovery_required';
        INSERT INTO tt_memory_dream_state
          (owner_id,chat_id,cursor_seq,message_cursor_seq,due_at,last_status)
          SELECT c.private_owner_id,c.id,0,0,CURRENT_TIMESTAMP + INTERVAL '4 hours','scheduled'
          FROM tt_chats c WHERE c.kind='private' AND NOT c.reset_pending AND EXISTS
            (SELECT 1 FROM tt_messages m WHERE m.chat_id=c.id AND m.state='published'
             AND m.kind IN ('human','twin','reply_for_me') LIMIT 1)
          ON CONFLICT (owner_id) DO NOTHING;
    END IF;
END $$;
COMMIT;
