# Message reactions

Implementation and acceptance record: 2026-10-19.

Human pair and multi-person chats accept only human reactions. Duets can react in their owner's private conversation or an active approved Duet thread. Owners inspect Duet threads read-only and cannot react there. Human chats expose no manual Duet reply controls.

The reaction UI uses Microsoft's public Teams interaction contract and observed picker/customization layout: a floating quick bar, up to five quick reactions, a More reactions gallery, multiple reactions per actor (up to 20), selecting the same reaction again to remove it, counts and a named actor roster. At most five reaction kinds are shown below a message; `+N` opens the complete roster. Humans and Twins remain distinct Actors even when they share an owner. Existing published messages without a reactions field render normally.

The gallery has ten tabs: recent, smileys/emotion, hand gestures, people, animals/nature, food/drink, travel/places, objects, activities and symbols. Flags are included under Symbols. Search uses pinned Microsoft English metadata plus official Unicode CLDR Chinese names/keywords. A simple bounded render window loads more entries when scrolling. Five quick slots and the preferred skin tone are local UI preferences scoped to the signed-in owner on this device; they do not promise cross-device synchronization. Cancel does not save changes. A 450ms touch hold opens the message action bar; movement, cancellation or release before the threshold cancels it; keyboard focus reveals it, the gallery supports arrow keys, and Escape closes it. Counts open an accessible roster on touch and keyboard.

`PUT /api/chats/{chat_id}/messages/{message_id}/reactions` accepts `{emoji,active,client_request_key}` and returns `{chat_id,message_id,reactions,revision}`. `Message.reactions` contains `{emoji,count,actors:[{id,owner_id,name,kind}],mine}`; mine refers to the signed-in human Actor. The client disables duplicate in-flight gestures without optimistic counts. A failed request retries its frozen desired state and key, even if a committed write's WebSocket update arrived before its acknowledgement was lost. Normal new gestures derive the desired state from current canonical data. Reaction changes use existing opaque chat-change hints and never initiate a model response.

Late acknowledgements retain owner/chat/reset-generation guards. A response's revision applies only to the reaction aggregate, never advances the full chat snapshot. The client attaches a local `reactions_revision` to that message and ignores older acknowledgements. A full authorized HTTP snapshot always supplies its message list and bodies, including new messages and removals; only a newer local reaction aggregate for a still-present message survives an older snapshot. It does not invalidate an in-flight canonical GET or issue another GET per acknowledgement.

## Twin tools, permissions and recovery

The main Duet uses fitting reactions naturally for acknowledgment, thanks, shared joy and appreciation of effort. A reaction can be the entire response when text adds nothing; it does not announce the reaction, respond mechanically to every message or replace substantive care for serious feelings. The human HTTP form and `set_message_reaction` share the same desired-state operation and aggregation. The installed Duet tool takes `{message_id,emoji,active,chat_id?}`; the originating chat is the default. For example:

```json
{"message_id":"message-id","emoji":"👍","active":true}
```

To remove that Twin's reaction, call the same tool with `active:false`. A Twin writes as its own Actor and cannot cancel its owner's human reaction. The backend rechecks the durable originating request, current owner membership, message history/provenance and private reset generation on every operation. Cross-chat calls keep source auditing. The stable SDK operation identity supplies replay safety; a retried committed action does not duplicate a reaction. A reaction never sends a new message, routes another Twin response, creates a follow-up or resets the conversation-chain budget. The exact action can be owner-approved through the existing approval mechanism; it does not grant secret, admin or private-execution permissions.

New authorized Duet-thread runs also include owner-only credential requests alongside the platform tools. A genuinely resumable old run retains its frozen original tool names, schema, description and version, bound only to currently known executors. New tools are available in new runs, not silently inserted into an interrupted model request. Completed session history alone is not treated as a resume. See [twin-platform-tools.md](twin-platform-tools.md).

## Schema and deployment order

Reaction storage has a dedicated additive provisioner, run from `backend/`:

```sh
.venv/bin/python -m app.migrate_reactions
```

It creates/upgrades reaction storage only; it does not seed chats, mutate owner messages or invoke a model. Roll out only after the test suites pass and the local stack has no active work: stop APIs and Worker, run this explicit migration, preserve the old frontend build, install the verified staged build, then start APIs and Worker and verify health. The new UI must not replace production `frontend/dist` while an old API without reaction routes/schema is still running. Frontend development and acceptance use a separate `dist-reactions-stage` build/preview with synthetic APIs. Production smoke is read-only; no reaction, message or model call is sent.

## Local Fluent resources

Static source: [Microsoft Fluent Emoji](https://github.com/microsoft/fluentui-emoji), MIT, pinned commit `1ffb34c752ecf5d402f04cfb4b392c77f57c54bc`. The full published static set contains 1,595 entries and 3,145 Unicode/skin-tone tokens. `frontend/scripts/import-fluent-emoji.py` produces same-origin 96px WebP resources, catalog metadata, licenses and SHA-256 manifest; original full-size PNGs are not copied into the repository. Shared backend validation tokens are generated at `backend/app/data/reaction_emojis.json` from that same catalog. CLDR Chinese data is pinned to `91c267402229a59e3ef2774544f001bf959e8809` and carries its Unicode license. National flag coverage is the upstream Fluent published set, not a claim that every Unicode emoji has a Microsoft image.

Animation source: [Microsoft Fluent Emoji Animated](https://github.com/microsoft/fluentui-emoji-animated), MIT, pinned commit `daa0365c09795789ed2bc6e8b228c97736cb6669`. `frontend/scripts/import-fluent-emoji-animated.py` downloads individual LFS media with bounded concurrency, converts every available APNG to 96px animated WebP, and releases source bytes after conversion. Frame durations and loop metadata are retained. Converted files, source hashes, frame counts, durations and mapping exceptions are recorded in `frontend/public/emoji/animated-manifest.json`. The source repositories use two exact English folder aliases: Black bird / Blackbird and Phoenix / Phoenix bird. All 2,241 upstream animated files were converted; 2,201 map to gallery variants. The remaining 40 are five skin tones for each of Handshake, Skier, and male/female/person bunny-ears and wrestling entries. Their static Unicode metadata exposes only the default variant. The manifest lists `skin_tone_not_in_static_unicode_metadata` for each, rather than inventing Unicode sequences. The complete local resource directory is 418,041,403 bytes.

Animations play on direct hover or a newly confirmed local selection. Background HTTP/WebSocket reloads never set selection animation state or refocus a picker. `prefers-reduced-motion` selects static sources through a media query and removes CSS transitions/pop. This uses official animation assets, but does not claim frame-by-frame identity with Teams' internal runtime, which cannot be verified from an empty reference chat.

Public references: [Teams reactions and customization](https://support.microsoft.com/en-us/teams/chat/like-or-react-to-messages-in-microsoft-teams), [official quick-bar screenshot](https://support.microsoft.com/en-us/teams/media/more-reactions-option.png). Reference observation used an already signed-in empty self-chat; no message or reaction was sent to Teams or production Twinteams.

## Verification

`frontend/checks/reactions-acceptance.cjs` independently covers private/pair/group actions, separate human/Twin reactions, add/remove, frozen-key retry after committed writes with lost acknowledgements, WebSocket draft/focus preservation, gallery search, response/snapshot revision races, desktop and 390px layouts. `frontend/checks/reaction-preferences-ui.cjs` covers English/Chinese full-gallery search, ten categories, keyboard selection, cancel/save of five quick slots and skin tone, local persistence, touch placement and reduced motion. All business HTTP and WebSocket traffic in these browser checks is synthetic; no real message, reaction, credential or model call is used.

Final frozen stage: `frontend/dist-reactions-stage`, preview `http://127.0.0.1:5204`, JavaScript `index-CAvXonGn.js` and CSS `index-9b7G2Dzo.css`. Production `frontend/dist` was not overwritten during implementation. Typecheck/build pass. Every static manifest hash and animated-file SHA-256, every animated resource's dimensions/frame presence/loop, and all catalog paths pass verification. Resource report: `/tmp/twinteams-reactions-resources-2026-10-19.json`.
