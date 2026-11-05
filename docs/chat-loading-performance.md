# Chat loading measurements (2026-11-05)

The slow path was authorization of dependent Duet messages, not the latest-page
query. A 30-message page could recursively visit the full conversation and issue
two reads per historical source. Bootstrap repeated that work for the preview.

The backend now batches immediate source metadata and hash inputs, checks its
request-local result cache before repeated ORM access, and reuses hash/grant
results within each bootstrap chat projection. Source hashes, attachment hashes,
withdrawal and current membership checks remain unchanged. There is no new
database schema, index or Redis cache. Private tracking remains unchanged; its
measured cost was small and did not justify changing that contract.

Viewport read receipts use the same cache for their whole batch. Otherwise, after
the page appeared, each visible Duet message could independently revalidate the
full history while holding the chat lock. In the 1,000-message dependent corpus,
marking two messages read fell from 3,998 SQL statements / 4,938 ms to 18 / 256 ms
without a profiler. Already-existing receipts still undergo source authorization.

Presence view-interval audit writes no longer increment chat-content revisions.
Previously, simply switching away from a chat closed its interval and invalidated
the cache just saved for that chat. Presence and audit records still update;
actual new read receipts continue to change the chat revision.

## Backend results

The synthetic uncompressed corpus has 5,000 messages, 2,500 completed requests
and 2,500 read receipts. Each Duet message depends on the previous 80 messages,
including other Duet outputs. It reproduces recursive history growth even when
everything is already read. Measurements use disposable local PostgreSQL schemas,
not production content, and make no model calls.

With **cProfile enabled**, before and after on the same data:

| Read | SQL statements before → after | Elapsed before → after | SQL execution before → after |
| --- | --- | --- | --- |
| Latest 30 messages | 9,959 → 96 | 15.65 s → 2.96 s | 5.31 s → 0.28 s |
| Bootstrap | 9,927 → 91 | 19.46 s → 2.99 s | 5.54 s → 0.27 s |

Full response DTOs matched, excluding bootstrap's current server time. Profiling
adds Python tracing overhead; these durations are not browser latency estimates.

Separate runs **without cProfile** used three samples per read. The checkpoint
corpus contains 5,000 prior facts compacted through 20 real `prepare`/`save`
operations, covering sequence 4,980, followed by 60 current messages with hashed
message and summary dependencies. Summary text is a synthetic fixture input.

| Corpus | Read | Median elapsed | SQL statements | Temporary Python peak |
| --- | --- | --- | --- | --- |
| 5,000 uncompressed dependent messages | Latest 30 | 1,382 ms | 96 | 111 MiB |
| Same | Bootstrap | 1,370 ms | 91 | 109 MiB |
| 5,060 messages with checkpoint | Latest 30 | 121 ms | 93 | 2.10 MiB |
| Same | Bootstrap | 85 ms | 88 | 1.94 MiB |

Memory was measured in separate `tracemalloc` runs, not the timing samples. After
the uncompressed request returned, tracked retained allocations were below
0.61 MiB. These are Python allocation measurements, not process RSS or a capacity
guarantee. Uncompressed transitive history remains O(N) and can consume substantial
temporary memory. No cross-request backend cache retains its authorization data.

On the 1,000-message diagnostic corpus, `EXPLAIN (ANALYZE, BUFFERS)` showed the
existing chat/sequence index returning 31 rows in 0.033 ms and pending-request
indexes taking 0.025 ms. Unread scans took about 2–3 ms. New indexes would not
address the measured source-by-source round trips, so none were added.

## Verification and evidence

The 81 affected PostgreSQL regressions passed, including long dependency chains,
source edits, withdrawal, attachment hashes, checkpoint invalidation, cross-chat
permissions and question continuation. The new regression bounds SQL counts and
checks that editing an old source hides newly invalid dependent replies.
After identifying the read-receipt long tail in browser acceptance, five focused
performance/unread tests passed again with the batch change, including refusal to
mark a newly unauthorized message read; two existing Duet-read tests also passed.
A focused presence-switch audit regression and the existing notification metadata
suite passed 10 tests after separating audit writes from chat-content changes.

Local diagnostic evidence: `/tmp/duet-synthetic-load-1000-before.json`,
`/tmp/duet-synthetic-load-1000-after.json`,
`/tmp/duet-synthetic-load-5000-comparison.json`,
`/tmp/duet-load-final-unsummarized.json`, and
`/tmp/duet-load-final-checkpoint.json`. Production sampling was read-only and emitted
only timings, query counts and function names; its small histories are not the
large-history benchmark.

## Browser navigation

Installed Chrome used the 5,000-message uncompressed dependent corpus above,
with real HTTP, PostgreSQL, WebSocket and rendering paths, without model calls. These are **one
sample per scenario**, not medians or a production capacity claim. The measurement
ends when the expected transcript is visible, before background synchronization
necessarily finishes.

| Scenario | Before | After |
| --- | --- | --- |
| Sign in and open the private chat | 45,600 ms | 3,381 ms |
| Switch back in the same login | 12,864 ms | 69 ms |
| Refresh the page | 37,031 ms | 3,382 ms |
| Return after loading 130 messages | 12,872 ms | 170 ms |
| Refresh with 700 ms emulated network latency | 63,533 ms | 5,309 ms |
| Switch back with 700 ms latency | 12,869 ms | 62 ms |
| Rapid A→B→A switching with 700 ms latency | 15,959 ms | 179 ms |

The browser keeps up to five recent chats and 130 messages per chat in memory
within the same login. Returning restores the transcript and scroll anchor before
HTTP synchronization completes; it does not eliminate that synchronization. The
130-message test restored the same message at the same 98 px offset. A history
larger than the cache cap returns to the latest 30 messages. Refresh starts fresh;
no persistent private-message cache was added. Initial authentication reads run
in parallel, and the first WebSocket connection reuses the initial bootstrap.

Evidence: `/tmp/duet-chat-performance-before-2-20261105` and
`/tmp/duet-chat-performance-release-20261105`, including reports, screenshots and
fixture cleanup records. Intermediate runs exposed the read-receipt long tail
and presence-induced invalidation described above; their artifacts remain intact.

The final cache-boundary browser case also passed with 700 ms emulated latency:
recent chats appeared in 35–39 ms, while an evicted chat took 833 ms and a chat
after an actual WebSocket disconnect/reconnect took 828 ms to reauthorize.
Real source withdrawal notifications cleared dependent snapshots; a delayed old
response did not repopulate them, and switching back never flashed the withdrawn
content. Account switching cleared private cache state. The older-question
composer draft/focus and personal reset browser regressions passed separately.
Evidence is in `/tmp/duet-chat-cache-complete-20261105`,
`/tmp/duet-chat-history-focus-20261105`, and
`/tmp/duet-chat-reset-cache-20261105`. All four final fixtures confirmed removal
of their PostgreSQL schemas, Redis keys, processes and temporary workspaces.
