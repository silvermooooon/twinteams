# Duet message-driven communication acceptance — 2026-10-29

Acceptance and the authorized local production deployment are complete.

The release replaces classifier routing and timed followups with direct delivery of every published message in an active, approved Duet channel. Receivers may finish silently. A causal chain permits six published peer messages, including its opening; a seventh stays unsent until an active participant authorizes six more. Private reports, questions, receipts and task execution do not spend that allowance. Owner-only draft inspection/cancellation and existing Once/Always/Revoke controls remain available.

Prepared outgoing statements retain their full internal request, context-manifest and SDK audit. Receiving the prepared body does not grant access to the sender’s private history. Pending publication rechecks original sources and consent. Old communication design documents and retired routing/followup entry points were removed or marked historical.

## Verification

- Frontend TypeScript checking and candidate production build passed; running production assets were untouched. Formal offline Chrome journeys passed: ordinary owner answer to final private report, silent completion, Once/Always/Revoke, six published messages with seventh pending, non-author Continue, owner-only draft visibility and cancellation. These used the real SDK with scripted decisions and **zero provider HTTP requests**.
- Backend coverage comprises **533 current unique cases**, with no uncovered cases, unresolved failures or skips in the final coverage ledger. This combines the initial full run and subsequent V2/V3/V4 targeted regressions; it is **not** a claim that a final single full run passed 533 cases. It includes actual offline SDK main/child private-history roundtrips, pending-source withdrawal/reset/closed-channel checks, lock ordering, and concurrent cross-channel six/seven boundaries.

| Real-model phase | Actual HTTP attempts | Observed result |
|---|---:|---|
| Initial V1 natural scenario | 13 | Ordinary unquoted B answer associated correctly, but B’s generated final failed product projection because of its private source dependency. A received no final result. Evidence retained. |
| Retained-state V3 Retry | 1 | Browser Retry superseded the failure; the model returned an empty final from the retained SDK session. No answer reached A. Recorded as unsuccessful recovery. |
| Fresh V4 natural scenario | 15 | Both owners approved Once in Chrome. B asked one focused question. Its owner answered through the ordinary composer without quoting or using the question-card answer control. The model associated the answer, published its normal terminal final, and A’s Duet privately reported the correct deadline and confirmation scope. |

All **29 / 32** authorized actual HTTP attempts used `gpt-6-luna` through Responses; no additional input was sent after the successful result. The three published peer messages were opening, B’s answer, and A’s delivery acknowledgement. The final B receipt task completed with empty text. Six requests completed, with one answered question, no pending messages, peer-chain spend **3/6**, and ordinary private-answer chain spend **0**. Two final snapshots 56.73 seconds apart were unchanged; the final snapshot was 72.18 seconds after the last SDK event, with HTTP count remaining 15.

This single natural scenario observed no repeated owner question. It does not establish that every previously reported duplicate-message case is fixed. The fictional marker in B’s answer was not repeated; the required confirmed time and scope reached A. Fresh V4 did not choose private-history reads; those are covered by the separate actual offline SDK regressions.

## Evidence, provenance and preservation

Evidence root: `/tmp/duet-explicit-peer-20261029`. Negative phases remain in `live-final`; successful fresh evidence, exact message/request/chain IDs, SDK events, immutable provider ledger and `acceptance.json` are in `live-v4-fresh`. Key screenshots are `a-private-final.jpg`, `b-owner-question.jpg`, `b-ordinary-answer.jpg`, and `a-private-and-peer-completed.jpg`. No screenshots, private production text or credentials are stored in this report.

V4 product manifest: `/tmp/duet-message-driven-product-v4-frozen-hashes.json`. The only approved change during fresh live verification was comment wording in `backend/.env.example`; the starting manifest and exact delta remain preserved. A post-live comment-only summarizer naming correction is also recorded in the final manifest; neither comment change alters execution. Formal offline helper drift was reconstructed exactly and consisted only of unused imports/local-variable removal, with no behavior change. Backend coverage ledger: `/tmp/duet-message-driven-v4-final-validation.json`.

All isolated stacks were cleaned up: owned processes stopped with SIGTERM only, disposable schemas/roots and namespaced Redis keys removed, and test browser tab closed. The pre-release read-only snapshot matches the original baseline across all 53 production table hashes, original worker/API/FRP processes, frontend assets, protected workspace/configuration files and SDK state. This preservation comparison was taken before the authorized deployment below.


## Authorized local deployment

Core stopped the old APIs and worker with SIGTERM after fresh idle checks, applied only the reviewed transactional `006_duet_message_channels.sql` upgrade, checked the schema, published the tested candidate assets with an atomic index replacement, and started worker **47249** and APIs **47250/47251**. The upgrade removed the four retired communication tables/column and reset obsolete allowance accounting; no full initialization or unrelated backfill ran. FRPC **24440**, PostgreSQL and Redis were not restarted.

Post-start preservation passed: all **240 messages**, including **102 human messages**, message hashes and chat sequence counters remain intact, as do configuration, credentials, owner consent and SDK records. Active work is zero. Both local API ports and the public endpoint returned **200** for health, index and both candidate entry assets; downloaded hashes match the tested candidate. Old cached assets remain available. No production test chat, task or model request was created. Rollout evidence is in `release-v4/preservation-result.json` and `release-v4/health-static-verification.json` under the evidence root.
