# Implementation and verification status — 2026-10-18

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
