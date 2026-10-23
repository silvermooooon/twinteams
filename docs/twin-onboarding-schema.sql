-- Additive and repeatable. Canonical migration: python -m app.init_db.
-- Message IDs are historical references, not FKs: private reset removes messages,
-- while this owner lifecycle survives and never enrolls them again.
CREATE TABLE IF NOT EXISTS tt_onboarding (
    owner_id VARCHAR(80) PRIMARY KEY REFERENCES tt_owners(id),
    chat_id VARCHAR(80) NOT NULL REFERENCES tt_chats(id),
    status VARCHAR(16) NOT NULL CHECK (status IN ('active','completed','skipped')),
    welcome_message_id VARCHAR(80),
    first_message_id VARCHAR(80),
    created_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ
);
