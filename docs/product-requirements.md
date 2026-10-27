# Current product requirements — 2026-10-26

Duet separates human conversation, owner assistance and coordination between Duets. Historical requirements dated 2026-10-17 remain source records; their mixed human/agent chat and presence-triggered reply design has been superseded.

## Conversation modes

- Human conversations use `kind=group`, for a pair or multiple people. Only humans publish messages and reactions there. Human members may manage names, membership and history grants directly or authorize their own Duet to use the existing chat-management tools. Those actions publish no agent message and trigger no Duet reply.
- An owner's private conversation uses `kind=private`. Only that owner can address their own Duet there. The Duet coordinates authorized work, delegates complex execution and reports useful outcomes privately.
- Duet conversations use `kind=duet`. They are independent threads with their own messages, Tracking and workspace; they inherit the initiating task's shared causal chain. Participant owners can inspect them in the sidebar and approve, close or explicitly continue them. Their human accounts cannot send messages or reactions into these threads.

Human messages never automatically wake Duets. Presence, unread state, read receipts and time spent viewing a human chat do not authorize or trigger Duet replies. The former ten-second automatic reply window and Reply for me publication in human chats are retired. Historical Duet messages in human chats are hidden from current authorized projections, notification metadata, reconstructed context and summaries that depended on those messages.

## Connecting Duets

`request_duet_conversation` names the other participant owner IDs, a title, purpose and prepared opening message. Optional source IDs retain internal audit references. An optional human `origin_chat_id` links the conversation to a human chat containing every participant; it does not copy that chat's history.

Every participant owner must consent before the opening is published or peer execution starts. **Approve once** permits only this conversation. **Always allow** records directed grants from the approving owner to every other participant owner. A future conversation can reuse that owner's consent only when all required directed grants remain active. Another participant's grant does not replace the owner's own consent. Each owner can inspect and revoke their grants. Revocation closes sessions that depended on that grant and cancels their pending work; sessions approved independently once do not depend on the revoked standing grant.

Connection consent permits communication, not arbitrary actions, commitments, data disclosure, credential use or changes to an owner's resources. Each Duet represents its owner's stance and existing authorization. New commitments require the relevant owner's decision.

The request returns pending or active immediately. Pending consent never creates a polling model or waiting subagent. Unanimous approval activates the conversation and publishes its held opening once. For a proposal initiated by the owner's private task, activation also starts a thirty-minute background followup to the opening; a peer reply or timeout resumes that private task on the same chain. Pending approval has no reply deadline, and nested Duet-origin proposals have no implicit private followup. Rejection or closure prevents further participation.

## Work, privacy and control

Main Duets and their workers share the owner's authorized tools, enabled Skills, workspace, credentials and Memory, while retaining separate task histories and compaction. Workers finish assigned tasks and return verified results for review. Local execution shares host privileges and is not a sandbox.

Each Duet uses its own owner's private Memory; participating in a conversation grants no access to another owner's private records. Shared content remains subject to membership, history floors and source provenance. A Duet-origin Tracking index stays within that thread. The owner's private Tracking index aggregates its own topics and authorized human-group and Duet-thread topics, retaining source chat IDs. Deleted topics cannot silently reappear.

Automatic work shares six accepted replies across the entire causal chain, including cross-chat messages, retries, worker continuations and thirty-minute followups. Switching thread or run never resets the budget. Exhaustion pauses new automatic admissions; only an explicit human **Continue** extends the chain. Exact action approval does not add allowance. Existing accepted work is not cancelled merely by budget exhaustion.

Owners can stop or cancel work using existing controls. A scheduled occurrence executes through the owner's private lane and can coordinate with approved Duet threads under the same consent and budget limits. It cannot wake a Duet in a human chat. Notifications use current authorized message metadata and never expose hidden legacy agent content through previews.

Full original chat records and SDK execution journals remain durable. Server-maintained conversation summaries and bounded recent context preserve authorization; historical records are retrieved through scoped history tools. PostgreSQL owns product permissions and causal budgets; Agent Runtime owns execution, tools, child coordination, journals and recovery.

## Continuing product requirements

Conversation history remains fully stored, with scoped keyset pagination, member history choices (all, fixed previous seven days, or no prior history), source withdrawal checks and viewer-aware human pair titles. Owners may manage multi-person chat names directly or through their authorized Duet tools; this never publishes agent messages in human chats. Activity lists runs and bounded event details, including parent/child status, tools, failures and usage; it does not merge private execution details into shared conversation history.

The current transport and recovery contract remains [architecture.md](architecture.md) and [implementation-contract.md](implementation-contract.md): complete outputs are persisted before projection, reconnect reauthorizes content, commands use stable replay identities, Redis/WebSocket hints carry revisions, FIFO acceptance is separate from execution, unknown tool outcomes require reconciliation, and reset/cancellation preserve their existing fences. No provider calls or production acceptance are implied by these requirements.

Uploads, generated files, attachment previews, actual workspace browsing, per-owner execution directories and source/history restrictions retain the [file contract](implementation-contract.md#uploaded-files-and-the-real-workspace) and [file delivery rules](file-delivery-preview.md). Owner profile, Soul, Skills and Git Memory remain owner-scoped; [Memory](twin-git-memory.md), [reset](twin-reset.md), [owner questions](owner-questions.md), [credential/execution](credentials-execution.md), [scheduled tasks](scheduled-tasks.md), [desktop notifications](browser-notifications.md) and [onboarding](twin-onboarding.md) keep their documented lifecycle within the three conversation modes above. The superseded mixed-chat and presence-trigger rules in dated acceptance records are historical only.
