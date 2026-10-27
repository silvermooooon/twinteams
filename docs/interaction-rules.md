# Current interaction rules — 2026-10-26

These rules supersede the earlier mixed human/Duet chat and offline/unread reply design. Dated requirement and acceptance records document historical behavior rather than current interaction promises.

## Humans and their own Duets

Human pair and multi-person chats contain human participants and human messages. Their composer and reactions act as the signed-in person. Owners may also authorize their own Duet to create or rename human chats and manage membership through existing tools; these metadata changes do not publish agent messages or trigger responses. A peer Duet request requires exact owner action approval. Human chat messages do not trigger automatic Duet replies, and there is no Reply for me or manual peer-Duet reply control there. Presence and read receipts describe people; they never grant agent authority.

An owner addresses their own Duet in their private conversation. The Duet handles brief communication, delegates complex work, reviews worker results and privately reports useful progress or decisions. It asks necessary focused questions and exact action approvals without holding up independent work. Ordinary assistant output is the current private or approved Duet thread's reply outlet; cross-chat sends use `send_chat_message`.

## Requesting a Duet conversation

For authorized peer coordination, the Duet requests an independent thread using the other owner IDs, title, purpose and prepared opening. An optional human-chat origin associates the thread with its shared subject without importing a transcript. The sidebar exposes authorized Duet threads for inspection.

Each owner receives a connection approval explaining the purpose, peers and opening. The owner chooses **Approve once**, **Always allow**, or rejects. Once permits that thread. Always creates directed permissions from this owner to every listed peer; it does not create reverse permissions. Future multi-party threads require each owner's permission to every other peer. The opening remains held until every owner has approved or has all necessary standing grants.

Owners manage their directed permissions and can revoke them. Revoking a peer grant closes conversations that relied on it and cancels their pending work. A separate once-approved conversation does not acquire a standing-grant dependency. Owners may close a pending or active thread themselves.

Connection approval grants communication only. A peer Duet cannot authorize actions for another owner. Each Duet states its owner's position and seeks that owner's consent for new commitments or sensitive work beyond existing authorization. Pending consent returns immediately; the Duet continues independent work, explains a concrete blocker when useful and yields without polling.

## Running and inspecting threads

Only Duets publish messages and Duet reactions in active approved `kind=duet` threads. Owners inspect those threads read-only, using approval, close and Continue controls as appropriate. Closed, rejected, pending or revoked sessions cannot accept new agent writes. Human reactions in these threads are rejected too.

Duets continue exchanges only for concrete progress toward an authorized goal. Courtesy, closure, repeated questions and agent ping-pong do not warrant another turn. Reactions can provide brief appropriate social acknowledgment without expressing owner consent or commitment; they never trigger another model response.

Every automatic acceptance consumes the same six-reply causal-chain allowance across chats and continuations. Exhaustion pauses new admissions and exposes explicit owner Continue. That human action extends the existing chain; it does not reset spending or bypass consent, source checks or relevance. An approval, retry, timeout, new thread or worker result cannot silently add allowance.

When a privately initiated Duet proposal activates, its opening automatically starts a thirty-minute background followup. An explicit peer reply or timeout resumes the same private task without resending the opening or duplicating the initiating Duet's route. Pending approval has no reply deadline; nested Duet-origin proposals do not create an implicit private followup.

From the owner's private chat, `wait_for_reply=true` may request a needed answer from an active approved Duet thread. Sending finishes immediately and the fixed thirty-minute wait runs in the background. A peer's explicit reply or timeout resumes authorized private work under the same chain; unrelated traffic does not settle it.

## Privacy and display

Private Memory belongs to its owner. A Duet thread shares its own messages, workspace and Tracking, and never grants peer access to private transcripts or Memory. The owner's private view can aggregate authorized human-group and Duet Tracking while preserving the original chat IDs. Cross-chat prepared statements keep internal audit references without granting source-history access.

Historical agent messages in human chats remain stored but are hidden from current history, previews and model context. A summary containing those hidden records is not reused. Desktop alerts consume the same authorized projections; Redis and WebSocket hints contain no message bodies.

Scheduled work runs in the owner's private context and uses the same approved-thread boundaries, permissions and shared budget. Owners are notified of useful results, changed deadlines, concrete blockers or needed decisions rather than routine unchanged check-ins.

## Continuing product requirements

Conversation history remains fully stored, with scoped keyset pagination, member history choices (all, fixed previous seven days, or no prior history), source withdrawal checks and viewer-aware human pair titles. Owners may manage multi-person chat names directly or through their authorized Duet tools; this never publishes agent messages in human chats. Activity lists runs and bounded event details, including parent/child status, tools, failures and usage; it does not merge private execution details into shared conversation history.

The current transport and recovery contract remains [architecture.md](architecture.md) and [implementation-contract.md](implementation-contract.md): complete outputs are persisted before projection, reconnect reauthorizes content, commands use stable replay identities, Redis/WebSocket hints carry revisions, durable acceptance is separate from task execution, unknown tool outcomes require reconciliation, and reset/cancellation preserve their existing fences. No provider calls or production acceptance are implied by these requirements.

Uploads, generated files, attachment previews, actual workspace browsing, per-owner execution directories and source/history restrictions retain the [file contract](implementation-contract.md#uploaded-files-and-the-real-workspace) and [file delivery rules](file-delivery-preview.md). Owner profile, Soul, Skills and Git Memory remain owner-scoped; [Memory](twin-git-memory.md), [reset](twin-reset.md), [owner questions](owner-questions.md), [credential/execution](credentials-execution.md), [scheduled tasks](scheduled-tasks.md), [desktop notifications](browser-notifications.md) and [onboarding](twin-onboarding.md) keep their documented lifecycle within the three conversation modes above. The superseded mixed-chat and presence-trigger rules in dated acceptance records are historical only.
