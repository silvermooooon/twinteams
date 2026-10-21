# Owner-attributed sends and reply followups

From either a group or its owner's private conversation, a Twin may prepare a new statement
and send it to another existing chat where that owner is currently a member, including its owner's
private chat for a progress report or decision. Current source and destination access are still
checked. The published body does not grant recipients access to the originating history; its
internal request, ContextManifest and ProductOperation retain the audit link. Recipient message
JSON omits a private originating request reference. Never copy unrelated private details into
an outward statement. This publication rule does not relax ordinary model-output audience checks.
The current chat receives normal assistant output; `send_chat_message` rejects that destination
to avoid a tool send followed by a second ordinary reply. Previously committed operation receipts
still replay their original result without sending again.

`send_chat_message` accepts `reply_to_message_id` and `wait_for_reply` (default `false`). Only an
explicit `true` from the owner's private Twin to an existing authorized group creates a followup.
The message and followup are committed together. The deadline is fixed at thirty minutes after
the initial send; replaying the same tool operation returns the same message and deadline.
Sending completes immediately. No model, subagent or execution worker remains waiting.

`TwinFollowup` stores the origin private lane/request, owner, destination, sent message, original
causal chain, deadline, terminal outcome and continuation delivery state. Completed rows remain
for audit. The authoritative schema is `backend/app/models.py`; the repeatable additive provisioner
and startup checks are `backend/app/followup_schema.py`. `twin-followups-schema.sql` is generated
reference DDL; deployments should run the existing `python -m app.init_db` provisioning command.

A published message by another owner closes the wait only when it explicitly replies to the
outbound message in the same target chat. Human messages and explicit Twin send-tool actions
are checked at commit. An ordinary model run closes a wait only at completed final output;
intermediate narration does not count. Unrelated group traffic does not affect the deadline.
Reply and timeout take the same existing chat-scope and followup-row locks; the first terminal
commit wins. A late reply after timeout does not create a second notification. After a reply
already completed the followup, a further explicit reply is ordinary group traffic rather than
another completion of that task. Timed-out late replies suppress the old owner's ordinary route
to avoid a duplicate wakeup; other eligible group recipients retain their normal routing.

The terminal state and one reliable `followup_result` ReplyRequest are committed in the same PG
transaction. The request enters the original owner's private FIFO, carrying only the outbound
text, destination and the visible reply/author or timeout result. External text is untrusted data,
not a new owner instruction. Current source and destination permissions are rechecked and reply
provenance is included in the private continuation's ContextManifest. Revoked or cancelled
origins are blocked. A source string alone cannot grant private shell or credential access.
The continuation inherits the original nonsecret execution configuration; it does not scan or
replay full session history. Its completed empty final response is silent.
An explicit retry keeps the `followup_result` source and canonical intent, incrementing the
request generation. It reuses the reservation and chain, rechecks reply access and retains the
terminal-only/silent policy; retrying does not start a new wait or reset its deadline or budget.

Delivery consumes one AutomaticReservation on the original causal chain. The existing six-reply
allowance and lifetime spending remain in effect across chats, replies and timeouts. An exhausted
chain leaves a durable terminal followup with delivery paused; the existing owner Continue API
adds the explicitly granted allowance and delivers it once. No timeout resets the budget.
An exhausted chain also pauses response routing before a model router call. Explicit replies
already consumed by a followup exclude that owner from ordinary group routing for that reply.

The worker uses the existing Redis change hints and scheduler `deadlines` map. The followup
index provides the next deadline and bounded due ranges through `process_followups`; there is
no new recurring per-followup polling job, Redis scheduler or permanent LLM waiter. Startup and
the existing low-frequency compensation tick recover due or pending delivery rows when hints
are lost. This existing compensation is distinct from adding a new periodic wait scanner.
