# Implementation coordination contract — 2026-10-17

Implemented integration contract. For verified behavior and remaining release gates, see `implementation-acceptance.md`.

## Boundaries
- SDK owns DatabaseSession, runtime journal and execution/recovery mechanisms; product owns identity/chat/membership/history/message/trigger/FIFO/UI projections.
- SQLite local default. DATABASE_URL switches to PostgreSQL. Python 3.13 only.
- Development identity is explicitly labeled local. POST /auth/login accepts DEV-AAA, DEV-BBB or DEV-CCC only in explicitly opted-in local SQLite development (off by default) and sets an opaque expiring HttpOnly/SameSite cookie. X-Owner-Id is rejected. Production verifies JWT subject/tenant/issuer/audience/expiration and a provisioned owner, otherwise fails closed.
- Token deltas are ephemeral SSE. Browser reconnect reloads canonical state. Completed semantic outputs are durable in SDK, product messages reference them. No token tables.
- Twin-origin messages retain their actor identity and use the source owner for recipient eligibility. A model-backed `needs_response` decision gates automatic group replies, including pair groups and explicit mentions. Private direct-owner inputs retain their direct reply path. Automatic acceptances share one durable causal-chain allowance (default six); retries, recovery, child results and proactive tool sends preserve lineage. Explicit human continuation adds a recorded allowance without cancelling already accepted work.

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
- POST /chats/{id}/reply-requests {message_id,target_twin_id,client_request_key} -> ReplyRequest
- POST /reply-requests/{id}/cancel -> ReplyRequest (requester or owner; running only owner)
- POST /reply-requests/{id}/retry {client_request_key} -> ReplyRequest
- POST /presence {device_id,focused_chat_id:string|null,visible:boolean} -> {ok:true}
- POST /chats/{id}/read {message_ids:string[]} -> {ok:true}
- PATCH /chats/{id}/settings {reply_for_me_enabled?,prompt_override?,sidebar_open?,sidebar_tab?} -> ChatSettings
- PATCH /settings {default_reply_prompt:string} -> {default_reply_prompt}
- GET /settings -> {default_reply_prompt}
- GET /chats/{id}/activity -> ActivityRun[] (private own chat only)
- GET /chats/{id}/activity/{session_id}/{run_id} -> {run:ActivityRun,events:ActivityEvent[]}
- POST /approvals/{id}/decision {decision:'approved'|'rejected'} -> Approval
- GET /chats/{id}/workspace -> WorkspaceEntry[]
- GET /chats/{id}/tracking/{id} -> Tracking with updates
- POST /chats/{id}/tracking {title,description,source_message_ids?:string[]} -> Tracking
- PATCH /chats/{id}/tracking/{id} {title?,description?,status?} -> Tracking
- DELETE /chats/{id}/tracking/{id} -> {ok:true}
- POST /chats/{id}/tracking/{id}/updates {body,source_message_ids?:string[]} -> Tracking
- POST /reminders {title,detail?,due_at?:string,chat_id?:string} -> Reminder
- PATCH /reminders/{id} {done:boolean} -> Reminder
- GET /events?chat_id={id} -> SSE events `{type:'changed'|'token'|'status'|'operation_started'|'output_committed',chat_id,request_id?,text?}`. Changes reload canonical chat; deltas never replay.

## Shapes
Actor {id,kind:'human'|'twin',owner_id?:string,name,initials,color}
Chat {id,kind:'private'|'group',title,member_owner_ids:string[],unread_count:number,last_message?:string}
Message {id,chat_id,seq,author_id,kind:'human'|'twin'|'reply_for_me'|'system',body,raw_owner_input?:string,created_at,reply_to?:{id,author_name,body,unavailable?:boolean},read_by:string[],request_id?:string,state:'published'|'withdrawn'}
ReplyRequest {id,chat_id,source_message_id:string|null,target_twin_id,requester_id,source,status:'queued'|'running'|'completed'|'failed'|'cancelled',accepted_at,error?:string,can_cancel:boolean,can_retry:boolean,session_id?:string,run_id?:string}
ChatSettings {reply_for_me_enabled:boolean,prompt_override:string|null,sidebar_open:boolean,sidebar_tab:'activity'|'workspace'|'tracking'}
Approval {id,chat_id,approver_id,title,description,target,impact,status:'pending'|'approved'|'rejected',created_at}
Tracking {id,chat_id,title,description,status,created_at,updated_at,updates?:{id,body,created_at}[]}
Reminder {id,title,detail,due_at:string|null,done:boolean,chat_id?:string,kind?:string}
ActivityRun {session_id,run_id,parent_run_id?:string,source,status,started_at,finished_at?:string,title,summary?:string}
ActivityEvent {id,seq,type,time,label,input?:string,output?:string,status?:string}
WorkspaceEntry {id,name,kind:'file'|'folder',path,size?:number,children?:WorkspaceEntry[]}
AutomaticConversation {id,causal_chain_id,status:'paused',reason:'budget_exhausted'|'repeated_content',spent,allowance,remaining,paused_pending_count,continuation_allowance,can_continue,source_owner_id}

## Proactive tools and credential configuration

`list_authorized_chats` returns only existing authorized metadata: private owner context may discover the owner's chats, while group context discovers its current chat only. `send_chat_message {chat_id,body,source_message_ids}` checks the persisted originating request, complete context provenance and every destination audience member. Discovery is not permission to share private context. A stable SDK operation ID makes replay idempotent. Tool sends cannot create a human manual authorization or reset the causal chain. Final normal replies enter recipient routing only at successful run completion; intermediate semantic outputs remain recoverable but do not trigger other Twins.

The separate local credential routes use sanitized fixed-code responses rather than the ordinary `{detail}` errors. See `local-provider-configuration.md`: only the local AAA administrator can validate a personally submitted key against the fixed official endpoint, with explicit optional local saving. Neither validation nor saving enables Runtime generation.

## Module boundaries
Backend product worker owns app/db.py, app/models.py, app/service.py, app/api.py, app/config.py, pyproject/lock and app/main.py composition. Expose `service` object from app.service with database operations and `bind_runtime(runtime)` for run scheduling/activity; coordinate exact callbacks with Astra lead. Runtime bridge is app/runtime_bridge.py owned by Astra lead. Frontend worker owns entire frontend. Runtime SDK worker owns sibling agent-runtime.
