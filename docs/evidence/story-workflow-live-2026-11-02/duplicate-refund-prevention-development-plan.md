# Duplicate-refund prevention — development plan

**Due:** November 20, 2026  
**Status:** Plan for review; no code has been written or started.

## Concise design

Treat `(transaction identity T, refund-request identity R)` as the logical request identity. Use the existing durable database to retain the idempotency key/record, receipt time, request details needed to detect conflicts, and the known outcome. The storage operation must ensure concurrent submissions for the same pair converge on one logical request; the particular schema and atomicity mechanism remain to be selected during implementation.

An exact replay within 24 hours resolves to the existing request and its result or current status. A same-key request with a different amount is a key/payload conflict: reject it and never issue a refund for that conflicting submission. A distinct `R` for the same `T` is evaluated separately under applicable eligibility and business rules; do not merge it merely because `T` matches.

Keep a request pending while it is in flight or its outcome is unknown. Do not send another refund because of a timeout or uncertainty. Reconcile the original attempt first; only a confirmed no-effect failure can make a retry safe. Preserve the 24-hour recognition requirement from initial receipt. This plan does not assume what protection applies after that window.

## Implementation steps

1. **Resolve design questions** listed below with the relevant product, provider, and engineering owners before locking schema or behavior.
2. **Inspect the existing durable database and refund flow** to identify where request identity, provider calls, persistence, and reconciliation currently belong; document any constraints or migration needs.
3. **Design the durable request record and atomic claim/update path** so one `(T, R)` has one logical record under concurrent submissions. Define the minimum persisted payload needed for conflict detection and recovery; do not rely on process-local state.
4. **Integrate request handling:** resolve replays to the existing record; reject amount conflicts without a provider call; evaluate distinct request identities independently; keep uncertain/in-flight outcomes pending until reconciliation; permit retry only after confirmed no effect.
5. **Add reconciliation and observability** sufficient to resolve pending outcomes and audit state transitions, subject to agreed ownership, retention, and escalation policy.
6. **Validate migration/rollout and recovery behavior** against the existing system, then review acceptance evidence before release.

## Edge cases to cover

- Two or more simultaneous submissions with the same `(T, R)`, including a race around initial persistence/provider submission.
- Exact replay while pending, after success, and after a confirmed no-effect failure.
- Same `(T, R)` with a different amount: conflict, no second provider call/refund.
- Timeout or lost response after provider submission; process restart while pending; delayed or contradictory provider result during reconciliation.
- Confirmed no-effect followed by a retry that succeeds; ensure no more than one refund effect.
- Same `T` with a distinct `R`; evaluate independently and enforce applicable business limits.
- Replay near and after the 24-hour boundary, including clock/retention behavior once policy is decided.
- Database failure or interruption before/after provider interaction; ensure recovery does not mistake an unknown result for confirmed no effect.

## Test plan

- **Unit/contract:** identity matching, exact replay, amount-conflict rejection, distinct-request handling, eligibility hooks, and pending-versus-confirmed-no-effect rules.
- **Concurrency/integration:** parallel same-key submissions produce one logical request and at most one refund effect; verify durable behavior across service restart.
- **Failure/reconciliation:** simulate provider timeout, lost response, delayed/contradictory result, and database/process interruption. Assert unknown remains pending and no repeat refund is sent until reconciliation establishes no effect.
- **Retry:** after confirmed no effect, retry can proceed and eventual outcome still has at most one effect; without that proof, retry is blocked.
- **Window/boundary:** verify recognition for 24 hours from initial receipt and test boundary cases; do not assert post-window semantics until agreed.
- **End-to-end/regression:** exercise duplicate, conflict, distinct-request, and recovery paths through the refund flow; verify audit/retention behavior once specified.

## Decisions and risks to resolve

- Source, stability, scope, and uniqueness guarantees for `T` and `R`.
- How to prove no effect; provider capabilities; handling late or contradictory outcomes; and retry actors/authority.
- Pending-state ownership, reconciliation process, escalation, and customer-facing response.
- Protection for replays after 24 hours.
- Currency and other payload-field conflict behavior; amount/currency immutability; eligibility, partial refunds, and limits for distinct requests.
- Audit detail, data retention, and any migration/rollout constraints.

These remain open rather than assumed in this plan. **Next step:** review the plan and decisions with CCC before implementation; stop here until that discussion is complete.
