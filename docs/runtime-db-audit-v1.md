# agent-runtime database and recovery audit v1

> Historical audit/design evidence, retained with its original date. Current accepted execution, notification and transport behavior is in [architecture.md](architecture.md). Earlier recommendations are not an implementation contract.


Date: 2026-10-17. Inspected checkout: `/workspace/shared/agent-runtime`, commit `1873697c8c599bb1f7c4889f120b627babc0aea5`.

This is a read-only source audit and design input. No runtime code, migrations, tests, or deployed database were changed or run. References below are relative to the inspected checkout. “Existing” describes source code, not a verified production deployment; “Proposed” is work needed for Twinteams.

## Executive conclusion

Existing agent-runtime is an event-sourced recovery engine at durable logical boundaries, not an all-events durable orchestration service. Reuse its canonical journal and reducer. It already supports durable model request/response boundaries, individual tool start/raw return/final result boundaries, compaction checkpoints, historical snapshots, independent forks, and explicit uncertain-tool reconciliation. It does not durably capture every streaming delta, acknowledge durable external input, provide distributed worker exclusivity, persist browser delivery, or guarantee exactly-once external effects.

“Recover at any point” must mean: reconstruct state at any committed event; resume computation from a valid committed execution boundary; preserve ambiguous effects as unknown; display previously committed stream events; do not promise to continue a remote provider stream at an exact token or undo external effects by forking.

## Verification clarification: the SDK already owns database sessions

Rechecked at 2026-10-17 16:22 UTC: HEAD remains `1873697c8c599bb1f7c4889f120b627babc0aea5`; runtime checkout is clean. There is **no missing DatabaseSession layer**. Do not read the streaming gaps below as a claim that messages, tool results or session recovery are memory-only.

Concrete write chain: `DatabaseSession(Session), durable=True` (`sessions/database.py:8–9`) → inherited `Session.commit` (`sessions/base.py:502–553`) → `DatabaseSession._persist` calling `await self.store.append(self, record)` (`sessions/database.py:53–55`) → journal insert, next_seq compare-and-swap and projections (`storage/postgres.py:57–81`). Memory publication follows settled persistence (`sessions/base.py:537–550`).

Concrete recovery chain: `DatabaseSession.open` reads recovery records and invokes `_restore` (`sessions/database.py:19–24`) → `replay_records` (`sessions/base.py:371`) → `reduce_record` (`:742`). Historical `asnapshot_at` explicitly reads to event_id and replays (`sessions/database.py:41–45`). Execution `Agent.resume` (`agent.py:250–253,467–470`) calls `run_agent_loop_resume`, records run_resumed and enters the phase-aware loop with recovering=True (`agent_loop.py:76–93`).

The final assistant message/tool plan is durably committed **before** message_end is emitted (`agent_loop.py:350–359`; `sessions/base.py:621–625`). Tool start, raw result and final result are separately durable (`agent_loop.py:603,627,632`). These facts already support semantic business-history reconstruction and safe-boundary recovery. Persisting each text delta/intermediate progress event is additional fidelity, not a prerequisite for replaying the already committed final facts.

Queue persistence also exists: `sync_queues` emits a durable queues record (`sessions/base.py:559–561`), called before drain and after run (`agent.py:428–438,479,542–543`). The gap is the interval **between enqueue returning and that next safe boundary**, not permanently nonpersistent queues. Source test `tests/test_sessions.py:362–371` explicitly checks both halves: no journal immediately after enqueue, then queued input is present after a prompt boundary. `tests/test_sessions.py:48–52` likewise checks final model_completed is retained although message_update is absent and restored messages match.

## 1. Existing SQL inventory and constraints

There are exactly seven tables in the three bundled SQL migrations. Complete original DDL is reproduced in Appendix A, including all fields, types, defaults, primary keys, unique constraints, foreign keys and indexes.

| Table | Authoritative source / role | Key and notable constraints |
|---|---|---|
| agent_sessions | `storage/sql/001_sessions.sql:3–12`, identity extension `003_session_identity.sql:3–4`; sequence allocator and identity listing | PK `(tenant_id,session_id)`; `next_seq bigint >= 0`; all identifiers text; `user_id text` is nullable at SQL level |
| agent_session_events | `001_sessions.sql:13–23`; hot canonical journal | PK `(tenant_id,session_id,seq)`; unique `(tenant_id,session_id,event_id)`; session FK; `record json NOT NULL`, not jsonb |
| agent_session_archives | `001_sessions.sql:24–37`; cold journal manifests | PK `(tenant_id,session_id,start_seq)`; session FK; `start_seq>=0`, `end_seq>=start_seq`, format version exactly 1; no SQL exclusion constraint preventing overlapping ranges |
| agent_runs | `001_sessions.sql:38–52`; retained run projection | PK `(tenant_id,session_id,run_id)`; source enum check user/subagent/system; no FK, no status enum check |
| agent_audit_events | `001_sessions.sql:53–69`; explicit host/tool access facts | PK `(tenant_id,audit_id)`; actor_type user/agent/system check; optional session/run; no FK |
| agent_model_usage | `001_sessions.sql:70–93`; retained per physical model attempt | PK `(tenant_id,attempt_id)`; usage_status known/unknown; optional run/root IDs and token counts; no FK or nonnegative SQL token checks |
| agent_session_checkpoints | `002_checkpoints.sql:4–11`; checkpoint position index | PK `(tenant_id,session_id,seq)`; unique scoped event_id; session FK; no checkpoint state column and no FK to hot events because source may be archived |

All metadata columns are jsonb default `{}`. SQL does not validate the JSON record's embedded seq/id/type against indexed columns. IDs are text, not native uuid. Event ID uniqueness is session-scoped: forks deliberately preserve historical IDs. There are no business users/workspaces/memberships/rooms/tasks/agent-definitions tables, no inbox/outbox, no leases/fencing, no blob attachment registry, and no RLS policies in these migrations. Tenant columns and filtered reads are not a substitute for authenticated authorization.

`PostgresStore.install_schema` is explicit and not part of connect/open (`storage/postgres.py:43–48`). Migrations are non-idempotent; apply once in order. `DatabaseSession._require_identity` requires nonempty tenant/user before first write despite nullable SQL user_id (`sessions/database.py:47–59`).

## 2. Canonical journal, taxonomy and payloads

Every normal committed journal record has `id` (uuid4 hex text), `type`, `seq`, `data`, `time` (timestamp milliseconds), and `run_id` (may be null). A compaction additionally has `checkpoint={version:2,state:...}`. Serialization rejects NaN; candidates are reduced before persistence and only published in memory after persistence settles (`sessions/base.py:502–553`).

All currently accepted reducer record kinds are listed below (`sessions/base.py:168–334`). These are distinct from display-event names.

| Durable kind | Principal payload / recovery meaning |
|---|---|
| session_created | info: session_id, tenant_id, user_id; only sequence 0 |
| subagent_created | parent_session_id, operation_id, root_run_id, configuration name, original task |
| run_started | run_id, source, root_run_id, inputs, model, saved options, tool declarations, execution mode, queue modes, queued messages, required hook names; optional context replacement |
| messages_added | messages appended and matching queue entries consumed |
| queues | complete steering/follow_up queue snapshots |
| input_queued | queue plus message supported by reducer; public enqueue does **not** emit it |
| model_request | model, saved options, tools, step_id; optional context and transformed llm_messages |
| provider_parameters | actual normalized parameters, dropped/adjusted parameters and timeout; built-in adapter commits before HTTP |
| model_call_started | attempt_id, provider, model, purpose; compaction also stores request messages/options |
| model_call_finished | attempt_id, status, usage/response metadata or compaction output, optional error |
| model_completed | complete successful assistant message, including tool call plan; establishes tools/finish_turn phase |
| model_attempt | error/aborted partial final message as evidence; not executable history |
| tool_started | call_id, prepared/validated args, time |
| tool_returned | call_id, raw result, time, saved before after-tool hook |
| tool_completed | call_id, time and result OR use_returned=true |
| tool_retry_authorized | call_id; removes started marker so explicit retry can occur |
| tools_completed | batch boundary; builds toolResult transcript in assistant-plan order |
| turn_completed | finish-turn decision, establishing after_turn boundary |
| run_completed | completed/idle state |
| run_interrupted | status and reason; retains pending execution phase |
| run_resumed | running state; original run continues |
| history_reset | replacement messages; preserves identity/subagent creation/has_runs/spawn_operations |
| compaction_started | reason, first_kept_message_id, tokens_before, model, settings, instructions |
| compaction_failed | reason, error, aborted |
| compaction | summary, first_kept_message_id, tokens_before, details, plus full post-transition checkpoint |
| context_replaced | replacement messages |
| session_forked | parent_session_id, parent_event_id, new info; origin and independent run-root attribution |

Payload production references: `sessions/base.py:563–625,662–705`; `agent.py:328–368`; `sessions/database.py:70–83`; `storage/tracking.py:8–33`; `subagents/manager.py:95–105`; `ai/__init__.py:150–158`.

### Events that are not durable today

The observer API emits `agent_start`, `agent_end`, `turn_start`, `turn_end`, `message_start`, `message_update`, `message_end`, `tool_execution_start`, `tool_execution_update`, `tool_execution_end`, `compaction_start`, and `compaction_end`. These are presentation callbacks, not the journal. Some have corresponding durable semantic boundaries, but neither event names nor envelopes should be assumed interchangeable.

Streaming parser `text_delta`, `thinking_delta`, `toolcall_delta` and related blocks flow through message_update.assistantMessageEvent (`ai/parsers.py:171–177`; `agent_loop.py:327–359`). `Session.observe` only keeps latest message/tool values in memory and clears them at completion (`sessions/base.py:707–720`). Tool progress is queued in an asyncio.Queue then emitted (`agent_loop.py:579–625`); it is not persisted. `tool_execution_start` occurs before validation and durable tool_started (`agent_loop.py:518–542`), so it does not prove execution began. Full successful assistant output is persisted before message_end (`agent_loop.py:350–359`).

Tests explicitly document this: `tests/test_sessions.py:36` test_file_layout_projection_and_live_events_are_not_durable; `:362` test_enqueue_is_in_memory_until_execution_boundary. These tests were read by name/source, not executed for this audit.

## 3. Transaction and failure boundaries

### PostgreSQL append

One pool connection transaction performs session ensure, journal insert, checkpoint index insert if applicable, compare-and-swap next_seq update, and run/usage projection (`storage/postgres.py:50–81`). A stale writer cannot successfully append the same sequence; failure rolls back the transaction. This is optimistic journal consistency, **not** a distributed executor lock or fencing guarantee for tools already dispatched.

`Session.commit` shields persistence from cancellation until settled, then publishes memory; persistence failure poisons the session instance and requires reopen (`sessions/base.py:502–553`). There is no stable caller-supplied normal event ID / request idempotency key: commit generates a new ID. An uncertain DB acknowledgment must be resolved by reopen/read; blindly calling commit again is not an exactly-once operation.

`storage/audit.py:12–90` projects run and usage facts only. It does not automatically create agent_audit_events for user inputs/tool accesses. Host/tool adapters explicitly call `PostgresStore.audit` with stable audit_id; ON CONFLICT DO NOTHING is idempotent but does not validate conflicting payload equivalence (`storage/postgres.py:268–307`). Do not describe daily input metrics as automatically complete without that integration.

### Model attempts

DatabaseSession wraps physical model invocations with started/finished attempt records (`sessions/database.py:70–83`, `storage/tracking.py:8–73`). A crash can leave usage unknown. Unknown is not zero. Completion of usage metadata and publication of model_completed are separate commits; tracking records finish when response.result() returns, before the loop commits full output (`storage/tracking.py:64–71`, `agent_loop.py:350–353`). A crash between them may require repeating a model request even though usage reports that a prior provider call completed.

Credentials/headers and callbacks are not restored from model records (`sessions/base.py:51–94`); model options include an allowlist. Tool declaration/version and required hooks need reinjection. This does not mean arbitrary user tool arguments/results or metadata are automatically secret-redacted.

### Tool effects

Persist tool_started before invocation, then raw tool_returned before after_tool_call, then tool_completed, finally tools_completed (`agent_loop.py:595–644,228–240`). Each is a separate commit. No DB transaction can atomically include a third-party tool effect. A crash after remote success but before raw-result commit is unknown. A crash after raw-result commit can reuse the raw result without rerunning the tool, but the after-tool hook may rerun and therefore should be pure or idempotent.

Nested `run_tool_call` uses a config without a Session (`agent_loop.py:452–484`), so nested tool effects are not independently journaled by that helper. A generic all-tools guarantee must cover nested operations/adapters, not merely top-level AgentTool invocations.

### Input acceptance gap

steer/follow_up/enqueue only append to in-memory lists; enqueue is async in signature but does not perform a durable write (`agent.py:176–188`). The active loop saves queue snapshots before draining and after completion (`agent.py:428–438,479,542–543`). A process loss before that boundary loses an apparently accepted message. Concurrent direct Session.commit from a separate input handler would violate the single execution-coroutine contract (`sessions/base.py:337–338`); use a host durable inbox instead of introducing a competing journal writer.

## 4. Snapshot, checkpoint and fork semantics

The full state shape is `sessions/base.py:106–141`: info; messages/message_ids; compaction/compaction_attempt; origin/subagent_creation/has_runs/spawn_operations; status/phase; run_id/run_source/root_run_id; model/options/tools; assistant/results/returned/started/decision/last_turn/request; steering/follow_up; error; tool_execution/queue_modes/required_hooks; run_start/run_messages/step_id.

Compaction atomically records summary and checkpoint version 2 state in one event (`sessions/base.py:528–531`). The checkpoint reducer requires exact state-key equality and version 2 (`:168–179`). Replay rejects unknown kinds, duplicate IDs, gaps, and suffixes not beginning at a compaction checkpoint (`:732–745`). It is not a general schema-versioned replay upgrader. Adding new event types or changing state keys requires explicit reducer/version compatibility design.

Checkpointing is coupled to compaction; there is no independent periodic execution-checkpoint record or snapshot table. Full journal remains authoritative. Compaction rejects unsafe cut boundaries, retains call/result grouping and effective system message (`sessions/projection.py:20–57`). Message IDs are derived from event ID/field/index (`:9–17`).

`DatabaseSession.open` replays nearest checkpoint plus suffix (`sessions/database.py:18–26`); `asnapshot_at(event_id)` returns reconstructed historical state without changing running state (`:41–45`). PostgreSQL reads use a repeatable-read snapshot for hot journal/manifest discovery, then fetch immutable cold archives outside that connection (`storage/postgres.py:105–179`). Ordinary archived event IDs have no retained per-event lookup index, so historical target resolution can require full archive history scan (`:119–167`). Recovery fails closed on missing/overlapping event ranges.

Fork copies the complete prefix, preserving event IDs, then appends session_forked and imports atomically into an empty target (`sessions/base.py:460–500`; `storage/postgres.py:83–96`). Import does not project historical usage/audit to avoid duplicate billing/counts. This is a physical journal copy, not a cheap pointer-only branch. Reusing preserved event IDs globally without a session namespace would collide. Fork cannot roll back external effects; unresolved tool uncertainty remains unresolved. Operation IDs incorporate session ID, so a new branch is a new effect namespace: any replay policy for already-started effects must remain conservative.

## 5. Recovery matrix

| Committed position at crash | Existing resume behavior / constraint |
|---|---|
| No accepted durable input | SDK cannot recover memory-only input; host inbox needed |
| run_started before model request | run can resume and prepare next request |
| model_request / provider_parameters, no model_completed | repeat provider request with saved context/config; no token-level provider continuation guarantee |
| model_call_finished but no model_completed | usage fact may exist; assistant plan not committed; request may repeat |
| model_completed with tool calls, none started | reuse plan, no model regeneration |
| tool_started with no returned/completed | replay only tool.replay=safe, otherwise ToolRecoveryRequired |
| tool_returned, no completed | reuse raw return and rerun finalizing hook; do not rerun tool |
| some tool_completed, no batch boundary | reuse individual final results; execute/reconcile remaining calls |
| tools_completed | reconstruct results and finish turn |
| turn_completed | reuse saved decision rather than recompute finish_turn decision |
| run_completed | no unfinished run to resume; new prompt is a new run |
| compaction committed | restore checkpoint then subsequent suffix |
| subagent_created only | explicit child recovery starts original task once; duplicate spawn finds same child |

References: `agent_loop.py:76–93,135–285`; `sessions/base.py:627–705`; `agent.py:390–421`. Tool definitions/version must match saved declarations; required hook presence, original provider/model/API are checked. Hooks are only checked for presence, not content fingerprint, so host version pinning is still needed.

Stable tool operation identity is `session_id/run_id/step_id/call_id` (`sessions/base.py:652–656`). It survives same-session recovery and distinguishes reused provider call IDs across steps. It is a useful downstream idempotency key but no automatic downstream deduplication is provided. `resolve_tool_call` records externally reconciled result, while `authorize_tool_retry` explicitly permits retry without asserting the old effect failed (`:686–705`).

## 6. Subagent durability and limitations

Subagent task IDs are deterministic uuid5 from parent session and operation ID (`subagents/manager.py:39–40`). Creation is persisted before spawn returns; child discovery examines parent spawn_operations even if parent tool result was not yet saved (`:68–114`). Child creation records retain parent, root run, config name and task. Repeating spawn finds existing child without restarting it.

Background tasks, active manager dictionary, and child.error are process-local (`:15–20,30–37,116–127`). get() is read-only, explicit resume() launches unfinished children (`:150–208`). Root recovery does not imply all children are automatically scheduled. A durable host scheduler and child registry/projection are needed for cross-worker discovery/liveness.

The factory contract is synchronous `session_factory(id)` (`:26–27,58–75,86`), while DatabaseSession.open is async. Directly supplying DatabaseSession.open does not satisfy this API. A host preloaded/session-cache adapter or an explicit SDK async-factory extension is needed; do not claim production multi-process PostgreSQL subagent scheduling already exists.

send_agent_message is replay=never, and its description says safe-boundary durability (`subagents/tools.py:28–33`). Active child send calls memory-only enqueue; idle completed-child send launches a background task and returns before a run_started durability acknowledgment (`subagents/manager.py:188–199`). Route agent-to-agent messages through the same durable inbox/outbox design as user inputs.

## 7. Archive and storage integrity

Archive uploads are optional explicit host jobs requiring exclusion of writers and other archivers (`storage/postgres.py:181–200`). Content-addressed gzip JSONL S3 objects use IfNoneMatch, SSE-KMS, optional version ID, SHA256 and read-back verification before publication (`storage/s3.py:12–20,37–86`). Manifest insert and deletion of hot event rows happen in a single DB transaction after upload and session-change recheck (`storage/postgres.py:232–266`). A failed publish may leave an unreferenced object but should preserve hot records. The host still must enforce lock/exclusivity, retention, backup and KMS availability.

Reader verifies SHA256, format and contiguous range (`storage/s3.py:91–112`); overlap/missing merged ranges fail closed. Checkpoint index remains after hot-row deletion. SQL hot journal is not itself cryptographically hash-chained or immutable against privileged update/delete; there are no append-only grants/triggers here. PostgreSQL replication/RPO/PITR settings are deployment responsibilities, not established by this source audit.

LocalSession is a separate backend: JSONL envelopes with SHA256, fsync, partial-tail repair, and optional disposable checkpoint index (`sessions/local.py:50–160,206–238`). directory=None is in-memory only. Local fsync is not a replacement for a multi-host database/object store deployment.

## 8. Proposed additive Twinteams / SDK contracts (not existing capabilities)

1. Preserve existing runtime tables and source-of-truth reducer; business IDs and projections should reference composite tenant/session identity. Do not introduce a competing authoritative transcript.
2. Persist accepted external and inter-agent inputs in an application inbox transaction before returning accepted to sender. Unique producer/message id, payload hash, target, state and consumption journal reference are required. Only the current fenced session owner drains it. Consumption must be transactional with journal insertion or explicitly reconciled using a stable input ID; “mark delivered then enqueue” is unsafe.
3. Add host execution leases and monotonically increasing fencing tokens. Fence all journal writes, effect-dispatch claims, queue draining and archive ownership. A lease TTL alone cannot prevent a paused old worker from calling an external service; downstream idempotency/fenced dispatch is also needed.
4. Define an all-observed-events journal/stream extension capturing ordered stream deltas, tool progress, lifecycle, errors, decisions and transport delivery references. Persist-before-publish; use stable event IDs, per-stream sequence, causation/correlation to run/step/attempt/operation and committed boundary. Keep execution reducer semantics separate from display deltas. Batching is allowed only if acknowledgment/display waits for the batch commit; do not silently sample away events under an “all events” promise. Capturing observed provider chunks does not guarantee persistence of bytes received immediately before process loss or ability to resume provider generation.
5. Persist effect attempts and reconciliation facts, approval reference, downstream idempotency key, dispatch/unknown/succeeded/failed status and evidence. Include nested tool calls and hooks where they cause effects. Never relabel unknown as failed to enable automatic replay.
6. Persist delivery outbox and consumer cursors for SSE/WebSocket reconnect and third-party notifications. Journaling model output is not proof it was delivered; use idempotent delivery identities and explicit at-least-once semantics.
7. Persist immutable agent/config/tool/hook versions plus artifact content hashes; reinject credentials from secret store. Define reducer/event/checkpoint schema versions and compatible migration/upcasting before adding kinds.
8. Add scalable archived event-ID lookup if arbitrary historical-event navigation must be bounded-latency. Maintain immutable blob references, integrity, access controls and retention for large outputs; a pathname in a tool result is not durable shared attachment storage.
9. Add optional execution checkpoints independent of semantic compaction only with explicit reducer/version support. Never overwrite the existing checkpoint index as if it held state.
10. Make business permissions and tenant isolation explicit. Source code session ownership validation is not authentication. Define event-level sensitive payload access, retention/deletion obligations and audit coverage.

## 9. Required verification before implementation sign-off

Fault-inject crashes at input acceptance; before/after each journal commit; provider result/usage gap; tool remote success before return commit; raw-return before final hook; child creation before parent result; outbox publish before ack; lease expiry with paused old worker; archive upload/manifest/delete; and fork at pending unsafe tool. Verify no accepted input loss, no unfenced writes, no automatic uncertain-effect replay, no double usage on fork, ordered replayable UI events and explicit unrecoverable/missing-archive errors. Run PostgreSQL integration tests and restore/PITR drills in implementation phase; this audit did not run them.

Existing relevant test coverage locations: `tests/test_database.py:129–439`, `tests/test_sessions.py:36–650`, `tests/test_projection.py:78–545`, `tests/test_subagents.py:58–255`, `tests/test_runtime_boundaries.py:37–250`. These establish intended cases, not evidence of a currently passing deployment.

## Appendix A. Original schema (verbatim source; no migration proposed here)

### 001_sessions.sql

```sql
-- PostgreSQL 14+. Apply explicitly once; runtime startup never performs migrations.
BEGIN;
CREATE TABLE agent_sessions (
    tenant_id text NOT NULL,
    session_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    next_seq bigint NOT NULL DEFAULT 0 CHECK (next_seq >= 0),
    metadata jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (tenant_id, session_id)
);
CREATE INDEX agent_sessions_updated ON agent_sessions (updated_at);
CREATE TABLE agent_session_events (
    tenant_id text NOT NULL,
    session_id text NOT NULL,
    seq bigint NOT NULL CHECK (seq >= 0),
    event_id text NOT NULL,
    record json NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (tenant_id, session_id, seq),
    UNIQUE (tenant_id, session_id, event_id),
    FOREIGN KEY (tenant_id, session_id) REFERENCES agent_sessions
);
CREATE TABLE agent_session_archives (
    tenant_id text NOT NULL,
    session_id text NOT NULL,
    start_seq bigint NOT NULL CHECK (start_seq >= 0),
    end_seq bigint NOT NULL CHECK (end_seq >= start_seq),
    object_uri text NOT NULL,
    version_id text,
    sha256 text NOT NULL,
    format_version smallint NOT NULL DEFAULT 1 CHECK (format_version = 1),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    metadata jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (tenant_id, session_id, start_seq),
    FOREIGN KEY (tenant_id, session_id) REFERENCES agent_sessions
);
CREATE TABLE agent_runs (
    tenant_id text NOT NULL,
    session_id text NOT NULL,
    run_id text NOT NULL,
    user_id text NOT NULL,
    root_run_id text NOT NULL,
    source text NOT NULL CHECK (source IN ('user', 'subagent', 'system')),
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    status text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (tenant_id, session_id, run_id)
);
CREATE INDEX agent_runs_user_time ON agent_runs (tenant_id, user_id, started_at);
CREATE INDEX agent_runs_user_finished ON agent_runs (tenant_id, user_id, finished_at);
CREATE TABLE agent_audit_events (
    tenant_id text NOT NULL,
    audit_id text NOT NULL,
    actor_id text NOT NULL,
    actor_type text NOT NULL CHECK (actor_type IN ('user', 'agent', 'system')),
    user_id text NOT NULL,
    session_id text,
    run_id text,
    occurred_at timestamptz NOT NULL,
    action text NOT NULL,
    resource_type text NOT NULL,
    resource_id text NOT NULL,
    outcome text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (tenant_id, audit_id)
);
CREATE INDEX agent_audit_user_time ON agent_audit_events (tenant_id, user_id, occurred_at);
CREATE TABLE agent_model_usage (
    tenant_id text NOT NULL,
    attempt_id text NOT NULL,
    session_id text NOT NULL,
    run_id text,
    root_run_id text,
    user_id text NOT NULL,
    provider text NOT NULL,
    model text NOT NULL,
    purpose text NOT NULL,
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    status text NOT NULL,
    input_tokens bigint,
    output_tokens bigint,
    cache_read_tokens bigint,
    cache_write_tokens bigint,
    total_tokens bigint,
    usage_status text NOT NULL CHECK (usage_status IN ('known', 'unknown')),
    metadata jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (tenant_id, attempt_id)
);
CREATE INDEX agent_usage_user_time ON agent_model_usage (tenant_id, user_id, finished_at);
CREATE INDEX agent_usage_root ON agent_model_usage (tenant_id, root_run_id);
COMMIT;

```

### 002_checkpoints.sql

```sql
-- Existing deployments apply this migration once, after 001_sessions.sql.
-- Only positions are indexed. Checkpoint states remain exclusively in the journal.
BEGIN;
CREATE TABLE agent_session_checkpoints (
    tenant_id text NOT NULL,
    session_id text NOT NULL,
    seq bigint NOT NULL CHECK (seq >= 0),
    event_id text NOT NULL,
    PRIMARY KEY (tenant_id, session_id, seq),
    UNIQUE (tenant_id, session_id, event_id),
    FOREIGN KEY (tenant_id, session_id) REFERENCES agent_sessions
);
COMMIT;

```

### 003_session_identity.sql

```sql
-- Session ownership is projected from its creation/fork journal events.
BEGIN;
ALTER TABLE agent_sessions ADD COLUMN user_id text;
CREATE INDEX agent_sessions_user_time ON agent_sessions (tenant_id, user_id, created_at);
COMMIT;

```
