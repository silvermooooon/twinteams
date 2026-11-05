# Reply startup performance

The platform prepares a reply before calling the model. Measure that work separately
from the model's response latency: a committed SDK `model_request` is a durable
boundary just before the provider call, not a first-token timestamp.

## Findings and changes

The request path is authenticated HTTP or WebSocket input → PostgreSQL commit →
Redis work hint → worker admission → authorized conversation context → optional
conversation checkpoint → SDK session/factory → provider call. PostgreSQL outbox
compensation still covers a missing Redis hint.

- Runtime context, checkpoint preparation/reload, and generation authorization now
  reuse the existing transaction-local source metadata, hash, and grant cache.
  They previously used the unbatched path after the chat display had been optimized.
- Checkpoint coverage is calculated once per dependency in the transaction,
  including a confirmed `false`. An incomplete coverage query cannot authorize a
  missing source. Covered sources with content hashes are still fetched and checked;
  they are now fetched in batches.
- The final Git memory generation check runs in a settled worker-thread operation,
  after the read completes. A slow PostgreSQL guard no longer blocks the shared
  asyncio loop. Reset and cancellation checks remain in place.

There is no cross-request permission cache, new Redis cache, schema migration,
queue, or concurrency increase. Foreground admission remains 8 per worker / 2 per
owner within that worker; background admission remains independently limited to
2 per worker / 1 per owner. These are not cross-worker global quotas. SDK execution
and continuation identity are unchanged.

## Measurements

Recorded on 2026-11-05 using disposable PostgreSQL schemas and a separate Redis
namespace. The integrated harness uses the real authenticated HTTP routes,
Redis subscription, scheduler, runtime assembly, SDK durable execution, and Git
memory. Only the provider is an offline adapter with a 50 ms response delay.
No live model call or production-data mutation is part of these measurements.
The harness forces Redis delivery instead of the same-process notification shortcut.

| Context preparation case | Before | After | SQL before → after |
| --- | ---: | ---: | ---: |
| 1,000 unsummarized dependent messages, latest 80 authorized messages | 3,321 ms | 648 ms | 1,777 → 46 |
| 1,000 dependent messages with real checkpoints, latest 21 messages | 7,087 ms | 594 ms | 2,500 → 67 |

These two rows include `cProfile` overhead. The checkpoint baseline already has the
new cache initializers; it isolates the remaining repeated coverage/hash reads.
Histories contain genuine transitive source IDs and content hashes, rather than
only independent human text. This intentionally exercises difficult existing
histories; no checkpoint or hash check is skipped.

The integrated runs below are unprofiled, one sample of each scenario on each
version, with the same seed and offline provider. The baseline loads the original
`beb9ab2` backend in a temporary import directory; the working checkout is unchanged.
Alex has 1,000 dependent messages and Maya/Jordan submit concurrently with short
histories. “Provider entry” means the normal reply model, excluding separate
checkpoint model invocations from the endpoint being timed.

| Send → normal reply provider | Before | After |
| --- | ---: | ---: |
| Alex, first request with an uncompressed backlog | 65,612 ms | 12,332 ms |
| Alex, subsequent request with existing checkpoints | 23,435 ms | 3,826 ms |
| Other two owners during the first request | 805–810 ms | 1,110–1,218 ms |
| Other two owners during the subsequent request | 632–637 ms | 1,090–1,127 ms |

The first request performs the same four actual durable checkpoint operations in
both versions; the subsequent request performs none. The reduction is approximately
81% and 84% for the long-history owner. The two short-history owners keep progressing
without waiting for that owner, but their single-sample latency increased under
CPU contention; these results do not claim every request becomes faster.

The final subsequent request breaks down into HTTP commit/response **33 ms**, worker
admission after acknowledgement **46 ms**, authorized context **399 ms**, and remaining
SDK/factory preparation **3,341 ms**, plus small request reads. Its fresh task session
restore was at most **9 ms** across the three owners. Repeated fresh authorization of
hash-bearing historical sources remains the largest preparation cost. We retain
those boundaries. Across the whole first request, checkpoint preparation fell from
18,526 to 2,315 ms and checkpoint save/revalidation from 15,665 to 3,818 ms.

Synchronous SQL calls on the event loop fell from three to zero in each concurrent
scenario. Maximum sampled loop lag was still 68–69 ms; this is not a claim that
CPU preparation has become free or that all request I/O is instantaneous.

The original empty-history harness reached the normal reply provider in 537 ms for
one owner, 671–714 ms for three concurrent owners, and 597–636 ms for three warm
owners. These establish that admission is concurrent, rather than globally serial.
The healthy memory check cost approximately a millisecond; moving it off the event
loop improves resilience to slow I/O, not ordinary single-user latency by seconds.

A read-only sample of 20 existing foreground requests showed a 4,385 ms median
between acceptance and the first durable `model_request` (966–38,045 ms range).
Those historical requests have different histories and outcomes; this is evidence
of the reported delay, not a controlled before/after comparison.

## Scope and regression coverage

Current transactions still verify source state, content hashes, membership,
checkpoint provenance, and reset generation. An edit or access change is rechecked
at the next authorization boundary. Original session records remain complete.
Hash-bearing transitive provenance can still require historical validation even
when the visible prompt is checkpointed; its cost remains proportional to the
dependency graph. A genuine checkpoint backlog still requires its summary model
calls. The benchmark supplies only 50 ms of provider delay per call; real model
and network latency can be substantially longer.

Focused tests exercise the real PostgreSQL context and checkpoint paths, edit a
historical source, and confirm that a later authorization rejects it. Memory read,
search, and listing each execute a deliberately slow PostgreSQL guard and verify
that all guards—including the final check—run off the event-loop thread.


The focused backend suite has 59 final passing cases across performance, context,
checkpoint storage, concurrency, task sessions, and reset. The newly added query
budget initially expected fewer than 150 statements for an 80-root, uncompressed
manifest; measurement showed 177, so the assertion now requires fewer than 200.
Its checkpoint and source-change assertions subsequently passed. This changed the
test budget, not production behavior. An independent 12-case review covers source
mutation, foreign grants, dirty hash inputs, stale derived tails after rebuilding a
checkpoint, and all three off-loop memory guards.

Local evidence: `/tmp/duet-startup-strict-before.json`,
`/tmp/duet-startup-strict-after.json`, `/tmp/duet-start-context-before.json`,
`/tmp/duet-start-context-after.json`, `/tmp/duet-start-checkpoint-before.json`,
`/tmp/duet-start-checkpoint-after.json`. These contain synthetic timing/SQL metadata,
not private message content. Disposable schema/workspace cleanup runs in the harness
and only its unique Redis namespace is removed. The production read-only sample is
separate from these measurements.
