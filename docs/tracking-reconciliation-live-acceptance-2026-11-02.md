# Private Tracking reconciliation: real-model acceptance — 2026-11-02

A focused isolated Chrome run exercised a genuine two-owner Duet with the configured `gpt-6-luna` provider. The owner established an unfinished onboarding goal and a separate handoff goal: Maya would review two story summaries, and the handoff would end only after she confirmed both reviews. Implementation was explicitly someone else's later work. The owners approved the Duet using the real interface; no messages, model responses or Tracking records were seeded.

Maya answered the real owner question that she had reviewed the duplicate-refund summary but not the failure-reason summary. Her Duet relayed that answer, and Alex's Duet reported it privately. The silent private `peer_report_reconcile` request used that actual report as its source, recorded the partial progress on the same handoff topic and kept it active. The separate onboarding goal remained active and unchanged. An independent reviewer compared the real owner answer, peer message, private report and Tracking state before the second round.

Maya then said she had also reviewed the failure-reason summary and that implementation had not started. Her Duet relayed this, and Alex's Duet privately reported both reviews. The private reconciliation read and updated the same topic to completed, correctly ending only the review handoff. The unrelated onboarding topic remained active and unchanged. A normal Duet courtesy reply was sent separately; the reconciliation itself sent no owner or peer message and created no duplicate goal. No required owner questions or held sends remained.

This is a **recovered run**, not a clean first-pass result. The original browser runner encountered a test-fixture error because the common settlement helper assumed a Center service was present. That helper was corrected and the remaining rounds continued in the same served fixture without repeating the completed owner turns. In the final reconciliation, `get_tracking` and `update_tracking` succeeded, after which provider attempt 41 failed with `ConnectError`. The completed topic update was retained. The owner-authorized retry used the existing authenticated retry endpoint and the original source report; its new generation retained `peer_report_reconcile`, quiet/background behavior and the restricted execution context. It completed silently using `list_tracking`, with no additional message or topic mutation. The original failed generation remains part of the evidence. The independent final review verified the factual sequence and the recovery.

The fixture was bounded before launch to 60 provider attempts and 900 seconds. It used 43 attempts: 42 returned HTTP 200 and one had the transport error. The fixture recorded usage for 41 model calls: 166,953 input, 8,266 output and 371,202 cache-read tokens; no price estimate is available. At the final snapshot all non-failed requests were complete, the retry had completed, and the single failed original generation remained recorded. Reconciliation requests could use only `list_tracking`, `get_tracking`, `update_tracking` and `add_tracking_update`; their model output did not publish a message or start a child, peer send, web or execution task.

The [partial owner report](evidence/tracking-reconciliation-live-2026-11-02/partial-owner.png) and [final Tracking view](evidence/tracking-reconciliation-live-2026-11-02/final-owner-tracking.png) are sanitized screenshots of the fictional fixture. The final view was captured after the Tracking panel finished loading. Standard cleanup removed the isolated database schema and temporary workspace, left zero fixture Redis keys, and stopped all fixture processes without SIGKILL. Interrupting the served fixture printed a `CancelledError` traceback after the successful cleanup record.

## Reproduction

Start the isolated guarded stack with the existing configured provider and Chrome:

```sh
TWINTEAMS_BROWSER_CHANNEL=chrome backend/.venv/bin/python backend/checks/browser_e2e.py \
  --live-model --scenario tracking-reconciliation-live --live-max-attempts 60 --serve \
  --artifact-dir /tmp/twinteams-tracking-reconciliation-rerun
```

Once `instance.json` is written, start the existing browser runner separately:

```sh
node -e 'Object.assign(process.env, JSON.parse(require("fs").readFileSync("/tmp/twinteams-tracking-reconciliation-rerun/instance.json", "utf8"))); process.env.TWINTEAMS_BROWSER_CHANNEL="chrome"; require("./frontend/e2e/run.cjs")'
```

The runner writes `partial-review-request.json` and `final-review-request.json` at the corresponding checkpoints, waiting up to five minutes at each. An independent reviewer must compare the real owner input, peer reply, private report, reconciliation and both goals. Only if the stage is factually correct should the reviewer write the matching `partial-review-approved.json` or `final-review-approved.json` with `{"token":"<token copied from that request>","approved":true}`. A failed provider request is not a passing request: preserve the failure and any authorized retry as separate generations. Interrupt the served fixture after review and verify `cleanup.json`.
