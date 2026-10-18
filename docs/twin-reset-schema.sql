-- Reset epoch schema. Existing application + Memory schema are required.

-- Canonical provisioning is python -m app.init_db: it first preserves legacy Soul in SOUL.md, then drops old DB Soul columns.

-- This SQL deliberately never drops legacy Soul columns or user files.

BEGIN;

ALTER TABLE tt_chats ADD COLUMN IF NOT EXISTS reset_generation INTEGER NOT NULL DEFAULT 0;

ALTER TABLE tt_chats ADD COLUMN IF NOT EXISTS reset_pending BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE tt_reply_requests ADD COLUMN IF NOT EXISTS private_generation INTEGER;

ALTER TABLE tt_activity_runs ADD COLUMN IF NOT EXISTS private_generation INTEGER NOT NULL DEFAULT 0;

ALTER TABLE tt_memory_edit_audit ADD COLUMN IF NOT EXISTS private_generation INTEGER NOT NULL DEFAULT 0;

UPDATE tt_reply_requests r SET private_generation=0 FROM tt_chats c WHERE r.chat_id=c.id AND c.kind='private' AND r.private_generation IS NULL;

CREATE TABLE IF NOT EXISTS tt_twin_resets (
	id VARCHAR(80) NOT NULL,
	owner_id VARCHAR(80) NOT NULL,
	chat_id VARCHAR(80) NOT NULL,
	idempotency_key VARCHAR(160) NOT NULL,
	expected_generation INTEGER NOT NULL,
	generation INTEGER NOT NULL,
	status VARCHAR(16) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (owner_id, idempotency_key),
	FOREIGN KEY(owner_id) REFERENCES tt_owners (id),
	FOREIGN KEY(chat_id) REFERENCES tt_chats (id)
);

CREATE INDEX IF NOT EXISTS ix_tt_twin_resets_owner_id ON tt_twin_resets (owner_id);

COMMIT;
