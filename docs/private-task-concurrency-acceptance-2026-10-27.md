# Private task concurrency acceptance — 2026-10-27

## Scope and candidate

Independent owner tasks in the same private chat use separate durable SDK sessions. Continuations remain attached to their originating task and reuse its session. Background admission is classified by persisted `background` metadata; `quiet_background` separately controls low-noise presentation.

The frontend candidate is `/tmp/private-tasks-dist`, with hashes in `/tmp/private-tasks-frontend-manifest.json`. It has not been deployed to production. Private input status lists every visible queued/running request with its own source label and request-bound Stop/Cancel action. Quiet schedules/reviews stay in existing Scheduled tasks and Activity. Visible Memory Dream retains its clock, Memory review label and individual cancellation control. Generic background continuations never borrow a human message excerpt. Activity can show concurrent runs and labels explicit background source kinds.

Node 22 typecheck, isolated build, and component regression passed. The regression verifies two simultaneous Running rows, a distinct queued row, per-message labels, inherited background classification, quiet-work separation, visible Dream controls, and precise cancellation after row reorder. A held cancel response disables only its own Stop action. Existing Duet separation/approval/navigation/retry/close regressions also passed. The build emits its existing large-chunk advisory.

## Provisional browser evidence

The initial real CUA/API/worker run used disposable PostgreSQL schema, Redis namespace and workspaces with outbound provider HTTP disabled. Chrome sent both owner messages through the actual composer. An explicit provider release barrier held `CONCURRENCY HOLD first`; canonical metadata confirmed it was Running with an active SDK run. `CONCURRENCY COMPLETE fast` then started and completed in the same private chat, in a different durable session, and published exactly one result while the first task remained Running and unpublished. The reusable `concurrent` assertion passed.

This initial campaign is **provisional** because `backend/app/runtime_bridge.py` changed after its processes started. Evidence is retained at `/tmp/private-task-concurrency-offline/`, including `concurrent.json`, `first-running.json`, `final-provisional.json`, and `campaign-boundary.json`. After releasing the first task, the stack was stopped; its owned schema, five Redis keys and temporary workspaces were removed. It made zero external provider HTTP attempts.

![Independent task finishes while older task remains running](/tmp/private-task-concurrency-offline/fast-finishes-while-first-running.jpg)

## Final frozen-source acceptance

The final fresh campaign, `/tmp/private-task-concurrency-final-b/`, passed all nine reusable stages on the frozen product sources. Recorded source hashes were unchanged at completion. It used the actual frontend, HTTP/WebSocket API, worker, PostgreSQL, Redis, durable SDK sessions, tool execution and owner actions through CUA. Model decisions were scripted and outbound provider HTTP was disabled. These results validate execution and presentation boundaries, not natural-language model quality.

| Journey | Verified result |
| --- | --- |
| Same-chat independent tasks | The older root reached its explicit provider barrier and remained Running. A new owner message started and completed in a different durable session, publishing exactly once before the first root was released. |
| Two Running statuses and Stop | Both private rows showed Running with their own source excerpts. CUA stopped only the selected request. The sibling remained Running and cancellable. Releasing both provider results left the stopped request cancelled with no output; the sibling published exactly once. |
| Owner answer waits only for its task | A real `ask_question` tool published the card while its origin stayed Running at a barrier. CUA answered Blue. Its `question_result` stayed queued, with no provider-start marker. An unrelated new private task still completed. |
| Continuation resumes in correct session | Releasing only the question origin let its answer continuation complete once in that origin's durable session, while another unrelated held task remained Running in a different session. No duplicate question was created in the final campaign. |
| Background system review plus human input | The real schedule enqueue path accepted a system-review root. While it stayed Running, CUA sent a new private message that completed in a distinct session. The quiet review had no composer status or fake human input; Activity labeled it Background review. |
| Background/private results arrive together | A system review and private task were both held, then both releases were written in one fixture control call. Both published exactly one correctly attributed result with distinct canonical sequence numbers. |
| Request-specific Retry | One controlled provider failure remained durably failed under its own human source message. CUA clicked that request's existing Retry. Its continuation reused the failed task's session and published one recovered result; an unrelated held root stayed Running and untouched. |
| Saved scheduled task plus human input | A saved scheduled task stayed Running while another CUA owner message completed. The schedule retained its quiet classification and distinct session, then published once after release. |

The final snapshot has **16 requests: 14 completed, 1 intentionally failed and retained, and 1 cancelled**. No requests remain queued or running. There were **16 completed scripted provider decisions and zero external provider HTTP attempts**. The single intentional failure tests Retry; it was not erased by recovery. The Memory Dream status/Stop policy is component-tested; Dream/background admission is covered by `test_background_running_does_not_block_same_owner_human[memory_dream]`. A real Dream consolidation browser journey is not claimed here.

![Two foreground private tasks really run together](/tmp/private-task-concurrency-final-b/two-running.jpg)

![An owner answer waits for its own origin while unrelated work completes](/tmp/private-task-concurrency-final-b/answer-waits-only-for-own-origin.jpg)

![Background and private results both publish with their own identity](/tmp/private-task-concurrency-final-b/background-and-private-results.jpg)

## Retained fixture and timing boundaries

The earlier frozen attempt at `/tmp/private-task-concurrency-final/` passed overlap/cancellation but found an acceptance-provider bug: the script interpreted a question continuation using its original source body and emitted `ask_question` again. This was scripted model behavior, not a product-created duplicate. Its canonical snapshot and screenshots were retained, its disposable stack was cleaned, and the script was corrected to prioritize explicit `question_result` metadata. Final-B started from a fresh schema/namespace after that correction and repeated the complete set.

Two CUA default short visibility waits in Final-B expired before asynchronous Retry/scheduled results reached the page. Subsequent canonical inspection and the next browser state showed the expected completed output. No extra Retry or repeated owner send was performed in response to those visibility deadlines.

## Test ledger and local running version

The final runtime focused suite recorded **116 passed, 0 skipped** in 92.07 seconds (`/tmp/duet-task-sessions-runtime-final.log` and `.xml`). Core's final focused concurrency set recorded 13 passing tests. Its direct SDK regression `test_answer_waits_only_for_its_origin_while_unrelated_private_task_finishes` independently covers a still-streaming question origin, a queued owner answer with no accepted SDK command, unrelated completion and exact-origin session resumption.

The initial complete backend run is retained: **506 passed, 36 failed**, 461.50 seconds, exit 1, in `/tmp/duet-task-concurrency-full.log` and `.xml`. It is not an all-green full-suite result. Those failures were legacy session-ID/FIFO fixture expectations. After their migration, the complete affected sets passed: **83/83 core plus 52/52 runtime, 135/135 total**. JUnit ID comparison confirmed all 36 original failures now pass, with no changed frozen product sources. The summary is `/tmp/duet-task-concurrency-verification-summary.json`; affected logs/XML are `/tmp/duet-task-concurrency-core-affected.*` and `/tmp/duet-task-concurrency-owned-fixture.*`. A subsequent all-green full 542-test rerun is not claimed. Frontend typecheck, isolated build/component regression, acceptance-harness ruff and `git diff --check` passed.

The isolated candidate was available for root/user review at `http://127.0.0.1:59510/` after acceptance. After root accepted the evidence, its verified launcher (31873) received graceful SIGTERM. The launcher and both owned child processes exited; port 59510 closed; the owned PostgreSQL schema, five Redis keys and temporary workspace root were removed. `/tmp/private-task-concurrency-final-b/cleanup.json` records those checks. The superseded stacks were also cleaned. Screenshots, canonical snapshots, ledgers, source hashes and `/tmp/private-tasks-dist` remain preserved.

## Production rollout and read-only smoke

Root authorized the runtime agent's separate deployment, completed at **18:21:53 Asia/Shanghai on 2026-10-27**. Evidence is `/tmp/duet-task-concurrency-rollout.json`, with served-asset checks in `/tmp/duet-task-concurrency-served-assets.json`. Only the additive request-concurrency schema upgrade ran; no init, seed, reset or SDK migration ran. Managed services stopped with graceful SIGTERM only. Verified hashed assets were published before the atomic index replacement, and prior static assets were retained.

Both local APIs (8000/8001) and the formal public HTTPS endpoint returned health 200. All nine index/JavaScript/CSS checks matched the candidate SHA-256 values; formal HTTPS checks used normal certificate validation. The deployment preserved all **217 message rows, including 94 human messages**, by complete-row hash, every chat sequence, environment files, SDK Git state and FRPC. Postflight remained idle, with zero new product requests, SDK commands, production test messages or rollout-invoked provider calls.

The independent read-only CUA smoke then opened `http://localhost:8000/` and `https://154.217.234.218/`. Both rendered the Duet login page and exposed exactly `/assets/index-BhuwDfMT.js` plus `/assets/index-o3WCLiE0.css` in their DOM. Neither had an existing signed-in session, so no sign-in, account switch, message or provider action was performed. Authenticated task-state behavior is established by the isolated CUA journeys above, not by this production smoke. Browser screenshots and the observation record are in `/tmp/private-task-concurrency-final-b/production-*`.

![Formal public entrance serves the deployed private-task build](/tmp/private-task-concurrency-final-b/production-public-login.jpg)

The isolated acceptance and browser smoke performed no production writes. Deployment was separately authorized and executed by root/runtime. No real model request, commit or push was performed in this optimization/acceptance workflow.
