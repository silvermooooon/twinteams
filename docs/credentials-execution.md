# Twin credentials and local execution

The personal Credentials tab is an owner-only interface. The list contains names/references,
descriptions and environment variable names. Values are entered through a dedicated HTTP form,
never through conversation messages. Responses, validation errors and exception logs do not
include submitted values. The browser must not retain or refill values after a form closes.

`AGENT_SECRETS_KEY` is a persistent Fernet key supplied by the host. `AGENT_SECRETS_DIR` optionally
selects storage; the default is `backend/data/runtime-secrets`, outside the Twin workspace.
Missing/invalid configuration leaves the rest of the application available and exposes
`configured: false` on the credential list. No keys are generated during startup. Back up the
host key with the encrypted files; losing it makes the files unreadable.

All authorized conversations use the target Twin owner's local execution and credential tools,
including approved two-Duet and multi-Duet threads. Human chats are inspection-only for agents. Runtime identity, workspace, Skills, credentials and Memory belong to the executing Duet's own owner. Connection approval once or directed always grants authorize communication only; they do not authorize arbitrary actions or peer access to private resources. Sensitive actions still require the owner's existing task consent or exact approval. Revocation closes dependent conversations and cancels their pending work.

The first factory invocation after the SDK acquires its execution lease freezes a nonsecret
`ReplyRequest.execution_config`: the Twin's existing private-chat workspace, enabled Skills
catalog and permitted credential metadata. Main recovery and background workers use the same
configuration. Child-result continuations inherit it; a credential-completion continuation is a
new request and reads the updated credential catalog. No model/provider configuration is frozen
here; the SDK's existing journal retains that recovery contract.

Unfinished runs accepted before this feature have no execution snapshot. They recover with the
original product tools and original private-main memory tools, without adding shell, credential
or Skills tools to the journal's existing contract. Their workers also keep the old tool scope.
A new prompt in an idle session receives the new configuration normally.

Execution uses the SDK `LocalExecutionBackend` and `create_execution_tools`. A small backend
adapter sets `cwd` to the existing private-chat workspace; SDK home/tmp/output paths stay host
managed. The host freezes `describe` metadata and resolves values afresh for every shell call.
Rotation with unchanged environment variable names is observed by the next call; deletion or
changed names rejects execution. A call receives only its explicitly requested references.
Each shell has a new environment mapping, and its credentials are not inherited by later calls.

`request_credential` accepts metadata only, inserts a durable pending `CredentialRequest` and
returns immediately. The Agent continues independent authorized work, then explains the blocker and ends its response,
releasing the runtime worker and FIFO lane. Response completion is not task completion while a form is
pending. The pending card is owner-only. It can be dismissed without performing an action.

Completing a form writes the encrypted credential first. The same PG business transaction then
marks the request fulfilled and enqueues one `credential_result` request through the existing
FIFO using a stable request key. The original requester, source owner, source message and causal chain are retained rather than promoted to the credential-supplying owner. Owner administrative writes serialize on the existing Owner
row; no new lock service or plaintext queue is used. Repeated completion returns the existing
continuation without rewriting the value. Target-owner identity, authorized origin, original cancellation,
and current source authorization are checked before admission. Cancellation and admission use
the existing lane/origin row order. A cancelled request cannot continue.

File replacement and the PG commit are intentionally not atomic. If the process fails after
saving the file, the form remains pending and the credential may already exist. Recovery never
infers that this particular form completed. The owner can resubmit the form or explicitly select
the matching existing credential. No plaintext value, write receipt or extra saga is persisted.
The selected credential must retain the requested reference and exact environment variable names.

The continuation asks for remaining work using existing history. It never resumes the old run
or programmatically reruns its shell commands. Exact installed execution actions can use the existing owner approval inbox. Their declared schemas
are validated server-side, and approval continuations retain the frozen originating configuration.
Local execution authorization binds one tool operation, owner, target Twin and causal chain;
no post-approval argument overrides are accepted. Local-capable approval execution and SDK bash
retain `replay="never"`; uncertain shell
outcomes still require the existing recovery/reconciliation flow. This does not promise that a
model can never independently propose a semantically duplicate command.

Before its first model request, the continuation rechecks originating source authorization and
reads the exact originating SDK run's committed tool calls and results through `read_run_records`.
If a worker requested the credential, both the root run and that worker run are included, each
read once. `CredentialRequest` keeps only source session/run/command references; it does not
mirror the journal. The resulting facts appear in a labelled historical-data section of the
new system context, including successful shell commands and their observed results. The SDK
then freezes that context normally; recovering the same continuation does not reread or alter
an already frozen model request. There is no full-session scan and no execution of recorded calls.

If a worker is still normally finishing when the owner submits the form, the continuation stays
queued through the existing `RuntimeBusy` path and releases its execution slot. The worker's
terminal notification clears its deferred scheduling hint and wakes the existing queue. Missing
or unavailable source history, an unsettled failed run, or an unknown tool outcome blocks
automatic continuation and requires reconciliation instead of supplying an empty history.

This is a trusted local demonstration with the same OS user and privileges. Literal value masking
occurs before shell output buffering, and prompts prohibit printing/persisting/exfiltrating
credentials. These controls are not a sandbox and do not guarantee secrets cannot be leaked by
arbitrary code with the same OS privileges.

Offline acceptance is in `backend/tests/test_credentials.py`: isolated PG schemas, temporary
encrypted stores, fixed fake credentials and real local shells. It covers durable nonblocking
requests, owner/group scope, single continuation, cancellation, the file/PG crash gap, output
masking, per-call environment lifetime, rotation/deletion/binding changes, and form-error redaction.

Verification on 2026-10-18: `tests/test_credentials.py tests/test_recovery.py` passed all 20 tests
in 14.51 seconds. The eleven credential tests also cover Unicode names, main/worker/group tool
scope, a frozen catalog across factory recreation, live value rotation, legacy unfinished runs,
and cancellation after a completion already queued its continuation. This run used a disposable
PG schema and fake credentials; it did not call a real model or change the live application schema.
Two later targeted regressions additionally reproduced preparation with a real shell followed by
a credential form and verified that the next model input contains the committed command/result
without rerunning it. They also cover root plus worker progress, a normally finishing worker
remaining queued, terminal-notification wakeup, and an unknown outcome requiring reconciliation.
