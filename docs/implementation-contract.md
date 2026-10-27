# Implementation coordination contract — updated 2026-10-26

Independent root requests in the same chat have distinct task-scoped SDK sessions and may execute concurrently within per-worker total and per-owner admission limits. The chat stores authorized history; only turns of the same task share a journal. `continuation_of_request_id` binds a continuation to its exact origin and preserves its session, frozen configuration and tool history. Existing accepted commands and legacy sessions retain their identity. SDK leases and session FIFO remain authoritative. With the default limits above one, system review, schedules and Dream leave foreground capacity on each worker (a configured limit of one cannot reserve a second slot); new requests receive bounded authorized active-task/worker metadata to avoid duplicate work. Output may complete out of send order and Stop targets its request.

Current execution/transport behavior is specified in [architecture.md](architecture.md). Historical coordination ownership below does not introduce additional process roles.

Implemented integration contract. For prior release evidence, see `implementation-acceptance.md`. Dated verification paragraphs below are historical records and do not establish acceptance of this redesign.

## Boundaries
- SDK owns DatabaseSession, runtime journal and execution/recovery mechanisms; product owns identity/chat/membership/history/message/trigger/task admission/UI projections.
- PostgreSQL is required in all environments. DATABASE_URL is explicit. Python 3.13 only.
- Development identity is explicitly labeled local. POST /auth/login accepts DEV-AAA, DEV-BBB or DEV-CCC only in explicitly opted-in local PostgreSQL development (off by default) and sets an opaque expiring HttpOnly/SameSite cookie. X-Owner-Id is rejected. Production verifies JWT subject/tenant/issuer/audience/expiration and a provisioned owner, otherwise fails closed.
- WebSocket sends commands, opaque changed revisions and bounded sender-only send acknowledgments containing correlation metadata, never chat content. HTTP reads content with authoritative permissions. Reconnect subscribes before refreshing bounded history windows. Semantic outputs remain durable SDK facts; no token tables.
- Human pair/multi-person chats are `kind=group`, owner-to-own-Duet chats are `kind=private`, and owner-approved Duet threads are `kind=duet`. Humans cannot post or react in Duet threads; agents cannot publish in human chats. Every Duet participant owner approves once or has active directed grants to every peer before opening and execution. Revocation closes dependent threads and cancels pending work. Connection permission grants no arbitrary action authority.
- Automatic Duet admissions share six replies across the original initiating task's entire causal lineage. Only explicit human Continue extends it. Presence, unread state, receipts and the former ten-second human-chat window do not trigger Duets. Historical nonhuman human-chat rows remain stored but are hidden from projections, notification metadata, context and dependent checkpoints.


## HTTP
Prefix /api. JSON uses snake_case. All errors `{detail:string}`. Reads enforce owner membership/history. GET /health and GET /api/config expose backend/provider state without secrets.
- POST /auth/login {username} -> {owner,...}; GET /auth/me; POST /auth/logout
- GET /bootstrap -> {current_owner_id, actors:Actor[], chats:Chat[], reminders:Reminder[], model_configured:boolean, development_mode:boolean}
- GET /people?q= -> Actor[] humans
- POST /chats {owner_ids:string[],title?:string,history_policy?:'all'|'fixed_7d'|'no_prior'} -> Chat
- POST /chats/{id}/members {owner_ids:string[],history_policy} -> Chat
- GET /chats/{id} -> {chat:Chat,messages:Message[],requests:ReplyRequest[],approvals:Approval[],tracking:Tracking[],settings:ChatSettings,members:Actor[],automatic_conversations:AutomaticConversation[]}
- POST /chats/{id}/automatic-conversations/{chain_id}/continue {client_request_key,additional_replies,expected_allowance} -> {id,causal_chain_id,spent,allowance,remaining,continuation_allowance,grant_id,already_applied}; the explicit count must match the configured increment and the exhausted allowance version must still match. Replayed intent keys return their original grant without charging again; stale independent grants are rejected rather than stacked.
- POST /chats/{id}/messages {body,client_request_key,reply_to_message_id?:string,reply_for_me?:boolean} -> {message:Message|null,request:ReplyRequest|null}
- POST /chats/{id}/reply-requests {message_id,target_twin_id,client_request_key} -> ReplyRequest (own private chat only; human-group and peer-Duet invocation rejected)
- POST /reply-requests/{id}/cancel -> ReplyRequest (requester or owner; running only owner)
- POST /reply-requests/{id}/retry {client_request_key} -> ReplyRequest (target Twin owner only; can_retry is authoritative)
- POST /chats/{id}/routing-decisions/{decision_id}/retry {client_request_key,expected_command_id} -> {id,chat_id,command_id,status:'pending'} (current source-message owner only, with current membership/history/source visibility; original failed attempt must still match)
- POST /presence {device_id,focused_chat_id:string|null,visible:boolean} -> {ok:true}
- POST /chats/{id}/read {message_ids:string[]} -> {ok:true}
- PATCH /chats/{id}/settings {reply_for_me_enabled?,prompt_override?,sidebar_open?,sidebar_tab?} -> ChatSettings
- PATCH /settings {default_reply_prompt:string} -> {default_reply_prompt}
- GET /settings -> {default_reply_prompt}
- GET /chats/{id}/activity -> ActivityRun[] (private own chat only)
- GET /chats/{id}/activity/{session_id}/{run_id} -> {run:ActivityRun,events:ActivityEvent[]}
- POST /approvals/{id}/decision {decision:'approved'|'rejected',scope?:'once'|'always'} -> Approval
- GET /chats/{id}/workspace?path=<relative-directory> -> WorkspaceEntry[] (direct children)
- GET /chats/{id}/attachments/{attachment_id}/preview -> authorized text or PDF bytes
- GET /chats/{id}/tracking/{id} -> Tracking with updates
- POST /chats/{id}/tracking {title,description,source_message_ids?:string[]} -> Tracking
- PATCH /chats/{id}/tracking/{id} {title?,description?,status?} -> Tracking
- DELETE /chats/{id}/tracking/{id} -> {ok:true}
- POST /chats/{id}/tracking/{id}/updates {body,source_message_ids?:string[]} -> Tracking
- POST /reminders {title,detail?,due_at?:string,chat_id?:string} -> Reminder
- PATCH /reminders/{id} {done:boolean} -> Reminder
- WS /ws: version-1 subscribe/unsubscribe/command/pong frames; changed notifications contain chat_id and revision only. Commands send/reply/stop return business_persisted ACK after transaction commit; presence TTL ACK means presence_renewed. The former GET /events SSE endpoint is removed.
- GET /chats/{id}?limit=100&before_seq={seq}: bounded history window; has_more/before_seq pagination.
- GET /chats/{id}/activity?limit=50&before={cursor}: {items,has_more,next_cursor}; details accept limit/after_seq and return has_more/next_seq.

## Shapes
Actor {id,kind:'human'|'twin',owner_id?:string,name,initials,color}
Chat {id,kind:'private'|'group'|'duet',title,member_owner_ids:string[],unread_count:number,last_message?:string}
Message {id,chat_id,seq,author_id,kind:'human'|'twin'|'reply_for_me'|'system',body,raw_owner_input?:string,created_at,reply_to?:{id,author_name,body,unavailable?:boolean},read_by:string[],request_id?:string,routing_failure?:RoutingFailure,state:'published'|'withdrawn'}
RoutingFailure {id,chat_id,command_id,error:string,can_retry:boolean}
ReplyRequest {id,chat_id,source_message_id:string|null,target_twin_id,requester_id,source,status:'queued'|'running'|'completed'|'failed'|'cancelled',accepted_at,error?:string,can_cancel:boolean,can_retry:boolean,session_id?:string,run_id?:string}
ChatSettings {reply_for_me_enabled:boolean,prompt_override:string|null,sidebar_open:boolean,sidebar_tab:'activity'|'workspace'|'tracking'|'duets'}

The `reply_for_me` request parameter, `reply_for_me_enabled`/prompt settings and `reply_for_me` message kind are retained for historical compatibility and audit. New submissions with `reply_for_me=true` and attempts to enable the setting return 403; stored values never restore that mode. Historical agent records in human chats remain stored but are hidden from current human-chat history and notification/context projections. `sidebar_tab='duets'` opens the authorized separate Duet-thread list.

Approval {id,chat_id,approver_id,title,description,target,impact,status:'pending'|'approved'|'rejected',created_at}
Tracking {id,chat_id,chat_title,chat_kind,title,description,status,created_at,updated_at,updates?:{id,body,created_at}[]}
Reminder {id,title,detail,due_at:string|null,done:boolean,chat_id?:string,kind?:string}
ActivityRun {session_id,run_id,parent_run_id?:string,source,status,started_at,finished_at?:string,title,summary?:string}
ActivityEvent {id,seq,type,time,label,input?:string,output?:string,status?:string}
WorkspaceEntry {id,name,kind:'file'|'folder',path,workspace_path?:string,size?:number,children?:WorkspaceEntry[]}
AutomaticConversation {id,causal_chain_id,status:'paused',reason:'budget_exhausted'|'repeated_content',spent,allowance,remaining,paused_pending_count,continuation_allowance,can_continue,source_owner_id}

Own-Duet private output does not automatically set a visible reply quote from `source_message_id`; Duet-thread output retains that default. Explicit human quotes and cross-chat `send_chat_message.reply_to_message_id` remain available. Private child continuations inherit only an explicitly selected parent quote. Source IDs, provenance, request identity and causal chain remain intact independently of quote presentation; no historical rows or Soul are rewritten.

2026-10-19 verification: independent `tests/test_private_reply_quote_acceptance.py` passes all four real-PG/offline-SDK cases, including unchanged group/explicit-tool quotes and source-withdrawal authorization. Together with child continuation, followup and recovery checks, 27 targeted cases pass. Idle API/Worker reload required no DDL, seed, frontend build or production message. Local/public health and unchanged `index-CAvXonGn.js` pass; actual Worker remains on `gpt-6-luna` and proxy 7894.

## Conversation history and compaction

Private replies, approved Duet replies and the Duet router use a single authorized
checkpoint-plus-tail projection. The server selects exact contiguous coverage;
the summarizer only produces factual text from the prior checkpoint and selected
message prefix. It receives no private Memory, Soul, execution configuration or
Tracking. Routing receives only this chat projection and the participant roster.
Checkpoint text retains task goals, decisions, verified progress, outstanding
questions/approvals/credentials, blockers, next steps and source attribution.

`TWINTEAMS_SUMMARY_AFTER_TOKENS=24000` is the primary estimated-token trigger,
`TWINTEAMS_SUMMARY_KEEP_TOKENS=8000` the recent-tail target, and
`TWINTEAMS_SUMMARY_MAX_TOKENS=2000` the checkpoint output cap. Estimates reuse the
SDK estimator. The auxiliary `TWINTEAMS_SUMMARY_AFTER_MESSAGES=40` trigger retains
at most `TWINTEAMS_SUMMARY_KEEP_MESSAGES=20` raw messages; set the trigger to zero
to disable the count trigger. Individual messages are never split. A single large
newest message remains raw. Batches summarize old history in bounded steps before
admitting a new model request. The server validates coverage, membership/history
floor, provenance, private generation and the prior checkpoint before publishing
a replacement. There is no cumulative source-ID cap on checkpoints; original rows
and journals are retained. Failed summary attempts keep valid prior state and
cause visible recoverable attempt failures, never a request with an omitted
unsummarized prefix. Incomplete/empty/oversized provider output is rejected before
SDK completion; its original stop reason and usage remain recorded. A later
caller attempt may resume that exact failed summarizer command once, with no
internal retry loop. Completed checkpoints replay without another provider call,
even if the caller's context manifest changes.

Main and worker Agents enable native SDK `CompactionSettings` for their independent
tool histories: 16,384 reserve tokens and 20,000 recent tokens, triggered near the
configured model context limit. Native compaction complements product history
budgeting because product history lives in the system prompt. Its journal
checkpoint preserves tool call/result pairs and original records; failed
compaction retains old state. Already accepted SDK commands bypass new product
checkpoint preparation and preserve frozen requests. `save_summary` is absent
from new tool declarations, approval actions and prompts; only a legacy resumable
run with that exact frozen declaration may settle its already-authorized call.

## Proactive tools and credential configuration

`list_authorized_chats` returns all current owner-member chat metadata and identifies `owner_private_chat_id` / `is_owner_private`, from both Duet and private tasks. A Duet may directly report progress or request a decision in its owner's private chat. Discovery does not grant another owner's private data or bypass membership/history limits. `send_chat_message {chat_id,body,source_message_ids,reply_to_message_id?,wait_for_reply?:false}` validates the durable originating request and the owner's source/destination access. Destinations are restricted to the owner's private chat or active approved Duet threads; human chats and the originating chat are rejected: normal assistant output is the current chat's reply outlet. A newly composed cross-chat message is the owner's outward statement: recipients receive its prepared body without access to the originating transcript. Internal request/manifest/operation references retain audit provenance. Same-chat model output still respects its authorized audience. Stable SDK operation IDs make replay idempotent and preserve the causal chain; receipts for previously committed sends remain replayable without a second send. Never include unrelated private details in a new outward message.

| Capability | Installed platform tools | Boundary |
| --- | --- | --- |
| Discover people/chats | `list_people`, `list_authorized_chats`, `get_chat` | Owner memberships; bounded metadata/status, no hidden request input/configuration. |
| Read conversations | `history` with optional `chat_id` | Current membership and history floor; defaults to origin. |
| Connect and coordinate Duets | `request_duet_conversation`, `send_chat_message`, `present_files` | All-owner connection consent; sends/files only to own private or active approved Duet threads. Owner-authorized human-chat metadata management remains available through the existing tools, without publishing an agent message. |
| Human chat management | `create_chat`, `rename_chat`, `add_chat_members` | Existing owner membership, group-size/history rules; peer requests require exact owner action approval. No messages or automatic responses; Duet participants stay fixed. |
| Message reactions | `set_message_reaction` | Desired state on an authorized message, attributed to the Twin Actor; does not route a response. See [message-reactions.md](message-reactions.md). |
| Tracking | `list_tracking`, `get_tracking`, `create_tracking`, `update_tracking`, `delete_tracking`, `add_tracking_update`, optional `chat_id` | Owner private index unifies its own and authorized human-group/Duet topics; Duet-origin tools stay in that thread even with explicit `chat_id`. Details and changes use the topic's original `chat_id`. Source and audience checks remain; no silent revival of deleted topics. |
| Conversation checkpoints | Server maintenance; no model tool | Authorized chat summary plus contiguous raw tail; shared by replies and router. |
| Reminders | `list_reminders`, `create_reminder`, `update_reminder` | Owner's in-app reminders; no external notification promise. |
| Workspace | `list_workspace`, `read_workspace_file` | Authorized metadata/pagination and UTF-8 text ≤64 KiB; no upload, shell or execution grant. |
| Twin/request status | `get_twin_status`, `get_reply_request`, `cancel_reply_request`, `retry_reply_request` | Sanitized status only; retry uses the controlling task's causal chain, without automatic allowance. Only explicit human Continue can extend an exhausted chain. |
| Approval | `create_approval`, `get_approval`, `execute_approved_action` | Exact typed platform-write action; owner-only decision; original execution permissions and chain. |
| Personal configuration/security | Human profile, Soul, Skills, Memory, credentials and reset forms | No platform tool for personal configuration, secret values, approval decisions, extra allowance, reset or tenant administration. Owner execution/Memory tools are available to authorized main Twin runs in private and approved Duet chats; sensitive operations require own-owner consent unless already granted. |

Approval JSON may include the safe frozen `{tool,arguments}` action for its approver, excluding SDK internal references and secret values. The existing card's View details exposes that exact tool and arguments as escaped React text/JSON, alongside target and impact; old cards without an action remain usable. `frontend/checks/approval-details-ui.cjs` verifies all 13 action variants, legacy cards, approver filtering, escaped markup, English desktop and Chinese 390px layout without submitting any decision. Exact owner approval may authorize a single retry of an existing legitimately frozen private task with the private execution impact made explicit, without changing the retried task's frozen tool contract. `owner_ids` is capped at 100 and `source_message_ids` at 500 in platform declarations.

Platform approval action variants include `set_message_reaction`, `send_chat_message`, `create_chat`, `rename_chat`, `add_chat_members`, `create_reminder`, `update_reminder`, `create_tracking`, `update_tracking`, `delete_tracking`, `add_tracking_update`, `cancel_reply_request` and `retry_reply_request`. Their arguments reuse the installed typed tool declarations. Approval cards may appear in the owner's private inbox for a Duet task; decision continuation stays in the originating thread with frozen execution configuration and original requester/source identity. Installed non-replay-safe execution tools also offer their exact schema as approval variants; local execution remains bound to the owner, task, frozen configuration and one SDK operation. A pending card does not block later replies.

On a genuinely resumable SDK run, platform declarations are re-injected from that run's frozen tool records using only currently known platform executors. Each invocation still performs current service authorization. A saved unknown tool name cannot install an executor. Completed session history alone does not freeze a new run's declarations. New platform declarations use version `twin-teams-platform-v2`; a resumed declaration retains its saved version. Execution and delegation tools also retain the accepted declaration set on resume; new child runs use the same authorized owner assembly as main runs.

New chat membership emits an owner-targeted `personal_changed` notification with `section:'chats'` after commit. Reusing an existing direct chat does not emit a new-membership hint. The client reloads canonical bootstrap on that hint and once on connection/reconnection, preserving a still-authorized current selection, draft, detail and focus. Subscriptions compare sorted chat-ID sets, so list reordering does not reconnect. `frontend/checks/chat-discovery-ui.cjs` passes private/group scenarios for new-chat discovery, missed-hint reconnect recovery and unchanged-set reordering with fully synthetic HTTP/WS. Typecheck and production build pass; the final approval-detail build is `index-s3FTSbjB.js` / `index-BMWbdcWb.css`.

Activation of a `request_duet_conversation` initiated from the owner's own private task automatically creates a thirty-minute followup to its opening, retaining the initiating request and original chain. Pending approval has no deadline. Nested Duet-origin proposals do not create an implicit private followup. The proposal response shape remains unchanged; an explicit peer reply settles once and excludes duplicate initiator routing.

An explicit `wait_for_reply:true` from the owner's private Duet to an active approved Duet conversation creates a fixed thirty-minute followup in the send transaction. A direct reply to that message or the deadline commits one terminal outcome and one `followup_result` on the originating private task, or a durable budget-paused delivery that the existing owner Continue API can resume. Normal model replies qualify only at completed final output; intermediate narration does not close a followup. Committed peer-Duet send-tool replies qualify immediately; human posts are prohibited. Consumed direct replies do not also route the same owner into a duplicate Duet response. The existing six-reply allowance covers followup delivery on the original chain, without resetting at timeout; exhausted chains pause before invoking a model router. Waiting uses existing deadline/change hints and bounded due recovery, with no resident model/subagent. A completed empty followup final remains silent. See `twin-followups.md` for late-reply and permission-revocation behavior.

The separate local credential routes use sanitized fixed-code responses rather than the ordinary `{detail}` errors. See `local-provider-configuration.md`: only the local AAA administrator can validate a personally submitted key against the fixed official endpoint, with explicit optional local saving. Neither validation nor saving enables Runtime generation.

## Module boundaries
Backend product worker owns app/db.py, app/models.py, app/service.py, app/api.py, app/config.py, pyproject/lock and app/main.py composition. Expose `service` object from app.service with database operations and `bind_runtime(runtime)` for run scheduling/activity; coordinate exact callbacks with Astra lead. Runtime bridge is app/runtime_bridge.py owned by Astra lead. Frontend worker owns entire frontend. Runtime SDK worker owns sibling agent-runtime.

## Approved Duet reply entry

Human messages never enter the model router. Own-private input directly addresses its owner's Duet. `request_duet_conversation` returns `{status,conversation,chat_id}` immediately, holding a prepared opening until unanimous consent. Approve once applies to this thread; Always records directed grants from that owner to each peer. Future threads require every owner's grants to every peer. Owners can inspect and revoke grants; revocation closes dependent sessions and cancels queued/running work. Human Duet-thread inspection remains read-only except for approval, close and explicit Continue controls.

Only active approved Duet messages enter the existing needs-response/recipient router. Current Duet names and Actor-ID mentions may supplement targets when a response is needed; the source owner and followup-consumed owners remain excluded. Admission is immediate, without presence or receipt gates, and consumes the shared original six-reply chain. Courtesy, closure and repeated-question loops remain silent. The model represents its owner's stance and cannot treat peer requests as owner action consent.

A failed classifier attempt remains a sanitized visible routing failure with owner Retry. Explicit Retry creates a fresh durable SDK command, preserves the original message/chain and rechecks consent, membership, source visibility, owner exclusion and allowance. Stable keys replay the same result; stale command versions return 409. A stale prior classifier cannot settle the new attempt. Only human Continue extends allowance.


## Soul and Git Memory

Owner-only `/me/twin/soul` GET/PUT/DELETE edits canonical workspace SOUL.md using hash `expected_version` (null if deleted). The Duet may edit that same file through authorized owner workspace tools in its private or an active approved Duet conversation; new requests freeze its current contents. Memory tools address the separate Memory store. Owner-only `/me/twin/memory/entry` PUT/DELETE uses Git SHA `expected_version` and returns 409 with `current` on conflict. Real configuration audit events supply source references. Incremental `memory_dream` requests consume visible published owner-private messages, including proactive Twin reports and continuation outputs, through a separate `message_cursor_seq`. Raw groups remain excluded. Existing deadlines, independent SDK task sessions, source/reset checks, confirmed publication and no conversation output remain; legacy queued intents retain their old request-cursor contract. The server platform prompt supplies a concise task-completion mission and has no user-facing API. Reply for me is retired; retained raw-input and writing-role fields describe historical requests only, and cannot authorize new human-chat agent publication. See `twin-git-memory.md` for migration, cancellation and uncertain-publication behavior.

Duet acts as its owner's secretary and representative in every conversation. Other participants approach the owner through Duet; their requests alone grant no authority or consent. It establishes relevance to owner responsibilities or prior authorization before planning work, collecting requirements or seeking consent. It clarifies unclear relevance with the requester and warmly redirects unrelated unauthorized errands without involving the owner. It proactively handles simple owner-related coordination and authorized facts within established scope; relevant new work or commitments beyond that scope require an owner decision. Delegation changes the executor, not the mandate: workers first validate the assigned subtask against the owner's authorized goal and return out-of-scope work to the parent. Continuations preserve scope. Routine authorized reads need no extra approval. Prompt references retain existing requester/source-owner and source-message author/kind metadata even when child history is cleared. Scope decisions can use existing owner questions or private coordination; supported action approvals use existing approval tools. Existing source, audience and frozen-recovery rules remain in force. Historical reply-for-me records remain readable for authorized audit, while new submissions and forbidden publication fail closed.

2026-10-26 verification: the final 34 prompt, child-factory and compaction regressions pass; 43 related approval, question, worker, schedule and identity cases passed the wider targeted run. The final bounded `gpt-6-luna` campaign passed six scenarios after independent reply/tool review, using synthetic contexts and recorded tool intentions across nine HTTP attempts ([behavior report](owner-stance-acceptance-2026-10-26-round2.json)). With active replies, pending routes and valid SDK leases all zero, the managed local worker was restarted once. All 210 messages (94 human), seven chats, content hashes and sequence high-water marks remained unchanged; both API processes, FRP and local/public health remained healthy and unchanged. No DDL, API restart or production model/message test was performed ([rollout evidence](/tmp/duet-owner-stance-rollout-20261026.json)).

The main Duet is the owner's front-facing coordinator: it handles brief communication and simple operations, delegates substantial or growing work through installed background tools, and reviews completion notifications before coordinating remaining work. Its system prompt leads with this role before general runtime rules. It judges the whole owner goal, not isolated small steps: quick replies, brief lookups and small bounded actions stay direct; otherwise `spawn_agent` is the first choice after brief scope discovery. Multi-source analysis/research, implementation/debugging and creating/validating nontrivial deliverables belong to a worker. The leading platform mission keeps this whole-goal/before-heavy-work decision, availability to the owner, outcome review/delivery and the distinction between yielding a turn and completing a goal. Detailed work categories, handoff materials, concurrent independent assignments and asynchronous completion belong to product-level spawn/get/wait descriptions rather than repeating the workflow in the mission. These shared descriptions stay recipient-neutral for nested workers and keep the SDK’s full main/child capabilities unchanged. The main reports privately to its owner; a worker reports to its parent unless its assigned, owner-authorized task requires direct delivery. A child does not automatically receive a copy of its parent’s chat transcript; it retains the authorized owner resources and task scope, can read authorized history, and preserves its own history on continuation or recovery. Automatic completion resumes the original task for review; no extra send-message action is required to return the child result. Explicit waits remain available but are not the normal handoff path. Interrupted model requests keep their exact frozen declarations; new turns use the current descriptions. Skill procedures follow this same division. Default Soul resourcefulness encourages using suitable help; existing owner Soul files are never overwritten. Worker prompt assembly selects an explicit execution role, independently of summary maintenance: workers complete only the assigned subtask and return verified results to the parent, without inheriting the main coordination role or historical parent writing instructions. Main and child sessions use the same owner capabilities, including Memory and delegation, while keeping separate task histories and compaction. Children can retrieve authorized history on demand. Nested results resume the immediate worker parent through the SDK durable FIFO; only its reviewed result reaches the root task journal. Recursive delegation uses a maximum depth of three, up to 32 distinct children per parent run, and eight active child executions per host. Spawn confirms durable queued acceptance, not startup. Workers yield while descendants run; arbitrary blocking waits can still occupy a child slot. Existing owner permissions, source checks, frozen declarations, reset fences and causal-chain limits remain.

## Uploaded files and the real workspace

Chat attachments accept every file type, with the existing four-file and 20 MiB aggregate limits. Recognized static images retain previews; other formats, including images without a supported preview, use ordinary file cards. New uploads have one physical copy in the current chat's `uploads/<id>/<original-name>`; old paths remain valid. Model input supplies metadata and absolute `workspace_path` references, never automatic image bytes. File-only messages can request a reply. Installed `read` handles text and images on demand; `bash` can process other formats. Existing paged-read and tool-result limits remain.

The private chat directory is the Twin owner's execution cwd. Approved Duet runs keep that owner cwd and receive the separate current thread's shared workspace path for intended deliverables; browsing a group never lists owners' private directories. Workspace browsing lists actual directory children lazily, so generated files appear without a registration step. Hidden internal directories are omitted. Uploaded files are read-only references by convention; `uploads`/legacy `.attachments` and registered ancestors still enforce their existing attachment/node, source, draft, private-input and history permissions, including direct downloads by path ID. Unregistered group files use creation time for restricted history; where birthtime is unavailable, unknown creation time stays unavailable to history-limited members. Existing OS-level local execution remains trusted, not a new file sandbox.

`present_files` publishes explicit snapshots as ordinary `Message.attachment_ids` / `ChatAttachment` records; no separate artifact store or schema is introduced. Its optional caption and files form a separate delivery message, including in the current chat. Source permissions apply at publication and target-message visibility applies on later reads; this is an independent authorized share, not a promise to revoke every copy when the original working file changes. Operation replay returns the same message and snapshots. Downloads and previews validate the returned bytes against the recorded size and SHA-256.

Attachment metadata includes `preview_kind` (`markdown`, `text`, `pdf`, `html`, or null) and a canonical authorized `preview_url`. Markdown/text/HTML previews require UTF-8 and are limited to 1 MiB; PDFs retain the 20 MiB attachment limit. Markdown and text render as application content without executing embedded HTML. HTML downloads remain attachments; an explicit Open action runs self-contained HTML in a new tab's sandboxed iframe with scripts allowed, no same-origin privilege and no external network access. The server never serves uploaded HTML as an executable application-origin document. Unsupported or oversized previews remain downloadable.

Message bodies have no application character cap and remain complete in PostgreSQL. Frames exceeding the existing 64 KiB WebSocket command limit use the same HTTP send/key; this does not remove transport, memory or model context limits, nor silently truncate a message. Before deploying, run `python -m app.init_db` to widen attachment `relative_path` to `TEXT`; this repeatable additive upgrade changes no files or stored paths. See `attachment-path-schema.sql` for the reference DDL. Previously accepted SDK commands retain their immutable inputs during recovery.

## Human conversation titles

`kind=private` remains the owner's conversation with their own Twin and displays that Twin's configured name. A `kind=group` conversation with exactly two current owners displays the other owner's human Actor name to each viewer; its stored historical title is ignored and it cannot be renamed. Chat JSON includes `can_rename`, true only for group chats with at least three current owners. Bootstrap, chat detail, summary, Agent context and authorized-chat metadata use the same viewer-aware title rule.

`PATCH /chats/{chat_id} {title}` allows a current member to rename a group of at least three owners. The title is trimmed and must contain 1–256 characters (400 on invalid content, 409 for a private/two-owner chat, 403 for a nonmember). It returns the viewer's complete Chat JSON and uses the existing revision/change notification mechanism. Creating a two-owner conversation ignores a submitted title. Adding a third owner initializes a group title from all current human names; later additions preserve an existing group name. Local seed creation retains its defaults but never rewrites an existing pair/group title. No schema migration is needed; API and Worker service code both require reload to use the new display rule.

`POST /chats` with exactly one distinct other owner is a bidirectional get-or-create. A nullable unique `direct_pair_key` hashes the sorted two-owner IDs. Opposite/concurrent API calls return the same complete viewer Chat. Existing membership history policy/floor/version, settings, title and history are never reset by a reused POST; submitted title/history policy apply only when the pair is first created. The existing Chat mutation transaction rechecks current membership after a uniqueness conflict. Adding a third owner clears the key in that transaction, so a later direct chat starts a new pair rather than returning the upgraded group.

Deploy the pair-key migration before starting the new API/worker: `python -m app.migrate_direct_chats` performs only this schema/backfill, without seeding, memory work or model calls. `app.init_db` also invokes it for normal provisioning. The reference `direct-chat-schema.sql` only shows DDL; ADD COLUMN alone is insufficient because existing unique pairs must be backfilled. Ambiguous duplicate pairs cause an explicit migration failure. The generic migration never picks a canonical duplicate, deletes a conversation, merges history or alters user grants. Any individually approved empty duplicate cleanup must be separately revalidated in a maintenance transaction before backfill. There is no alias registry, new pair lock, or deterministic chat-ID reservation.

The owner-private Tracking index in chat detail, runtime context, `list_tracking`, and `GET /api/chats/{chat_id}/tracking` uses the same active-membership and source-history checks. Records retain their original chat ID, title and kind; no topic is copied. Group sources in private runtime manifests retain their `chat_context` boundary. Tracking writes, membership changes and source withdrawals emit a lightweight personal Tracking hint, so the private index can refresh without loading the conversation again. Cross-chat writes retain inherited provenance: private context is not silently made readable to other group members.
