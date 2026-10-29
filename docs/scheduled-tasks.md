# Scheduled tasks

Tell your Twin the outcome and timing in your own words; you do not need to name
a feature or tool. A request for later or recurring work authorizes the Twin to
save a suitable schedule, with clarification only when the goal or timing is
materially missing. Ordinary questions do not authorize new ongoing commitments.
You can review or adjust saved work in **Account → My Twin → Scheduled tasks**.
A personal task contains a name, instructions, a time zone and a rule. Choose
**Once**, **Daily**, **Weekly** (select weekdays), or **Interval** (at least
60 seconds). Save the task to enable future runs. Times shown on each card and
in its history use that task's IANA time zone. Changing the time zone in a
one-time task interprets the entered date and time in the newly selected zone.
A daylight saving gap moves forward; a repeated local time runs once.

The built-in **System review** normally runs every four hours. Its instructions
are managed by TwinTeams. You can change its interval or pause it. It reviews
ongoing work and pending items. **Completed quietly** means the deterministic
precheck found no work to review and skipped the model call. A model run that
finishes without a chat message still shows **Completed**. Review errors remain
visible in the task and its history.

You can also tell your Twin how proactive to be in your private conversation.
**Low** limits autonomous notices to important results, risks, blockers,
approaching deadlines and decisions; **Normal** balances meaningful changes;
**High** advances useful next steps already authorized. **Off** pauses only the
built-in review. These levels do not change the existing four-hour interval,
create commitments or pause explicit personal tasks, reminders or Dream.
The Twin saves changes with `update_proactivity` and can read them with
`get_proactivity`; it confirms only after saving succeeds.

Optional quiet hours have start/end wall-clock times and an explicit IANA time
zone. Automatic system reviews due inside that window move to its end using the
same PostgreSQL next-run time and Redis index. Overnight windows and DST are
supported. The window applies to admission: already accepted work may finish,
and **Run now**, explicit schedules, answers, approvals, credentials and worker
results continue normally. It is not a global desktop-notification mute or a
queue of held messages. Clearing quiet hours or changing level keeps an existing
planned review time; only reenabling an off review calculates a new next run.
These explicit preferences survive private content reset.

**Pause future runs** prevents later occurrences. An already queued or running
request continues. While a task is waiting for delegated work or an approval,
its card says **Waiting for pending work** and prevents a duplicate run or
deletion. **Stop active run** cancels the current queued or running request
through the normal Twin cancellation control; it appears only when such a
request exists. Pausing future runs does not settle pending approvals or
delegated work. **Run now** requests one execution without
resuming a paused schedule. The task shows its next run and last result;
**Run history** shows the latest 20 requests, their statuses, timestamps and any
errors. Background work does not add queued/thinking or failure banners to chat.
Approvals and credential requests still appear when your input is needed, and
runs remain in Activity. Personal tasks can be deleted once their pending work
has settled. System tasks cannot be deleted.

Tasks execute in your private Duet context and retain normal tool permissions,
source visibility and approval requirements. A schedule does not grant access to
another person's private data. Peer coordination uses a separate active approved Duet conversation; a task may request one and return pending without polling. Approval once or directed always grants permit the connection only. Revocation closes dependent sessions; actual peer messages share the original six-send chain and only human Continue extends it. Scheduled work never wakes or publishes a Duet in human chats. In-app **Reminders** remain a separate list of
human-facing reminders. Resetting Twin content pauses personal scheduled tasks;
review the retained instructions before explicitly resuming them.

## Architecture and boundaries

PostgreSQL owns task rules, next-run times and the durable occurrence/request
journal. Redis holds a disposable due-time index. Worker reconciliation repairs
missed index hints from PostgreSQL in bounded batches (100 by default), with a
default 300-second compensation interval. This interval is a reconciliation
setting, not an execution deadline during worker outages or backlog. Accepted
occurrences use independent task sessions through the existing SDK execution path. Foreground messages do not wait for an unrelated occurrence; background admission leaves foreground capacity. Missed
recurring times coalesce into one occurrence; the same task cannot start another
occurrence while its current request or delegated/pending work is unresolved.
Durable occurrence keys prevent duplicate acceptance, but do not promise
exactly-once external side effects; tool and service idempotency still matters.

The readable migration is [twin-scheduled-tasks-schema.sql](twin-scheduled-tasks-schema.sql).
The four schedule settings are documented in [.env.example](../backend/.env.example):
`TWINTEAMS_SCHEDULED_TASK_MIN_INTERVAL_SECONDS` (60),
`TWINTEAMS_SCHEDULED_TASK_MAX_PER_OWNER` (100),
`TWINTEAMS_SCHEDULED_TASK_BATCH_SIZE` (100), and
`TWINTEAMS_SCHEDULED_TASK_RECONCILE_SECONDS` (300).

## Browser verification

The real browser suite registers three `scheduled tasks / …` cases for rule
editing/time-zone conversion/persistence, system restrictions and cross-API
refresh without losing draft focus, and run-now/history/failure/cancellation.
They use the existing isolated PostgreSQL/Redis/API/worker/SDK stack with its
explicitly deterministic **offline** provider. No business API/WebSocket JSON is
intercepted, and no browser clocks are faked. SLOW and FAIL_ONCE are intentional
provider fault inputs for lifecycle verification; their output is not evidence
of real model reasoning or autonomous scheduling tool choice.

Run these cases through the normal browser launcher with its environment filter:

```sh
cd backend
TWINTEAMS_BROWSER_CHANNEL=chrome TWINTEAMS_E2E_CASE='^scheduled tasks /' \
  .venv/bin/python checks/browser_e2e.py --node /usr/local/n/versions/node/22.14.0/bin/node \
  --artifact-dir /tmp/twinteams-scheduled-tasks-offline
```

A passing offline result does not establish that a live model creates or
executes the desired natural-language workflow. Live results must be reported
separately with their actual provider attempts and artifacts.

### Verification recorded on 2026-10-23

The implementation candidate was tested on top of TwinTeams `42cf825` with
Chrome `155.0.8059.85`; this base SHA is not the final feature commit.
The disposable PostgreSQL backend suite passed **335 tests in 228.32 seconds**.
The browser evidence covers **40 distinct offline workflows**, across these
preserved runs rather than a single all-green 40-case run:

| Artifact directory under `/tmp/` | Result |
| --- | --- |
| `twinteams-scheduled-offline-full-20261023` | 35 passed, then the first schedule case failed on its select's accessible label; four cases were not run. |
| `twinteams-scheduled-offline-targeted-20261023` | CRUD passed; the next case found the same label issue on prefilled instructions. |
| `twinteams-scheduled-offline-targeted-final-20261023` | CRUD passed with explicit accessible names on all editor fields. The next case's refresh assertion counted an independent worker-status profile refresh. |
| `twinteams-scheduled-offline-remaining-20261023` | All four remaining cases passed: system restrictions/live refresh, lifecycle/history/quiet background status, automatic due dispatch, and Reset. |

The label defects were fixed in the form. The refresh test now passively records
real WebSocket frames and permits profile refreshes only when attributable to an
independent worker-status frame; it still requires zero bootstrap refreshes and
bounded schedule-index refreshes. All original failure reports remain intact.
The due-dispatch case uses an interval rule and changes only the disposable
row's `next_run_at` and dirty-index flag. It verifies one actual worker occurrence,
its output, and a future next-run time without clicking **Run now**.

The separate live scenario passed **1/1 in 54.366 seconds** at
`/tmp/twinteams-scheduled-live-20261023`. Its hard ceiling was 40 HTTP attempts
and 4096 output tokens per request. The ledger records **10 attempts, all HTTP
200: one connection smoke request and nine workflow requests**, all using
`gpt-6-luna` through the real Responses provider. The model:

- Created the requested daily 09:30 `Asia/Shanghai` task with the actual schedule tool; the browser verified its card.
- Executed an honestly advanced due occurrence, read the real group Tracking details, and reported the synthetic quantity of 222 from a Tracking update.
- Reported a new, unapproved 62-unit expense decision once during system review.
- Completed another review of the ongoing work with two actual model requests and no repeated owner message.

The project and updates were human test setup through the real authorized API,
not agent-created Tracking. Only due metadata in the isolated fixture was
advanced; no four-hour elapsed wait is claimed. This scenario demonstrates group
Tracking access, not a live conversation with another Twin or external effects.
It is one live sample in the existing owner-private SDK session, not evidence of
all scheduling rules or fresh-session memory retrieval.
`live-final-inspect.json` retains the terminal request/run/tool metadata and
`live-budget.json` retains the bounded HTTP ledger. The successful-run artifact
retains message IDs, output counts and the executed body assertions, but not the
complete final chat texts; independent inspection must not claim a retained
full-response review. In the original report, `setup.update_id` and
`material_change.update_id` are the **updated topic IDs** returned by the API,
not Tracking update-row IDs. The test source now calls these fields
`updated_topic_id`; the original report was not rewritten.

Every campaign removed its disposable schema, Redis namespace, temporary root
and owned processes. No production messages, memories, schedules or model calls
were used. Reproduce the dedicated live check only with explicit authorization:

```sh
cd backend
TWINTEAMS_BROWSER_CHANNEL=chrome .venv/bin/python checks/browser_e2e.py \
  --live-model --scenario scheduled-task-workflow --live-max-attempts 40 \
  --artifact-dir /tmp/twinteams-scheduled-live-new
```
