-- Additive only. Explicit migration: python -m app.migrate_reactions
-- init_db also provisions this table; no history backfill or model invocation.
CREATE TABLE IF NOT EXISTS tt_message_reactions (
    message_id VARCHAR(80) NOT NULL REFERENCES tt_messages(id) ON DELETE CASCADE,
    actor_id VARCHAR(80) NOT NULL REFERENCES tt_actors(id),
    emoji VARCHAR(32) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (message_id, actor_id, emoji)
);
-- The PK supports batch reads by message and the per-message/actor 20-kind cap.
-- Chat is derived from Message; there is no duplicate chat_id or trigger.
