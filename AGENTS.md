# Duet project handoff

Verified against product commit `dbbacbb` on 2026-11-06. This is a navigation and
contribution guide, not a second specification. Current source and linked contracts
take precedence over earlier proposals. Dated acceptance reports establish only
the behavior, version and workload actually tested.

## Working agreements

The owner values a coherent, small system: prefer conventions and existing modules
to additional abstractions. Finish authorized work and verify actual behavior.
Breaking changes are welcome when requested; that does not authorize unrelated
data deletion, history rewriting or removal of current permission boundaries.

These personal rules apply across repositories. They were adapted from
agent-runtime Discussion #2, originally collected from a post by @马斯 | GPT实测:

1. Do not add complexity for guessed, near-impossible races or security edge
   cases. First explain the concrete failure, likelihood and code cost and obtain
   agreement. Do not silently widen the change scope.
2. Validate external data once at the entry layer; pass typed values inward.
3. Extract functions only for reuse, genuinely complex logic or a clear concept.
4. Keep the main function about normal flow; put validation and details below it.
5. Comments explain non-obvious constraints, not what the code plainly does.
6. Change the source of generated files and regenerate; do not hand-edit outputs.
7. Test real implementations rather than copying their logic; prefer few mocks.
8. Follow the repository's existing workflow, tools and processes.

- The owner requested automatic commit and push after verified, completed changes.
  Use the existing `silvermooooon` remotes and report the resulting SHA.
- Check each repository's status and preserve unrelated changes. Delegate useful
  independent work with the requested model/effort; keep one writer per file and
  independently review the result.
- UI and platform prompts are English; Duet may answer in participants' requested
  language. Preserve user-chosen names and use Duet as the role/product name.
- Avoid exposing secrets or private conversations in logs, reports or commits.
  Retain only necessary diagnostic details; prefer counts, IDs and hashes.

## Workspace boundaries

The observed workspace container is `/Users/silver/Documents/Codex/2026-10-04`.
It is not a Git repository or a monorepo. Keep three independent sibling checkouts:

| Directory | Responsibility |
| --- | --- |
| `twinteams/` | This product, branded **Duet / 双重奏**; React UI, FastAPI, execution worker |
| `agent-runtime/` | Original Python SDK: execution, providers/tools, sessions, journals, leases, Memory, subagents |
| `message-center/` | Optional message hub: dashboard, messages, audience, subscriptions, delivery |

The backend installs the SDK from editable `../../agent-runtime`, relative to
`twinteams/backend`; Message Center also references that sibling SDK. Do not create
another `agent-runtime-twinteams` copy or merge these repositories. An initial cwd
of `agent-pi-agent-agent-loop-pi` is the earlier PI/notes reference workspace, not
the product root. PI informs the SDK; it does not override later product decisions.

Infrastructure instructions live separately under
`/Users/silver/Documents/Codex/2026-10-18/docker-pg-docker/README.md` and
`/Users/silver/Documents/Codex/2026-10-18/ssh/TWINTEAMS-FRP.md` / `HANDOFF.md`.
Consult them when needed; do not copy private configuration into repositories or
change the machine's global proxy settings to fix an application configuration.

## Read first and code map

Read [requirements](docs/product-requirements.md),
[interaction rules](docs/interaction-rules.md), [architecture](docs/architecture.md),
[implementation contract](docs/implementation-contract.md) and
[visual identity](docs/visual-identity.md). [README](README.md) covers setup.

| Area | Entry points |
| --- | --- |
| HTTP/auth/config | `backend/app/main.py`, `api.py` (`identity`), `config.py` |
| Business transactions/queries | `backend/app/service.py`, `models.py` |
| SDK assembly and execution | `worker.py`, `runtime_bridge.py`, `twin_execution.py`, `request_sessions.py` |
| Prompts, persona, introduction | `twin_prompts.py`, `soul_file.py`, `onboarding.py` |
| History and source permissions | `conversation_context.py`, `message_references.py`, `service.py` |
| Peer channels and consent | `duet_conversations.py`, `duet_messages.py` |
| Tools and owner decisions | `platform_tools.py`, `owner_replies.py`, `questions.py`, `credentials.py` |
| Settings, Memory and follow-through | `personal.py`, `git_memory.py`, `memory_dream.py`, `contacts_dream.py`, `scheduled_tasks.py` |
| Files, reset and hub integration | `attachments.py`, `deliverables.py`, `twin_reset.py`, `message_center.py` |
| UI state/transport | `frontend/src/App.tsx`, `api/client.ts`, `LocalSends.tsx` |
| UI surfaces | `DetailSidebar.tsx`, `MyTwin.tsx`, `AskUserCard.tsx`, `FilePreview.tsx`, `Duet.css` |
| Provisioning/verification | `backend/app/init_db.py`, `backend/app/sql/`, `backend/checks/`, `backend/tests/`, `frontend/e2e/` |

Unprefixed backend filenames above are in `backend/app/`; UI component filenames
are in `frontend/src/`. Extend the relevant domain module instead of growing a
second general orchestration layer.

## Confirmed conversation and execution contracts

- `kind=group` means a human pair or multi-person chat. Humans publish messages
  and reactions. Pair chats are deduplicated and show the other person's name;
  only multi-person groups can be renamed.
- `kind=private` is the owner talking only to their own Duet. `kind=duet` is a
  separate Duet-to-Duet channel; owners inspect it read-only in the sidebar.
  A person cannot directly chat with someone else's Duet.
- Presence, unread state and timers do **not** trigger human-chat Duet replies.
  Offline/ten-second auto-reply, mixed human/agent chats and “Reply for me”
  publication are retired. Compatibility fields do not reinstate those features.
- Every owner must consent once or have all required directed standing grants
  before peer communication starts. Revocation closes dependent channels.
  Connection consent does not grant arbitrary action or file-sharing authority.
- Every published peer message reaches the other participating Duets. They may
  inspect and finish silently. Do not restore a routing model, polling subagent
  or communication task/followup state machine in front of the message channel.
- The initiating causal chain allows six published peer sends, including the
  opening. A seventh stays prepared and unsent until explicit **Continue** adds
  six. Private reports, questions, execution and child results do not spend sends.
- Main Duet is the owner's front-facing representative: quick replies, small
  investigations and coordination. Delegate substantial work, review results and
  report privately. Peer requests do not grant authority over the owner.
- Children share the executing owner's authorized workspace, Skills, credentials
  and Memory, with independent task context/compaction. Main-only tools such as
  `reply_as_owner` remain main-only; shared resources do not mean identical authority.
- Independent incoming requests have separate SDK sessions and can run in
  parallel. Questions, credentials, approvals and child continuations attach to
  their original task journal. Only turns within that session are ordered; never
  restore an owner-wide/chat-wide queue blocking unrelated private requests.
- Foreground and background admission/child pools are separate per-worker limits.
  SDK leases/fences and task-session FIFO are real current mechanisms. One active
  worker is a deployment convention, not another Redis lock or a global quota.

## Storage, reads and recovery

- PostgreSQL stores product facts (`tt_*`) and SDK state (`agent_*`) in a dedicated
  tenant database. Product requests/outbox own acceptance and repeatable SDK
  handoff; the SDK owns execution journals, commands and leases. Do not add a
  second product Runtime log or embedded database fallback.
- **Published messages are independent snapshots.** Reads check current membership,
  their own history floor and publication state, not recursive audit ancestors.
  Removing a source does not retract a separately published answer.
- Quote/reference originals and downloads independently check current access and
  withdrawal; unavailable originals become placeholders. Runtime manifests freeze
  directly consumed snapshots/originals and hashes; execution/publication recheck
  them. Unsent pending messages receive no snapshot exemption.
- Full records remain intact. Server conversation checkpoints plus bounded recent
  messages form model context; SDK tool-history compaction is separate. Neither
  summary nor cache grants access to a withdrawn original or retired reset scope.
- API startup validates provisioned schemas, without starting Agents, recovery or
  scheduling. The worker owns execution, delivery and bounded compensation.
  Provisioning/migrations are explicit; inspect `app.init_db` before running it.
- Redis holds presence TTLs and disposable notification hints, not authoritative
  task/message state. WebSocket ACK means business persisted, not execution,
  delivery or reading. Disconnect does not cancel the actual worker.
- `/api/ws` carries commands/ACKs and scope revision hints, not model tokens.
  Authorized HTTP fetches content. Do not restore per-token DB writes, SSE replay
  infrastructure or frequent full-chat polling.
- SQL transactions run in the existing DB pool, never across model/network awaits.
  Reuse current constraints/leases; do not add application-wide commit locks or
  a second execution ownership service.

## Memory, follow-through and owner controls

- Workspace `SOUL.md` is the editable persona source. Renaming updates only its
  managed heading. New execution reads current configuration; recovery retains
  frozen tools/profile. Introduction is optional, modular and only for empty
  private chats; reset does not repeat a completed introduction.
- Memory uses the owner's SDK Git store and native version/CAS semantics, separate
  from workspace history or conversation transcripts. Main/child contexts share
  that store. Load the small index and retrieve relevant entries, not all memory.
- **Dream and system review are separate**, normally four-hour mechanisms. Dream
  consolidates private published messages, including Duet progress reports;
  raw group history is not general private-Memory input. Manual Dream is supported.
  Authorized human interaction evidence can separately consolidate contact tags.
- Contacts follow actual visible interactions, not group membership alone. Tags
  are short project/responsibility hints, not generic expertise. Use Memory and
  the task to choose whom to approach; ask the owner when the person is unknown.
- Tracking represents accepted goals requiring later work, blockers or follow-up;
  simple answers/group creation alone need no topic. Duet-thread Tracking stays
  there; private Tracking aggregates authorized private/group/Duet topics. Delivery
  receipts are evidence, not automatic proof the owner's goal is complete.
- System review's “Relevant reminders” currently includes only unresolved Dream
  failure with brief time/context. Recovery removes it: **do not repeatedly report
  repaired problems**. Do not wake a model or show login banners for every error.
- Standard Skill folders live in `workspace/skills/`; uploads preserve nested
  files and start disabled. Enabled catalogs join the frozen execution config.
- Credential values use owner-only forms; models see references/descriptions/env
  key names. Resolve/inject values per shell call, never inherit them into later
  shells. Local execution shares host privileges; per-user Linux/container
  isolation was discussed but is **not implemented**.
- Ordinary questions offer optional choices; open/custom answers use the main
  composer. Exact reply-to or `answer_question` associates an answer with its
  original task; unrelated requests must remain independent.
- `reply_as_owner` requires a current primary-private owner instruction and exact
  reviewed text/files. `chat_id` alone proposes an unquoted human message; optional
  `reference_message_id` identifies the owner's private reference-bearing message,
  not the original source ID; it fixes the original/destination and any `chat_id`
  must match.
  Only **Confirm send** on that card publishes frozen content/audience. Typed button
  text, tool invocation and Cancel do not send. Reuse questions and idempotency.
- Reset is owner-confirmed, never a deployment test. It fences old generations
  and clears private content/Tracking/Memory, preserving group history and the
  documented identity/configuration/workspace resources. It is not secure erasure
  of SDK audit/backups or an undo of committed external actions.
- Optional Message Center publishes/searches/subscribes and delivers to private
  Duets, immediately or as a system-review digest. Processing is not human reading
  or acceptance. Configure API and worker together; no connection means no tools.

Details: [Memory](docs/twin-git-memory.md), [schedules](docs/scheduled-tasks.md),
[questions](docs/owner-questions.md), [credentials](docs/credentials-execution.md),
[reset](docs/twin-reset.md), [tools](docs/twin-platform-tools.md).

## UI and performance

- Apply the **art of whitespace**: retain the Teams-style layout, graphite/white
  with selective red accents, clear icons and minimal technical narration. This
  is Duet's identity, not an official HSBC/Teams skin. These are development rules,
  not instructions to append to each Duet's conversational prompt.
- Foreground replies need simple typing feedback. Platform background activity
  is hidden from ordinary Activity; internal journals remain. Required decisions,
  unsent messages and actionable failures still need visible controls. Do not
  steal focus, automatically expand panels or add another composer input.
- History pages fetch related display records in batches. GET bootstrap/chat must
  not create settings, provision schedules or take write locks. Tracking is a
  lazy tab read, not a `ChatDetail` field.
- Same-login memory cache holds five visited windows of at most 130 messages.
  Cold opens fetch 30, earlier pages explicitly. Reuse preserves position and
  reauthorizes; account/reset/access/disconnect/revision boundaries still apply.
  This is not localStorage or Redis transcript caching.
- Sender-local echo is immediate/in-memory; retry retains the submission key.
  Reload fetches canonical data, not a durable offline outbox. Message text has no
  business character cap; large WebSocket sends fall back to HTTP.
- All file types, including images, live in workspace and enter model context
  as references for inspection. Four attachments/20 MiB limits remain. Delivered
  chat files are snapshots; Markdown/text/PDF preview in-app, HTML opens through
  an explicit action in a separate sandboxed tab and remains downloadable.
- Non-AI APIs have a **3,000 ms successful-response budget**. Errors, missing data,
  timeouts and silent truncation do not count. Query changes require existing
  budget/load checks and reported volume/concurrency. External transfer time is
  distinct from local API processing.
- Not every complete people/chat/reminder/Tracking/shared-file list is paginated.
  Current load acceptance covers 5,000 history messages, 100-item peripheral data
  and ten concurrent clients. Larger scale requires explicit cursors/UI support,
  not an unlimited-capacity claim. Do not restore recursive audit-ancestor reads.

See [loading choices](docs/chat-loading-research.md),
[measurements](docs/chat-loading-performance.md) and
[budget verification](docs/api-performance-budget.md) rather than copying timings.

## Build, verify and deploy

Duet backend requires Python **3.13.x**; use pinned locks and Node **>=22.12**.
The SDK's supported Python range is separate. Preserve the sibling editable SDK.
Real generation uses OpenAI Responses with **`gpt-6-luna`** unless the owner changes
that instruction. New Responses conversation/subagent runs enable native
`web_search`; Dream and other APIs retain their tool policy. SDK consumers opt in,
and recovering runs preserve frozen declarations.

From `backend/`, preserving the existing private environment:

```sh
uv sync --python 3.13
uv run --env-file .env python -m pytest tests -q -p checks.api_budget --api-budget-report /tmp/duet-api-budget.json
uv run --env-file .env python -m checks.api_performance --report /tmp/duet-api-load.json
.venv/bin/python checks/local_stack.py status
```

From `frontend/`: `npm ci`, `npm run typecheck`, then build outside served assets,
e.g. `npm run build -- --outDir /tmp/duet-candidate-dist`. Select meaningful focused
tests, broadening for failures/scope. Documentation-only changes need no business
test run or service restart.

Browser acceptance uses `backend/checks/browser_e2e.py` and `frontend/e2e/` with
disposable PG schemas, Redis namespaces, workspaces and real API/worker/SDK effects.
Follow [browser instructions](docs/browser-e2e.md) for cases/Node path. Scripted
providers prove plumbing, not autonomous model judgment. Keep live-provider use
within the user's authorized scope and bound attempts; existing authorization
persists. Reuse authorized connection settings without exposing values. Never put
fake output into the app or test by messaging actual owners. Verify fixture cleanup.

For a necessary rollout, use existing `checks/local_stack.py` from `backend/`:

1. Check active requests, SDK commands/runs/leases, dispatch/child work and resets.
   Wait for real user work; do not cancel it to deploy.
2. Save a read-only before count/hash snapshot. Stage verified frontend assets;
   preserve the old index and hashed assets needed by already-open clients.
3. Stop only managed Duet worker/APIs. Run explicit migrations only if required;
   ordinary startup must not perform destructive upgrades.
4. `start --port 8000 --second-port 8001` starts the managed stack. Message Center
   (normally 8020), PostgreSQL, Redis and FRP are separate; leave them alone.
5. Verify health, asset hashes and data preservation; check the existing public
   entry if affected, with compressed requests and normal TLS verification.
6. Commit/push verified changes and report SHA/checks/limits. Never commit private
   environments, `backend/data`, generated dist, credentials or private screenshots.

`backend/data/local-stack/` holds ignored managed PIDs/logs. Do not kill by broad
process name. Previous `/tmp` scripts/reports are evidence, not permanent launch
dependencies. The local launcher loads explicit private environment files; the
application does not search arbitrary directories for credentials.

## Recent history and remaining limits

- `dbbacbb`: optional original reference for confirmed owner sends; unquoted cards
  omit the empty original and publish ordinary human messages after confirmation.
- `16ac04b`: independent message snapshots, batched reads, lazy Tracking and API
  budgets; original access and current execution inputs remain guarded.
- `a51e75e`: reply preparation performance and minimal typing feedback.
- `beb9ab2`: bounded history loading and same-login conversation caching.
- Sibling SDK `1fe5394`: native batched Git Memory reads, preserving version/CAS.

These commits identify completed work, not a permanently fixed HEAD. Current
dual-API/single-worker acceptance is not full HA, cross-worker global quotas,
sandboxing, production OIDC browser login or backup/restore certification.
Multi-host execution needs persistent shared workspace/Memory storage. Message
Center's demo permission model and unresolved natural-language department audience
selection remain separate work; see its own README, not old product proposals.

Update this guide when a confirmed contract/entry point changes. Remove superseded
guidance from active documents; retain dated evidence as historical. Link to a
module's specification rather than duplicating its complete design here.
