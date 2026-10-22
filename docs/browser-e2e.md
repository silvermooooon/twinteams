# Real browser E2E

This suite drives the compiled product through Chromium against two real API
processes, PostgreSQL, Redis, the independent worker, and the durable SDK. Business
HTTP and WebSocket traffic is not intercepted. The only substituted component is
the model provider: deterministic responses choose real tools and continuations.

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
8000/8001, production processes, configured provider credentials, and
`frontend/dist` are not used for browser mutations. The fixture disables outbound
HTTP and uses a dummy provider key. Its scripted shell commands operate on
isolated test resources; this is not an operating-system network sandbox.

The runner requires the launcher's nonce, namespace and deterministic-model flag.
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

## Coverage and evidence

There are 26 registered workflows: 17 conversation workflows and 9 personal
workflows. Each compound workflow counts once, regardless of its assertions.

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

## Results and artifacts

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

Verified on **2026-10-22**: **26/26 workflows passed, 0 failed**, with
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

This proves browser-to-product-to-SDK plumbing for the assertions that ran. It
does not prove a live model independently chooses tools, follows prompts, reasons
correctly, or produces useful answers. It does not call a real provider or an
external service. Production identity/OIDC, public Internet/TLS behavior, arbitrary
external effects, crash/failover stress, and an exhaustive browser/device matrix
remain outside this suite. The older disposable-PG and intercepted-API checks
still cover narrower fault injection and protocol boundaries; they are not
reported as real browser E2E results.
