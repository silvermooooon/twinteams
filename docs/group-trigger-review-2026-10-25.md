# Group trigger investigation — 2026-10-25

This is the preserved baseline audit before fixes. For the implemented behavior, post-fix browser/provider verification, retained network-failure report, and local rollout, see [Group routing fix acceptance](group-trigger-acceptance-2026-10-25.md).

The real browser/provider investigation reproduced two recipient-selection problems on commit `5f3e7dd8d626065e0437e63272c108ace8b06354`: a fresh custom Duet alias did not trigger its owner, and a two-owner conversation created an automatic request for the other owner's Duet when the sender addressed only their own Duet. Multi-recipient routing, a genuine Duet-to-Duet follow-on, terminal stopping, per-message read suppression, and actual online/offline handling succeeded in the tested flows.

## Baseline trigger rules at 5f3e7dd (before fixes)

| Stage or input | Current implementation |
| --- | --- |
| Owner's private conversation | Immediately requests that owner's Duet; it does not use the group recipient classifier. |
| Human message in a shared conversation | Creates a routing decision; the classifier decides whether another response is needed and selects owners. Deterministic human mentions supplement its recipient set only when a response is needed. |
| Two-owner shared conversation | Still calls the classifier, but `needs_response=true` replaces inferred recipients with the eligible other owner. |
| Three or more owners | Uses filtered classifier targets plus deterministic human mentions. Source-owner and follow-up-consumed owners are excluded. |
| Normal Duet answer | Only the final normal-stop answer of a completed request is routed; ordinary intermediate assistant text is not. |
| `send_chat_message` / `present_files` | Explicit tool publications route immediately. The former targets another authorized chat; the latter can publish and route in the current chat. |
| Target owner online | An unread, unanswered candidate waits until ten seconds after the source message was created. Classifier time is included in that window. |
| Target owner offline | After the worker refreshes presence, an unread, unanswered candidate can be accepted without the online grace period. Unknown presence waits. |
| Target owner has read/replied | The specific receipt or a later human reply suppresses unaccepted work. An already accepted request is not withdrawn by a later receipt. Each new unread source message is evaluated separately. |
| Automatic chain | The default allowance is six accepted automatic tasks across recipients and branches, not six conversational rounds. New human posts start new chains; Duet-generated continuations retain lineage. Owner Continue grants another six once per idempotency key. |
| Follow-up waiting | An explicit reply to the tracked message resumes the private origin and excludes that owner from ordinary group routing; an unthreaded answer does not settle the follow-up. Timeout is thirty minutes. |

The composer Reply for me control rewrites an owner's proposed message; it is not a general group task-dispatch control. Being focused or online alone does not suppress a reply: the message must actually be read or answered. Acknowledgements and completed answers can be classified as needing no response even when they contain a mention.

## Method and bounds

The new `group-routing-live` harness used Chrome 155, the actual frontend, two isolated application APIs, the actual SDK worker, PostgreSQL, Redis, and `gpt-6-luna` through the already configured provider connection. Three real browser sessions signed in as synthetic owners AAA/BBB/CCC; each renamed its own Duet through the Profile UI to Atlas/Beacon/Cedar Duet. The group visibly contained all three humans and all three Duets.

Every mutation used normal browser UI: signing in, renaming a Duet, creating chats, sending messages, navigating to Profile, reading a message, and closing a browser context. API and guarded fixture inspection were read-only. No fabricated router, route settlement, business API/WebSocket interception, forced focus state, artificial clock, shortened presence TTL, or production write was used.

The first campaign had a frozen ceiling of 60 provider attempts and stopped after 5 successful HTTP requests because the harness attempted to fill a group-name field during two-owner chat creation. That field is correctly absent in the actual UI. Its failed report is preserved. After the test helper was corrected, the parent explicitly authorized a second campaign with a ceiling of 55, preserving a combined maximum of 60. The second campaign used 21 attempts and completed all seven phases in 206 seconds. Total usage was **26 provider HTTP attempts**, including both smoke checks; every response was HTTP 200 and every model was `gpt-6-luna`. No third campaign ran. Each campaign retained the existing 900-second and 4096-output-token guards.

## Completed live results

| Flow | Actual result | Assessment |
| --- | --- | --- |
| Fresh group, first message addresses only `@Beacon Duet` | Raw router returned `target_owner_ids=[]`, `needs_response=true`; settled inferred targets and human mentions were empty; no candidate or request | **Missed intended recipient**, reproduced in both campaigns |
| Equivalent `@BBB (Beacon Duet)` question | Raw targets were again empty, but the host's human mention list contained BBB; exactly one completed Beacon request published the requested answer | Passed through deterministic human mention fallback; accepted after 10.177 real seconds in the completed campaign |
| Two-owner chat, AAA addresses only `@Atlas Duet` and explicitly excludes BBB/Beacon | Raw router returned empty targets with `needs_response=true` and explained that neither human owner was addressed; settled inferred targets became BBB; a completed automatic Beacon request consumed one allowance unit | **Incorrect automatic recipient**; the request published no visible message in this run |
| BBB addresses `@AAA` and `@owner-jordan` in the three-owner group | Exactly two requests, for Atlas and Cedar, published their separate requested answers | Passed; no extra automation from the terminal answers |
| AAA asks Beacon to pose the next question to Cedar | Beacon published a new conversational question. Its real routing output selected CCC's owner without a deterministic `@` mention. That Duet-authored message caused a Cedar request, and Cedar published `54` | Passed genuine Duet-to-Duet follow-on; same causal chain, spent 2 of 6 |
| CCC reads a newly targeted question, then navigates to Profile | Normal browser read POST persisted CCC's receipt 253.284 ms after source creation; router still selected CCC and required a response; candidate was suppressed with `owner_read`; no request | Passed; suppression came from the specific message receipt |
| CCC browser context closes; wait for actual presence expiry; send explicit owner-ID question | Redis observation changed from online with a 33-second remaining TTL to offline 35.488 seconds after context close. Candidate observed offline/unread and was accepted; Cedar published the answer | Passed; accepted 2.899 seconds after source creation, without an online ten-second wait |

Every observed automatic request excluded the source message's owner. The wrong pair-chat request targeted the other owner; the failure was loss of the intended recipient constraint, rather than a self-owner request. Terminal arithmetic answers were classified as needing no response. No tested chain naturally reached the six-reply product allowance; the maximum observed was two, so this campaign does not establish behavior at exhaustion.

## Mechanisms supported by the evidence

The fresh-alias miss is consistent with the router input lacking an actor roster that maps every current human/Duet name to its owner. `RuntimeBridge.route_targets` receives allowed owner IDs plus message history, not the full roster (`backend/app/runtime_bridge.py:940`). `_prepare_route` supplies deterministic mentions only for human actor names or human IDs (`backend/app/service.py:1380`). A previously unseen custom Duet name therefore has no guaranteed mapping. The successful follow-on occurred after actual group history associated the names and owners; it does not disprove the fresh-conversation failure.

The pair-chat misroute is directly explained by `_settle_route`: when `needs_response` is true and there are two owners, it replaces the model's inferred targets with every eligible other owner (`backend/app/service.py:1396`). The captured raw empty target decision and persisted BBB target demonstrate that overwrite in the real stack. A question directed solely to the sender's own Duet can consequently spend allowance and run an unintended peer Duet.

There is also a current-rule gap for genuine task dispatch to one's own Duet from an ordinary group post: candidate creation explicitly excludes the source owner (`backend/app/service.py:1322`). The composer's Reply for me mode is described to the runtime as transforming raw owner input into a ready-to-send message (`backend/app/twin_prompts.py:163`), and its default prompt asks for rewriting (`backend/app/models.py:21`). It should not be presented as a general task-execution path. These are code observations about the current rules, not an additional live failure claim.

Presence and reading are distinct. Being online in Profile left the new question unread and allowed an automatic reply after the fixed ten-second window. Reading the specific message suppressed it even after navigating away. The offline flow used the original 35-second presence TTL (`backend/app/config.py:46`) and demonstrated acceptance after the real Redis expiry. Candidate creation metadata can temporarily retain `online=null` until the worker refreshes presence; the report therefore relies on direct read-only Redis observations and durable acceptance, rather than treating that cached field alone as authoritative.

## Related read-only production audit

The parent investigation independently reported a production pair-chat record with the same raw empty-target/true-response decision overwritten to the other owner. It also identified two routing failures whose durable SDK interruption reason was `ConnectError`; those are provider/network execution failures, not evidence that the model intentionally declined to answer. Finally, two completion-looking message records in another case were one `present_files` tool caption with an attachment plus a normal final answer from the same request, rather than two automatic requests. These are deidentified corroborating findings from the parent's read-only audit; production message text, names, and IDs are intentionally absent here.

The file-publication nuance matters: `present_files` immediately commits and routes its tool caption message, including when publishing in the current chat (`backend/app/deliverables.py:85`). That message can be classified before the originating request finishes and publishes its normal final message. Two visible messages from one request therefore do not establish duplicate trigger acceptance. The tool description already tells the model not to repeat its caption in the final reply (`backend/app/runtime_bridge.py:307`); checking the separate caption/final routing decisions remains necessary when investigating unintended follow-ons.

## Deterministic matrix and additional findings

`backend/tests/test_group_trigger_matrix.py` adds 21 tests. With 41 related existing tests, **62 passed in 40.26 seconds**. These exercise actual service transitions and disposable PostgreSQL, with controlled router outputs, presence observations, and final reply text. They do not substitute for the browser/provider campaign above. Three source owners and eight target-union cases each cover 24 many-to-many recipient combinations. The suite also covers six-task exhaustion, cross-branch accounting, idempotent Continue, new-message read independence, unknown presence, and follow-up consumption.

Three passing observational tests deliberately record current defects or limitations, rather than approve their behavior: the pair override, unresolved custom Duet aliases, and substring mention matching. In the latter, `@Anna` also matches a different human named Ann and accepts both Duets (`service.py:1380`). This third addressing issue is proven with controlled service inputs, not claimed as a live browser reproduction. The matrix artifacts are `/tmp/twinteams-group-trigger-matrix-20261025.json` and its sibling XML.

The production `ConnectError` cases have a separate visibility/recovery problem: the routing decision becomes failed, pending-route processing no longer selects it, and no accepted reply request exists for the UI's usual failure banner. The user can therefore see silence. This is a source-and-production-record finding, not an assertion that the provider was unavailable during the successful isolated campaign.

The smallest follow-up changes identified by this baseline investigation were to supply the existing participant/name-to-owner mapping to both mention resolution and the classifier, honor resolved recipients in pair chats, use bounded mention matching, and expose failed routing with an explicit retry through existing execution facilities. No new orchestration system or tables were proposed. Those fixes were implemented and verified later; their results are recorded separately in [Group routing fix acceptance](group-trigger-acceptance-2026-10-25.md), without changing this audit's original observations.

## Evidence, verification, and cleanup

Local artifact directories:

- `/tmp/twinteams-group-routing-live-20261025-one`: preserved harness-failure report, first alias/control observations, final inspector, provider budget, traces, screenshots, and cleanup evidence.
- `/tmp/twinteams-group-routing-live-20261025-two`: complete seven-phase report, raw router decisions correlated with real SDK commands/runs, request/candidate/receipt/chain metadata, normal UI read-network observations, presence samples, retained Chrome traces, and screenshots.

The second `report.json` is deliberately **failed** because its final assertion reports the two actual behavior mismatches. Its `summary` records all seven executed phases; it does not relabel missed or incorrect recipients as passing. The final screenshot was visually inspected and shows the three human/Duet pairs, the Beacon-to-Cedar exchange, the actual “Seen by CCC” receipt, and the later offline answer.

Node syntax checks, Python compilation, and `git diff --check` passed. The existing browser fixture/budget guard suite passed **16 tests** using an isolated PostgreSQL test schema. No product implementation file was edited; changes are limited to the live harness launcher, scenario selection, the new browser scenario, guarded read-only inspector metadata, and this report.

After independent review, the scenario assertions were strengthened to explicitly require the CCC candidate's durable `owner_read` suppression, require offline acceptance before the online ten-second grace threshold in this bounded check, and reject any remaining waiting candidate when settling a phase. These assertions were validated against all seven retained phase snapshots and the final inspector; no provider rerun occurred, and the second campaign remained at 21 attempts. The live-run scenario SHA-256 was `917de2882ae9568903c8bfeec02da7342bda743aee8103279217de382b729f9d`; the strengthened file SHA-256 is `22ee5dd7863111635e398d9add30451c2d0e30e86698bf9cac395f0cbc14ad7d`. The second artifact directory contains `strengthened-assertion-validation.json`, which records this distinction and the successful offline validation. The preserved live report still correctly reports the original two behavior mismatches.

Both campaign `cleanup.json` files confirm their disposable PostgreSQL schemas were removed, Redis namespaces had zero remaining keys, every owned process stopped, and temporary roots were removed. No production process was restarted, no deployment occurred, and no commit or push was made.
