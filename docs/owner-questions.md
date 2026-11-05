# Focused owner questions

When a task needs an ordinary decision, the Duet uses `ask_question` with one
clear question and two or three actionable alternatives where they are known.
For missing information, it can ask an open-ended question without suggestions.
A real question message and card appear in its owner's private chat, with a link
to the originating conversation. An owner can select a suggestion directly or
reply in the main chat composer. Open-ended and custom answers use that same
composer; ordinary cards have no separate answer field.
This is task input, not action approval or secret entry. Credentials continue
to use their dedicated form.

The tool returns immediately. Twins and subagents can continue independent
authorized work and yield; no worker waits for the owner. Quoting a question
explicitly associates a composer answer, but a quote is not required. Unquoted
messages start as new requests. When a fresh owner message clearly answers a
pending question, the Twin may associate its message ID using `answer_question`; the
server records the owner's actual text verbatim. It rejects stale messages,
someone else's message, an unrelated existing quote or a message already used
for another question. It never infers an answer from model-supplied text or
silently consumes a new task because only one question is pending. The original
task resumes separately; the associating turn must not duplicate its work.

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
content and hard-stops pending answer carriers, including Duet-thread-origin carriers,
without cancelling unrelated Duet tasks.

`ChatDetail.questions` supplies owner-only metadata keyed by `message_id`; Duet-thread
views receive an empty array. Existing chat revisions and WebSocket hints refresh
cards without polling. Authenticated owner endpoints
`POST /api/questions/{id}/answer` (`{answer:string, selected_option?:boolean}`) and
`POST /api/questions/{id}/dismiss` return the question DTO directly. Replaying
the same answer returns the same continuation; a different second answer conflicts.

Provision the table before API/worker startup. Existing installations may apply
[question-schema.sql](question-schema.sql) to add the nullable `owner_reply` JSON
column; existing ordinary questions keep their original answers and no send action.
Fresh installations provision it through `python -m app.init_db`.

## Confirming a message sent as the owner

A reply prepared by `reply_as_owner` uses the same private question card with a
server-saved draft. Without a `reference_message_id`, an explicit `chat_id`
selects an authorized human conversation and the result is an ordinary message
with no quote. A `reference_message_id` instead selects the owner's private
reference command, which fixes the original message and destination; an optional
`chat_id` must match that destination. The card shows the destination, audience,
exact text and attachments, plus the original message only when referenced.
Selecting **Confirm send** delivers that draft as the owner;
**Cancel** leaves the conversation unchanged. Only a selected answer from the
authenticated owner on this specific card can send. Typing the same words,
replying in chat, or choosing a similarly named option on an ordinary question
does not authorize publication. Access to the source, file versions, and
recipients are checked again on confirmation. A changed draft needs a new card.
Both forms require an explicit instruction in the owner's current primary
private conversation. Text must be exact wording supplied in that instruction
or a visible earlier private Duet draft; files must already have been shown
privately before it, or belong to the referenced original. When a reference is
provided, the private draft, privately shown files and send instruction must follow
the reference.
Background work and delegated workers cannot request this owner-send action.

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
