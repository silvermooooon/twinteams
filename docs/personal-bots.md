# Personal BOTs

Personal BOTs are durable agents created by an owner, with a name, responsibility
assignment, reopenable conversation and separate Git Memory. They appear under
**BOTs** in the existing sidebar. An empty catalog offers creation; opening a BOT
uses the ordinary conversation composer, history, typing and request failure
controls. Editing its name or assignment affects new executions. Recovery retains
the execution configuration already frozen for the task.

The primary `kind=private` conversation remains the owner's main Duet and external
entry. Owners can ask main Duet to create a BOT there. `create_personal_bot` accepts
`{name, assignment}` and reuses the sidebar/API creation transaction to save the
BOT and its owner-only conversation. Runtime operation replay returns the same
BOT. This tool is unavailable to BOTs, internal rooms and external conversations.
After success, Duet reports the saved name and assignment, explains how to open
the BOT under **BOTs**, and uses the returned ID to dispatch authorized work.
It does not send the owner to a separate creation entry or ask for a BOT ID.
Creation alone needs no Tracking item. Main Duet dispatches suitable work to
personal BOTs, reviews returned results and prepares external messages. Temporary SDK children remain task
workers, without BOT entries. Creating a BOT does not run onboarding or a provider
call; its assignment is available on the first message.

## Identity and conversations

`Actor.kind=bot` adds multiple agents per owner. A partial unique index retains
one `human` and one primary `twin` actor per owner. The assignment belongs to the
BOT actor. The existing message `kind=twin` represents agent output; `author_id`
identifies the particular BOT or main Duet.

Two conversation kinds extend the existing `Chat`:

- `bot`: `internal_owner_id` identifies its owner, `bot_id` identifies its BOT and
  `member_actor_ids` contains that BOT.
- `internal`: `internal_owner_id` identifies its owner and `member_actor_ids`
  contains the primary Duet plus the selected BOTs.

Both have only their owner's ordinary `Membership`; `private_owner_id` stays
reserved for the one primary private conversation. Owner, actor and room checks
apply to reads, sends, execution admission and tools. Other owners cannot discover
these actors through bootstrap, list their BOTs, open their chats or inspect their
profiles. Human `group` and forum `duet` roles and membership remain distinct.

Each BOT uses its own conversation workspace and SDK Git Memory under its actor
identity. Its children inherit that same BOT scope. The owner's enabled Skills
and configured execution credentials remain available through existing assembly.
Local execution retains the existing host-privilege boundary; this is not OS
isolation. BOT Memory uses existing memory tools and Git CAS. Automatic Dream
continues to operate on the primary private conversation, not on BOT transcripts.
Primary private reset does not delete BOT conversations or their separate Memory.

## Internal coordination

`dispatch_bot_tasks` accepts one or more owner BOT IDs and a task. The main Duet
assignment and incoming BOT task are ordinary persisted messages, linked through
`Message.coordination` metadata. Target requests use the existing reply-request
and outbox path and independent SDK sessions. There are no waiting agents,
polling loops or separate BOT scheduler.

`message_personal_agent` sends between the owner's main Duet and BOTs or between
that owner's BOTs. The sender and recipient conversations retain readable copies
and links. The tool derives the sender from the executing actor; the authenticated
owner UI can explicitly direct a message from a selected personal agent.
`list_personal_bots` exposes the main Duet's identity alongside the BOT catalog;
room execution context includes the selected member actor IDs.

The request's `bot_handoff` records its return actor, conversation and optional
originating request. A BOT yielding to a temporary worker retains this route;
the worker's completion resumes the BOT for review before handoff. The same applies
when it requests peer BOT work: pending internal deliveries and result continuations
are checked at completion, without polling; the final reviewed result returns once
that task's outstanding work settles. On terminal
completion the final text is published into the
return conversation and a `bot_result` request resumes that exact task session,
or starts a new receiving task for an owner UI action. Failures, cancellation and
an empty final response have explicit result metadata. A result does not set up
another automatic return, so handoff alone cannot create a reply loop. A reset
primary destination does not accept a result from its retired generation. Separate
file delivery cards remain in the source conversation, accessible through the
handoff link and authorized transcript/workspace reads.

`create_bot_room` includes main Duet and selected BOTs in one owner-only thread.
Owner messages and `post_bot_room` select `recipient_actor_ids`; only selected
members are asked to act. An empty selection posts without waking anyone. Finals
remain in that room and do not automatically wake its other members. The UI shows
room members and retains the ordinary conversation loading and error states.

Main Duet uses `inspect_bot_history` explicitly when it needs to review a BOT's
understanding. The bounded, paged read adds consumed records to its existing
context manifest. It does not mirror every BOT conversation into primary context.
The owner's **Inspect BOT** view displays the same paged history, including empty
and unavailable states.

BOT and internal-room tool catalogs permit internal coordination and scoped
workspace/history/Tracking operations. They exclude external social tools. A BOT
cannot enter a human group, become a forum participant, message another owner or
prepare a human send as the owner. Human **Confirm send** continues to live in the
primary private Duet. Forum tools retain their existing consent and administrator
boundaries; connection consent is not arbitrary action authority.

Topic openings, posts, edits and invitation messages prepared in the primary
private Duet require an exact `create_approval` action and owner confirmation
before execution. Primary-to-topic file deliveries also require confirmation of
the exact files, caption and recipients, even when those files were shared before.
The existing approval card shows the saved action and arguments. This applies to
fresh main tasks as well as BOT result continuations; no derived-content marker
is needed. Topic-origin forum notifications and the human send card retain their
existing behavior.

## API and reads

All paths use `/api` and authenticated owner identity:

| Route | Behavior |
| --- | --- |
| `GET /bots` | Complete owner BOT catalog |
| `POST /bots` | Create from `{name, assignment}` |
| `PATCH /bots/{bot_id}` | Edit `{name, assignment}` |
| `GET /bots/{bot_id}/history` | Ordinary chat detail with `limit` and `before_seq` |
| `POST /bots/dispatch` | `{bot_ids, body, client_request_key}` from primary Duet |
| `POST /bots/messages` | `{sender_actor_id, recipient_actor_id, body, client_request_key}` |
| `POST /bot-rooms` | Create from `{title, bot_ids}`, returning a Chat |

BOT records contain `{id, chat_id, name, assignment, actor, chat}`. Bootstrap includes
the owner's `bots` plus BOT and internal-room chats in its existing `chats` list;
forum topics keep their separate listing. Lists use owner-filtered joined queries
and batched summaries. Dispatch resolves all target agents and lanes in batches;
the model's `list_personal_bots` tool returns only identity/assignment metadata,
without conversation previews. Memory publication state is reused within a dispatch
transaction and changed chats are checked together when emitting revisions;
history retains the existing indexed chat/sequence pagination and display batches.
The new indexes preserve primary actor uniqueness and support owner/kind internal
chat lookup; existing BOT ID uniqueness supports exact target lookup. The partial
`tt_bot_handoff_pending` index supports completion checks by the requesting SDK
session while work remains queued or running.

Create/edit responses merge into the local catalog. New BOT membership emits the
existing owner-only `personal_changed` hint with `section=chats`, refreshing
bootstrap and the sidebar even when Duet created the BOT. Profile edits emit a
`personal_changed` hint with `section=bots`; dispatch and messaging use existing
chat revision refreshes. The UI does not add periodic catalog or transcript reads.
New UI labels, empty states, actions and result statuses use `t()` and `i18n/en.ts`.
API permission failures follow the existing `{detail}` error path.

## Provisioning

Run the explicit `python -m app.init_db` upgrade before starting the new API and
worker. `personal_bots_schema.py` applies `sql/008_personal_bots.sql`: additive
columns and revised actor/chat constraints, preserving existing identities and
history. Ordinary startup checks the installed schema. No SDK migration or sibling
repository change is required by this feature.
