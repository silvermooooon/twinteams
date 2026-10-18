# PostgreSQL operation

Local development and deployment use PostgreSQL. The local service is described
in `<postgres-provisioning-workspace>/README.md` and listens
on `127.0.0.1:5432`, database `appdb`. Redis carries disposable notifications and presence TTLs; PostgreSQL owns every durable task/log/business fact. See [architecture.md](architecture.md).

Both `DATABASE_URL` and `AGENT_DB_URL` point to this database. Product tables use
`tt_*`; canonical Runtime tables use `agent_*`. Credentials live in ignored mode-600
project environment files and must not be committed. `app.init_db` creates product
metadata tables and upgrades SDK migrations 001–005 explicitly and repeatably. API/worker startup verifies
schema without changing it. The SQL files belong to the SDK package.

Local DEV login requires an explicit local environment, development opt-in and a
loopback PostgreSQL address. The development server binds loopback. Production
uses verified JWT identity and does not enable DEV login.

Regression tests use unique `tt_test_*`, `runtime_test_*` and `test_*` schemas.
They create/drop only their own schemas; application data remains in `public`.
The SDK pool preserves connection-string search_path options when it applies
statement timeouts. Backup/restore and cold S3 retention remain SDK/deployment
operations as documented in the Runtime database guide.

## Earlier PG-only verification — 2026-10-18 (historical)

The following records predate the separate-process WebSocket refactor. They do not
certify the current transport; consult the latest distributed acceptance report.

Verified on PostgreSQL 18.6 using the existing local service:

- Applied product metadata and SDK DDL: 30 business tables and 9 Runtime tables.
- Ran all 296 SDK tests with PostgreSQL/S3 test extras; none skipped.
- Passed 8 backend tests and all five bounded acceptance scripts against isolated schemas.
- Logged in as DEV-AAA in the normal browser UI and received a real GPT-6 Luna reply.
  PostgreSQL retained the completed business request, completed Runtime run,
  semantic events, model usage and both chat messages.
- Restarted the application and reloaded the browser: the committed reply remained
  available without another model call.

Previous local database files were discarded as requested. New application state
begins with the demonstration identities and this verification dialogue.

## Twin personal settings migration

Run `cd backend && uv run python -m app.init_db` before starting the API and worker.
Provisioning calls the additive DDL in `backend/app/personal_schema.py`; that file
and `backend/app/models.py` are the maintained schema sources. API and worker
startup only check that this migration has been applied. Re-running provisioning
keeps existing Twin names, avatar choices, enabled Skills and request snapshots.

The migration adds `tt_actors.avatar_data` (`BYTEA`, deferred in ORM queries),
`avatar_digest`, `avatar_preset`, `profile_revision` (default 1),
`enabled_skill_names` (JSON, default empty list), and nullable
`tt_reply_requests.execution_config` (JSON). Normal Actor projections contain
only public profile metadata. Only the authenticated avatar route loads image
bytes. Credential request metadata is registered separately in
`backend/app/credential_models.py` and provisioned by the same ORM operation;
credential values are never columns in either execution configuration or the
credential request table.

The profile uses the existing unique private chat as its fixed Twin workspace;
Skills live in its `skills/<name>/SKILL.md`. New valid files appear disabled.
Missing, unreadable or malformed enabled files remain visible so the owner can
switch them off; they are excluded from new execution snapshots. Configuration
is frozen once after the SDK has acquired execution ownership, so queued tasks
observe the latest settings and resumed tasks retain their original catalog.
Completed skill loads remain ordinary SDK tool results in the existing journal.
The Skill switch limits the dedicated loaders; this local demo's execution
backend still has its documented ordinary filesystem/process privileges.

The API and worker must share `TWINTEAMS_WORKSPACE_ROOT` and `AGENT_MEMORY_DIR`
when that Memory setting is used. Both construct Memory through
`personal.make_memory_store`; without an explicit Memory root, local development
uses the common backend data directory and production leaves Memory disabled.
Personal Memory pages support Git-backed editing and deletion with optimistic
commit versions. Owner-only Soul configuration reads the canonical workspace SOUL.md with a content hash; explicit provisioning migrates old DB Soul values before removing those obsolete columns.
New private human experiences schedule incremental Dream work through the
existing FIFO and scoped deadlines; only confirmed Git publication advances the
durable cursor. Listing pages have an opaque-to-the-UI key cursor and no total
count. Source references expose recorded session/event IDs, including real owner
configuration audit events; rendering a page never scans session journals to
infer source text. See [Git Memory and Dream](twin-git-memory.md) for explicit
legacy migration, conflict and unknown-publication behavior. Runtime status is worker-reported model metadata,
not a promise that an existing run uses a newly changed model configuration.

Message reactions use the additive `tt_message_reactions` table, with a composite Message/Actor/emoji primary key. Deploy `python -m app.migrate_reactions` before reloading the API and Worker; it only provisions this table, performs no history backfill and invokes no model. New installations also provision it through `app.init_db`. See [message-reactions.md](message-reactions.md) and the standalone [DDL](message-reactions-schema.sql) for desired-state idempotence, batch projections, Actor ownership and private-reset cleanup.
