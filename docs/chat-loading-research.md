# Chat loading: upstream references and Duet choices

Primary-source review, 2026-11-05. These are implementation references, not benchmark results. Commit links pin the source inspected; performance measurements belong to the corresponding acceptance report.

## Zulip

Inspected `zulip/zulip@7380849ee6db900ba5e2ce84aed13156a471e33d`.

- History uses a message anchor and bounded counts on either side. The server constrains message IDs, orders them and applies a limit. [API](https://zulip.com/api/get-messages), [server query](https://github.com/zulip/zulip/blob/7380849ee6db900ba5e2ce84aed13156a471e33d/zerver/lib/narrow.py#L1236).
- An in-memory message store is accompanied by an LRU of up to 100 message-list views. Cached views can provide data for compatible narrower views. This does **not** mean switching requires no HTTP: the official performance document explicitly describes fetching even when a view's history is cached, to avoid subtle bugs. [view cache](https://github.com/zulip/zulip/blob/7380849ee6db900ba5e2ce84aed13156a471e33d/web/src/message_list_data_cache.ts#L5), [performance notes](https://github.com/zulip/zulip/blob/7380849ee6db900ba5e2ce84aed13156a471e33d/docs/subsystems/performance.md).
- Real-time delivery uses long-poll event queues with `last_event_id`, separate from history requests. It is not a WebSocket implementation. [event API](https://zulip.com/api/get-events).

**Takeaway:** retain bounded view state while revalidating with the server. Do not copy Zulip's extensive global history backfill into Duet.

## Mattermost

Inspected `mattermost/mattermost@24115350280dc683a1d7d0bebb9cd02375522e73`.

- The client separates latest, before/after, around-message and since-time retrieval. Channel synchronization uses saved fetch/disconnect times for catch-up. New-post WebSocket events are batched into client state; edit events update individual posts. [channel loading](https://github.com/mattermost/mattermost/blob/24115350280dc683a1d7d0bebb9cd02375522e73/webapp/channels/src/actions/views/channel.ts#L340), [event handling](https://github.com/mattermost/mattermost/blob/24115350280dc683a1d7d0bebb9cd02375522e73/webapp/channels/src/actions/websocket_actions.ts#L1119).
- Its server cache targets common first-page requests of 30 or 60 posts when caching is permitted. It does not cache every arbitrary page. [cache implementation](https://github.com/mattermost/mattermost/blob/24115350280dc683a1d7d0bebb9cd02375522e73/server/channels/store/localcachelayer/post_layer.go#L131).
- Post queries constrain channel/deletion state and sort by creation time. The posts migration includes compound channel/update and channel/deletion/creation indexes. Some endpoints also support offset pagination. [query](https://github.com/mattermost/mattermost/blob/24115350280dc683a1d7d0bebb9cd02375522e73/server/channels/store/sqlstore/post_store.go#L1386), [migration](https://github.com/mattermost/mattermost/blob/24115350280dc683a1d7d0bebb9cd02375522e73/server/channels/db/migrations/postgres/000020_create_posts.up.sql#L23).

**Takeaway:** optimize the hot window and avoid repeating unrelated projections. Duet can retain its existing WebSocket-hint plus authorized-HTTP design; adopting Mattermost's full event payload/reducer architecture is unnecessary for this change.

## Matrix / Element

Inspected `matrix-org/matrix-js-sdk@cc4ae934887a7157452dc332d5de0383197c98c8` and `element-hq/element-web@a3f5be54f4bf77eee950e38aedb3251bf059dc38`.

- Matrix `/sync` uses a prior `next_batch` token as `since`, with bounded timelines and backward pagination. The SDK starts incremental synchronization while processing saved state. Element sets an initial sync limit of 20 and enables lazy member loading. [protocol](https://spec.matrix.org/latest/client-server-api/#get_matrixclientv3sync), [SDK startup](https://github.com/matrix-org/matrix-js-sdk/blob/cc4ae934887a7157452dc332d5de0383197c98c8/src/sync.ts#L718), [Element options](https://github.com/element-hq/element-web/blob/a3f5be54f4bf77eee950e38aedb3251bf059dc38/apps/web/src/MatrixClientPeg.ts#L144).
- Element uses IndexedDB where available, with a memory fallback. However, `IndexedDBStore` explicitly keeps data in memory and eagerly restores it; it improves startup without reducing memory consumption. [store setup](https://github.com/element-hq/element-web/blob/a3f5be54f4bf77eee950e38aedb3251bf059dc38/apps/web/src/utils/createMatrixClient.ts#L174), [store contract](https://github.com/matrix-org/matrix-js-sdk/blob/cc4ae934887a7157452dc332d5de0383197c98c8/src/store/indexeddb.ts#L77).
- `TimelineWindow` limits the visible window and drops events from the opposite end when its cap is exceeded. Element requests 30 messages for its initial timeline window. [window implementation](https://github.com/matrix-org/matrix-js-sdk/blob/cc4ae934887a7157452dc332d5de0383197c98c8/src/timeline-window.ts#L92), [Element timeline](https://github.com/element-hq/element-web/blob/a3f5be54f4bf77eee950e38aedb3251bf059dc38/apps/web/src/components/structures/TimelinePanel.tsx#L66).

**Takeaway:** keep cached state and rendered history bounded. Persistent browser storage is a separate decision, not a prerequisite for fast chat switching.

## Duet boundaries

Duet already has keyset history pagination and a PostgreSQL unique `(chat_id, seq)` index. A duplicate index would not fix projection work; new indexes require query-plan evidence. Chat details should not calculate data the client does not consume.

Presence view-interval records remain persisted for audit but do not advance chat content revisions or invalidate message windows. Actual read receipts, message changes and membership changes retain their normal notifications. A published message is a snapshot in its own conversation: displaying it or marking it read does not recursively inspect its audit ancestors. Viewing the original referenced message still checks its current state and access; an unavailable original becomes a quote placeholder without removing the published message.

Tracking is a separate, lazy projection for both private and group chats. Opening a conversation does not query Tracking; opening its sidebar tab calls the existing Tracking endpoint. Sidebar refresh follows the server chat revision, so restoring a cached sidebar and receiving the same revision does not fetch Activity or workspace twice. Explicit workspace refresh and actual content changes still refresh their views.

For switching chats, retain a small, same-login memory cache of visited windows and scroll positions. Display a valid cached window immediately, then refresh through authorized HTTP. The chosen bound is five conversations and up to 130 messages per conversation. If the loaded history exceeds that bound, retain the latest 30 and return to the bottom on revisit; preserving arbitrary old anchors would require a more complex history-window contract. Do not persist the whole transcript in IndexedDB or Redis for this change.

Cache invalidation must reuse existing signals:

- A newer `changed` revision discards that chat's cached content but keeps its bounded viewport metadata. If the user switches before synchronization finishes, reauthorize the same expanded window before displaying it and restore the scroll position. Only windows of 31–130 messages retain an earlier cursor; ordinary and trimmed windows reopen at the latest 30.
- Account/authentication changes, logout, reset, access loss and disconnection clear incompatible cached state.
- `personal_changed` for `tracking` or `chats` invalidates private windows conservatively. The transaction collector already emits `tracking` for withdrawn source messages and membership changes, including the owner losing membership. Ordinary read receipts do not emit this personal invalidation.
- An invalidated cache must not be repopulated by an older in-flight request. A destination revision alone cannot detect a changed source in another conversation.

Acceptance should cover long-history first load, A→B→A switching, expanded history, incoming updates, scroll restoration, logout/reset, and a referenced source withdrawn in another chat. Synthetic history should include real questions, references and Tracking: plain message rows alone can miss the cost of ancillary projections.
