-- Additive PostgreSQL upgrade; repeatable. Run before API/worker rollout.
-- Normal provisioning also applies this column: python -m app.init_db.
ALTER TABLE tt_routing_decisions ADD COLUMN IF NOT EXISTS command_id VARCHAR(80);
-- NULL retains the original message-ID SDK command for existing decisions.
