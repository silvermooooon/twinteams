-- Explicit additive personal BOT upgrade. Existing actors, chats and history remain.
ALTER TABLE tt_actors ADD COLUMN IF NOT EXISTS assignment TEXT;
ALTER TABLE tt_actors DROP CONSTRAINT IF EXISTS tt_actors_owner_id_kind_key;
ALTER TABLE tt_actors DROP CONSTRAINT IF EXISTS tt_actor_kind;
ALTER TABLE tt_actors ADD CONSTRAINT tt_actor_kind CHECK (kind IN ('human','twin','bot'));
CREATE UNIQUE INDEX IF NOT EXISTS tt_owner_primary_actors ON tt_actors (owner_id,kind)
    WHERE kind IN ('human','twin');

ALTER TABLE tt_chats ADD COLUMN IF NOT EXISTS internal_owner_id VARCHAR(80) REFERENCES tt_owners(id);
ALTER TABLE tt_chats ADD COLUMN IF NOT EXISTS bot_id VARCHAR(80) UNIQUE REFERENCES tt_actors(id);
ALTER TABLE tt_chats ADD COLUMN IF NOT EXISTS member_actor_ids JSON NOT NULL DEFAULT '[]';
ALTER TABLE tt_chats DROP CONSTRAINT IF EXISTS tt_chat_kind_owner;
ALTER TABLE tt_chats ADD CONSTRAINT tt_chat_kind_owner CHECK (
    (kind = 'private' AND private_owner_id IS NOT NULL AND internal_owner_id IS NULL AND bot_id IS NULL)
    OR (kind IN ('group','duet') AND private_owner_id IS NULL AND internal_owner_id IS NULL AND bot_id IS NULL)
    OR (kind = 'bot' AND private_owner_id IS NULL AND internal_owner_id IS NOT NULL AND bot_id IS NOT NULL)
    OR (kind = 'internal' AND private_owner_id IS NULL AND internal_owner_id IS NOT NULL AND bot_id IS NULL)
);
CREATE INDEX IF NOT EXISTS tt_internal_owner_chats ON tt_chats (internal_owner_id,kind)
    WHERE internal_owner_id IS NOT NULL;

ALTER TABLE tt_messages ADD COLUMN IF NOT EXISTS coordination JSON;
ALTER TABLE tt_reply_requests ADD COLUMN IF NOT EXISTS bot_handoff JSON;
CREATE INDEX IF NOT EXISTS tt_bot_handoff_pending
    ON tt_reply_requests ((bot_handoff ->> 'session_id'))
    WHERE status IN ('queued','running');
