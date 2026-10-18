# Twinteams implementation and acceptance record

Current recovery/consolidation follow-up: [recovery-fixes.md](recovery-fixes.md).
The dated evidence below remains historical; the follow-up supersedes the earlier
record-only approval and absent regression-suite descriptions.

Date: 2026-10-17. This is an implementation record, not a replacement for the
approved design. Later confirmed requirements in the authoritative Markdown
files take precedence over older HTML/PDF mockups.

## Delivery status and environments

The React/FastAPI application and the sibling Agent Runtime changes are implemented
and runnable. The baseline independent normal-app and isolated-fixture browser review
completed at 18:28 UTC. Local functional acceptance passed for the exercised flows;
production release gates remain below. The normal application uses real persistent business state and the
existing SDK DatabaseSession. It has no simulated-model fallback.

- Normal cloud-desktop preview: **http://127.0.0.1:8010/** (desktop process221)
- Local development logins: **DEV-AAA**, **DEV-BBB**, **DEV-CCC**
- Isolated browser-acceptance fixture: **http://localhost:8011/** (desktop process137)
- Fixture launch: `cd backend && .venv/bin/python checks/acceptance_fixture.py --port 8011`
- Fixture data is confined to `backend/data/acceptance-fixture/`. Its persistent
  amber banner says that model responses are simulated. It exercises the same
  product service, SDK, SQLite persistence, permissions and frontend. No third-party
  provider or external-effect call is made. Use `localhost` for this fixture and
  `127.0.0.1` for the normal preview to separate browser cookie hosts.
- Python 3.13.x, SQLite for local work. PostgreSQL SQL and driver support are
  implemented; **a PostgreSQL server has not been exercised**.
- **Application Runtime generation is not enabled.** A separately saved credential,
  if the user chooses to save one, is not automatically loaded. The normal application preserves the
  human message and reports a clear Failed result. Real model quality, target
  classification, model-driven Tracking/summary behavior and live provider recovery
  still require configured-provider acceptance.
- No remote deployment or push was performed. The local DEV login must not be
  exposed publicly. Standard startup leaves it disabled by default. The explicit `run_local.py` loopback-only launcher enables it for SQLite; production requires verified JWTs.

### Follow-up revision at 19:17 UTC

The approved proactive Twin dialogue changes are implemented in source and have
passed isolated product/SDK-adapter checks plus an independent source review.
An isolated Vite build also passed, and its assets were copied to the served
`dist` at 19:10 UTC. The normal and fixture backends have not yet been restarted
with this revision. The new pause/Continue and proactive-send UI flows still need
manual acceptance after the credential handoff finishes. Do not confuse the baseline
browser evidence with acceptance of this follow-up revision.

A separate **save-only** credential page is running at
`http://127.0.0.1:8012/api/local-provider-config/save-page`. Its initially empty
password input, unchecked explicit storage consent, disabled save button, exact
`openai.env` path and plaintext 0700/0600 disclosure were inspected before handoff.
It saves only when the user personally submits; saving does not validate against
OpenAI, initialize Runtime, or enable generation. The user subsequently completed
the save; the final reviewer checked only that one environment assignment exists
with 0600 permissions, without exposing the value. This establishes local storage,
not API usability. The standalone page and validation flow share one authoritative
environment-file location. A separate, explicitly billed user-click validation
using that saved file is implemented and passed fake-file checks; it is never
triggered by page load or status. Its real result is not yet established.

The earlier validation error was a generic connection message. A subsequent
credential-free GET probe to the official API from the desktop server's network
namespace returned `ConnectError` after 0.057 seconds, with no HTTP status. This
is evidence of a connection failure, not an invalid key or a billing result.

### Evidence labels

- **Verified**: exercised through the normal browser, actual HTTP/business API, or
  a named bounded isolated SQLite check.
- **Implemented, provider-dependent**: real adapter/tool path exists; the provider
  interaction was exercised only with an explicitly test-only adapter.
- **Deployment verification required**: needs the actual PostgreSQL/auth/provider/
  storage infrastructure; local checks are not a substitute.
- **Deferred by design**: expressly unresolved or postponed in the approved design.

## Approved design coverage

This table follows every section of `design-review-2026-10-17.md`. The later
sections of `product-requirements.md`, `interaction-rules.md`, and supplements
map to these rows; their detailed cross-reference follows below.

| Design section | Implementation and evidence | Remaining boundary |
| --- | --- | --- |
| Top principle; §1 responsibility | Product identities, ACLs, trigger eligibility and business FIFO live in `app/service.py`; execution/journal/leases/recovery/child communication live in Agent Runtime. Existing DatabaseSession is reused. English typed frontend resources. | Real provider and PostgreSQL deployment remain unverified. |
| §2 Teams layout | Left navigation contains Reminders and Chat only; people-only search; light blue-gray shell, conversation list, composer and sidebar. Normal browser screenshots 01–06. | Native Teams parity for every file format is not claimed. |
| §3 owner/Twin and chats | Unique paired actors; private owner chat; searching another human creates a group with both Twins; member admission adds the pair. DEV-AAA/BBB/CCC login and private cross-account denial manually verified. | OIDC browser login is an operator integration; included login is development-only. |
| §4 routing and presence | Every group input first passes model-backed needs_response; pair groups then target the other owner, larger groups use addressed targets plus mentions only when a response is needed. Twin-origin inputs preserve attribution and use their source owner for eligibility, excluding that owner's own Twin. Durable eligibility separates offline, online-other-chat, focus and reads. Shared causal-chain allowance defaults to six automatic acceptances, with atomic deduplication, explicit version-checked Continue and repeated-content holds. | Language routing accuracy needs a real model. Empty/failed routing never broadcasts. New dialogue UI has isolated build/logic evidence; manual browser acceptance is pending. |
| §5 manual request/read receipt | Self/other Twin requests, target picker, logical request dedupe, human-only read receipts and history-filtered quotes. API role tests and browser quoted reply verified. | Multi-select and non-author requester placement were not mandated by the confirmed design. |
| §6 FIFO, Stop, retry, child | Per-chat/per-Twin persistent FIFO; SDK execution lease/fence; accepted requests survive owner focus. Compact source queue items and composer typing; owner Stop settles SDK, withdraws only this response and releases next item; Retry is a new FIFO request. Six integration scenarios and 11 SDK checks. Child can outlive main, returns via durable SDK notification into the same product FIFO. | Real cross-pod PostgreSQL failover and provider side effects require deployment tests. |
| §7 Reply for me | Per-owner/per-chat avatar toggle, colored composer, global prompt plus override frozen at acceptance, raw input never published as a human message. Complete Twin result contains separately stored raw input plus processed text in a hard diagonal lavender/mint bubble. SQLite integration verifies a two-model-operation/tool-call path updates one stable bubble while both complete outputs remain in SDK. | Provider wording/quality needs a real model. |
| §8 approvals/reminders | Scoped approval records, tabs in arrival order, no focus stealing, next/auto-close, Approve/Reject/View details. Approval is independent of the reply lane. Personal in-app reminders and done/reopen. Fixture can create real scoped approval records through the actual SDK tool. | No email, purchase, payment or other external-effect adapters are installed. Approval freezes exact action arguments in the SDK and enqueues a FIFO continuation. The installed local reminder action executes through an idempotent product operation; no external action is implied. Long-term authorization UI and expiry policy remain deferred. |
| §9 history/summary | Full original history persisted. All/fixed-at-join seven-day/no-prior grants cover raw text, raw Reply-for-me inputs, quotes, receipts, derived messages, Tracking, summaries, files and live projections. Exact summary source coverage and no-gap raw tail; reusable only for authorized sources. Model writes inherit complete immutable input provenance, not just model-provided citations. Bounded adversarial ACL checks pass. | Per-run summary maintenance is requested after 40 uncovered messages, keeping 20 raw messages by default; both are configurable. The summary body is model-generated through `save_summary`, so actual model compliance/quality remains provider-dependent. No history is dropped if no summary exists. |
| §10 Tracking | Private/group scopes, title/description index, detail tool, chronological updates, manual and Twin CRUD, optimistic revision, sourced provenance and deletion tombstone. Group members share edit rights within their history view. Normal browser CRUD and no-prior taint checks pass. | Automatic extraction/natural-language editing quality needs a model; deleted topics cannot be silently recreated from old sources. |
| §11 sidebar/workspace | Private Activity/Workspace/Tracking; group Workspace/Tracking; per-owner/per-chat selected tab and collapse persistence. Real upload/download, safe inline text preview and chat-specific stored paths with traversal/symlink checks. HTTP and browser scope checks pass. | Current preview supports safe text formats, not full native Office/PDF rendering; unsupported files download. Shared production storage must be configured. |
| §12 Activity and recovery | Only current private chat and its descendants. Canonical SDK runs/events, newest first, distinct Main/Subagent with exact direct parent_run_id. Safe event chain exposes allowed complete input/output, no hidden reasoning/credential fields. Every committed model/tool result remains in SDK; SSE deltas are ephemeral. Reopen reconstructs complete output without token animation/replay. Integration and SDK checks pass. | No distributed live-token broker; other workers reload canonical state by polling. This is intentional because deltas are disposable. |
| §13 SDK integration | SQLiteStore compatible with DatabaseSession; explicit versioned migration; durable input commands and cancellation; lease/fence ownership; child spawn/recover/notify/ack; canonical read APIs. Eleven focused checks include crash-gap and stale-lease recovery. | Uncertain external tool effects require reconciliation. Fencing cannot retract requests already received by a provider. |
| §14 data relationships | Product `tt_` tables carry business relationships and SDK references. SDK `agent_*` tables remain canonical execution state. Single-tenant-per-database identity is persisted and mismatched tenants fail closed. JWT scope checks and opaque local login sessions and secure-by-default development opt-in verified. | Row-level multi-tenant database hosting, production migration operations, backup/restore/PITR and RPO/RTO are not asserted. |
| §15 unresolved/deferred | Conditional Twin-to-Twin dialogue is now explicitly approved and implemented with durable limits. Private memory injection into groups and external-effect shortcuts remain unavailable. | See deferred list. |
| §16 sources | Authoritative five Markdown documents and runtime audit are retained. This record adds implementation evidence without rewriting historical design confirmations. | Older generated HTML/PDF/ER artifacts may describe an earlier design; use Markdown for current requirements. |

### Detailed document mapping

- `product-requirements.md` §§1–8 → design §§1–5,7–11; §§9–11 and 13 contain
  historical discussion, superseded where later confirmations exist.
- §§12,14–17 → FIFO, no backfill, eligibility versus execution, per-role queue
  cancellation, SDK-owned child communication (design §§4–6,13).
- §§18–19 → complete history, exact summaries, fixed admission grants (design §9).
- §§20–22 → three message kinds, Reply-for-me raw/final and prompts, Stop and
  independent approval drawer (design §§6–8).
- §§23–25 → automatic/model Tracking tool, index/detail chain, private/group
  permissions (design §10).
- §§26–28 → sidebar persistence, Failed/Retry, quoted completion, Activity scope,
  compact queue, typing, diagonal bubble/avatar toggle and disposable-token,
  complete-output recovery (design §§6–7,11–12).
- §29 and supplement 44 → proactive Twin sends, source-owner equivalence,
  needs_response, global causal lineage, six automatic acceptances, repetition
  guards and explicit continuation (design §4; database design §7).
- `interaction-rules.md` §§1–8 → routing/eligibility/manual controls; §§9–11 → FIFO
  and communication; §12 → history grants; §§13–14 → Reply-for-me/Stop/approvals;
  §15 → Tracking; §§16–18 → sidebar, retries, Activity and stream recovery.
- `requirements-supplement-2026-10-17.md` supplements 1–10 → read/manual/routing/
  FIFO/SDK boundary; 11–14 → history/summary; 15–23 → Reply-for-me and approvals;
  24–27 → Tracking; 28–39 → sidebar/retry/Activity/queue/visual controls;
  40–43 → owner attention, disposable tokens, SDK ownership, SQLite/PostgreSQL.

## Database design coverage and differences

| `database-design-v1.md` sections | Implemented mapping / evidence |
| --- | --- |
| 1–3: boundaries, conventions | One dedicated tenant per DB, `tt_` business schema and SDK canonical tables. No duplicated Runtime journal. SQLite local/PG driver and DDL deployment path. |
| 4–6: identity, messages, presence | Actor pair uniqueness, memberships with fixed history boundary, message kinds/references/read records, expiring local login sessions, independent device presence and immutable eligibility suppression. |
| 7–8: route/acceptance/FIFO | Candidate targets, durable grace deadline, dedupe and accepted request/attempt sequence; shared causal chains, atomic acceptance reservations and explicit allowance grants. Continue checks the displayed exhausted allowance version to prevent stale concurrent grants. Per-lane business order remains separate from SDK execution leases/fences. |
| 9: outputs and ephemeral streams | Product output references canonical SDK event IDs. Token text is never stored in a replay table. SSE completion reloads durable projections; heartbeat reconciles other workers. |
| 10: command/child communication | SDK `agent_commands` acceptance/inbox and `agent_session_leases`; child callback is acknowledged only after product FIFO acceptance or durable retention of a budget-paused child result. Idempotency and crash gap checked. |
| 11: approval projection | SDK immutable request/decision/operation facts back the scoped UI. Reconciliation enqueues one FIFO continuation; local reminder execution checks exact-action consent and current access. External effect adapters remain absent. |
| 12: side-effect safety | Product tools carry SDK stable operation IDs and durable idempotency records; original SDK tool-start/returned/completed/recovery boundaries retained. Unknown external outcomes are not automatically repeated. |
| 13: restore/version/fork | SDK same-run resume, snapshot/fork, explicit SQLite v1→v2 migration and PG additive migration004. Local restore/fence cases checked; PG/PITR not exercised. |
| 14: summary/context provenance | Context manifest, exact authorized summary coverage, complete post-summary tail and immutable provenance union through model-created/updated objects and child results. |
| 15: Tracking | Topic/update/revision/tombstone and source provenance, private/group permissions. Empty model citation lists cannot strip hidden-source taint. |
| 16: workspace/settings/reminders | Per-chat file metadata and storage, guarded paths; sidebar/reply prompt/mode settings; personal in-app reminders. |
| 17: reads/Activity | Canonical SDK feed with owner/private-root/descendant authorization and safe event projection. Main/child direct linkage, no global Twin run aggregation. |
| 18: invariants/failures | Targeted reproduction/fixes: delayed busy cannot resurrect old request or clear a newer lane; Stop cannot allow late output; source laundering denied; stale child lease eventually recovered. |
| 19: implementation split | Actual execution mechanics added in Agent Runtime, product/UI mapping in Twinteams. DB table names are concrete implementation choices rather than a verbatim copy of the design's example inventory. |

### Deliberate conservative choices

- Local identity is explicit test identity, with expiring HttpOnly/SameSite cookies.
  `X-Owner-Id` is rejected. Development mode defaults off, and the explicit loopback local launcher rejects a non-local environment and non-SQLite database settings. Production DEV login is unavailable; JWT tenant, issuer,
  audience, expiration and provisioned subject are required.
- Presence is a short-lived device lease; visible focused chat means viewing.
  Server TTL defaults to 35 seconds; automatic grace is 10 seconds.
- No implicit broadcasts for unknown routing. Automatic Twin dialogue follows
  the approved needs_response, presence, causal-budget and repetition guards.
  Accepted work is never stopped merely because an allowance was exhausted.
- Proactive sends run within accepted main/child work and existing memberships;
  they do not create an independent timer or external messaging channel.
  Private-origin context cannot be sent into another chat. Cross-group sends
  require every destination reader to retain access to the full source context.
- Read receipts represent humans only. Opening an old message cannot create a new
  automatic reply after leaving.
- Canceled main reply projections are withdrawn; committed proactive tool sends
  are not undone. Cancellation is not used to reverse completed external work.
  Child lifetime is separate from the parent main slot.
- Private SDK memory tools are installed only in private chats. Production memory
  needs an explicit persistent path. Group use of private memory/files and broad
  long-term authorization remain deferred as requested.
- The first deployment architecture is a dedicated database per tenant. It does
  not claim shared-DB RLS/multi-tenant isolation.
- Continuous-message merge policy, low-confidence route UI, membership removal /
  permission-change UI, approval expiration/history UX, broad notification delivery,
  exact native Teams Office/PDF preview behavior, and durable external-effect
  adapters remain unspecified or outside this implementation's installed capabilities.

## Checks actually run

The later user instruction permitted targeted checks for logic not directly
verifiable by clicking. No broad regression suite, lint suite or typecheck was run.
The Vite build command intentionally runs Vite only.

```sh
cd twinteams/backend
.venv/bin/python checks/product_acceptance.py
.venv/bin/python checks/runtime_acceptance.py
cd ../../agent-runtime
PYTHONPATH=src ../twinteams/backend/.venv/bin/python tests/test_durable_sqlite.py -v
```

Latest product/integration rerun at 18:27 UTC (SDK run at 18:20 UTC): product **10 groups**, integration **6 scenarios**,
SDK **11 checks**, all passed. The complete run took about six seconds. Test files
create isolated temporary SQLite DBs and do not reset the normal development DB.
The offline adapter appears only in explicit check/fixture modules.

Follow-up verification through 19:17 UTC: product **10 groups**, new
dialogue **12 groups**, bridge **5 groups**, Runtime integration **6 scenarios**,
and credential boundary **10 groups** passed (saved-key validation added at
19:14 UTC). The new dialogue checks cover shared
six-acceptance budgeting, concurrent acceptance and stale continuation rejection,
retry/manual distinction, durable paused child delivery, final-only routing,
proactive provenance/repetition/idempotency, Stop preserving committed sends,
actual additive legacy-schema migration, concurrent lineage backfill, and owner
view/read/reply suppression of budget-paused unaccepted candidates without
canceling accepted work or retained child results. The credential checks use only fake temporary files and fake HTTP transport. A Vite
build passed in an isolated output directory and was then copied to served assets;
the new main/fixture backend revision has not yet been restarted or browser-accepted.

- Product: secure defaults and explicit local/SQLite guards; local sessions/private ownership; all/fixed-seven-day/no-prior raw and
  derived ACL; source laundering rejection; workspace/traversal; viewing/no backfill;
  dedupe/FIFO/cancel permissions; delayed busy; concurrent Stop/output projection; monotonic single-bubble Reply-for-me projection.
- Integration: durable semantic output and safe Activity; multi-operation Reply-for-me raw/final and complete semantic history;
  failure releases next; real SDK cancellation and output withdrawal; child outlives
  main and returns through FIFO with exact parent Activity; reopen without duplicates.
- SDK: idempotent command/reopen/snapshot/fork; same-run interruption recovery;
  fencing; pre-start/cross-worker cancellation; additive migration; notification
  versus prompt FIFO; completion-before-notification gap; stale child lease takeover;
  notification DB/callback retry; shutdown of lease waiters without losing accepted work.
- Manual HTTP: unauthorized access401; private cross-owner403; spoofed owner header401;
  role-specific cancel403/200; history and quote/receipt/file restrictions; model
  unconfigured failure and Retry; production DEV route404; valid scoped test JWT200,
  cross-tenant JWT401. These JWT checks use local test credentials, not a live IdP.
- Normal browser: DEV-AAA/BBB/CCC, logout/session reload, message persistence across
  reload and backend restart, quoted reply, honest Failed/Retry, group creation,
  paired member admission, CCC no-prior visibility and private rejection, Tracking
  CRUD, reminder create/complete, Reply-for-me raw input not published on failure.
- Final reviewer independently logged into normal8010 as AAA, sent a unique message,
  reloaded to confirm persistence, and observed the explicit missing-provider failure.

### Browser artifacts

`design-artifacts/acceptance/` contains:

1. `01-login.png`
2. `02-private-persistence.png`
3. `03-tracking.png`
4. `04-ccc-history-boundary.png`
5. `05-denied-private.png`
6. `06-bbb-reminders.png` — captured before the reminder-header count fix; the
   final bundle now counts the selected tab rather than only pending items.

Final reviewer has additionally verified the fixture's persistent simulated-response marker before/after login, scoped approval Details (action/target/impact), Approve with last-item drawer auto-close, distinct Main/Subagent Activity cards, and child event-chain/direct-parent details. The reviewer also exercised a slow request, saw typing and Stop above the composer, queued the next request, stopped the first, verified partial output withdrawal and Stopped status, and watched the next quoted reply complete with queue/typing cleared. After the final restart the reviewer verified that complete historical outputs reload without token replay; the Reply-for-me avatar changes the composer, produces one Twin bubble with collapsible weak original above processed text and a hard diagonal lavender/mint background; switching to another chat leaves that chat mode off; group sidebar contains only Workspace/Tracking; and Add members exposes paired membership plus all/fixed-seven-day/no-prior options. These are actual UI checks against the isolated fixture, not live-model quality evidence. Do not treat deterministic fixture text as evidence of live provider generation.

## Remaining release gates

1. Supply provider configuration securely on the server, restart, and exercise
   real streaming, route selection, model tools, summary policy, subagents and
   provider interruption/recovery. Do not send credentials in chat.
2. Provision actual PostgreSQL, apply reviewed explicit migrations, run concurrent
   process/restart/lease and backup-restore acceptance against that server.
3. For production, wire the chosen OIDC browser flow to the existing JWT-verification
   API, configure persistent workspace/private-memory storage and operational
   health/backup controls. Local DEV accounts are not production authentication.
4. Before enabling real side effects, install authorized SDK adapters with
   idempotency/reconciliation and action-time permission checks. An approval record
   alone does not implement an external action.

These gates prevent a truthful claim of fully accepted production readiness.
They do not erase the local functional implementation and verified behavior above.
