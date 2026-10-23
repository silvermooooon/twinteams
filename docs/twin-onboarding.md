# Optional first meeting

When an owner first opens a genuinely empty private Twin conversation, the
server saves one short welcome as an ordinary system message. It offers examples
and one optional starting question. The normal composer stays available: the
owner can give a real task immediately, or use **Skip introduction**. There is
no required questionnaire or naming step. Existing conversations and group chats
do not receive this opening.

Progress belongs to the owner and survives reloads, devices and content resets.
Resetting private history does not repeat the introduction. Skipping closes the
optional guidance without deleting its persisted message. A first real input can
finish the introduction through the runtime's `finish_onboarding` tool; a second
ordinary input also closes it. This progress flag does not complete the owner's
actual task or store a profile. Explicit durable preferences use the existing
Memory tools.

`TwinOnboarding.tsx` starts or skips the server-owned lifecycle and displays only
a compact optional control. It does not manufacture chat bubbles, move focus,
block sends, poll, or keep onboarding state in browser storage. Existing chat
notifications/reloads expose the canonical welcome. Account/chat/reset scope
changes abort stale requests. `TWINTEAMS_ONBOARDING_ENABLED` controls the backend
feature; disabled mode creates no onboarding state or welcome.

The separate `personal-onboarding` offline browser scenario uses fresh isolated
owners and real product APIs. It covers the initial canonical message, reload
and two-API uniqueness, optional skip with draft/focus preservation, natural task
input, account/group boundaries, existing-history exclusion and reset behavior.
Its intentional second-input fallback does not claim that a live model chose
`finish_onboarding`. The ordinary regression scenario keeps onboarding disabled
so its established fixtures remain independent.

```sh
cd backend
TWINTEAMS_BROWSER_CHANNEL=chrome .venv/bin/python checks/browser_e2e.py \
  --scenario personal-onboarding --node /usr/local/n/versions/node/22.14.0/bin/node \
  --artifact-dir /tmp/twinteams-personal-onboarding-offline
```

Provider-backed first-turn behavior and durable preference storage require their
own separately reported live evidence; they are not established by this UI
lifecycle scenario.

## Verified offline browser result

The dedicated scenario passed **3/3 cases, 0 failed** on 2026-10-23, using Chrome
**155.0.8059.85**. Its report spans `2026-10-23T15:27:08.014Z` to
`2026-10-23T15:27:18.587Z`; the three case durations total 4.999 seconds.
`full_suite: true` covers this separate three-case scenario, not the ordinary
40-case regression suite. The retained report is
`/tmp/twinteams-personal-onboarding-offline-20261023/report.json`.

The first opening was one persisted `system` message, remained unique across
reload and two APIs, and created no SDK Activity run. Remote skip retained the
unsent draft and composer focus. A real first task reached canonical history;
completion used the explicit second-input fallback rather than an asserted model
tool choice. Owner switching, reset persistence, group isolation, foreign-private
denial and existing-history preservation passed.

Visual inspection of `onboarding-persisted-welcome.png` confirms the welcome in
its ordinary message bubble, a compact skip icon above the available composer,
and no wizard or naming gate. `onboarding-group-scope.png` shows the normal group
view without introduction controls. Cleanup removed the disposable schema/root,
stopped all six owned processes and left zero namespaced Redis keys. The report's
`3fccef3eb313019013f8a7c460df5df2cd1854ce` identifies the candidate's base commit;
it is not a final delivery commit containing the uncommitted feature.

## Verification recorded on 2026-10-23

The isolated backend target set passed **72 tests in 18.14 seconds**, including
16 first-meeting cases for persistence/concurrency, rollback, disablement,
owner/role scope, one-shot task handling, reset and frozen tool declarations.
The first test run had one incorrect fixture-name expectation (`Alex` instead
of the account's actual display name `AAA`); the implementation used the correct
existing name. The corrected target set passed. A dedicated real-browser run
passed **3/3** at `/tmp/twinteams-personal-onboarding-offline-20261023`, covering
two API replicas, reload, optional skip, draft focus, group/owner isolation,
ordinary task input, reset and pre-existing history. Its scripted offline model
is not evidence of autonomous tool choice. The default 40-case regression suite
was not repeated; onboarding is disabled in that older fixture unless the
separate `--scenario personal-onboarding` is selected.

A separate focused live API check passed **3/3 synthetic owner turns** at
`/tmp/twinteams-personal-onboarding-live-20261023`. The actual Responses ledger
contains **9 HTTP 200 requests (one smoke and eight workflow requests)**, all
`gpt-6-luna`, within a hard 16-attempt/4096-output-token limit. The first owner
introduced a durable reporting preference; actual `memory_read`/`memory_write`
tool receipts and the normal Memory API readback retained the three requested
headings. Another owner requested a draft immediately and received the requested
project facts without an introductory interview or a claimed send. A third
owner explicitly skipped, and the real lifecycle tool persisted `skipped`.
`live-api-results.json` retains the synthetic inputs, complete replies and Memory
readback; `live-final-inspect.json` retains matching request/run/tool metadata.
This is one small model sample, not a guarantee of every future response.

The live check reused the isolated launcher in `--serve` mode and was stopped
with SIGTERM after its focused client passed; the resulting launcher
`CancelledError` is the controlled shutdown, not a model-test failure. Both
browser and live stacks removed their own schema, temporary files, Redis prefix
and processes. No production owner content, Memory or model calls were used.
