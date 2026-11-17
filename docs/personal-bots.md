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
identity. Its children inherit that same BOT scope. Each BOT has its own immutable
Skill pack and enabled catalog. A BOT without a pack takes its initial active
Skills from the owner's main Duet at its first execution, share, or Skill change;
later main-Duet Skill changes do not change that BOT. Configured execution
permissions and credentials still come from the executing owner.
Local execution retains the existing host-privilege boundary; this is not OS
isolation. BOT Memory uses existing memory tools and Git CAS. Automatic Dream
continues to operate on the primary private conversation, not on BOT transcripts.
Primary private reset does not delete BOT conversations or their separate Memory.

## Snapshot sharing

**Share BOT** in a BOT row menu or its edit dialog freezes a new immutable node.
The dialog lists saved nodes and the owner's links, supports copying or issuing
another link, and revokes individual links. A signed-in recipient opens
`#/bots/share/{token}`, reviews the name, assignment summary, enabled Skill count
and Memory indication, then chooses **Add to my BOTs**. The existing login flow
preserves that hash. Installation creates a new owner-only BOT/chat/membership;
the same owner and snapshot always return the same installed BOT. Even the source
owner's self-install creates a separate copy, leaving the source BOT intact.

Snapshots include name/assignment, active Skill folder contents and enabled names,
the current BOT Git Memory tree, optional avatar and the BOT's own rule files.
Rule files are `SOUL.md`, `AGENTS.md`, `RULES.md`, `rules.md`, and text files under
`rules/` in its own chat workspace. Rule entries already in Git Memory travel with
that Memory. New executions freeze the workspace rules into the BOT profile.
Main-Duet Soul, transcripts, requests, Tracking, contacts, credentials and other
workspace artifacts are not copied. Symlinks and credential/login files in a
Skill or rules folder are rejected. User-authored Skill/Memory/rule prose remains
content, not a semantic secret-redaction service; configured credential stores
are never exported. Installed execution uses the recipient's existing credential
and permission paths, including missing-credential failures.

Each installed BOT keeps its original node ID even after edits. Sharing its current
version creates a child of its latest published node (or its originally installed
node for its first share). **Share original snapshot** creates a new link to that
original immutable node. Each link belongs to its issuer; an installer cannot
revoke someone else's link. Revocation stops new installs through that link and
does not change installed copies or other links. Main Duet, other owners' BOTs,
human groups and Duet topics retain their existing boundaries; a share is an
installation invitation, not membership or communication authority.

An existing installer can still reopen its own copy through a revoked link;
preview/install through that link fail for owners without an existing install.

Storage under `workspace_root/.bot-share` uses SHA-256 blobs and manifests.
Skill packs hardlink read-only blobs and are materialized once per digest.
Installed BOTs point to that pack, with no per-install Skill directory. The BOT
Skills UI uploads folders, toggles, edits `SKILL.md`, and removes Skills; each
mutation publishes a new pack and changes only that BOT's pointer. Running tasks
retain their frozen pack. Runtime prompts expose that fixed directory so bundled
scripts and companion files can be used directly, with outputs in the BOT's own
workspace. Full folder replacement is remove then upload; nested
references and executable metadata are retained. Main Duet's Skill folder is
never overwritten or populated by installation.

Memory freezing reads one current commit, creates a parentless commit for its
tree, and pins an immutable `refs/bot-snapshots/{snapshot_id}` before transferring
that commit into the shared bare `memory.git`. Removed/historical Memory versions
do not travel. Snapshot rows store the fixed commit, never the live source HEAD.
Each install initializes a small private bare repo at the existing
`runtime-memory/bots/{bot_id}` scope with `objects/info/alternates` pointing to the
shared objects. Subsequent SDK commits go only to that BOT's repo. Avatars reuse
the blob digest; small editable rule files are copied to the new BOT workspace.

Snapshot rows reject SQL updates. Foreign keys retain snapshots referenced by
tokens, installs, actors and lineage, and Git refs retain shared Memory objects.
There is currently no snapshot/object deletion or garbage collector: revoked
tokens, old packs and failed-operation orphan files are retained. Future cleanup
must first establish zero database references and account for frozen execution
configs and all Git alternate borrowers. Backups and multi-host storage must keep
the shared assets together with the private repositories and preserve/rewrite
the absolute alternate paths when moving storage.

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
| `GET /bots/{bot_id}/shares` | Owner's saved nodes and tokens, plus originally installed node |
| `POST /bots/{bot_id}/shares` | Freeze current state and issue a token; `{client_request_key}` |
| `POST /bots/{bot_id}/shares/{snapshot_id}/tokens` | Issue a token for this BOT's node or its original installed node; `{client_request_key}` |
| `DELETE /bots/{bot_id}/shares/tokens/{token_id}` | Revoke the caller's token |
| `POST /bot-shares/preview` | `{token}`; authenticated metadata preview and caller's existing install, if any |
| `POST /bot-shares/install` | `{token}`; idempotent installation returning a PersonalBot |
| `GET /bots/{bot_id}/skills` | BOT Skill catalog without file bodies |
| `POST /bots/{bot_id}/skills/upload` | Existing multipart folder format; starts disabled |
| `GET /bots/{bot_id}/skills/{name}` | Full editable `SKILL.md` and reference names |
| `PATCH /bots/{bot_id}/skills/{name}` | Set `{enabled}` for this BOT |
| `PUT /bots/{bot_id}/skills/{name}` | Replace `SKILL.md` using `{content}` |
| `DELETE /bots/{bot_id}/skills/{name}` | Remove a Skill only from this BOT |

BOT records contain `{id, chat_id, name, assignment, installed_snapshot_id, actor, chat}`. Bootstrap includes
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

Snapshot lists select owner/source metadata and load tokens in one batch. Assignment
summaries are explicitly limited to 500 characters; installation uses the full
assignment. List and preview endpoints do not read Skill file bodies or Memory.
The source index supports node listing/parent selection; the token catalog index
supports owner/BOT lists. Unique token and owner/snapshot install constraints
support lookup and idempotence. Install reserves that uniqueness in PostgreSQL
before creating the actor; a shared token row lock orders installs against revoke
without serializing different recipients with one another. Share/token command
retries reuse their client key. No polling or model calls are introduced.
Bearer share tokens travel in JSON request bodies; they remain in the browser's
hash link and do not enter HTTP URL/access-log paths.

## Provisioning

Run the explicit `python -m app.init_db` upgrade before starting the new API and
worker. `personal_bots_schema.py` applies `sql/008_personal_bots.sql`: additive
columns and revised actor/chat constraints, preserving existing identities and
history. `bot_sharing_schema.py` applies `sql/009_bot_sharing.sql`, adding
`tt_bot_snapshots`, `tt_bot_share_tokens`, `tt_bot_installs` and Actor pack/original
node pointers, plus the immutable-node trigger. Ordinary startup checks the
installed schema. No SDK migration or sibling repository change is required.
