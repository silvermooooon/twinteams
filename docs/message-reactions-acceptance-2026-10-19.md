# Message reactions acceptance — 2026-10-19

> Historical verification record. This describes the tested revision on its stated date, not the current peer-message contract. Current guidance: [interaction rules](interaction-rules.md) and [architecture](architecture.md).

## Scope and test isolation

The platform now has 28 group tool declarations and 14 exact platform write-action variants, including `set_message_reaction`. Earlier approval-detail regression evidence for 13 variants refers to the pre-reaction version.

Message reactions are desired state for one authenticated human or the acting Twin. They do not send chat messages or create model work. Per-message “Ask my twin” is hidden in the owner’s private chat and in two-owner chats; chats with at least three owners retain it. “Ask other twin” remains available.

Independent tests use disposable PostgreSQL schemas, temporary storage, real SDK sessions, and offline model callbacks. HTTP/WS tests run two actual API processes without a Worker. Chrome tests use synthetic API/WS data and local built assets. No production reaction/message/approval is submitted and no real provider is called.

## Independent results

- `tests/test_reactions_acceptance.py`: 8 passed (9.60 s), plus the subsequently added committed-tool recovery case passed (1.90 s). Covers human/Twin attribution, add/remove independence, old-operation replay, extra actor-field rejection, withdrawal/membership/history-floor/reset fences, cross-chat message visibility, private-source audit preservation, actual SDK history with human/Twin reaction identities, and one reaction aggregation query for a 35-message page.
- Recovery case commits a reaction, interrupts the SDK in the tool phase, reopens the database session, and resumes. It leaves one reaction, no extra revision, and does not repeat the previous model call.
- Existing real-SDK legacy model/tool-phase declaration recovery: 2 passed (1.49 s). Frozen old runs do not gain the new reaction tool; a subsequent new run gets it.
- `python -m checks.reactions_acceptance`: 35 checks passed after the final shared 3,145-token catalog was connected. Real PG, two APIs and WS verify twelve concurrent same-key adds, independent-key no-ops, one revision per actual change, metadata-only cross-replica invalidation, human/Twin identity, same-key collision, old add replay after removal, denied private/anonymous/actor injection, history projection, and private-reset cascade while preserving group reactions. Message/request/routing/chain/reservation counts remain unchanged by reactions.
- The HTTP fixture warms existing per-owner default ChatSettings creation before measuring reaction revision deltas; this avoids attributing that existing first-read behavior to reactions.

## Review findings resolved

Backend author reports 155 full-suite passes (127.86 s), followed by 18 targeted checks after final snapshot-revision and catalog changes (10.29 s). These are real isolated PG/offline-provider checks, not real provider calls.


The transaction collector derives a changed reaction’s chat from its message and excludes reaction ProductOperation audit rows from dirty-chat invalidation. This gives actual changes a revision while different-key no-ops do not refresh clients. Raw host reaction arguments use the existing exact-action field whitelist; actor identity is never supplied by the caller. HTTP responses include the exact revision for their aggregate snapshot using the existing transaction UPDATE RETURNING, with no extra query. Frontend retry replays the frozen intent after WS changes; local reaction revisions do not advance the whole chat snapshot. An authorized full GET owns body/message membership and retains a newer reaction only for IDs it still contains. Explicit schema checks include the composite primary key and message FK `ON DELETE CASCADE` needed by private reset.

## UI and deployment

The independent Chrome runner `frontend/checks/reactions-acceptance.cjs` passed on the final staged build `http://127.0.0.1:5204` (`index-CAvXonGn.js`) at desktop 1360 px and mobile 390 px. Mobile uses `hasTouch/isMobile` and CDP touch input: tap does not open actions, movement/cancel clears the hold, a 550 ms hold opens the toolbar, and its buttons can be tapped. Both sizes cover the three chat shapes, actor roster, add/remove, committed-response-loss retry in both directions, late PUT versus WS in both orders, older full GET containing a new message while preserving newer local reactions, removal of an unauthorized/missing message without resurrection, draft/focus preservation, gallery search, and no JavaScript errors or overflow.

The four default official WebP animations independently decode to 51/73/72/73 frames and 2142/3066/3024/3066 ms respectively, with manifest hash/duration/loop matches. Chrome verifies animated currentSrc on hover, selection still playing after 550 ms with the pointer outside, static fallback after the full cycle, and static images under reduced motion. All 2,241 upstream animations were converted and their final local hashes independently verified, together with the static manifest. 2,201 map to catalog variants; 40 upstream skin-tone files without matching static Unicode variants remain explicitly unmapped. Static coverage is 1,595 base entries and 3,145 tokens/images. The frontend author also reports final preferences/search/customization, four workspace scenarios and Reset regression passes on the same frozen build. Deployment completed on 2026-10-19: queued/running requests = 0 and queued Dream = 0; stop → explicit `python -m app.migrate_reactions` → nine existing business-table count/full-row hash comparison → staged dist rename → dual API/Worker start. No init_db, seed, production reaction, chat message, approval or real provider call was used.

The nine tables remained identical across migration: actors (6), chats (6), memberships (10), messages (57), reply requests (42), context manifests (44), Tracking topics (0), reminders (0), approvals (0). New reaction rows = 0. Composite primary key, actor FK, and message FK `ON DELETE CASCADE` passed the schema check. Worker PID 77486 and APIs 77487/77488 are healthy. Actual Worker environment checks confirm `gpt-6-luna` and the existing 7894 proxy.

Real DEV-AAA/DEV-BBB login and read-only private-chat/runtime reads succeeded. 8000, 8001 and the public HTTPS entrance each returned correct bytes for 13 index/bundle/catalog/manifest/default-static/default-animation resources; WebP and JSON media types were verified, so SPA HTML fallback cannot satisfy the check. Public health is 200 and unauthenticated bootstrap remains 401. Parent independently confirmed the local/public health and final bundle bytes.

Final bundle SHA-256:

- `index-CAvXonGn.js`: `b6e598f3a18219e3d383d6cf1fbc279cbb50c07d716e4b60d4225969d2131e3a`.
- `index-9b7G2Dzo.css`: `aac48920a9502fb6c11704d6962b20d9905f9b3e83070d1e4f2ef07ec08fe456`.

Reports: `/tmp/twinteams-reactions-live-report.json`, `/tmp/twinteams-reactions-independent-report.json`, `/tmp/twinteams-reaction-assets-report.json`. Screenshots: `/tmp/twinteams-reactions-independent-{1360,390}.png`. Preview servers 5199/5204 were stopped; production API/Worker/FRP were left running. The one-use deployment runner and its bytecode were removed. The old dist backup was retained through successful health/assets smoke and then removed; no rollback copy remains. No commits or pushes were made.
