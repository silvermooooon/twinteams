-- Additive owner-question schema, provisioned by python -m app.init_db.

BEGIN;

CREATE TABLE IF NOT EXISTS tt_question_requests (
	id VARCHAR(80) NOT NULL,
	owner_id VARCHAR(80) NOT NULL,
	chat_id VARCHAR(80) NOT NULL,
	origin_chat_id VARCHAR(80) NOT NULL,
	origin_request_id VARCHAR(80) NOT NULL,
	message_id VARCHAR(80) NOT NULL,
	runtime_session_id VARCHAR(128) NOT NULL,
	runtime_run_id VARCHAR(128) NOT NULL,
	runtime_command_id VARCHAR(256) NOT NULL,
	question TEXT NOT NULL,
	options JSON NOT NULL,
	source_provenance JSON NOT NULL,
	status VARCHAR(16) NOT NULL,
	answer TEXT,
	answer_message_id VARCHAR(80),
	continuation_request_id VARCHAR(80),
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	answered_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT tt_question_status CHECK (status IN ('pending','answered','cancelled')),
	FOREIGN KEY(owner_id) REFERENCES tt_owners (id),
	FOREIGN KEY(chat_id) REFERENCES tt_chats (id),
	FOREIGN KEY(origin_chat_id) REFERENCES tt_chats (id),
	FOREIGN KEY(origin_request_id) REFERENCES tt_reply_requests (id),
	UNIQUE (message_id),
	FOREIGN KEY(message_id) REFERENCES tt_messages (id),
	UNIQUE (answer_message_id),
	FOREIGN KEY(answer_message_id) REFERENCES tt_messages (id),
	FOREIGN KEY(continuation_request_id) REFERENCES tt_reply_requests (id)
);

CREATE INDEX IF NOT EXISTS ix_tt_question_requests_chat_id ON tt_question_requests (chat_id);

CREATE INDEX IF NOT EXISTS ix_tt_question_requests_origin_request_id ON tt_question_requests (origin_request_id);

CREATE INDEX IF NOT EXISTS ix_tt_question_requests_owner_id ON tt_question_requests (owner_id);

CREATE INDEX IF NOT EXISTS tt_question_pending_run ON tt_question_requests (runtime_session_id, runtime_run_id, status);

COMMIT;
