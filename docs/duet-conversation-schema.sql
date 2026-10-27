-- Explicit topology upgrade. Application provisioning uses the same declarations
-- in app/duet_conversation_schema.py; no API-startup migrations in production.
BEGIN;
ALTER TABLE tt_chats DROP CONSTRAINT IF EXISTS tt_chat_kind_owner;
ALTER TABLE tt_chats ADD CONSTRAINT tt_chat_kind_owner CHECK (
  (kind = 'private' AND private_owner_id IS NOT NULL) OR
  (kind IN ('group','duet') AND private_owner_id IS NULL)
);
CREATE TABLE IF NOT EXISTS tt_duet_conversations (
  chat_id VARCHAR(80) PRIMARY KEY REFERENCES tt_chats(id),
  origin_chat_id VARCHAR(80) NOT NULL REFERENCES tt_chats(id),
  initiating_request_id VARCHAR(80) NOT NULL REFERENCES tt_reply_requests(id),
  initiating_owner_id VARCHAR(80) NOT NULL REFERENCES tt_owners(id),
  operation_id VARCHAR(256) UNIQUE NOT NULL,
  purpose TEXT NOT NULL,
  opening_message TEXT NOT NULL,
  source_message_ids JSON NOT NULL,
  causal_chain_id VARCHAR(80) NOT NULL REFERENCES tt_causal_chains(id),
  status VARCHAR(16) NOT NULL CHECK (status IN ('pending','active','rejected','closed')),
  close_reason VARCHAR(64),
  created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tt_duet_conversations_origin_chat_id ON tt_duet_conversations(origin_chat_id);
CREATE TABLE IF NOT EXISTS tt_duet_contact_grants (
  owner_id VARCHAR(80) NOT NULL REFERENCES tt_owners(id),
  peer_owner_id VARCHAR(80) NOT NULL REFERENCES tt_owners(id),
  created_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (owner_id,peer_owner_id)
);
-- Preserve existing messages. Suppress only work that has not finished.
UPDATE tt_reply_requests SET cancel_requested=TRUE,
  status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END
WHERE chat_id IN (SELECT id FROM tt_chats WHERE kind='group')
  AND status IN ('queued','running');
UPDATE tt_dispatch_outbox SET kind='cancel',state='pending'
WHERE request_id IN (SELECT r.id FROM tt_reply_requests r JOIN tt_chats c ON c.id=r.chat_id
  WHERE c.kind='group' AND r.cancel_requested=TRUE AND r.status IN ('cancelled','running'));
UPDATE tt_trigger_candidates SET state='suppressed'
WHERE chat_id IN (SELECT id FROM tt_chats WHERE kind='group') AND state IN ('waiting','paused');
UPDATE tt_routing_decisions SET status='completed',reason='human_only_chat'
WHERE message_id IN (SELECT m.id FROM tt_messages m JOIN tt_chats c ON c.id=m.chat_id WHERE c.kind='group')
  AND status IN ('pending','paused');
COMMIT;
