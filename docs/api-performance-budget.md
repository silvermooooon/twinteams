# API performance acceptance

The non-AI HTTP response budget is **3,000 ms for a successful response**. A
timeout, cancellation, validation error, or empty substitute does not satisfy
the budget. The acceptance checks use disposable PostgreSQL schemas, isolated
Redis keys, synthetic conversations and local files. They do not invoke a live
model or modify the running application's records.

## Coverage

`backend/checks/api_budget.py` discovers all 98 HTTP route declarations from the
application source, including router prefixes. The optional pytest plugin times
requests through the product's actual FastAPI routes and records their test
case, successful status, duration and observed SQLAlchemy PostgreSQL statements. Provider
`MockTransport` calls and synthetic test-only HTTP routes are excluded.

The inventory contains 96 non-AI routes and two explicit AI exceptions:

- `POST /api/local-provider-config/verify`
- `POST /api/local-provider-config/verify-saved`

The report lists every route and every missing successful path; a fast `404` or
`422` cannot count as coverage. Domain fixtures use real PostgreSQL, while model
execution is offline. Some activity tests supply a deterministic journal reader.
A separate successful HTTP test executes the real SDK with an offline model
adapter, commits its events to PostgreSQL, then reads the completed output and
paginates the actual journal. Remote S3 download latency is outside this local
acceptance run.

The optional frontend fallback is tested when its production bundle is built.
Uploads, downloads and previews remain in the measured inventory, but their
timings describe the local fixture sizes and local transport. They are not a
promise about arbitrary file sizes or an external network. The long-lived
`/api/ws` connection is outside the 98 HTTP declarations; its commands and ACKs
are covered by the existing WebSocket/browser tests, not by treating the entire
connection lifetime as a response deadline.

## Reproduction

Use the existing backend virtual environment and configure `TWINTEAMS_TEST_DB_URL`
for the local PostgreSQL instance, as for the normal test suite. Build the
frontend first when testing the optional production HTML route.

```sh
cd backend
.venv/bin/python -m pytest -q -p checks.api_budget \
  --api-budget-report /tmp/duet-api-budget.json
.venv/bin/python -m checks.api_performance \
  --report /tmp/duet-api-load.json
```

The load runner reads only the existing local launcher's PostgreSQL/Redis
configuration. It creates and removes its own schema, Redis namespace and
temporary workspace. The successful-response budget is enforced as a test
failure; no deadline middleware replaces slow responses with an error.

The load fixture contains:

- 5,000 transitive-history messages, with their completed request records and read receipts;
- 100 additional group chats and their messages;
- 100 pending questions and 100 approval records;
- 100 shared-file records and 100 Git memory entries;
- 100 completed activity projections and an actual generated workspace file;
- 10 Tracking topics, each with three updates referencing conversation history.

It verifies the latest message page contains the expected real records, then
measures individual pages, ten simultaneous history reads, and ten simultaneous
mixed homepage/history/Tracking/shared-file/memory reads. Run this check without
an unrelated benchmark competing for the same local PostgreSQL and CPU.

## Local load result, 2026-11-05

All response-content assertions passed at the volume above. Every measured
request returned `200`; none exceeded the budget.

| Request | Duration | Observed SQLAlchemy statements |
| --- | ---: | ---: |
| Bootstrap, including 100 additional rooms | 51.340 ms | 8 |
| Latest 30 messages, 30 question cards, 100 approvals | 60.463 ms | 23 |
| Conversation summary | 25.241 ms | 7 |
| Activity, 50 of 100 runs | 12.524 ms | 4 |
| Tracking, 10 topics | 20.743 ms | 15 |
| Tracking detail, 3 updates | 15.916 ms | 10 |
| Shared files, 100 entries | 22.732 ms | 6 |
| Workspace, including a generated file | 8.617 ms | 6 |
| Memory, 100 entries | 107.405 ms | 6 |
| Memory search, 20 matching entries | 111.076 ms | 6 |
| Contacts, including an existing peer | 11.576 ms | 2 |
| Scheduled tasks, including system review | 11.937 ms | 4 |
| Owner settings | 4.503 ms | 2 |

Ten simultaneous history reads completed in **190.204 ms maximum**; ten mixed
homepage/history/Tracking/shared-file/memory requests completed in **184.318 ms
maximum**. The fixture schema, temporary files and Redis namespace were removed
successfully. These are local measurements, not response-time claims for other
hardware or network conditions.

The full HTTP inventory run covered **all 96 non-AI routes**, with **476 successful
responses**, a **416.825 ms maximum** and no missing successful route. The separate
real PostgreSQL SDK-journal test returned `200` for its full page, first page and
continuation in **11.297 / 8.259 / 7.541 ms**. Its native SDK psycopg queries are
not included in the SQLAlchemy statement counter. No paid model call was made.

The full functional suite initially reported **636 passed and 8 failed**: eight
tests still asserted the retired recursive-ancestor behavior. Those expectations
were corrected to the approved published-snapshot contract, with direct source,
withdrawal, reset and grant checks retained. The affected suites were rerun:
**18 context tests passed**, then **50 question/Dream/performance tests passed**.
The real journal HTTP supplement passed separately. This is the full run plus
its targeted verification, not a claim that the original run was entirely green.

## Query design

Published messages are durable conversation snapshots. Reading one applies its
own state, conversation membership and history boundary. Its provenance remains
an audit record; reading a page does not recursively reopen every ancestor.
Current runtime/action inputs still validate their directly referenced records,
content versions and grants. Reset, withdrawal, file access and membership
changes retain their own checks.

Bootstrap batches related members and profiles, retrieves the latest eligible
message using its index and does not acquire an owner lock to provision existing
system schedules. Approval and shared-file lists also load their referenced
records in batches. Tracking is fetched through its own endpoint only when its tab
is needed. Git memory reads use a native Git batch instead of two subprocesses
per entry. These changes reduce the work itself, rather than relying on a warm
cache to conceal an expensive cold query.

The reports describe tested capacity on the local machine. They do not assert
that an unlimited directory, unlimited files, arbitrary concurrency, failing
infrastructure or remote network transfers always finish within three seconds.
