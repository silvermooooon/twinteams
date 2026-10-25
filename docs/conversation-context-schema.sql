-- Additive provisioning only. Original messages and runtime journals are retained.
ALTER TABLE tt_summaries ADD COLUMN IF NOT EXISTS owner_id VARCHAR(80) REFERENCES tt_owners(id);
ALTER TABLE tt_summaries ADD COLUMN IF NOT EXISTS grant_version INTEGER;
ALTER TABLE tt_summaries ADD COLUMN IF NOT EXISTS private_generation INTEGER;
CREATE INDEX IF NOT EXISTS tt_summary_checkpoint
    ON tt_summaries (chat_id, owner_id, grant_version, coverage_end_seq);
CREATE INDEX IF NOT EXISTS tt_summary_sources_gin
    ON tt_summaries USING GIN ((source_message_ids::jsonb)) WHERE state = 'valid';

-- Old tool-created summaries have NULL owner/grant and are never implicitly
-- promoted to server-validated checkpoints. Frozen legacy tools can still save
-- those records during recovery. Checkpoints are rebuildable caches over the
-- authoritative tt_messages sequence, scoped to one owner/history grant/reset.
-- Summary.source_message_ids is internal transitive invalidation metadata. It is
-- never inserted into model input, model tool arguments or the context manifest.
-- Compact summary provenance preserves external membership/history/reset grants.
-- Substantive source edits/deletion invalidate matching checkpoints via the GIN
-- index in the same product transaction; raw original history is not removed.

-- Authorization metadata is paged past denied rows before visible bodies are
-- fetched. Usual reads are one checkpoint plus the recent authorized window.
-- A long denied-only prefix may require several indexed metadata pages, without
-- loading its original message bodies or keeping a second negative-ACL cursor.

CREATE INDEX IF NOT EXISTS ix_tt_summaries_owner_id ON tt_summaries (owner_id);
CREATE INDEX IF NOT EXISTS tt_question_owner_pending
    ON tt_question_requests (owner_id, chat_id, created_at) WHERE status = 'pending';
-- Membership changes set an owner's valid checkpoint grant_version to NULL.
-- This retires cache reuse, while actual source ACLs still govern historical
-- summary references. Exact server batch validation can reactivate a checkpoint.
