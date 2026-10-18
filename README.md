# Twinteams

A working React + TypeScript / FastAPI application built around the existing
Agent Runtime SDK. Python **3.13.x** is required. Local development uses SQLite;
PostgreSQL is the deployment backend. The application is not deployed remotely.

## Run locally

Keep the two sibling directories together: `twinteams/` and `agent-runtime/`.

```sh
cd twinteams/backend
uv sync --python 3.13
cd ../frontend
npm ci
npm run build
cd ../backend
uv run --python 3.13 python run_local.py --port 8000
```

Open http://127.0.0.1:8000. FastAPI serves the built frontend and the `/api`
endpoints from the same origin. If that port is occupied, choose another free
loopback port; the frontend requires no URL change. Development frontend work
can instead use Vite, whose `/api` proxy defaults to backend port 8000.

`npm run build` runs Vite only. It does not silently run typecheck or tests.

### Development login

The login usernames `DEV-AAA`, `DEV-BBB`, and `DEV-CCC` open the corresponding
AAA, BBB and CCC accounts. These are explicitly local test accounts, each with
its own human identity, paired Twin, private chat, and shared group memberships.
Login uses an opaque, expiring HttpOnly/SameSite cookie. There is no password and
this development instance must remain on loopback. Owner-ID request headers are
not authentication and are rejected.

The explicit `run_local.py` entrypoint opts into `TWINTEAMS_ENV=local` and
`TWINTEAMS_DEVELOPMENT_MODE=true`, binds loopback only, and refuses non-SQLite
database settings or a non-local environment. Standard `app.main:app` startup
leaves development login disabled by default. DEV login additionally requires
a SQLite database; setting a production environment or PostgreSQL cannot enable it. Existing
business data and SDK journal records survive restart.

### Local key validation and optional saving

For a separate **save-only** page, keep the normal local application running,
sign in as `DEV-AAA` at `http://127.0.0.1:8010`, and launch
`.venv/bin/python run_credentials.py --port 8012` from `backend`. Open
`http://127.0.0.1:8012/api/local-provider-config/save-page`. Personally enter the
key and confirm the displayed storage path and replacement scope. This writes
one `OPENAI_API_KEY` assignment to a private, ignored
`backend/data/provider-secrets/openai.env` file. It makes no API call and does not
alter global shell variables, running processes, model configuration or queues.
Only an explicitly requested later process should load the file. The same page
provides a separate **Verify using saved key** button with an explicit small-charge
warning. Only the user's click makes one fixed GPT-6 Luna request; it sends no
application content, makes no automatic retry, and never enables Runtime generation.

The normal app also has **OpenAI configuration** in its account menu
(`#/provider-config`) for an optional billed, fixed minimal GPT-6 Luna validation.
Validation and saving never enable chat generation or run pending requests.
See [the local credential flow](docs/local-provider-configuration.md).

### Connect a real model

No model credential is included. Without one, chat and the other business
features work, and a requested Twin reply becomes **Failed** with a clear
configuration error. The application never substitutes a fake response.

Set the provider configuration on the server using environment variables or a
secret manager. `backend/.env.example` documents the supported names. Do not put
API keys in a chat message, screenshots, source code or committed files.

For OpenAI Responses, configure `AGENT_PROVIDER=openai`, `AGENT_MODEL` to an
available model ID, and `OPENAI_API_KEY`. `OPENAI_BASE_URL` is optional. For
Anthropic, use `AGENT_PROVIDER=anthropic` and `ANTHROPIC_API_KEY`. Restart the
server after changing configuration. Credentials are re-injected into the SDK;
they are not serialized into journal options or returned by `/api/config`.

If using a private `.env` file, load it in the process environment before launch;
the application does not search for or silently load arbitrary `.env` files.

```sh
set -a
. ./.env
set +a
uv run --python 3.13 python run_local.py --port 8000
```

## Implemented application paths

- Per-owner login, unique human/Twin pair, private chat and paired group members
- People-only search, group creation and member admission with all/fixed-7-day/no-prior history
- Persisted messages, original reply quotes, human-only read receipts and multi-device presence
- Per-chat/per-Twin FIFO, deduplicated manual/automatic triggers, 10-second eligibility,
  owner Stop, role-scoped queued cancellation, and Retry
- Proactive Twin chat tools with source-owner attribution, model-backed
  needs_response routing, durable shared automatic-reply allowances (default six),
  repetition holds and explicitly authorized continuation
- Reply-for-me mode with frozen global/chat prompt, a separate raw input, and a
  Twin-authored lavender/mint hard-diagonal bubble
- Per-approver approval tabs and details; pending approvals do not hold the reply lane
- Private/group Tracking, sourced update chains, deletion tombstones and provenance checks
- Source-checked summaries with contiguous remaining history and scoped history tools
- In-app reminders, per-chat sidebar preferences, shared/private workspace files,
  safe text preview and download
- Private-chat-only Activity from canonical SDK runs/events, with child runs and safe outputs
- Ephemeral live token delivery; reconnect reads complete committed outputs
- SDK-backed durable inputs, same-run recovery, execution leases/fencing, child
  completion notifications, and idempotent product FIFO handoff

See [implementation and acceptance status](docs/implementation-acceptance.md)
for the requirement-by-requirement evidence and remaining verification boundaries.

## PostgreSQL and production identity

Use a dedicated database per `TWINTEAMS_TENANT_ID`. The database records its
identity and rejects a mismatched tenant configuration. This first implementation
is a **single-tenant database** architecture, not row-level multi-tenant hosting.
Business tables use the `tt_` prefix. Runtime tables remain SDK-owned `agent_*`
tables; no second Run/Event journal is maintained.

Before deployment, configure:

- `TWINTEAMS_ENV=production`, `TWINTEAMS_DEVELOPMENT_MODE=false`
- `DATABASE_URL=postgresql://...` through a server-side secret store
- `TWINTEAMS_TENANT_ID`
- JWT verification key, issuer, audience and algorithm; tokens require `sub`, `exp`,
  `iat`, `iss`, `aud` and matching `tenant_id`
- Persistent, access-controlled workspace storage; `AGENT_MEMORY_DIR` must point
  to appropriate persistent storage if private SDK memory is enabled
- The real model provider credentials

Fresh schema provisioning is explicit:

```sh
uv run --python 3.13 python -m app.init_db
uv run --python 3.13 python -m app.provision_owner --subject 'verified-subject' --name 'Person Name'
```

Do not use fresh-install commands as migrations for an existing production
installation. Existing SDK PostgreSQL deployments through migration 003 apply the
SDK's additive coordination migration 004 explicitly. See
`../agent-runtime/docs/durable-execution.md`. Early local SDK SQLite v1 files have
an explicit non-destructive `SQLiteStore.migrate_schema()` upgrade; stop writers
and back up first. The existing development database was upgraded without reset.

Launch the production entrypoint only after setting the production environment and provisioning the database: `uv run --python 3.13 uvicorn app.main:app --host 127.0.0.1 --port 8000 --timeout-graceful-shutdown 10`. Put it behind your configured deployment ingress.

Production requests fail closed without verified JWT configuration and a
provisioned owner. The included browser login is a development flow; wiring a
real OIDC login/token acquisition UI is a deployment integration still required.

PostgreSQL SQL/driver compatibility has been implemented and reviewed, but no
PostgreSQL instance was available for an actual integration run. SQLite checks
are not evidence that production PostgreSQL, multi-pod deployment, backup/PITR,
or external-effect exactly-once behavior have passed acceptance.

## Focused verification

The user authorized bounded checks for behavior not directly clickable in the UI.
These checks use isolated temporary SQLite databases and an explicitly test-only,
deterministic adapter. They make no paid/provider calls and are not used by the app.

```sh
cd backend
.venv/bin/python checks/product_acceptance.py
.venv/bin/python checks/runtime_acceptance.py
.venv/bin/python checks/provider_config_acceptance.py
.venv/bin/python checks/twin_message_bridge_acceptance.py
.venv/bin/python checks/twin_conversation_acceptance.py
cd ../../agent-runtime
PYTHONPATH=src ../twinteams/backend/.venv/bin/python tests/test_durable_sqlite.py -v
```

No broad regression, lint or typecheck suite was run. Browser/HTTP acceptance and
its limitations are recorded separately. Live-model acceptance requires real
provider configuration; external sending/payment tools are not installed or
silently simulated by approvals.

### Explicit browser acceptance fixture

For UI acceptance without provider calls, run `.venv/bin/python checks/acceptance_fixture.py --port 8011` from `backend`, then open http://localhost:8011. This is a separately launched, permanently labeled test instance with its own DB/files. It never changes the normal app into a mock. Type `plain`, `slow`, `delegate` or `approve` to exercise actual product/SDK flows with deterministic test output.

For the approved dialogue controls, sign in as AAA, open **Team workspace**, keep
Reply for me off and BBB/CCC offline or outside that chat, then send `dialogue`.
The fixture routes numbered responses between BBB and CCC while AAA observes;
the shared limit pauses further acceptance after six. The explicit Continue
button authorizes the displayed additional allowance without resetting lineage.
Send `proactive` in that chat to exercise the actual same-chat messaging tool;
its test acknowledgment requires no automatic reply. These commands are fixture
scenarios, not model-quality evidence or a fallback in the normal application.
