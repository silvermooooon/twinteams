# Chat loading measurements (2026-11-05)

The latest-page SQL was already fast. The remaining delay came from recursively
checking the audit ancestry of published messages, then repeating that work for
previews, unread counts, Tracking and read receipts. Reducing SQL round trips
alone still left work proportional to the whole history. Three concurrent history
reads took 3.24–4.10 seconds on the 5,000-message fixture before this change.

## Current read contract

A published message is a snapshot in its own conversation. Reading it checks the
current conversation membership, history floor and publication state; it does not
recompute its audit ancestors. Source IDs and runtime journals remain audit data.
A quoted or referenced original still requires current access: an unavailable
original becomes a placeholder without removing the already-published message.
Pending actions and explicit source access retain their authorization checks.

Bootstrap loads the owner's conversations and their participants, rather than the
whole people directory. Previews, unread counts, approvals, attachments and shared
files use bounded or batched projections. Tracking is no longer included in chat
detail: both private and group sidebar tabs fetch its existing endpoint on demand.
Tracking itself also avoids recursive audit-ancestry traversal; moving it out of
chat detail is not the only optimization.

The frontend retains its five recent conversation windows, with a maximum of 130
messages each. A valid cached window displays immediately and revalidates in the
background. A newer revision discards its cached content but preserves the bounded
window position; revisiting reauthorizes that window before showing it. This
prevents a read-receipt update during a switch from losing 130-message history and
scroll position. Ordinary 30-message windows and windows exceeding the cap reopen
at the latest 30. Account changes, reset, access loss, disconnection and existing
cross-source invalidation still clear incompatible cache state. No transcript
cache was added to Redis or persistent browser storage.

Sidebar reads follow the server revision. Receiving the same revision after a
cached revisit does not fetch Activity or workspace again. Explicit workspace
refresh and real changes continue to work. Presence view-interval audit writes
remain separate from chat-content revisions.

## Real API load

The isolated PostgreSQL/Redis fixture contains 5,000 historical messages, 100 human
chats, 100 pending questions, 100 approvals, 100 shared files, 100 Activity runs,
100 Git memory entries, and 10 Tracking topics with three updates each. The
acceptance runner checks successful response content and a strict 3,000 ms budget;
fast errors or accidentally empty results do not count as a pass. No model calls
are made.

| API | Elapsed | SQL statements |
| --- | ---: | ---: |
| Bootstrap | 51 ms | 8 |
| Latest 30 private messages | 60 ms | 23 |
| Chat summary | 25 ms | 7 |
| Activity | 13 ms | 4 |
| Private Tracking union | 21 ms | 15 |
| Tracking detail | 16 ms | 10 |
| 100 shared files | 23 ms | 6 |
| 100 Git memory entries | 107 ms | 6 |
| Memory search | 111 ms | 6 |

Ten concurrent history requests completed in 142–190 ms each; ten concurrent
mixed page requests completed in 46–184 ms each. All responses were successful and
below three seconds. These are local acceptance samples, not a production capacity
claim or a guarantee under arbitrary load or network conditions.

Git memory previously started two Git processes per entry. SDK commit `1fe5394`
reads one revision with `ls-tree -r -z` and `cat-file --batch`, decoding only the
requested list page. The same standalone 101-entry repository measured list-100
at 3,399 → 78 ms and search at 3,326 → 89 ms; both now use four Git processes,
including clone and revision lookup. Version, provenance, publication and conflict
semantics remain unchanged.

## Real Chrome navigation

The browser fixture uses 5,000 uncompressed dependent messages with real HTTP,
PostgreSQL, Redis, WebSocket events and rendering. It does not replace business
responses. Measurements end when the expected transcript is visible; background
synchronization can continue. Each table value is one sample, not a median.

| Scenario | Before this change | After |
| --- | ---: | ---: |
| Sign in and open private chat | 3,380 ms | 287 ms |
| Refresh | 3,384 ms | 174 ms |
| Return in the same login | 81 ms | 83 ms |
| Return to expanded 130-message window | 142 ms | 145 ms |
| Refresh with 700 ms added network latency | 5,292 ms | 3,816 ms |
| Return with 700 ms added latency | 52 ms | 58 ms |
| Rapid A→B→A with 700 ms added latency | 183 ms | 180 ms |

Normal-network page API calls in the final browser case took at most 56 ms. With
700 ms added network latency, they remained below three seconds; a full cold
refresh still requires several sequential network exchanges and therefore takes
longer than three seconds end to end. Cached switching stays immediate.

A deterministic read-receipt race switches away after a real revision notification
while 700 ms latency delays HTTP synchronization. Returning takes 1,892 ms to
authorize both history pages, retains all 130 messages, and restores the same
message at the same 98 px offset. The cache-boundary case additionally verifies
five-entry eviction, actual WebSocket reconnect, source withdrawal placeholders,
preservation of published replies, no stale quoted content flashing, and account
isolation. All isolated fixtures confirmed database, Redis and process cleanup.

Nine focused real-browser regressions also passed: direct/group reference and
confirmed reply, local echo, generated workspace files, no-prior-history grants,
private/group Tracking scopes, Git Memory CRUD/conflicts, Tracking CRUD and reset.
Three further real-browser question cases passed: withdrawing the original hides
its pending actions while preserving the published question, ordinary choices
resume exactly once, and older-page questions retain their draft and focus during
reauthorization. A component-only browser test separately verifies that unavailable-origin question
cards retain their published text but expose no answer, confirmation, dismissal or
navigation actions. That component test uses intercepted API fixtures and is not
counted as real-server E2E.

A separate startup profile follows actual HTTP → Redis → worker → SDK execution
with a synthetic provider and no external model calls. All seven requests
completed: message submission took 38–70 ms; reaching the provider took 593 ms for
one owner, 738–808 ms for three concurrent owners, and 553–565 ms when warm. No
SQL ran on the worker event loop; its maximum measured lag was 7.69 ms. This
measures platform preparation, not model response latency.

## Reproduction and evidence

From `backend`, with the configured local PostgreSQL and Redis services:

```sh
python -m checks.api_performance --report /tmp/duet-api-load.json
python -m pytest tests -p checks.api_budget --api-budget-report=/tmp/duet-api-budget.json
```

The API budget plugin records coverage against registered product HTTP routes and
fails successful requests above the configured budget. Browser acceptance uses the
existing runner:

```sh
TWINTEAMS_BROWSER_CHANNEL=chrome backend/.venv/bin/python backend/checks/browser_e2e.py \
  --scenario chat-loading --node /path/to/node22 \
  --artifact-dir /tmp/duet-chat-loading
```

Evidence for this run: `/tmp/duet-api-load-final-20261105.json`,
`/tmp/duet-query-simplification-browser-before-20261105`,
`/tmp/duet-query-simplification-browser-final-20261105`, and
`/tmp/duet-query-simplification-functional-20261105`,
`/tmp/duet-query-simplification-questions-20261105`, and
`/tmp/duet-startup-snapshot-final.json`. The failed intermediate browser
run is retained in `/tmp/duet-query-simplification-browser-after-20261105`; it exposed
the read-receipt/cache-window race fixed and retested above. Temporary paths are
local diagnostic artifacts, not durable repository fixtures.
