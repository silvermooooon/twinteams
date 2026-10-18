-- Reference pair-key DDL; existing application schema is required.
-- NOT a complete migration: canonical keys must also be backfilled using
-- python -m app.migrate_direct_chats (or app.init_db during normal provisioning).
-- Python sorts both owner IDs and hashes canonical JSON identically to create_chat.
-- Backfill refuses duplicate active two-owner conversations. Review those explicitly;
-- this DDL and the application migration never merge/delete conversation history.
BEGIN;
ALTER TABLE tt_chats ADD COLUMN IF NOT EXISTS direct_pair_key VARCHAR(64);
CREATE UNIQUE INDEX IF NOT EXISTS tt_direct_pair_key ON tt_chats (direct_pair_key);
COMMIT;
