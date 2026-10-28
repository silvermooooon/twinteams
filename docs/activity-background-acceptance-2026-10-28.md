# Activity visibility and background capacity acceptance — 2026-10-28

The combined change was deployed and verified. Activity excludes scheduled-task, system-review and Memory Dream runs and their derived continuations/children. User work remains eligible, including user-rooted followups and automatic conversation activity. The stored `user_visible` flag and partial index filter before pagination; historical classification runs once during explicit upgrade. Journals, messages, scheduled-task configuration and internal execution remain intact.

Foreground and background requests now use independent per-worker admission and SDK ExecutionHost pools. Actual settings from the existing private environment are foreground **8 total / 2 per owner**, background **2 total / 1 per owner**, and **2 background children**. These are logical limits within each worker; database, provider, process and machine resources remain shared. The SDK checkout and actual private environment files were unchanged.

| Verification | Result |
| --- | --- |
| Earlier Activity phase | 198/198 offline cases passed at that phase's frozen revision |
| Final combined affected coverage | 103 unique cases verified: 82 runtime cases plus 21 core cases; no unresolved failures |
| Capacity scenarios | Real offline SDK + disposable PostgreSQL: 2 background and 8 foreground roots/children, cold recovery, ordered continuations, unknown-origin handling and child-answer routing |
| Frontend | Node 22 typecheck and temporary candidate build passed; verified candidate assets published |
| Real public Chrome Activity | AAA now has 34 visible cards; exact order matches database visibility; system/scheduled cards absent |
| User detail | Existing user run opens with four committed events; closing leaves no drawer content |
| Authenticated HTTP, both APIs | Existing hidden system detail returns 404; retained user detail returns 200 |
| Filtered pagination, limit 7 | Pages 7/7/7/7/6; all 34 visible runs in exact order, no duplicates or empty pages |

Two stale manager-key fixture assertions in the final runtime round were corrected; the complete affected seven-case file then passed. Original failure logs remain preserved. Capacity verification used offline adapters, not real-provider load testing. No production projected child runs existed, so nested visibility and capacity claims rely on isolated tests rather than browser observations.

Release first confirmed zero active work, gracefully stopped the old worker and two APIs using SIGTERM, applied only the Activity column/backfill/index upgrade, published the candidate assets with an atomic index replacement, and started worker **9284** and APIs **9285/9286**. All 12 local/public health, index, JavaScript and CSS checks passed with public TLS verification. FRPC **24440** was untouched. Frontend changes were expected: the new `index-BdOYP-5m.js` and index were published while old assets were retained.

The fresh release baseline and final snapshot have identical hashes for **238 messages, 7,151 journal events, 364 SDK runs/commands, 688 model-usage rows, 244 requests, five scheduled-task configurations**, and every original field of **191 Activity projections**. Only the new presentation flag/index changed in Activity. There are **105 visible / 86 hidden** projections overall; AAA has **34 visible / 32 hidden**. An additional normal system review occurred while waiting before release, explaining the earlier 65-card browser baseline versus the fresh 66-card baseline. No new request, model call or chat message occurred during release/acceptance. Final active-work counts were zero.

Chrome blocked direct navigation to the JSON endpoint; that evidence was retained without bypass. Separately authorized normal API login created one temporary DEV-AAA session, performed the HTTP checks, then logged out; session count/hash returned exactly to baseline and subsequent authentication returned 401. The three pre-existing Memory failure notices were not changed or represented as repaired.

Full sanitized evidence and screenshots remain outside the repository in `/tmp/duet-activity-20261028/`: `rollout.json`, `browser-acceptance.json`, `http-acceptance.json`, `after-visible-metadata.json`, and `final-preservation.json`. Test ledgers are `/tmp/duet-activity-visibility-validation-final.json` and `/tmp/duet-background-capacity-validation-final.json`; final product hashes are `/tmp/duet-background-capacity-frozen-hashes.json`. No private message/prompt bodies, credentials or screenshots are included in this document.
