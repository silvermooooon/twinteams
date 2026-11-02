# Real-model story workflow acceptance — 2026-11-02

This was one isolated browser workflow using the configured `gpt-6-luna` provider, real tools and four fictional owners. It completed across the original browser run and two same-fixture continuations. The original browser runner did **not** pass: its direct-message causal check rejected a valid private subagent-result report. The first continuation later exceeded the inspector's default process-output buffer. Neither interruption changed product records or restarted the provider budget; the remaining stages were resumed using the same conversations after independent review. This is one observed run, not a reliability guarantee.

The fixture allowed at most 320 provider attempts and 3,600 seconds from its original start. It used 256 HTTP attempts, all returning 200. The fixture recorded usage for 255 model calls: 1,565,464 input, 110,491 output and 4,033,290 cache-read tokens. No price estimate is available from this evidence. At final quiescence all 57 product requests completed, all five Center deliveries were processed with results sent and owner notifications, and no owner questions, approvals or held sends remained. One child run was explicitly stopped by its parent with `stop_agent`; its child result and parent completed, and the required report was present. This is distinct from a failed request.

The BA created three distinct story files, then revised the same refund and report files to add 24-hour key retention and UTC date filtering. The original ongoing BA goal was reused, and the unrelated HTTP 409 question created no goal. The BA's owner approved the conversation and specific files through the existing sharing flow. Three separate Center messages each carried one approved snapshot to Maya. The SM read all three full snapshots with matching hashes and synthesized their contents. All three story deliveries were processed. The report story treated multiple selected statuses as OR without an explicit owner confirmation; it remains an assumption to clarify during discovery.

Maya consulted both developers in an approved group Duet. Jordan and Riley's owners answered real capacity questions, and the SM reported both answers privately. For the technical round the peers first gave recommendations. The SM owner then made an additional browser turn asking the peers to confirm those recommendations with their owners. Jordan confirmed that reuse with a different amount must be rejected without issuing a refund. Riley specified a generic safe unknown-failure message, an incident reference for support and no internal error exposure. The SM privately reported both actual confirmations before the owner's decision; the reviewer checked the underlying messages and approved the decision checkpoint. Riley did not explicitly confirm the previously suggested customer-facing sentences.

The SM owner accepted duplicate-refund prevention for Jordan and customer-safe failure reasons for Riley, deferred custom reports and set November 20, 2026 as the document deadline. Separate processed Center assignments reached only the intended developers and requested documents, not code. The refund assignment included permissive same-payload replay language next to a confirmed recommendation; Jordan explicitly confirmed the changed-amount conflict rule, not a separate replay policy. Each developer owner accepted implementation responsibility and discussed the plan with their Duet. Both parent Duets reviewed and presented real workspace documents; no implementation code was created. Jordan's parent rejected a secondary stale draft and presented the canonical plan below. Riley's parent also presented a fuller review document in addition to the concise plan.

The SM's two completed tracking topics originated in the initial synthesis and the subsequent explicit owner-confirmation request, respectively; their outcome summaries overlap. Both describe planning and assignment as complete while leaving implementation open. Both developers have active implementation-related tracking. The BA topic remains active because its last recorded status check still saw the three receipts as pending; later delivery and SM reads succeeded without a BA wake-up. The test does not claim that this asynchronous status lag reconciled itself.

## Preserved evidence

The following are byte-for-byte copies of the fictional workspace files. SHA-256 was checked before the disposable workspace was removed. Browser preview/download checks verified the two canonical plans and the shared story. The fuller Riley review was copied and hash-verified but was not separately previewed in the browser.

| Artifact | SHA-256 |
| --- | --- |
| [Duplicate-refund story](evidence/story-workflow-live-2026-11-02/refund-duplicate-prevention.md) | `f373f102e1a9a6d7776f67ffa2b45550b1f6b17fd8dcd2b20e1b6f8b9fa8b94d` |
| [Failure-reasons story](evidence/story-workflow-live-2026-11-02/customer-refund-failure-reasons.md) | `4fd2b2d1e36cb745924fe41ef93e13ee3e84509dc65933058b8d6dc473b66f52` |
| [Custom-reports story](evidence/story-workflow-live-2026-11-02/custom-reports-date-status.md) | `758f30eb505839f389e833dcbf68d542409ee061a6850aae9ddb3984a0be3912` |
| [Jordan's canonical plan](evidence/story-workflow-live-2026-11-02/duplicate-refund-prevention-development-plan.md) | `0dc9a15f274c1f4bfdd645a02b182ea094f7e69a0a49a22ae2c2f35049768ed3` |
| [Riley's concise plan](evidence/story-workflow-live-2026-11-02/refund-failure-reasons-development-plan.md) | `45f6230144f6061a2633ecbf5bffbc3d47bd853ce8c6f9585c68f39d9005069f` |
| [Riley's fuller review](evidence/story-workflow-live-2026-11-02/refund-failure-reasons-development-plan-review.md) | `a7f86a28d858e9454d4cd0c90b33bb662591e87c70dc64b6d7e888f802d26423` |

Screenshots: [BA story](evidence/story-workflow-live-2026-11-02/ba-file-preview.png), [SM shared-file preview](evidence/story-workflow-live-2026-11-02/sm-shared-file-preview.png), [Jordan's plan](evidence/story-workflow-live-2026-11-02/dev1-file-preview.png), [Riley's plan](evidence/story-workflow-live-2026-11-02/dev2-file-preview.png).

Standard fixture cleanup removed the isolated PostgreSQL schema and temporary workspace, left zero fixture Redis keys and stopped all fixture processes without SIGKILL. The manually interrupted `--serve` process printed a `CancelledError` traceback after its successful cleanup record; that interruption was after the workflow had settled.

## Reproduction

From the repository root, start the guarded isolated stack with the existing real-provider configuration and installed Chrome:

```sh
TWINTEAMS_BROWSER_CHANNEL=chrome backend/.venv/bin/python backend/checks/browser_e2e.py \
  --live-model --scenario story-workflow-live --live-max-attempts 320 --serve \
  --artifact-dir /tmp/twinteams-story-rerun
```

After the stack writes `instance.json`, start the existing browser runner in another terminal:

```sh
node -e 'Object.assign(process.env, JSON.parse(require("fs").readFileSync("/tmp/twinteams-story-rerun/instance.json", "utf8"))); process.env.TWINTEAMS_BROWSER_CHANNEL="chrome"; require("./frontend/e2e/run.cjs")'
```

The runner writes `capacity-review-request.json` before the technical round and `decision-review-request.json` before the SM decision, waiting up to five minutes at each. An independent reviewer must compare both owners' actual answers with the subsequent SM-private reports, including what was and was not confirmed. Only when the relevant facts are present should the reviewer write the matching `capacity-review-approved.json` or `decision-review-approved.json` in the artifact directory with `{"token":"<token copied from that request>","approved":true}`. A stale or missing report is not approval. If an overstrict test predicate fails despite independently verified evidence, retain the `--serve` fixture and use a reviewed stage continuation; never seed messages or alter business records to advance it. Once review is complete, interrupt the serve process and verify its `cleanup.json`.
