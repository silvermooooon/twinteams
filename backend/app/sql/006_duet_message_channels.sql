-- Breaking product upgrade. Run only after old auto/followup SDK work has settled.
-- No SDK journals, owner communication consent, messages or workspace files are deleted.
-- Caller must execute the entire file in a single transaction.
DO $upgrade$
BEGIN
    IF to_regclass('tt_automatic_reservations') IS NOT NULL
       OR to_regclass('tt_trigger_candidates') IS NOT NULL
       OR to_regclass('tt_routing_decisions') IS NOT NULL
       OR to_regclass('tt_twin_followups') IS NOT NULL THEN
        UPDATE tt_reply_requests SET status='cancelled', cancel_requested=TRUE
        WHERE source IN ('auto','followup_result') AND status IN ('queued','running');
        UPDATE tt_dispatch_outbox SET state='delivered'
        WHERE request_id IN (SELECT id FROM tt_reply_requests
            WHERE source IN ('auto','followup_result') AND cancel_requested=TRUE);
        DELETE FROM tt_automatic_allowance_grants;
        UPDATE tt_causal_chains SET spent=0, allowance=6;
        UPDATE tt_pending_child_results SET state='pending', pause_reason=NULL
        WHERE state='paused' AND pause_reason IN ('budget_exhausted','repeated_content');
    END IF;
END
$upgrade$;
ALTER TABLE tt_reply_requests DROP COLUMN IF EXISTS automatic_reservation_id;
DROP TABLE IF EXISTS tt_automatic_reservations;
DROP TABLE IF EXISTS tt_trigger_candidates;
DROP TABLE IF EXISTS tt_routing_decisions;
DROP TABLE IF EXISTS tt_twin_followups;
ALTER TABLE tt_messages ALTER COLUMN seq DROP NOT NULL;
ALTER TABLE tt_messages DROP CONSTRAINT IF EXISTS tt_message_state;
ALTER TABLE tt_messages ADD CONSTRAINT tt_message_state CHECK
    (state IN ('pending','published','withdrawn') AND (state != 'published' OR seq IS NOT NULL)
     AND (state != 'pending' OR seq IS NULL));
CREATE INDEX IF NOT EXISTS tt_pending_duet_sends
    ON tt_messages (causal_chain_id, created_at, id) WHERE state='pending';
