# Implementation and verification status — 2026-10-18

> Historical verification record. This describes the tested revision on its stated date, not the current peer-message contract. Current guidance: [interaction rules](interaction-rules.md) and [architecture](architecture.md).

The current process/transport design is [architecture.md](architecture.md). API replicas and the single-active execution service are separate. Redis is disposable notification/presence transport; PostgreSQL remains authoritative.

The application uses PostgreSQL for product and SDK state in every environment.
Runtime journal ownership stays in Agent Runtime; Twinteams owns identity, source
permissions, business scheduling and product views. Python 3.13 is used locally.

Implemented paths include separate human/Twin identities, private/group chat,
paired membership and history grants, durable per-Twin FIFO, Stop and explicit
Retry, shared automatic-reply budgets, proactive sourced messages, Reply for me,
Tracking tombstones, source-checked summaries, exact-action approval continuations,
local reminders, workspace files, private memory, durable children, and Activity
from write-time run projections and paginated canonical SDK record details. Complete outputs survive browser reconnect and process
restart. Sensitive content is read through authorized HTTP; sockets broadcast only changed scope revisions.

`tests/test_recovery.py` verifies ownership loss, output projection crash gaps,
busy-router retries, independent dispatch, Tracking deletion and approval replay.
The scripts under `checks/` exercise the broader business rules with real
PostgreSQL and explicit offline model adapters. Their temporary schemas are
separate from the normal application's tables. Provider configuration tests use
fake keys and controlled transports only.

The ordinary local app uses real OpenAI Responses GPT-6 Luna. The optional browser
fixture is a separately launched, permanently labelled test instance. Its simulated
text is never evidence of model quality and never substitutes for normal generation.

Production still needs verified identity/OIDC integration, deployment failover,
backup/PITR drills and any intended external-effect adapters. Local database and
model verification is recorded in [postgresql.md](postgresql.md). The schemas and
SDK protocol do not promise exactly-once effects from third-party services.

The distributed acceptance suite starts two real API processes and an independent
worker against isolated PG/Redis namespaces. It checks durable pre-worker
acceptance, command replay, cross-API notifications, idle pong/presence SQL counts,
compensation and transport limits. See the current acceptance report for actual
runs; a source-level feature list is not a test result.

## Refactor regression results — 2026-10-18

Verified against isolated PostgreSQL schemas after the process/transport changes:

- 11 backend tests passed, including queued-input preservation after lease loss,
  105 old ready heads from one owner followed by another owner's work, exact 130
  unread messages with 100-message keyset windows, and 105 acknowledged historical
  stops followed by one new active stop.
- All five existing business acceptance scripts passed: product, Runtime,
  provider configuration, Twin message bridge and Twin conversation.
- Frontend TypeScript check and production Vite build passed.
- `git diff --check` passed. The earlier PG-only uncommitted work was preserved.

The pure business conversation fixture explicitly supplies TTL observations;
real Redis/presence failure and cross-process WebSocket behavior belong to the
independent distributed acceptance, not to the offline business model adapter.

## Performance checks — 2026-10-21

The repeatable checks use synthetic records and intercepted browser APIs. They do
not send product messages or call a model. Provide a local PostgreSQL test DSN;
the backend fixtures create and remove separate random schemas:

```sh
cd backend
TWINTEAMS_TEST_DB_URL="$LOCAL_TEST_DATABASE_URL" .venv/bin/python -m pytest \
  tests/test_performance_acceptance.py tests/test_performance_static_acceptance.py -q -s
```

The query fixture contains 100 messages, including 50 Twin outputs with up to ten
earlier message references, plus twelve additional rooms. It checks projections,
quotes, private attachment visibility, source withdrawal and history grants.
For this fixture, snapshot queries fell from 2,059 to 23 at limit 100; bootstrap
fell from 1,012 to 82. Runtime context fell from 1,084 to 126 queries with identical
message projections. These are fixture results: dependent unread checks and full
runtime history still grow with the history being authorized.

Run the browser check against a compiled preview or the deployed static assets,
using Node with Playwright and Chrome available. Every API and WebSocket in this
check is mocked, even when the assets come from the deployed server:

```sh
TWINTEAMS_UI_URL=http://127.0.0.1:8000 node frontend/checks/performance-acceptance.cjs
```

It measures initialization, chat switching, typing, running-state visibility,
hint coalescing and optimistic-send/ACK feedback under 4x CPU throttling and fixed
synthetic API delays. It also checks browser disconnect/reconnect handling, delayed historical
reauthorization, authorization failures, reset and owner switching. The default
JSON report is `/tmp/twinteams-performance-ui-report.json`; override it with
`TWINTEAMS_PERFORMANCE_UI_REPORT`. Use `TWINTEAMS_PERFORMANCE_BASELINE=1` only to
collect an older build without enforcing the new performance expectations.

In the before/after fixture, switching changed from two detail requests plus a new
socket and bootstrap/personal reloads to one detail request using the existing
socket. Running-state visibility with a 500 ms older-page delay changed from
871 ms to 317 ms; 34 typed characters changed from 2,433 ms to 1,325 ms. These
synthetic timings are machine-dependent and do not establish real model latency.

Static checks verify gzip decoding preserves bytes, identity encoding, conditional
ETag responses, immutable caching for the Vite entry bundles, index revalidation,
and unchanged API stream/WebSocket behavior. Actual network timing is a separate
read-only measurement: compare fresh connections with repeated transfers in one
curl invocation, recording `time_connect`, `time_appconnect`,
`time_starttransfer`, `time_total`, `num_connects` and `size_download`. The local
health baseline was about 1 ms, while this machine's public connection had about
370 ms first-byte latency when reused and roughly 1.5 s with a fresh TLS
connection. Health and asset timing cannot measure message authorization, model
thinking or end-to-end answer delivery. Temporary reports and production metadata
are not repository artifacts.
