# Isolated browser acceptance

Use a disposable PostgreSQL schema, Redis namespace, workspace and API/worker processes. Never point an acceptance runner at production or reuse a real user's conversation. Preserve failures and count all provider HTTP attempts, including transport failures. The current interaction contract is [message-driven Duet channels](interaction-rules.md).

## Current fixture

Build the frontend into a temporary output directory with the repository's supported Node version, then run the existing collaboration fixture from the repository root:

```sh
backend/.venv/bin/python backend/checks/duet_collaboration_acceptance.py \
  --serve --dist /tmp/duet-frontend-dist \
  --artifact-dir /tmp/duet-channel-acceptance
```

The fixture prints its guarded loopback URL and instance metadata. Use that exact instance in Chrome through CUA for browser actions and DOM observation. Guarded read-only inspection correlates visible messages with canonical IDs, requests, consent, send budgets and SDK events; database observations do not substitute for a browser interaction claim. The deterministic offline adapter tests product plumbing, not independent model behavior.

Verify owner-private assistance and human-only chats; unanimous once approval; standing grants and revocation; read-only channel transcript and history pagination; ordinary question answers returning useful results to the initiating owner; and silent completion without an empty chat bubble. Count canonical message IDs rather than nested card rendering or transient updates.

For the send boundary, exercise an actual chain's six published peer messages, including its opening. The sixth must be delivered. The seventh must remain unsent, absent from published transcript, peer context and notifications. Only its author's owner sees the prepared body and can cancel it. Other participating owners see the waiting count and shared allowance. Explicit Continue adds six sends with one durable consent key; retries must not grant twice. Private reports, questions, receipt, worker completion and retries do not themselves spend sends. Channel state stays independent of task execution state.

## Live campaign

A live run requires explicit authorization after candidate source/asset hashes are frozen and offline checks pass. Reuse the already authorized provider connection; inspect presence safely without printing credentials. The bounded guard permits only `gpt-6-luna` through OpenAI Responses and records every actual HTTP attempt. A 24-attempt campaign uses:

```sh
backend/.venv/bin/python backend/checks/duet_collaboration_acceptance.py \
  --serve --live --live-approved --max-attempts 24 \
  --dist /tmp/duet-frontend-dist \
  --artifact-dir /tmp/duet-channel-live
```

Use fictional non-sensitive users and a natural coordination request whose answer is initially known only to the receiving owner. Approve through the normal UI. Answer through the ordinary private composer before considering a card-specific control; do not name tools or force a desired model path. Record what the model actually did, distinguish progress from repeated real asks, and verify the final owner-visible outcome. Stop when the authorized observation is complete; do not spend the remaining budget seeking a failure or silently restart a campaign.

Save screenshots, DOM snapshots, provider ledger, message/tool/request lineage, the exact frozen source and cleanup evidence under `/tmp`. Keep private production text, keys and generated screenshots out of the repository. Source HEAD identifies the base revision; uncommitted candidates require a separate source hash manifest.

## Cleanup and limits

Stop only the owned disposable launcher after collecting final evidence. Verify its processes, schema, namespaced Redis keys and temporary root are removed. Compare production read-only baseline and final state to establish that production history, configuration, files and services were untouched.

An offline pass establishes only its executed assertions. A live pass establishes only the observed inputs and model responses. Neither implies exhaustive provider reliability, physical capacity, production identity/TLS, arbitrary external effects or a full browser matrix. Dated acceptance reports are historical evidence for their tested versions, not current routing or followup instructions.

On 2026-11-02, the offline `personal / upload Skill folder preserves references and owner isolation` case passed in installed Chrome 156 using `TWINTEAMS_E2E_CASE='upload Skill folder' TWINTEAMS_BROWSER_CHANNEL=chrome backend/.venv/bin/python backend/checks/browser_e2e.py --node /usr/local/n/versions/node/22.14.0/bin/node --artifact-dir /tmp/twin-skill-upload-acceptance`. It used the actual folder chooser and checked discovery, reference listing, disabled default, enablement after reload, conflict preservation, and separate owner folders; the backend personal test separately loaded the uploaded Skill and nested reference through the enabled SDK store. No paid provider was called. The local artifact directory contains `report.json` and `cleanup.json`; cleanup verified the disposable schema and temporary root removed, zero namespaced Redis keys, and all fixture processes stopped. No screenshots were captured. This run tested an uncommitted candidate based on `722cd574026f2d475aec1e3c06077032c6809ece`; subsequent hint text and a validation refactor were reviewed separately.

### Chat history switching (2026-11-04)

Installed Chrome used two disposable, offline PostgreSQL/Redis fixtures, each with the same three synthetic 251-message conversations containing short text and Markdown. On an unchanged browser session, we measured from selecting a different conversation to the requested latest message appearing in the DOM at the next animation frame. No prior conversation body was cached across visits. This measures transcript availability, not completion of scrolling or provider response time.

| Transport | Ordinary switch, median of 10 (before → after) | Reopen after manually loading two earlier pages (one sample each) |
| --- | --- | --- |
| Loopback | 100 → 71 ms | 207 → 52 ms |
| Chrome emulation: 700 ms latency, 2 Mbit/s download | 1,031 → 830 ms | 3,012 → 832 ms |

The reopened conversation went from three sequential history reads before display to one; ordinary first-page payloads fell from about 59 kB to 19 kB on this fixture. In rapid A→B→A switching on the emulated link, the new client cancelled B’s superseded read and the final selection showed only A’s rows. These figures describe the synthetic workload, not a measurement of a customer's intranet. Both orderings of a live refresh overlapping an earlier-page response were exercised by delaying a real HTTP 200 in Chrome; after reauthorization the requested view expanded from 30 to 130 messages in both cases. The `ask-user` older-question browser case covers same-chat reauthorization and explicit pagination after switching. Temporary browser measurements: `/tmp/duet-switch-{before,final}-20261104/`.

### Review a human message privately and reply as its owner (2026-11-04)

Earlier acceptance used `TWINTEAMS_E2E_CASE='references / review an incoming document' TWINTEAMS_BROWSER_CHANNEL=chrome backend/.venv/bin/python backend/checks/browser_e2e.py` with the repository's supported Node version. This November 4 case preceded the explicit send-confirmation card. It uploads an actual file in a direct chat, opens the reference action with the mouse and keyboard, cancels and reselects the draft, and verifies the private citation survives reload. The offline provider selects the real read, write and delivery tools. A value present only in the uploaded file must appear in the resulting review. The sender cannot open the private review, and their human conversation remains unchanged until the separate owner instruction, “Looks good. Please reply to them with the review you just showed me.” This instruction names no recipient, message ID or file. The final recipient checks the native quoted human reply, exact text and downloaded review bytes. This verifies the interaction and tool effects; the provider's judgment is scripted.

On 2026-11-04 the focused case passed in 5.098 seconds. Local evidence: `/tmp/duet-reference-acceptance-20261104/report.json` and `cleanup.json`. The isolated schema, Redis keys, processes and workspace were cleaned up.

### Owner question choices and confirmed replies (2026-11-05)

Five offline Chrome cases passed. Ordinary questions offered actionable choices that submitted immediately, an open response for missing information, and a multiline custom answer whose draft survived history refresh. The direct-chat and group cases used real uploaded files and privately reviewed replies. Both checked the human conversation remained unchanged until **Confirm send** was selected; typing the same words without selecting the option was rejected. The group case checked the audience, cancelled a draft and prepared a fresh one. On confirmation, each target received exactly one quoted human reply and the reviewed bytes. These scenarios exercise real platform actions with a scripted model. Evidence: `/tmp/duet-question-options-20261105`, `/tmp/duet-question-answer-draft-20261105`, and `/tmp/duet-question-send-preview-final-20261105` (each with its report and cleanup record).

This acceptance preceded the move of ordinary free-text answers into the main chat composer. Its card-input observations describe that earlier version; current ordinary cards retain suggestions but have no separate answer field. The explicit **Confirm send** / **Cancel** controls still apply to prepared human replies.

### Ordinary answers in the main composer (2026-11-05)

Five focused Chrome cases subsequently passed: suggested answers and an unquoted open answer through the real `answer_question` tool; multiline and quoted answers, dismissal and account isolation; main-composer draft/focus during history refresh; and both direct and group send confirmations. Ordinary cards have neither a text field nor a custom-answer button. Prepared human replies still require **Confirm send**; free text cannot send them. The backend question, reply and prompt suites passed 56 tests, including unquoted answers resuming the original main or child SDK session after restart.

The offline fixture chooses model tools; the browser, API, SDK and PostgreSQL effects are real. No paid provider was called. Evidence is under `/tmp/duet-question-main-composer-final-20261105` (the first case), `/tmp/duet-question-main-composer-draft-20261105` (the remaining two question cases), and `/tmp/duet-question-main-composer-reference-20261105` (both send cases). Earlier test-only failures for a missing-field assertion and hovering the Reply toolbar are retained; the affected cases passed after correcting the assertions and helper, without changing production code. Each fixture's cleanup record confirms its schema, Redis keys, processes and workspace were removed.
