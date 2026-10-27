# Duet / 双重奏

Duet separates human pair/multi-person chats (`kind=group`), owner-to-own-Duet private chats (`kind=private`) and independent owner-approved Duet threads (`kind=duet`). Humans inspect Duet threads read-only in the sidebar. Approve once permits one connection; Always creates directed owner-to-peer grants, and revocation closes dependent threads. Connection consent grants no arbitrary action authority. Automatic work shares six replies across the original task chain; only explicit human Continue extends it. Human chat messages never trigger Duets through presence, unread state or a ten-second timer. The checkout, configuration prefixes and storage identifiers retain `twinteams` for compatibility. See [the visual identity](docs/visual-identity.md).

React/TypeScript + FastAPI application using the original
[Agent Runtime](https://github.com/silvermooooon/agent-runtime) SDK.
Python **3.13.x** and PostgreSQL are required for local development and deployment.
Business tables (`tt_*`) and canonical Runtime tables (`agent_*`) share one database.
Redis is required for notification hints and presence TTLs. API and execution run in separate processes.
The current architecture is documented in [architecture.md](docs/architecture.md).

## Run locally

Keep `twinteams/` and `agent-runtime/` as sibling checkouts. The backend installs
Agent Runtime as an editable dependency from `../../agent-runtime`.

```sh
cd twinteams/backend
uv sync --python 3.13
cp .env.example .env
# Fill DATABASE_URL, AGENT_DB_URL and TWINTEAMS_REDIS_URL in the private .env file.
uv run --env-file .env python -m app.init_db
cd ../frontend
npm ci
npm run typecheck
npm run build
cd ../backend
# Terminal 1: API only; no model credential is required.
uv run --env-file .env python run_local.py --port 8000
# Terminal 2: one active execution service, with explicitly supplied model credentials.
uv run --env-file ../../agent-runtime/.env --env-file .env python -m app.worker
```

`app.init_db` explicitly creates or upgrades the business schema and the SDK's
packaged migrations (001–005). It is safe to repeat; ordinary API and worker
startup only validates schema. To import existing Activity projections once,
run `python -m app.init_db --backfill-activity`. No startup replays session logs.

The configured local instance connects to `127.0.0.1:5432/appdb`. Credentials are
stored only in ignored, private environment files. Both components use PostgreSQL;
there is no embedded database or backend fallback. Existing API credentials may
be loaded explicitly from the sibling Runtime environment file as shown above.
The application never searches for arbitrary environment files.

Open http://127.0.0.1:8000. The server serves the built frontend and `/api` on the
same origin. Development usernames `DEV-AAA`, `DEV-BBB`, `DEV-CCC` open separate
people/Duet identities. This launcher enables local development explicitly, binds
loopback, and requires a local PostgreSQL address. Normal startup defaults to
verified-identity mode. Production does not enable DEV login.

Use `AGENT_PROVIDER=openai`, `AGENT_API=openai-responses`,
`AGENT_MODEL=gpt-6-luna` and server-side `OPENAI_API_KEY` for real generation.
Subagents inherit that model unless explicitly configured. No credential is
committed or returned through the configuration API. Missing model credentials
produce a clear failure; the app never substitutes a test response.

New Duet conversation and subagent runs using `openai-responses` enable OpenAI's
native [`web_search`](https://developers.openai.com/api/docs/guides/tools-web-search)
tool by default, including supported page opening and in-page search. The model
decides when to use it. Other APIs and internal routing/Dream calls keep their
existing tool policy. Interrupted runs retain their exact frozen tool declarations
and options; older runs without search do not gain it during recovery. Citation
sources appear as clickable links in chat while the SDK journal
retains the original provider output. The SDK library itself remains opt-in.

## Storage and recovery

One dedicated database belongs to one `TWINTEAMS_TENANT_ID`. The product checks
that database identity on startup. Product tables own identities, memberships,
chat messages, source grants, task requests, routing, Tracking and reminders.
The SDK owns its journal, runs, usage/audit, checkpoints, commands and leases.
No second Runtime event log is maintained by the product.

Complete model outputs are committed before projection into chat. Reconnection
loads committed outputs. WebSocket broadcasts contain only scope revisions;
content is loaded through authorized HTTP. The former SSE/token transport and
three-second full-chat polling have been removed. Reconciliation deduplicates
by SDK event ID. SDK lease loss keeps the logical request recoverable. Slow model
routing is scheduled separately from ready replies. Failed Duet routing appears on its source message. That message’s owner can explicitly retry; each click creates one fresh durable classifier attempt, retains the original causal-chain allowance and read checks, and never retries itself. Apply [the additive routing retry column](docs/group-routing-retry-schema.sql) before rollout (also applied by `app.init_db`). Approval decisions are durable
SDK facts, then resume their originating task as a continuation. Exact installed platform actions
are frozen and replay-safe; approval does not install extra execution capabilities.
No email/payment adapters are installed.

Private replies, approved Duet replies and Duet routing use the same server-maintained
conversation checkpoint plus a contiguous recent-message tail. Estimated tokens
trigger checkpointing at 24,000 tokens, retaining about 8,000 recent tokens; a
40-message trigger also bounds many tiny messages (20 raw messages retained).
A dedicated SDK model operation writes a checkpoint of at most 2,000 estimated
tokens; ordinary replies have no summary-maintenance tool or instruction. Source
coverage is validated by the server, without a cumulative 500-ID tool limit.
Original chat rows and SDK journals remain intact. Checkpoint failure preserves
the previous valid state and visibly fails the attempt before incomplete history
can reach the reply or routing model. Routing receives only authorized chat
summary/history and participant metadata. Main and child tool runs additionally
use native SDK compaction near the configured model context limit, retaining its
20,000-token recent tool history with a 16,384-token reserve. Interrupted commands
keep their frozen input and tools; legacy frozen summary calls can still settle.

Workspace files and private memory remain separate persistent file components.
Duets can explicitly deliver finished workspace files into chat attachment cards with `present_files`; each delivery captures a snapshot. Markdown, text and PDF support in-app preview. HTML opens only after a user action in a separate sandboxed tab and remains downloadable.
Chat uploads of every type, including images, live once in the chat workspace and reach the Duet as file-path references; installed `read`/`bash` tools inspect them on demand. The existing four-file/20 MiB limits remain. Workspace browsing lazily lists real files, including Agent outputs; private chats show the owner's execution directory and groups show only their shared chat directory. Message bodies have no business character cap; oversized WebSocket sends use HTTP with the same idempotency key. Transport and model context limits still apply. See [the file contract](docs/implementation-contract.md#uploaded-files-and-the-real-workspace), including the additive attachment-path migration required before rollout.
A fresh database starts with local demonstration identities and empty conversation
state. PostgreSQL data survives application restart.

## Verification

Set `TWINTEAMS_TEST_DB_URL` (or `DATABASE_URL`) to an accessible PostgreSQL URL:

```sh
uv run --env-file .env python -m pytest tests -q
uv run --env-file .env python checks/product_acceptance.py
uv run --env-file .env python checks/runtime_acceptance.py
uv run --env-file .env python checks/provider_config_acceptance.py
uv run --env-file .env python checks/twin_message_bridge_acceptance.py
uv run --env-file .env python checks/twin_conversation_acceptance.py
uv run --env-file .env python checks/distributed_acceptance.py
```

Tests create uniquely named temporary schemas, apply canonical SDK DDL, and drop
only those schemas on exit. They do not clear application tables. Model adapters
are explicit test implementations. The separately launched
`checks/acceptance_fixture.py --port 8011` uses the same schema isolation and labels
its simulated responses; it is never an application fallback.

For real browser coverage against disposable PostgreSQL, Redis, two APIs and a
worker, see [browser E2E](docs/browser-e2e.md). Its one-command launcher uses a
deterministic offline provider while exercising real UI actions, tools and durable
state; it never targets the production instance.

See [database operation](docs/postgresql.md) and
[recovery boundaries](docs/recovery-fixes.md).

## Deployment

Set `TWINTEAMS_ENV=production`, `TWINTEAMS_DEVELOPMENT_MODE=false`, database URLs,
tenant identity and server-side model credentials. Configure verified JWT key,
issuer, audience and algorithm; tokens must contain `sub`, `exp`, `iat`, `iss`,
`aud`, `tenant_id`, and the subject must be provisioned:

```sh
uv run --env-file .env python -m app.provision_owner --subject 'verified-subject' --name 'Person Name'
uv run --env-file .env uvicorn app.main:app --host 127.0.0.1 --port 8000 --ws-max-size 65536 --ws-max-queue 64
uv run --env-file .env python -m app.worker
```

One active worker is a deployment convention; only SDK session leases grant execution.
Additional API replicas subscribe to PG-backed state through Redis hints.

A real OIDC browser login, backups/restore drills, shared persistent workspace
storage, deployment ingress and multi-worker failover acceptance remain deployment
responsibilities. PostgreSQL integration tests do not certify those operations.
The local provider configuration/save page remains an explicit local-only feature;
see [its documentation](docs/local-provider-configuration.md).

For the tested local two-API/single-worker stack, use the managed launcher from `backend/`:

```sh
.venv/bin/python checks/local_stack.py migrate
.venv/bin/python checks/local_stack.py start
.venv/bin/python checks/local_stack.py status
.venv/bin/python checks/local_stack.py restart-worker
.venv/bin/python checks/local_stack.py stop
```

It records only its own PIDs/logs and loads the explicitly configured private
credential sources. This local deployment convention is not full high availability.

Chat sends appear immediately as sender-local bubbles. Pending sends are kept only in memory: reloading fetches canonical server state, without a durable offline outbox. A retry keeps the same submission key and payload; “Sent” confirms server persistence, not delivery or reading. Human chat sends remain human-authored; private sends address only the owner's own Duet. Acknowledgments confirm each bubble without awaiting bootstrap; chat refreshes coalesce behind one active read and at most one follow-up, while preserving authorization checks for already loaded history.

Chat message bodies render CommonMark and GitHub-flavored Markdown with `react-markdown` and `remark-gfm`, including lists, tables and fenced code. Raw HTML stays literal text and unsafe URL schemes are removed. Links open separately with opener/referrer protection; Markdown image URLs appear as explicit links rather than loading external images automatically. Uploaded image attachments retain their authorized preview flow. Original-input disclosures and quoted-source previews remain literal text, and rendering does not change stored message content.
