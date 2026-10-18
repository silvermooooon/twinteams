# Recovery fixes and repository consolidation

Twinteams uses the original `silvermooooon/agent-runtime` as an editable sibling
checkout. The useful durable execution, SQLite and child-agent work from the
separate snapshot is incorporated there. Agent loop/session state stays canonical
in SDK records; product tables own presentation, business scheduling and access.

## Corrected boundaries

1. Lost execution ownership is retriable coordination, not a terminal reply failure.
   SDK `ExecutionLeaseError`, including errors wrapped by a parallel tool batch,
   propagates. The product keeps the request/lane for canonical reconciliation.
2. Recovery reprojects every committed model output by SDK event ID, including
   outputs committed before a process died and before the product write. Equal
   text from distinct events is preserved; replay of one event is deduplicated.
3. Concurrent routing that finds SDK work busy leaves the decision pending.
   Only an actual terminal routing result finalizes it.
4. SDK approval request, decision and exact-action binding reuse immutable commands.
   The product reconciles a durable decision into one new FIFO continuation. The
   original reply can finish while consent is pending. `execute_approved_action`
   checks the current audience/source access and original request cancellation,
   then executes the frozen action with a stable product-operation ID. The only
   installed action is `create_reminder`; pending/rejected approval cannot execute,
   conflicting decisions fail, and repeated execution returns one reminder.
5. Deleted Tracking topics stay deleted when old sources are reused, even with a
   changed title. Following a tombstone, automatic creation must cite explicit new
   messages; restoring the old topic uses the explicit user create action. This is
   a source convention, not a heuristic semantic similarity classifier.
6. Up to four routing jobs run independently of reply dispatch. A slow model router
   no longer blocks another chat's ready reply. Routing tasks are tracked and
   cancelled/settled on shutdown.

## Verification

`backend/tests/test_recovery.py` uses isolated temporary SQLite databases and an
offline model adapter. It checks stale ownership, crash-gap output replay,
concurrent routing retries, slow-router isolation, Tracking tombstones, and the
approval decision/projection crash gap plus one-effect replay. No mock is used by
the normal application. The existing bounded product/runtime/conversation/message
bridge checks additionally exercise FIFO, cancellation, child results, source
visibility and automatic-reply budgets. SDK tests and frontend typecheck/build
cover their respective packages.

The local instance uses Python 3.13, SQLite and an explicitly configured GPT-6 Luna
provider. PostgreSQL multi-worker behavior and external email/payment effects are
not certified by these local tests. No external effect adapter is installed.
Production continues to require verified identity, explicit schema provisioning,
backup/restore validation and persistent workspace storage.

## Local live verification (2026-10-18)

The normal app was started on `http://127.0.0.1:8000` using the existing original
Runtime environment file, without copying credentials or changing system settings.
DEV-AAA login, a real OpenAI Responses `gpt-6-luna` reply, and page reload with the
committed reply/Activity preserved were checked in the browser. The successful
canonical model event records 1,601 input and 14 output tokens. The first attempt
failed with an empty exception message, whose exact cause was not recorded; an
explicit UI Retry succeeded. SDK empty provider errors now retain their exception
class instead of incorrectly displaying “Operation aborted”.

Verification: 297 SDK tests (21 skipped for unavailable PostgreSQL/S3 test
conditions in the app environment), SDK Ruff checks, 8 backend tests, all five
bounded acceptance scripts, frontend typecheck and production build. The skipped
cases and offline adapters do not constitute a live PostgreSQL or production
deployment test.
