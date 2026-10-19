-- Local Git Memory and incremental Dream tables. File-based SOUL migration must use python -m app.init_db.

BEGIN;

CREATE TABLE IF NOT EXISTS tt_memory_edit_audit (
	id VARCHAR(80) NOT NULL,
	owner_id VARCHAR(80) NOT NULL,
	private_generation INTEGER NOT NULL,
	key VARCHAR(512) NOT NULL,
	action VARCHAR(16) NOT NULL,
	content TEXT,
	state VARCHAR(24) NOT NULL,
	version VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_id) REFERENCES tt_owners (id)
);

CREATE INDEX IF NOT EXISTS tt_memory_edit_owner_time ON tt_memory_edit_audit (owner_id, created_at);

CREATE INDEX IF NOT EXISTS ix_tt_memory_edit_audit_owner_id ON tt_memory_edit_audit (owner_id);

CREATE TABLE IF NOT EXISTS tt_memory_dream_state (
	owner_id VARCHAR(80) NOT NULL,
	chat_id VARCHAR(80) NOT NULL,
	cursor_seq INTEGER NOT NULL,
	message_cursor_seq INTEGER,
	due_at TIMESTAMP WITH TIME ZONE,
	queued_request_id VARCHAR(80),
	last_status VARCHAR(24) NOT NULL,
	last_version VARCHAR(64),
	last_error TEXT,
	last_success_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (owner_id),
	FOREIGN KEY(owner_id) REFERENCES tt_owners (id),
	UNIQUE (chat_id),
	FOREIGN KEY(chat_id) REFERENCES tt_chats (id),
	FOREIGN KEY(queued_request_id) REFERENCES tt_reply_requests (id)
);

CREATE INDEX IF NOT EXISTS tt_memory_dream_due ON tt_memory_dream_state (due_at, queued_request_id);

ALTER TABLE tt_memory_edit_audit ADD COLUMN IF NOT EXISTS private_generation INTEGER NOT NULL DEFAULT 0;

COMMIT;
