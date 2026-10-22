# Real browser E2E

This suite drives the compiled product through Chromium against two real API
processes, PostgreSQL, Redis, the independent worker, and the durable SDK. Business
HTTP and WebSocket traffic is not intercepted. In the default offline suite, the
only substituted component is the model provider: deterministic responses choose
real tools and continuations. An explicit opt-in live scenario uses the real
`gpt-6-luna` provider and is reported separately below.

## Run

Use Python 3.13, Node 22.12 or newer, the backend virtual environment, and local
PostgreSQL/Redis configuration accepted by `checks/local_stack.py`. Install the
frontend dependencies and a browser once:

```sh
cd frontend
npm ci
npx playwright install chromium
cd ../backend
.venv/bin/python checks/browser_e2e.py --node "$(command -v node)" \
  --artifact-dir /tmp/twinteams-browser-e2e-results
```

To use an installed Chrome instead, set `TWINTEAMS_BROWSER_CHANNEL=chrome`
for the launcher command.

The launcher builds into a temporary directory, provisions a separate random
PostgreSQL schema and namespaced Redis keys, creates temporary workspaces/Memory/credentials,
and starts two APIs on unused loopback ports plus one worker. Production ports
8000/8001, production processes and `frontend/dist` are not used for browser
mutations. In default offline mode, configured provider credentials are not used;
the fixture disables outbound HTTP and uses a dummy provider key. Its scripted
shell commands operate on isolated test resources; this is not an operating-system
network sandbox.

The runner requires the launcher's nonce, namespace and explicit model-mode flag.
Both API health endpoints must report the same test-instance marker. An ordinary
application URL, a production port, a mismatched nonce, or an empty case selection
is rejected before login or mutation. The fixture's metadata inspector also
checks the nonce, temporary root and random-schema DSN. There is no test-only
business route in the product API.

For development, `--serve` keeps the isolated stack running until interrupted and
prints a path to `instance.json`. Pass those `TWINTEAMS_E2E_*` values to
`frontend/e2e/run.cjs`; use `TWINTEAMS_E2E_CASE` to select a named subset. A subset
report is explicitly not a full-suite pass. Use the fresh one-command run above
for final verification. Only one runner may mutate a shared test instance at a
time. A new runner ID keeps labels unique, but it does not make concurrent owner
profile, Memory or Reset mutations safe.

## Offline coverage and evidence

There are now 27 registered offline workflows: 18 conversation workflows and 9
personal workflows. The results below distinguish the current 27-case full run,
the earlier 26-case full run, and the filtered Tracking-scope development run. Each compound workflow counts once, regardless of its assertions.

Each named case records its result in `report.json`. Browser actions perform
ordinary product mutations; authenticated GETs check the resulting durable
projection. Where the public API intentionally omits metadata, the guarded
read-only fixture inspector checks that test schema directly.

| Area | Real actions and assertions |
| --- | --- |
| Authentication | Reject anonymous API access and invalid login, sign in through the UI, and sign out the real session. |
| Private send | Observe a sender-local echo, a canonical message and a worker reply; reload without duplicate messages. |
| Composer | Use Shift+Enter and the real text clipboard; send a Unicode payload above 30,000 characters through HTTP and verify full persisted content. |
| Markdown | Render human and worker rich text; keep HTML and unsafe links inert and do not fetch a Markdown image URL. |
| Direct conversations | Reuse the same participant pair, observe delivery from the second API, quote a message, add/remove a persisted reaction, and exercise the real online ten-second versus offline automatic-reply gate. |
| Group and Reply for me | Create/rename a group and publish a generated rewrite with the product's existing explicit raw-input disclosure. This raw input is not treated as a credential. |
| Attachments | Upload four file types and an image-only message; preview/download actual bytes, reload, deny another owner, and exercise DOM drag/drop and file-paste handlers. File paste uses a synthetic ClipboardEvent, not an OS file clipboard. |
| Workspace | Generate a file with the real SDK tool, list it through the UI, preview/download it, and lazily expand uploaded directories. |
| Request controls | Cancel a queued request, Stop a running SDK request, dismiss a persisted failure, and Retry a different failure without resurrecting its old notice. |
| Browser recovery | Take the actual browser context offline, reconnect, then reload during a running request and verify one committed result through the second API. |
| Subagents | Finish the parent turn before child completion, resume from a real subagent result, and inspect projected child Activity. |
| Group owner resources | Another participant invokes the target owner's actual shell workspace and Memory; verify owner binding and keep the Memory body out of the group output. |
| Private owner report | Send one authorized cross-chat report to the Twin's owner while keeping a private-conversation sentinel out of the shared chat. |
| Automatic budget | Observe six actual acceptances and a pause; explicit owner Continue extends that same chain to twelve. |
| History grants | Add a member with no-prior history through the UI, deny old messages/attachment bytes, preserve old-member access, and deliver a new message. |
| Worker restart | Gracefully restart only the owned isolated worker during an active SDK run, then verify checkpoint recovery and one committed result. This does not claim exactly-once provider execution or abrupt-crash coverage. |
| Mobile | At 390px, exercise keyboard Escape/focus, empty-send protection, multiline input and a persisted canonical send without horizontal overflow. |
| Profile and Runtime | Rename the Twin, choose an avatar preset, reload, observe the private-chat title from the second API, inspect worker-reported defaults, and distinguish another owner's profile. |
| Soul | Create/edit SOUL.md, retain a stale draft across a real second-tab edit, observe the version conflict, rebase explicitly, save and delete through the UI. |
| Git Memory | Create a versioned entry, search it, open two editors, preserve and rebase a conflicting draft, verify the saved content, and delete it. |
| Skills and owner resources | Discover the seeded on-disk skill, inspect details, disable/re-enable it, run a real SDK `load_skill`, and verify Activity. An SDK Memory write is read back from the owner's Git store and is unavailable to the other owner. |
| Manual Dream | Click Dream now after real private activity. A real source read, Memory write and publication create a fresh Dream key; conversation source references must name actual private messages; owner-configuration and SDK audit references are checked separately. A raw group-message marker is excluded. Existing manual entries are not overwritten to make the test pass. |
| Credentials | Add/delete a fake credential through the UI, verify metadata and owner isolation, request a credential through the actual tool, fill its form, and observe the continuation. A real bound shell must report credential presence without printing the value. Values must be absent from chat, API metadata, visible page text and localStorage. |
| Approvals | Inspect an exact action, approve it, verify exactly one reminder effect after refresh, then reject another request and verify no corresponding effect. |
| Tracking and reminders | Track a real message, verify the stored source ID, edit status, append an update, delete the topic, and verify its tombstone. Create a reminder, complete it, reload and reopen it. |
| Reset | Last in the suite: cancel confirmation without changing generation, then confirm a real reset. Private messages and Memory clear; group messages, SOUL.md, identity/avatar, enabled Skills and credential metadata remain. |
| Tracking scopes (new) | Aggregate own private/group topics with origin labels; edit, append and delete in the source group; check other-owner visibility; keep each group local; refresh only the small index without losing the private draft or focus. |

Presence follows the real device TTL. Closing a browser context stops heartbeats
but does not immediately remove its Redis presence. Development logout currently
removes the session/database presence but leaves the Redis device record until
expiry; this existing boundary was observed, not changed by the harness. Other
live devices also keep an owner online. The offline automatic-reply check waits
for natural TTL expiry after logout; it does not shorten the product TTL or
rewrite presence records to make the assertion pass. A hidden or unfocused live
device is still online, although it is not viewing the conversation.

The online-window workflow exposed an existing stale-presence fast path: a cached
offline result could accept a newly routed group reply before sampling the
recipient's current live device. Candidate creation now leaves that work for the
existing presence-refresh/candidate-processing cycle. The private-chat immediate
request path is unchanged. The final full-suite result below is the evidence for
the real online/offline workflow after this correction, rather than an adjusted
model response or a shortened test timeout.

Fixture skills and owner sentinel files are seeded before the browser starts.
Normal actions are not seeded through hidden product endpoints. Model commands
are deliberately explicit (`E2E SKILL`, `E2E MEMORY`, `E2E CREDENTIAL`,
`E2E APPROVAL`, and the conversation commands). A scripted success sentence alone
is not evidence of an effect: cases also check canonical messages, Activity,
stored resources or inspector metadata.

## Opt-in live continuous workflow

Run only after authorizing the existing configured provider credential for this
model. The command reads the established runtime configuration internally; do not
put a key on the command line or paste one into the browser:

```sh
cd backend
TWINTEAMS_BROWSER_CHANNEL=chrome .venv/bin/python checks/browser_e2e.py \
  --node "$(command -v node)" --live-model --scenario continuous-workflow \
  --live-max-attempts 48 --artifact-dir /tmp/twinteams-live-continuous-results
```

This is **one compound live workflow**, separate from the offline workflows.
`--live-model` and its dedicated scenario are both required; it cannot be enabled
by an offline case filter. All model responses and tool choices in this mode come
from the real provider. The existing key and configured endpoint/proxy are passed
only to the owned worker, not to the browser, API processes or public instance
file. The guard permits only `gpt-6-luna` requests to the configured Responses
endpoint. The implementation ceiling is **60 HTTP attempts**; the command above
lowers this run to **48**. The other bounds are **4,096 output tokens per request**
and **900 real seconds**. Smoke and retry attempts count; restarting
the test worker does not reset the budget. These are request/output/time bounds,
not a currency or total-input-token limit. The opt-in run may incur provider cost.

| Phase | Intended browser workflow and required evidence | Claim boundary |
| --- | --- | --- |
| Supplier setup | BBB enters randomized, project-specific quote/delivery facts in personal Memory through the ordinary UI, then personally posts a natural project-sharing authorization in the public collaboration group. The actual quote stays in BBB's Memory until the Twin retrieves it. | These supplier facts are deliberate UI setup, not autonomous Memory learning or an automatically seeded Tracking topic. Current live setup is identified as `business_setup.kind: public_owner_authorization`. |
| Continuous project | AAA uploads a real brief and gives a natural ongoing business goal without tool names. The Twin must read that attachment, contact BBB's Twin, obtain confirmed supplier facts, generate a downloadable plan, and autonomously create an active sourced Tracking topic. | A success sentence is insufficient: inspect owner/request-correlated tool calls, actual peer messages, durable topic/source IDs, visible UI, and downloaded bytes containing the randomized brief and supplier facts. |
| Shared change | BBB posts a randomized additional decision/blocker only into the real collaboration group. AAA's Twin must persist it through an actual Tracking write in that group, readable by BBB; the harness does not create the topic or copy the change into private input. | Correlate the source message, AAA's group request/tool operation and originating topic ID. Record any automatic private notice as an alternative source rather than suppressing it. |
| Later return | Log out and close the first browser, wait at least 60 actual monotonic seconds, restart only the isolated worker, and return in a new browser with a generic question containing no project facts. Read the exact persisted group topic from private context and surface its randomized unresolved decision in a distinct private run. | Record wall times, elapsed time, topic ID, run IDs and session IDs. The source group and private run use different SDK sessions, while the private chat may reuse its earlier session. Its authorized chat history and aggregate index are also available. This is neither an overnight test nor proof that Tracking alone caused recall. |

The first actual live attempt **failed: 0/1 workflows passed**, despite a
successful provider-connectivity smoke. Its original report is retained at
`/tmp/twinteams-continuous-live-20261023/report.json`, from
`2026-10-22T16:33:39.105Z` to `2026-10-22T16:41:41.708Z`. It made **32 real HTTP
attempts**, including smoke, using only `gpt-6-luna` under the 60-attempt budget.
Cleanup removed its schema/root and namespaced Redis keys and stopped its six
owned processes.

Actual SDK operations did read AAA's uploaded brief, write a draft plan, send
cross-chat messages, and create/update an active sourced Tracking topic. That is
partial evidence, **not a completed deliverable/collaboration pass**: the required
peer quote was not visible to AAA, so the workflow never reached download, shared
change or the 60-second return assertions. Read-only causality inspection found a
replied follow-up whose notification was blocked as `source_unavailable`: BBB's
group reply inherited `private-maya` history provenance and was unavailable to
AAA. The recorded guard returned 404. No sources were cleared to bypass that
boundary, and the failed report is not replaced by an assumed passing outcome.

The current live script uses a different ordinary-business setup: BBB gives
project-sharing consent in the collaboration group while the confirmed quote
remains in BBB's Git Memory. It is not a retry of the same private-history handoff;
the earlier candidate's recorded inputs remain with its original artifacts. Do
not treat the current default setup as a reproduction of that earlier input.
Successful Memory evidence must correlate BBB's expected project entry with a
real read or an exact-key search result, rather than any same-named tool call.

The second and final authorized campaign also **failed: 0/1 workflows passed**.
Its public-owner-authorization setup and original result are retained at
`/tmp/twinteams-continuous-live-public-20261023/report.json`. It ran from
`2026-10-22T16:48:55.792Z` to `2026-10-22T16:51:17.558Z` using Chrome
**155.0.8059.85**; the case lasted **140.934 seconds**. It made **28 real HTTP
attempts**, including smoke, all returning HTTP 200, against its hard **48** cap.
The two different input setups therefore used **60 actual attempts total**
(32 + 28), below the task's self-imposed aggregate ceiling of 80. HTTP success is not a
workflow pass. No third collaboration campaign was launched.

The test stopped when the real peer reply settled but its follow-up notification
was blocked. AAA had an actual uploaded-brief read, draft-file write and active
Tracking topic, but no authorized visible peer answer or owner report was found.
The failure screenshot shows AAA truthfully presenting the draft with supplier
quote/delivery still pending. The full downloaded-plan assertions, shared-group
randomized change, real 60-second gap, later private recall and different-session
comparison were **not executed and remain unproven by these live runs**.

The retained second-run inspector snapshot was captured at 17 calls, before its
28-call terminal state; it records a successful BBB private-history read. The
terminal private-source manifest/authorization diagnosis was not captured before
cleanup. Thus the precise first-run provenance explanation must not be promoted
to a fully verified second-run causal diagnosis. Its observed terminal fact is
the blocked follow-up, not provider transport failure. Cleanup again removed its
schema/root, stopped all six owned processes and left zero namespaced Redis keys;
the infrastructure's configured-key artifact audit reported zero matches.

These two preserved failures expose a real collaboration/visibility gap. They do
not negate the independent Tracking permission/UI tests, nor do those offline
passes establish that the continuous live workflow succeeds. No production
publication permission or source boundary was relaxed to obtain a pass.

## Independent live Tracking-resumption check

This separate capability check has a **20-attempt** limit. It is not another
attempt at the failed peer-collaboration scene and does not change either
preserved collaboration result. To reproduce it in a new isolated environment,
after authorizing the configured credential:

```sh
cd backend
TWINTEAMS_BROWSER_CHANNEL=chrome .venv/bin/python checks/browser_e2e.py \
  --node "$(command -v node)" --live-model --scenario tracking-resumption \
  --live-max-attempts 20 --artifact-dir /tmp/twinteams-live-tracking-results
```

The actual compound check **failed: 0/1 workflows passed**. The original report
is `/tmp/twinteams-tracking-resumption-live-20261023/report.json`, from
`2026-10-22T17:05:00.967Z` to `2026-10-22T17:06:55.449Z`, using Chrome
**155.0.8059.85**. The case lasted **113.631 seconds** and used **8 real HTTP
attempts**, including smoke, all returning HTTP 200. Across the three isolated
campaigns this is **68 attempts** (32 + 28 + 8), below the task's self-imposed aggregate
ceiling of 80. Cleanup stopped all eight owned process instances, removed the
test schema/root and left zero namespaced Redis keys.

Two phases reached their actual assertions before the later test-driver failure:

- BBB uploaded a new randomized brief through the UI. Actual SDK `read`, `write`
  and `create_tracking` operations were correlated to BBB's request and owner.
  The persisted active topic `71980efc-c999-40c2-a2d0-f1fc40c76c17` referenced
  the actual source message. The browser downloaded
  `Harbor-b01e754a-preparation.md`; its retained bytes contain the randomized
  project, **41** samples, the blue/green packaging choice and its unapproved
  status. The topic was Agent-created, not seeded by the harness.
- BBB logged out, closed the browser, waited **60,721.68 actual monotonic
  milliseconds**, and returned in a new browser after a graceful restart of the
  owned worker. A generic question contained no project facts. A distinct run
  actually called `get_tracking` for the same topic and surfaced the pending
  blue/green decision. Both runs used the **same private SDK session**; the
  authorized private history and original brief were also available. This is
  evidence of a real timed return, not exclusive Tracking causality, a new SDK
  session, overnight behavior or unattended notification.

The subsequent group setup created a normal human-authored topic, then the
script tried to click the Tracking tab while that topic's detail drawer was
still open. The drawer intercepted the click until the 12-second timeout.
The failure screenshot shows the existing detail view with no updates. This is
a test interaction failure, not evidence that the model or Tracking permission
scope rejected the requested operation. The randomized update, AAA's first
private question, exact originating-group `get_tracking` read and exclusion of
other fact channels **were not executed**. They were unverified by that live
campaign; the later isolated third-phase check below is separate.

The retained `live-final-inspect.json`, download, preparation/return screenshots
and failed report distinguish these completed phase assertions from the failed
compound result. The later helper correction reuses the already-open drawer;
it does not change the original report or turn this compound case into a pass.
The collaboration failures remain unresolved, and the complete collaborative
release workflow has no passing live result.

## Isolated first-private group Tracking result

The previously unexecuted third phase has its own fresh isolated check. It does
not repeat supplier collaboration, autonomous topic creation or the timed-return
phases. The group topic and its update are deliberately **human-created through
the ordinary UI**, not Agent-created. Reproduce this narrower check with:

```sh
cd backend
TWINTEAMS_BROWSER_CHANNEL=chrome .venv/bin/python checks/browser_e2e.py \
  --node "$(command -v node)" --live-model --scenario first-private-group-tracking \
  --live-max-attempts 12 --artifact-dir /tmp/twinteams-live-first-private-results
```

This check **passed 1/1, 0 failed**, in **13.136 seconds**, using Chrome
**155.0.8059.85**. Its report is
`/tmp/twinteams-first-private-group-tracking-live-20261023/report.json`, from
`2026-10-22T17:12:34.359Z` to `2026-10-22T17:12:48.321Z`. It used **4 real HTTP
attempts**, including smoke, under its hard 12-attempt cap. All four campaigns
therefore used **72 attempts** (32 + 28 + 8 + 4), within this task's self-imposed
aggregate ceiling of 80. No further live run was required.

Independent inspection correlated the actual first `private-alex` request,
`owner-alex` SDK run and successful `get_tracking` call with the exact source
`group-alex-maya` and persisted topic
`cef67c0b-08a3-484a-91c8-de1b312b80dc`. The topic retained its real group-message
source and two human-authored revisions. Its private context manifest contains
the original group topic inside a `chat_context` envelope. Before the question,
AAA had **zero private messages and zero prior private requests**. The generic
question supplied no project facts or tool names.

The randomized `GROUP-6cfcce50` fact existed only in the topic's detailed update,
not chat bodies or the lightweight index. The actual `get_tracking` result
contained that marker; the only other successful tool was `history`, whose
result did not contain it. The visible answer and
`tracking-first-private-aggregate.png` correctly identify the moisture-proof
packaging decision, the randomized **62 yuan** fee and its unapproved status.
This is evidence of authorized group Tracking details being retrieved and used
in the owner's first private run, rather than a restatement of earlier private
chat. It is not evidence that an Agent created this group topic, that a group
SDK session migrated to private, or that every model attempt will choose the
same tools.

`cleanup.json` records all seven owned processes stopped, the schema/root
removed and zero namespaced Redis keys. The original three failed reports are
unchanged. These live runs tested candidate working trees based on
`18812e80767abeb4527351b8e30f49ec4ac1101e`; the report's HEAD is not a claim that
the uncommitted candidate equals that committed revision. The overall result is
**passing scope/first-private retrieval and completed timed-return phase
assertions, with the full peer-collaboration workflow still failing**.

## Requested Tracking scope

The scope is explicit: private chats aggregate authorized topics and groups stay
local. Twelve independent disposable-PostgreSQL/API cases in
`backend/tests/test_tracking_scope_acceptance.py` passed (an initial eleven-case
run plus the focused soft-delete case); these are permission checks, not live
model or browser workflow counts. With a local `TWINTEAMS_TEST_DB_URL` configured,
rerun them from `backend/` using
`.venv/bin/python -m pytest tests/test_tracking_scope_acceptance.py -q`.

| Request origin | Tracking projection | Required boundaries |
| --- | --- | --- |
| Owner's private chat | Its own private topics plus topics from all groups the owner currently belongs to and can read. | Never another owner's private topics. Check each group's membership, history grant, source messages and provenance independently; exclude deleted/unavailable topics. |
| Group A | Only Group A's topics. | Exclude the owner's private topics and Group B's topics from context/index, even if the owner belongs to both groups. Explicit Tracking tool arguments targeting another chat are rejected. |
| Private continuation edits a group topic | Read/update the existing topic in its original group. | Preserve its ID and original `chat_id`, expose its origin title, increment the original revision, and avoid creating a private copy or writing source references under the wrong chat. |
| Grants change after discovery | Recheck before reading details, writing or projecting results. | A previous aggregate result cannot preserve access after leaving a group, withdrawal, reset or source revocation. |

The focused matrix also covers never-joined groups, exited groups, soft deletion,
`no_prior` and seven-day history grants, topics with no explicit source IDs,
withdrawn sources, nested provenance, and owner isolation. Soft-deleted topics
are unavailable to fresh index/detail/write operations; a previously legal frozen
manifest remains a historical snapshot. Membership/source revocation still fails
closed. Cross-chat references
must retain their original chat boundary; aggregating a topic does not move its
source IDs into the private chat or grant a new audience access.


The added real-browser scope workflow passed **1/1** in **6.044 seconds**, using
Chrome **155.0.8059.85**, at `2026-10-22T16:22:57.330Z`–
`2026-10-22T16:23:04.160Z` (2026-10-23 in Asia/Shanghai). Its report is
`/tmp/twinteams-tracking-scope-offline/browser/report.json`; its group screenshot
is `tracking-group-scope.png` in that directory. Independent review confirmed
`full_suite: false`, one selected workflow out of 27 registered, real API/socket
traffic, source-group edits and no whole-private-chat/bootstrap reload on a
Tracking-only hint. This is not a full 27-workflow pass.

A pre-existing publication boundary remains: an Agent editing a group topic from
private context inherits that private runtime's provenance. Other group members
may then be unable to read the changed topic; preserving its ID/origin does not
prove everyone can see its new revision. Ordinary UI edits do not have that runtime
manifest. This suite must not erase sources to bypass the difference or claim
universal shared visibility without checking the other participants.

The live return phase can show a persisted group topic being reused from the
owner's private context. It must still record the actual SDK session/run IDs and
source topic, rather than treating a browser login or worker restart as a new SDK
session. The earlier proposed new-group automatic cross-chat-discovery test is
superseded by this scope and is not counted as passed coverage. Tracking and
in-app reminders do not by themselves wake an idle Twin at an arbitrary future
time; event continuations and explicit reply-followups are separate mechanisms.

## Offline results and artifacts

The command exits nonzero on a failed or incomplete run. `report.json` includes
the instance, selected/full-suite status, named cases, durations, observations and
failure artifacts. A failure is not silently retried into a pass. After the runner
finishes, the launcher stops only its own processes, removes its schemas and Redis
keys, and deletes temporary execution/Memory/secret directories. The artifact
directory is retained for diagnosis.

Normal failed cases retain screenshots and Playwright traces. Credential cases
are marked sensitive: tracing, video and failure screenshots are disabled, and
the fake-secret marker is redacted from runner errors/reports. Reports contain
metadata and assertions, not provider keys or saved credential values. Do not
commit generated logs, traces, reports, instance files or temporary state.

Latest full offline verification on **2026-10-23 (Asia/Shanghai)**: **27/27
workflows passed, 0 failed**, with `full_suite: true`, 27 registered/executed cases
and no unrun cases. The browser run used installed Chrome **155.0.8059.85** and
lasted **249.927 seconds**, from `2026-10-22T16:29:04.998Z` to
`2026-10-22T16:33:14.925Z`. Results are retained in
`/tmp/twinteams-tracking-offline-full-20261023-chrome/`.

Independent review checked its report, the private union's BBB/CCC origin labels
in `tracking-private-union.png`, the local-only group in
`tracking-group-scope.png`, and the source edit/update/delete assertions. The
credential case retained no trace/screenshot; final text artifacts contain no raw
fake credential value. `cleanup.json` records seven owned processes stopped,
its schema and temporary root removed, and zero remaining namespaced Redis keys.
This tested the candidate working tree based on HEAD
`18812e80767abeb4527351b8e30f49ec4ac1101e`, not a final committed revision of the
uncommitted changes. The full backend regression run separately passed **281
checks in 196.16 seconds**, including the independent scope checks; those are not
additional browser workflows.

An initial launch without an installed bundled browser stopped before executing
any browser case. Its separate directory,
`/tmp/twinteams-tracking-offline-full-20261023/`, is retained; the successful full
run above explicitly selected installed Chrome. No failed business case was
retried or omitted from that passing full report.

Earlier baseline verification on **2026-10-22**: **26/26 workflows passed, 0 failed**, with
`full_suite: true`, 26 registered/executed cases, and no unrun cases. Chromium
through the installed Chrome channel reported **155.0.8059.85**. The browser run
lasted **236.737 seconds**, from `2026-10-22T14:48:08.199Z` to
`2026-10-22T14:52:04.936Z`; this timing excludes build/provisioning and cleanup.

The retained local result directory is
`/tmp/twinteams-browser-e2e-final-20261022/`: `report.json`, `cleanup.json`,
`verification.json`, `private-success.png`, and `mobile-success.png`. Independent
review confirmed the full case count, desktop/mobile layout, no credential-case
trace or screenshot, and no raw fake-secret value in the final text artifacts.
Cleanup verification recorded all seven owned processes stopped, the temporary
schema/root removed, and zero remaining namespaced Redis keys. Production
processes were unchanged.

This ran the **candidate working tree**, including the new suite and the
stale-presence correction, before committing them. The report's `source_commit`
(`7b7c719b6e0528ef3dd866061547a27a8e3a342d`) identifies the then-current HEAD,
not a final tested commit containing those uncommitted changes. The original
report is retained unchanged.

In this single local scripted run, the online fallback took 10.678 seconds and
known-offline fallback took 0.641 seconds; the recipient's real Redis presence
was inspected before each send. These are workflow observations, not live-model
latency measurements. The full run also verified the 42,030-character Unicode
message and Dream after that accumulated history.

Two separate runner guard probes rejected a production-port URL and a wrong
nonce, each with the expected exit code 1 and zero browser cases. Their reports
are in `/tmp/twinteams-e2e-guard-production-port/` and
`/tmp/twinteams-e2e-guard-wrong-nonce/`, separate from the passing full run.
The eight backend guard tests and focused candidate/private-send regressions are
supplemental checks, not additional browser workflows.

## Limits

The verified offline run proves browser-to-product-to-SDK plumbing for the
assertions that ran. It does not prove a live model independently chooses tools,
follows prompts, reasons correctly, or produces useful answers, and it does not
call a real provider or an external service. An opt-in live run can establish only
the actual observed workflow for its inputs and model version, not deterministic
behavior on future runs. Production identity/OIDC, public Internet/TLS behavior, arbitrary
external effects, crash/failover stress, and an exhaustive browser/device matrix
remain outside this suite. The older disposable-PG and intercepted-API checks
still cover narrower fault injection and protocol boundaries; they are not
reported as real browser E2E results.
