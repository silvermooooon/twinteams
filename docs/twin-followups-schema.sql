-- Additive, repeatable PostgreSQL DDL. Existing app tables must already exist.
-- Normal provisioning: python -m app.init_db.

BEGIN;

CREATE TABLE IF NOT EXISTS tt_twin_followups (
	id VARCHAR(80) NOT NULL,
	owner_id VARCHAR(80) NOT NULL,
	chat_id VARCHAR(80) NOT NULL,
	origin_request_id VARCHAR(80) NOT NULL,
	target_chat_id VARCHAR(80) NOT NULL,
	sent_message_id VARCHAR(80) NOT NULL,
	causal_chain_id VARCHAR(80) NOT NULL,
	due_at TIMESTAMP WITH TIME ZONE NOT NULL,
	status VARCHAR(16) NOT NULL,
	reply_message_id VARCHAR(80),
	settled_at TIMESTAMP WITH TIME ZONE,
	notification_state VARCHAR(16) NOT NULL,
	pause_reason VARCHAR(40),
	continuation_request_id VARCHAR(80),
	source_provenance JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(owner_id) REFERENCES tt_owners (id),
	FOREIGN KEY(chat_id) REFERENCES tt_chats (id),
	FOREIGN KEY(origin_request_id) REFERENCES tt_reply_requests (id),
	FOREIGN KEY(target_chat_id) REFERENCES tt_chats (id),
	UNIQUE (sent_message_id),
	FOREIGN KEY(sent_message_id) REFERENCES tt_messages (id),
	FOREIGN KEY(causal_chain_id) REFERENCES tt_causal_chains (id),
	FOREIGN KEY(reply_message_id) REFERENCES tt_messages (id),
	FOREIGN KEY(continuation_request_id) REFERENCES tt_reply_requests (id)
);

CREATE INDEX IF NOT EXISTS ix_tt_twin_followups_causal_chain_id ON tt_twin_followups (causal_chain_id);

CREATE INDEX IF NOT EXISTS ix_tt_twin_followups_chat_id ON tt_twin_followups (chat_id);

CREATE INDEX IF NOT EXISTS ix_tt_twin_followups_origin_request_id ON tt_twin_followups (origin_request_id);

CREATE INDEX IF NOT EXISTS ix_tt_twin_followups_reply_message_id ON tt_twin_followups (reply_message_id);

CREATE INDEX IF NOT EXISTS ix_tt_twin_followups_target_chat_id ON tt_twin_followups (target_chat_id);

CREATE INDEX IF NOT EXISTS tt_followup_delivery ON tt_twin_followups (notification_state, chat_id);

CREATE INDEX IF NOT EXISTS tt_followup_due ON tt_twin_followups (status, due_at);

COMMIT;
