-- Additive, repeatable schedule storage. Canonical executable migration:
-- python -m app.init_db (app.scheduled_task_schema).
-- No SDK journal, execution lease or reminder tables are duplicated.
CREATE TABLE IF NOT EXISTS tt_scheduled_tasks (
    id VARCHAR(80) PRIMARY KEY,
    owner_id VARCHAR(80) NOT NULL REFERENCES tt_owners(id),
    kind VARCHAR(24) NOT NULL CHECK (kind IN ('user','system_review')),
    title VARCHAR(256) NOT NULL,
    instructions TEXT NOT NULL,
    timezone VARCHAR(80) NOT NULL,
    schedule JSON NOT NULL,
    enabled BOOLEAN NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    next_run_at TIMESTAMPTZ,
    index_dirty BOOLEAN NOT NULL,
    details JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS tt_scheduled_due ON tt_scheduled_tasks(enabled, next_run_at);
CREATE INDEX IF NOT EXISTS tt_scheduled_dirty ON tt_scheduled_tasks(index_dirty, id);
CREATE INDEX IF NOT EXISTS tt_scheduled_owner ON tt_scheduled_tasks(owner_id, created_at);
CREATE INDEX IF NOT EXISTS tt_scheduled_request_history
    ON tt_reply_requests(command_fingerprint, accepted_at DESC)
    WHERE source IN ('scheduled_task','system_review');
