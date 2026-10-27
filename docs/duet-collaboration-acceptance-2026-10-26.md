# Duet collaboration acceptance — 2026-10-26

## Final frozen-build result

The version is supported by offline authorization/concurrency checks and a completed live pair journey. **The live three-owner journey is partial:** both peer Duets produced the necessary independent comparisons, but the final initiator-private summary failed because of a second transport error. The campaign was stopped without another retry. No product blocker was identified; delivery is reasonable with this provider limitation recorded. This is not an “all live scenarios passed” result.

The final frozen candidate is `/tmp/duet-collab-dist`, entry `assets/index-D3ZIijk7.js`. The live campaign recorded **27 of 30 permitted HTTP attempts: 25 HTTP 200 responses, one RemoteProtocolError, and one ConnectError**. There was one explicitly authorized owner UI Retry, zero campaign restarts, no changed network/provider configuration, and no increased reply allowance. Product sources recorded at live startup were unchanged throughout the campaign. The final source and candidate SHA-256 manifests are preserved in `/tmp/duet-collaboration-live-final/`.

| Final acceptance | Evidence and outcome |
| --- | --- |
| Frozen private-origin followup | Pending invitation has no followup timer. Unanimous approval creates the opening and its 30-minute followup. A peer reply wakes the initiating owner's private task; exactly one peer Duet request exists, without an additional auto request for the initiating owner. Actual CUA private report was verified. |
| Opening review | Private approval cards expose the prepared opening in the existing expandable Markdown presentation. Sidebar transcript has one opening disclosure rather than duplicate cards. CUA and component regression passed. |
| Classifier failure recovery | A deliberately failed offline classifier appears under its transcript message. The peer sees Dismiss and no Retry. The source owner can retry through the existing endpoint; canonical route completes. Dismiss remains local. CUA and component checks cover this. Closed/rejected conversations expose no retry. |
| Owner-question origin link | A real SDK ask_question creates a private owner question with an independent Duet origin. Clicking From opens that selected read-only sidebar transcript while the main header remains the owner's private Duet. Sidebar input count is zero. Unknown IDs are authorized via summary before main navigation; known human/private destinations retain the direct path. |
| Valid timeout and closed timeout | A guarded fixture-only deadline advance exercises the real timeout processor. An active waiting followup resumes through its legitimate private continuation. Closing before the deadline leaves its followup cancelled/blocked with no continuation. No product clock or timeout was shortened. |
| In-flight revocation | A fixture provider reaches a persisted “running” marker and waits on an explicit release file. CUA revokes the standing permission before release. The canonical session is closed with authorization_revoked and private followup has no continuation. Releasing the late provider result produces no peer output: only the opening remains; request is cancelled and private followup remains blocked. This uses a real release barrier, not a guessed sleep interval. |
| Live human-chat isolation | Real CUA sends to a pair and a multi-person human chat, including Duet mentions, produced zero ReplyRequests, zero RoutingDecisions, and zero provider HTTP attempts before the private scenario began. The final canonical human transcripts still contain only humans. |
| Live pair | Natural-language private request led to an actual request_duet_conversation tool call. Both owners independently approved once through CUA. BBB's Duet asked its owner a focused availability question; the owner answered through the real UI. This was not unattended completion. After one explicitly authorized durable recovery Retry, BBB replied once and AAA received exactly one private result: Tuesday 27 October 2026 at 14:30 Asia/Shanghai, nothing booked. The retry retained its intent and source opening, generation advanced to one, and the peer output replied to that same opening. |
| Live three-owner collaboration | A second natural-language request asked BBB and CCC to independently cross-check already-supplied time lists. All three owners approved once through CUA, with no transcript before the third approval. Both peers separately computed the common option, Tuesday 27 October at 14:30. The final private summary did not publish because attempt 27 failed in transport. |
| Loop and budget boundary | At stop, all requests were terminal, no queue was running, and no new calls appeared after stopping the campaign. Maximum causal-chain spend was 5 against allowance 6. No Continue or other budget expansion was used in live testing. |

## Preserved live failures and recovery

Attempt 12 failed before an HTTP response with `RemoteProtocolError`. The original durable request error is exactly `Server disconnected without sending a response.` It occurred in the BBB `question_result` continuation after the owner answered. Its failed SDK run recorded no returned tool action or tool execution; only previously committed request_duet_conversation and ask_question operations existed. Root explicitly authorized one normal owner UI Retry after that check. It succeeded in the same campaign, retained the same intent and source message, produced one peer output, and yielded one initiator-private report. The initial failure remains in the evidence and counts.

Attempt 27 failed before an HTTP response with `ConnectError` during the three-owner `followup_result` summary. The persisted SDK/model error is normalized to `ConnectError`; the nested transport exception chain was not retained by the existing SDK/guard. No more precise cause is claimed. Read-only TCP checks could still connect to `127.0.0.1:7894`; the proxy cannot be described as stopped merely because lsof did not list a listener. No provider request was made for that diagnostic. After this second transport failure, no Retry was attempted and no extra scenario was started.

The private live evidence directory is `/tmp/duet-collaboration-live-final/`. It includes the complete bounded ledger, both preserved failures, replay/source verification, normalized SDK failure records, 257 sanitized SDK events, 14 SDK runs, the three committed product operations, canonical final snapshot, source hashes, candidate manifest, and cleanup record. No key, private provider endpoint, or proxy secret is included in the public report.

![Live pair report delivered to its initiating owner](/tmp/duet-collaboration-live-final/pair-initiator-report.jpg)

![Both live peer Duets completed their independent checks](/tmp/duet-collaboration-live-final/three-owner-live-transcript.jpg)

![Final three-owner summary remains failed and was not retried](/tmp/duet-collaboration-live-final/three-owner-summary-transport-failure.jpg)

## Verification ledger and cleanup

The preserved backend ledger is `/tmp/duet-collaboration-verification-final.json`. The original complete suite recorded **519 passed, 2 failed, 0 skipped** out of 521 tests. Those two failures were legacy followup assertions expecting 409. Authorization now rejects earlier with 403; the assertions were updated to require exactly 403 while retaining checks for zero new side effects. After those corrections and owner-authorized chat-management restoration, the complete affected set recorded **88 passed, 0 failed, 0 skipped**. The original full-run failure evidence was not overwritten, and a subsequent all-green 521-test run is not claimed.

Node 22 frontend typecheck, candidate build, and the isolated component regression passed. The regression includes pair/multi/private sidebar behavior, approval scope, prepared-opening Markdown, origin-link navigation, transcript pagination, own-only request and routing retries, closed-state restrictions, Continue, and revocation. The two new acceptance-harness I001 import diagnostics were fixed. Final harness ruff passes; full backend ruff retains **80 historical diagnostics and zero new diagnostics**. git diff --check passes.

All four owned acceptance stacks were stopped. Each owned PostgreSQL test schema, temporary workspace root, and Redis namespace was removed. The final live cleanup removed seven owned Redis keys; its temporary root and loopback listener are gone. Provider ledger, sanitized journals, failures, snapshots, and screenshots remain in the private artifact directories. No acceptance test changed production messages or process state. Production rollout was completed separately by root/core and is recorded below.

## Deployment and read-only production smoke — 2026-10-27

Root authorized rollout after accepting the recorded live limitation. Core's final deployment evidence, `/tmp/duet-collaboration-rollout-final.json`, was verified at 00:00:12 Asia/Shanghai. Both local API health endpoints and the formal public HTTPS health endpoint returned 200 with the model configured. The public index, JavaScript, and CSS hashes exactly match the frozen candidate, using normal TLS certificate verification. Hashed assets were copied before the atomic index replacement; prior assets were retained.

Core preserved all 215 production message rows (94 human messages) by complete-row hash and retained every chat sequence. There were zero new human-group agent requests, zero agent messages in human chats, zero active work items or leases, and zero production test messages. Only the Duet schema upgrade ran; no init, seed, reset, or SDK migration ran. FRPC and environment files were unchanged.

The independent read-only CUA smoke loaded both `http://localhost:8000/` and `https://154.217.234.218/` successfully. Both render the Duet login page and expose module entry `/assets/index-D3ZIijk7.js`. There was no existing signed-in session, so no sign-in, account switch, message, or model action was performed; authenticated sidebar/profile behavior remains covered by the isolated CUA journeys rather than this production smoke.

Chrome blocked direct navigation to `/health` on both local and public addresses with `ERR_BLOCKED_BY_CLIENT`. This browser-client limitation does not contradict the separate successful HTTP/TLS health checks. The earlier deployment connectivity concern came from checking the intermediate FRP port 18000, not the formal HTTPS entrance, and required no FRPC or server change. Neither that mistaken entrance check nor Chrome's health-page block is the pair/three-owner live provider transport failure described above.

![Formal public entrance renders the final Duet build](/tmp/duet-collaboration-live-final/production-public-login.jpg)

## Initial provisional offline campaign

The first integrated offline campaign passed the journeys below. It used Chrome through CUA, the real frontend candidate, actual HTTP/WebSocket API, worker, PostgreSQL, Redis, SDK execution, and platform tool transactions. Only model decisions were scripted. It made **zero real provider HTTP requests**. These results establish product wiring and authorization behavior; they do not establish natural-language GPT-6 Luna behavior.

This campaign is **provisional for the final backend revision**: `backend/app/duet_conversations.py` changed while its API and worker were running. Core confirmed the changes add initiator-private followup reporting and replace sidebar metadata projection without changing the DTO. A fresh isolated run must exercise the new initiator followup before final acceptance. Frontend sources did not change during the campaign.

Production APIs, workers, database, Redis namespace, workspaces, and frontend deployment were untouched by this acceptance run. The fixture copied `/tmp/duet-collab-dist` into its disposable root and served one nonproduction loopback port. The earlier frontend build correction restored the production `frontend/dist` entry point to HEAD `2f77abd`; the candidate remains separate.

## Reproducible launcher

```sh
backend/.venv/bin/python backend/checks/duet_collaboration_acceptance.py --serve
```

The launcher prints only its loopback URL, fixture nonce, namespace, private state-file path, and evidence directory. It creates a fresh `tt_test_*` PostgreSQL schema, a `browser-e2e-*` Redis namespace, and private workspace/memory/secret directories. The existing `browser_e2e_fixture.guard` verifies all those boundaries. Outbound HTTP is denied in offline mode. Child logs remain inside the private evidence directory. Interrupting the launcher stops only its own process groups and removes its owned schema, Redis keys, and temporary root.

An allowlisted metadata inspection and reusable assertions are available while the fixture is running:

```sh
backend/.venv/bin/python backend/checks/duet_collaboration_acceptance.py \
  --role inspect --state /path/printed/by/launcher/state.json
backend/.venv/bin/python backend/checks/duet_collaboration_acceptance.py \
  --role inspect --verify-stage final --state /path/printed/by/launcher/state.json
```

Other assertion stages are `human`, `pending`, `directional`, and `expanded`. Use each immediately after the matching journey. The launcher records hashes of product sources at startup so a campaign can identify subsequent source changes.

The scripted provider recognizes `ACCEPTANCE PROPOSE ` followed by JSON containing the real `request_duet_conversation` arguments. It emits a genuine SDK tool call; it does not insert approval, conversation, request, or transcript records directly. Opening messages beginning `ACCEPTANCE QUESTION` produce one necessary peer answer and a real `send_chat_message` owner report. Courtesy is routed to no response. `ACCEPTANCE CHAIN 0` supplies a deliberately continuing scenario for allowance tests. These markers are fixture behavior, never production prompts.

## CUA journeys and canonical evidence

| Journey | Result |
| --- | --- |
| AAA sends normal pair and multi-person human messages, including a Duet mention while peers are offline/unread | Both human messages persist; zero group ReplyRequests, zero RoutingDecisions, zero provider decisions. Header/member lists show two/three humans. No group Duet typing, reply-for-me toggle, or automatic controls appear. |
| AAA private Duet requests `Pair meeting once`, originating in the pair human chat | Real `request_duet_conversation` tool persists pending consent. No conversation messages or peer requests exist before unanimous approval. |
| AAA approves once in sidebar; BBB signs in | BBB's private chat immediately exposes the pending invitation, including explicit recipient Duet and Allow once / Always allow / Reject controls. AAA's decision does not authorize BBB. |
| BBB approves once in private inbox | Separate Duet transcript becomes active. Both owner decisions remain visible. One necessary peer answer and one recipient-owned private report persist. Courtesy closes without queued work. No permanent grants exist. |
| Read-only owner transcript and chat-list separation | Transcript has zero inputs/textareas. Both Duet identities, avatars, Markdown, timestamps, and statuses are visible. Main chat list contains only own private chat and human chats. |
| BBB requests `Pair trusted access`, chooses Always allow | Only directed BBB→AAA grant exists; AAA approval remains pending. |
| AAA independently chooses Always allow | Reverse grant is created and conversation activates. A later `Trusted chain six replies` request activates using standing consent, without approval buttons. |
| Six-reply allowance and Continue | Canonical chain reaches spent 6 / allowance 6 and stops. CUA clicks Continue automatically · 6 more replies; canonical allowance and spent reach 12. No extra automatic grant occurs. |
| Close continued chain | CUA closes the paused chain. Its status becomes closed and no queued/running requests survive. |
| Add unknown CCC recipient to existing trusted pair | `Team new recipient consent` remains pending, with all three owner approvals pending and zero transcript messages. Existing pair grants do not cover the expanded recipient set. |
| AAA, BBB, CCC each approve once through their own UI | Three-owner conversation activates. CCC's private inbox shows both AAA and BBB recipient Duets. The multi-person human chat's Duets tab lists only its associated collaboration. |
| AAA profile → Trusted Duets → revoke BBB | The directed AAA→BBB grant disappears. Always-dependent `Pair trusted access` closes with `authorization_revoked`. Pair-once and three-owner-once conversations remain active. The independent BBB→AAA grant remains. |
| Fresh request after revoke, with no human origin argument | New conversation has `origin_chat_id=private-alex`. AAA must approve anew while BBB's existing directed grant is standing consent. CUA rejects; status becomes rejected with no transcript or peer turn. |
| Foreign CCC reads/closes pair session or decides another owner's approval | Actual APIs return 403 for each. |
| Owner manually sends into a Duet chat or adds a participant | Manual human send returns 403; membership mutation returns 409. No spoofed message or participant change persists. |

The final provisional snapshot contains five collaborations: two independent once-approved active conversations, two closed conversations, and one rejected private-origin conversation. All requests are terminal. It contains **44 scripted provider decisions** across classifier and reply turns, and **zero real provider HTTP attempts**.

## Evidence

Private local evidence: `/tmp/duet-collaboration-acceptance-offline/`.

- `human-boundary.json`, `pending-once.json`, `once-approved.json`, `directional-grant.json`, `trusted-reuse.json`, `expanded-recipient.json`, `revocation.json`, `final-inspect.json`, and `authorization-denials.json` preserve canonical API/DB observations.
- `source-hashes.json` and `summary.json` identify the provisional revision boundary.
- Screenshots: `recipient-private-inbox.jpg`, `once-approved-transcript.jpg`, `six-reply-cap.jpg`, `trusted-permission.jpg`, `rejected-private-origin.jpg`, and `final-collaboration-states.jpg`.

![Recipient private approval inbox](/tmp/duet-collaboration-acceptance-offline/recipient-private-inbox.jpg)

![Separate read-only Duet transcript](/tmp/duet-collaboration-acceptance-offline/once-approved-transcript.jpg)

![Explicit six-reply continuation gate](/tmp/duet-collaboration-acceptance-offline/six-reply-cap.jpg)
