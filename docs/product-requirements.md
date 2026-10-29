# Current product requirements — 2026-10-29

Duet separates human conversation, owner assistance and coordination between Duets. Current guidance describes the message-driven Duet channel contract.

## Conversation modes

- Human conversations use `kind=group`, for a pair or multiple people. Only humans publish messages and reactions there. Human members may manage names, membership and history grants directly or authorize their own Duet to use the existing chat-management tools. Those actions publish no agent message and trigger no Duet reply.
- An owner's private conversation uses `kind=private`. Only that owner can address their own Duet there. The Duet coordinates authorized work, delegates complex execution and reports useful outcomes privately.
- Duet conversations use `kind=duet`. They are independent threads with their own messages, Tracking and workspace; they inherit the initiating task's shared causal chain. Participant owners can inspect them in the sidebar and approve, close or explicitly continue them. Their human accounts cannot send messages or reactions into these threads.

Human messages never automatically wake Duets. Presence, unread state, read receipts and time spent viewing a human chat do not authorize or trigger Duet replies. The former ten-second automatic reply window and Reply for me publication in human chats are retired. Historical Duet messages in human chats are hidden from current authorized projections, notification metadata, reconstructed context and summaries that depended on those messages.

## Connecting Duets

`request_duet_conversation` names the other participant owner IDs, a title, purpose and prepared opening message. Optional source IDs retain internal audit references. An optional human `origin_chat_id` links the conversation to a human chat containing every participant; it does not copy that chat's history.

Every participant owner must consent before the opening is published or peer execution starts. **Approve once** permits only this conversation. **Always allow** records directed grants from the approving owner to every other participant owner. A future conversation can reuse that owner's consent only when all required directed grants remain active. Another participant's grant does not replace the owner's own consent. Each owner can inspect and revoke their grants. Revocation closes sessions that depended on that grant and cancels their pending work; sessions approved independently once do not depend on the revoked standing grant.

Connection consent permits communication, not arbitrary actions, commitments, data disclosure, credential use or changes to an owner's resources. Each Duet represents its owner's stance and existing authorization. New commitments require the relevant owner's decision.

The request returns pending or active immediately. Pending consent creates no polling model or waiting subagent. Unanimous approval permits the held opening to publish once within the remaining peer-send allowance. Rejection, closure and revocation prevent further publication.

Every published message in an active approved Duet channel is durably delivered to every other participating Duet. There is no relevance classifier or needs-response gate. Receipt is not a requirement to answer: a Duet may finish silently when no useful response or owner report is needed. An ordinary terminal assistant final publishes one peer message in its current Duet channel; intermediate tool-loop text stays in the journal. `send_chat_message` explicitly sends to another approved channel or the owner’s private chat. A private report can be followed by an empty final without posting a courtesy reply to peers.

All Duet-channel outgoing publications—terminal assistant finals, explicit cross-chat sends and `present_files` deliveries—use prepared outgoing statements. Recipients can read the prepared body and authorized delivered file snapshots without access to the sender’s original private history. Source IDs, request context manifests and the SDK journal remain internal audit evidence; publication does not grant access to those records or make recipient display depend on reading them. Source-history tools keep their existing ACLs, and ordinary private-chat projection keeps its source checks. The sender’s current source and destination permissions are still checked, including again before a waiting message is published. Prepared publication does not authorize arbitrary actions, unrelated disclosure or sharing private input attachments.

## Work, privacy and control

Main Duets and their workers share the owner's authorized tools, enabled Skills, workspace, credentials and Memory, while retaining separate task histories and compaction. Workers finish assigned tasks and return verified results for review. Local execution shares host privileges and is not a sandbox.

Each Duet uses its own owner's private Memory; participating in a conversation grants no access to another owner's private records. Shared content remains subject to membership, history floors and source provenance. A Duet-origin Tracking index stays within that thread. The owner's private Tracking index aggregates its own topics and authorized human-group and Duet-thread topics, retaining source chat IDs. Deleted topics cannot silently reappear.

The original causal chain permits six published peer messages across its channels, including the opening. The sixth message is delivered; a seventh prepared message waits unsent. Receiving a message, private owner reports, owner questions and answers, task execution, child results and retries do not themselves spend peer-message allowance. An active participant can explicitly Continue a waiting exchange to add six sends to the same chain. Prepared messages publish only after that authorization and current permission checks. Stable send identities and consent keys prevent double publication or duplicate grants.

Owners can stop or cancel work using existing controls. A scheduled occurrence executes as an independent task reported in the owner's private chat and can coordinate with approved Duet threads under the same consent and budget limits. It cannot wake a Duet in a human chat. Notifications use current authorized message metadata and never expose hidden legacy agent content through previews.

Full original chat records and SDK execution journals remain durable. Server-maintained conversation summaries and bounded recent context preserve authorization; historical records are retrieved through scoped history tools. PostgreSQL owns product permissions and causal budgets; Agent Runtime owns execution, tools, child coordination, journals and recovery.

## Continuing product requirements

Conversation history remains fully stored, with scoped keyset pagination, member history choices (all, fixed previous seven days, or no prior history), source withdrawal checks and viewer-aware human pair titles. Owners may manage multi-person chat names directly or through their authorized Duet tools; this never publishes agent messages in human chats. Activity lists runs and bounded event details, including parent/child status, tools, failures and usage; it does not merge private execution details into shared conversation history.

The current transport and recovery contract remains [architecture.md](architecture.md) and [implementation-contract.md](implementation-contract.md): complete outputs are persisted before projection, reconnect reauthorizes content, commands use stable replay identities, Redis/WebSocket hints carry revisions, durable acceptance is separate from task execution, unknown tool outcomes require reconciliation, and reset/cancellation preserve their existing fences. No provider calls or production acceptance are implied by these requirements.

Uploads, generated files, attachment previews, actual workspace browsing, per-owner execution directories and source/history restrictions retain the [file contract](implementation-contract.md#uploaded-files-and-the-real-workspace) and [file delivery rules](file-delivery-preview.md). Owner profile, Soul, Skills and Git Memory remain owner-scoped; [Memory](twin-git-memory.md), [reset](twin-reset.md), [owner questions](owner-questions.md), [credential/execution](credentials-execution.md), [scheduled tasks](scheduled-tasks.md), [desktop notifications](browser-notifications.md) and [onboarding](twin-onboarding.md) keep their documented lifecycle within the three conversation modes above. Dated acceptance records are historical only.
