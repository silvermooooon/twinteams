-- Immutable BOT nodes and owner-local installs. No existing content is rewritten.
ALTER TABLE tt_actors ADD COLUMN IF NOT EXISTS bot_skill_pack_digest VARCHAR(64);

CREATE TABLE IF NOT EXISTS tt_bot_snapshots (
    id VARCHAR(80) PRIMARY KEY,
    parent_snapshot_id VARCHAR(80) REFERENCES tt_bot_snapshots(id),
    source_bot_id VARCHAR(80) NOT NULL REFERENCES tt_actors(id),
    source_owner_id VARCHAR(80) NOT NULL REFERENCES tt_owners(id),
    name VARCHAR(128) NOT NULL,
    assignment TEXT NOT NULL,
    assignment_summary VARCHAR(500) NOT NULL,
    initials VARCHAR(8) NOT NULL,
    color VARCHAR(32) NOT NULL,
    avatar_digest VARCHAR(64),
    avatar_preset VARCHAR(24),
    skill_pack_digest VARCHAR(64) NOT NULL,
    skill_count INTEGER NOT NULL,
    rules_digest VARCHAR(64) NOT NULL,
    memory_commit VARCHAR(40),
    memory_included BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS tt_bot_snapshot_source
    ON tt_bot_snapshots(source_owner_id,source_bot_id,created_at);

ALTER TABLE tt_actors ADD COLUMN IF NOT EXISTS installed_snapshot_id VARCHAR(80);
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'tt_actor_installed_snapshot'
                   AND conrelid = 'tt_actors'::regclass) THEN
        ALTER TABLE tt_actors ADD CONSTRAINT tt_actor_installed_snapshot
            FOREIGN KEY (installed_snapshot_id) REFERENCES tt_bot_snapshots(id);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS tt_bot_share_tokens (
    id VARCHAR(80) PRIMARY KEY,
    token VARCHAR(64) NOT NULL UNIQUE,
    owner_id VARCHAR(80) NOT NULL REFERENCES tt_owners(id),
    bot_id VARCHAR(80) NOT NULL REFERENCES tt_actors(id),
    snapshot_id VARCHAR(80) NOT NULL REFERENCES tt_bot_snapshots(id),
    client_request_key VARCHAR(160) NOT NULL,
    request_kind VARCHAR(16) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    CONSTRAINT tt_bot_share_request UNIQUE (bot_id,client_request_key)
);
CREATE INDEX IF NOT EXISTS tt_bot_share_catalog ON tt_bot_share_tokens(owner_id,bot_id,snapshot_id);

CREATE TABLE IF NOT EXISTS tt_bot_installs (
    owner_id VARCHAR(80) NOT NULL REFERENCES tt_owners(id),
    snapshot_id VARCHAR(80) NOT NULL REFERENCES tt_bot_snapshots(id),
    bot_id VARCHAR(80) NOT NULL UNIQUE REFERENCES tt_actors(id) DEFERRABLE INITIALLY DEFERRED,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (owner_id,snapshot_id)
);

CREATE OR REPLACE FUNCTION tt_reject_bot_snapshot_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'BOT snapshots are immutable';
END $$;
DROP TRIGGER IF EXISTS tt_bot_snapshot_immutable ON tt_bot_snapshots;
CREATE TRIGGER tt_bot_snapshot_immutable BEFORE UPDATE ON tt_bot_snapshots
    FOR EACH ROW EXECUTE FUNCTION tt_reject_bot_snapshot_update();
