# Focused owner questions

When a task needs information or an ordinary decision, the Twin can use
`ask_question` with one clear question and optionally two or three suggested
answers. A real question message and card appear in its owner's private chat,
with a link to the originating conversation. Free text is always available.
This is task input, not action approval or secret entry. Credentials continue
to use their dedicated form.

The tool returns immediately. Twins and subagents can continue independent
authorized work and yield; no worker waits for the owner. Answer the card or
send a human message explicitly replying to that question. Unquoted messages
remain new requests. When a fresh owner message clearly answers a pending
question, the Twin may associate its message ID using `answer_question`; the
server records the owner's actual text verbatim. It rejects stale messages,
someone else's message, an unrelated existing quote or a message already used
for another question. It never infers an answer from model-supplied text or
silently consumes a new task because only one question is pending.

One durable continuation returns the answer to the original task. Main Twins
retain their own SDK conversation; subagents resume their actual SDK session
and prior task/actions. A worker's final result still goes to its actual parent
for review. Pending questions and queued/running answer continuations keep the
existing scheduled-task pending-work checks active. Dismiss is explicit declined
input: the task learns the owner declined, then uses an authorized alternative
or reports its blocker without repeating that question.

Original requester, owner identity, causal chain, frozen execution configuration
and source permissions stay in force. An answer grants neither additional side
effects nor access to other private history. Its exact text is frozen as task
input on acceptance, like a prepared cross-chat statement. Later deleting the
human message does not cancel accepted work; task cancellation and reset remain
the stopping controls. Withdrawal before association is rejected. Original task
source revocation and cancellation are checked at runtime. Reset erases question
content and hard-stops pending answer carriers, including group-origin carriers,
without cancelling unrelated group tasks.

`ChatDetail.questions` supplies owner-only metadata keyed by `message_id`; group
views receive an empty array. Existing chat revisions and WebSocket hints refresh
cards without polling. Authenticated owner endpoints
`POST /api/questions/{id}/answer` (`{answer:string}`) and
`POST /api/questions/{id}/dismiss` return the question DTO directly. Replaying
the same answer returns the same continuation; a different second answer conflicts.

Provision the additive table with `python -m app.init_db` before API/worker startup.
[question-schema.sql](question-schema.sql) records the same DDL. Provisioning is
repeatable and preserves existing user data.

## Verification (2026-10-24)

The affected backend regression batch passed 156 tests. After preserving the
original unquoted answer message, the focused question suite passed 18 tests;
the final friendly Activity title check passed three actual SDK cases. New
modules pass Ruff, and TypeScript checking and the normal production build pass.

The isolated real-browser question scenario passed four cases; the proactivity
scenario passed two. Earlier failures remain recorded: a completed-request
lookup in the test helper, and a real cross-chat lock inversion fixed by keeping
question-origin authorization reads nonlocking while mutations retain sorted
chat locks. Quiet-hour due advancement is an explicit isolated fixture action,
not a claim to have waited four hours.

One bounded real `gpt-6-luna` browser scenario passed: the Twin asked two quiet-hour
choices, accepted the owner's selected answer in the original SDK session's new
run, then saved `low`, `22:00–08:00`, `Asia/Shanghai`, with the existing 14,400-second
review interval. The ledger records five HTTP attempts including one smoke call,
all successful, within the 16-attempt cap. Actual SDK receipts show one question
and one preference update, and API reload confirms persistence. Child continuation
is covered by offline actual-SDK tests, not this single real-model sample. All
isolated process, schema, Redis-prefix and temporary-directory cleanup passed.

Evidence remains outside the repository under
`/tmp/twinteams-ask-user-offline-20261024-r3`,
`/tmp/twinteams-proactivity-offline-20261024`, and
`/tmp/twinteams-interaction-live-20261024`. No production messages or model calls
were used for verification.
