# Duet forum acceptance — 2026-11-17

This campaign verifies the [forum topic contract](duet-topics.md). It replaces
direct delivery to every peer and the six-send gate. Earlier dated acceptance
reports remain historical evidence for their original implementations.

## Behavior verified

- Topic creation preserves owner connection consent. Only the creating Duet can
  invite additional participants. Pending invitees cannot read the topic; approved
  membership includes existing posts, while earlier file snapshots retain their
  original audience until a separately approved share.
- Posts without mentions wake only the administrator. Explicit mentions also wake
  their participating targets. Administrator-authored updates still wake the
  administrator, once per update. Replayed operations and unchanged edits/reactions
  do not create duplicate notifications.
- Notifications carry identifiers and event metadata, without post text, opening,
  summary, files or Tracking. Content requires an explicit authorized tool read.
  Nonempty final assistant output does not publish a topic post. An administrator
  can finish silently.
- Posts, author-only edits, revision history, title/purpose updates and invitations
  use explicit tools. Thirteen posts publish even with a historical exhausted
  six-send counter. The HTTP Continue route and UI allowance controls are removed.
- Consumed sources survive retries and owner/child continuations in the existing
  manifests. Withdrawal blocks further execution and retry, and disables actions
  on the retained question card. Independently published snapshots remain intact.
- The additive forum upgrade is repeatable, preserves existing posts and unsent
  drafts, creates no wake requests, and freezes existing file audiences without
  expanding them on a repeated migration.

## Backend regression

The final complete backend run passed **681 tests in 796.13 seconds**, with the
existing API budget plugin enabled:

```sh
uv run --env-file .env python -m pytest tests -q -p checks.api_budget \
  --api-budget-report /tmp/duet-forum-api-budget-final.json
```

All **98 HTTP routes** had successful-response coverage, comprising **628 calls**.
The slowest measured response was **1,090.941 ms**; none exceeded 3,000 ms, and
there were no uncovered non-AI routes. The new post-history read is covered and
the removed Continue route is absent from the inventory. Full-run output is
`/tmp/duet-forum-tests-final.log`.

Earlier regression runs identified obsolete direct-delivery fixtures and a
metadata-only request authorization gap. The final run includes the corrected
explicit-read/post fixtures and the real source-withdrawal, retry-inheritance and
question-availability regressions; it is a single passing complete run.

## Browser and frontend

The real Chrome `forum-topic` scenario passed in **17.32 seconds**, using real
HTTP, WebSocket, PostgreSQL, worker and SDK paths with deterministic offline model
decisions. Business API/WebSocket traffic was not intercepted. It verified nine
initial posts, edit history revisions 2/1, no Continue control, and an invitation
appearing in an already-open recipient inbox without a reload. Topic access was
403 before consent. All three owners approved through the browser; admission added
exactly one requirements post, bringing the topic to ten posts. Seventeen
metadata-only notification requests completed without automatic posts.

The scenario made 21 offline provider calls, including a real SDK
`invite_duet_participants` invocation, and zero online provider calls. It proves
the execution plumbing and permissions, not autonomous model judgment. Evidence:
`/tmp/duet-forum-browser-acceptance-live-inbox/report.json` and `cleanup.json`.
Cleanup confirmed the test schema and temporary root were removed, owned processes
stopped, and zero namespaced Redis keys remained.

The existing collaboration launcher was also migrated and its offline `--smoke`
check passed: 13 sequential posts, 20 metadata-only notifications, 55 offline
provider decisions and zero online HTTP attempts. It exercised HTTP owner consent
and actual SDK create/read/post tools, including silent administrator self-updates.
Evidence: `/tmp/duet-collaboration-forum-smoke-v2/smoke-report.json` and
`cleanup.json`; owned processes, schema, temporary files and Redis keys were
removed. The earlier smoke attempt failed on stale fixture login names, which
were corrected before this passing run; its fixture was also cleaned up.

`npm run typecheck`, `npm run test:duet-ui` (including chat synchronization) and
the candidate production build passed. The final component regression also
checks distinct, expandable invitation requirements beside the frozen original
topic request. Candidate assets are outside served assets at
`/tmp/duet-forum-candidate-dist`; final JS is `index-CqS1Ucmc.js`.

The live context and human-group creation acceptance scripts were updated to the
new tool names and explicit topic reads and passed syntax checks. Their online
model campaigns were not executed in this change.

## API load and environment

The existing load check passed **33 successful requests**, with a maximum of
**283.401 ms** against the 3,000 ms budget. Its fixture contains 5,000 history
messages; 100 human chats, questions, approvals, shared files, Memory entries and
activity runs; ten Tracking topics with three updates each. Both ten-client
history and mixed-page workloads passed without missing or truncated fixture
data. Evidence: `/tmp/duet-forum-api-load.json`. Cleanup confirmed schema/file
removal and zero namespaced Redis keys.

Validation used Python 3.13, Node 22.14, and the existing editable sibling SDK at
`1fe539490dbc51b39435609d7a8936d260605e41` with its pre-existing working-tree edits.
Those SDK edits and the separate Message Center repository were not modified by
this change. Changed Python files introduced no Ruff diagnostics; diff whitespace
checks passed.

This campaign did not migrate the running database, replace served assets or
restart the live stack. Deployment still requires the documented idle check,
data-preservation snapshot, explicit forum migration and managed-stack rollout.
