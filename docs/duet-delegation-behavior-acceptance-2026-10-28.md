# Duet delegation and social behavior acceptance — 2026-10-28

> Historical verification record. This describes the tested revision on its stated date, not the current peer-message contract. Current guidance: [interaction rules](interaction-rules.md) and [architecture](architecture.md).

## Scope and method

This acceptance used only **GPT-6 Luna / OpenAI Responses** through the previously authorized provider configuration and proxy. All six original V2 cases completed. The aggregate result was **29 actual HTTP attempts: 28 HTTP 200 and one pre-response ConnectError**. The isolated acceptance did not change production chats, state, services or files. Any later authorized deployment is recorded separately below. No further model calls are planned.

The initial aggregate ceiling was 24 attempts, including failures and retries. After observing the cost of the real child/parent chain, Root explicitly increased it once to **32**, solely to complete the original controls. The existing guard was updated under its file lock, without restarting the campaign or changing provider configuration. Three attempts remain unused. The [sanitized JSON ledger](duet-delegation-behavior-acceptance-2026-10-28.json) retains every attempt, revision and failure.

The existing `duet_collaboration_acceptance.py` launcher provides a disposable PostgreSQL schema, Redis namespace, workspaces, API and worker. The small acceptance-only `duet_behavior_fixture.py` hook observes actual tool execution and confines file tools to those temporary workspaces. Function names, descriptions and schemas are retained. Bash remains declared but its executor rejects every command rather than pretending success. Native provider web search is omitted because it executes outside the local executor boundary. Accordingly, this run cannot establish shell or web end-to-end behavior. No model tool choices are scripted. Reactions, file reads/writes, product actions, SDK sessions and automatic subagent notifications use their real implementations when selected.

The natural complex request asks for deduplicated paid orders, monthly/customer reconciliation, corrections to an unverified draft, traceable order IDs and two justified recommendations in `paid-orders.csv` and `review.md`. Its wording contains no agent/delegation instruction. The synthetic rules exclude cancelled/refunded orders and exact duplicate IDs. Expected paid receipts are 790 across eight orders: September 370, October 420; North 380, South 300, West 110.

The approach follows the official [OpenAI prompting guide](https://developers.openai.com/api/docs/guides/prompting): keep role guidance in system instructions, keep concrete task details in user inputs, version prompt code and evaluate representative behavior before publication. These are manually reviewed observations, not a statistical guarantee.

## V1: preserved delegation failure

The first frozen-source campaign used a fresh schema/workspace and made **6 HTTP attempts, all HTTP 200**, with no retries or transport errors. The main Duet listed the workspace, read all three source files, wrote both deliverables, reread them, published the files and returned an accurate owner summary. It never called `spawn_agent`; there was no worker or automatic result continuation.

This **fails the early-delegation criterion**, despite correct deliverables. The actual installed `spawn_agent` declaration included the revised delegation guidance. No bash or network operation was attempted or blocked, so the execution boundary does not explain this choice. Product source hashes were unchanged from stack startup through the observation.

Evidence is `/tmp/duet-behavior-live-20261028/`: `complex-final.json`, `complex-case-assessment.json`, `v1-live-budget.json`, `v1-freeze-boundary.json`, model-input/declaration/tool journals, the actual files under `v1-deliverables/`, and the screenshot below. The case was not resent or prompted to delegate. Root accepted the failure and requested an evidence-based V2 revision. The first stack was gracefully stopped; its owned schema, five Redis keys and temporary root were removed. `cleanup.json` records the checks.

![V1 completed all deliverables in the main run without delegation](/tmp/duet-behavior-live-20261028/complex-main-did-heavy-work.jpg)

## V2: fresh revision, real delegation and controls

V2 started with another disposable schema and workspace, the same complex user request and none of the V1 conversation history. The frozen revision clarified the main role's whole-goal decision and put its mission earlier in the system prompt. Its first attempt failed with **ConnectError before an HTTP response**. There were zero tool events or changed deliverables before that failure. Remote inference processing could not be determined; there were no returned tool effects to replay. Root authorized exactly one normal owner UI Retry after checking transport availability. No prompt, input, model or network setting was changed, and no further retry occurred. This is a successful behavioral observation **after transport recovery**, not an uninterrupted first-attempt pass.

On recovery, the main Duet briefly listed the workspace and checked a memory index, then naturally called `spawn_agent` **before reading the three inputs or generating the deliverables**. It sent a normal acknowledgment and yielded. The real SDK child read the inputs and wrote both actual files. Its attempted shell verification received the fixture's explicit denial; it then verified through real file reads and completed. A persisted `PendingChildResult` was accepted and automatically created the main `subagent_result` continuation. The main reviewed the three inputs and two child outputs, published the files and delivered a useful owner summary. No manual child-result injection or `get_agent`/`wait_agent` polling was used.

The child made both deliverable writes; the main made **zero deliverable writes**. Its rereading is coordination and review evidence, not proof of zero repeated internal analysis. The actual files contain eight unique paid orders and the expected 790 total, monthly/customer totals, traceable draft corrections and justified recommendations.

| Case | V2 HTTP attempts | Observed result |
|---|---:|---|
| Complex reconciliation and automatic return | 15, including the failed transport attempt | Early natural spawn, real child work, durable automatic main continuation, correct files and useful owner delivery |
| Natural gratitude | 2 | Product `set_message_reaction` persisted 😊 on the human message with actor `twin-alex`; brief warm reply |
| Natural good news | 2 | Product `set_message_reaction` persisted 🎉 with actor `twin-alex`; appropriate congratulations |
| Distress after a poor interview | 1 | Substantive empathetic response, invitation to talk, no delegation or emoji-only dismissal |
| “How many minutes in one hour?” | 1 | Direct one-sentence answer, no tools or spawn |
| One-sentence `reminder.md` write | 2 | One real main `write`, correct temporary file content and concise confirmation, no spawn |
| **V2 total** | **23** | **22 HTTP 200, one ConnectError** |
| V1 retained failure | 6 | Six HTTP 200, no delegation |
| **Aggregate** | **29 / 32** | **28 HTTP 200; one authorized UI recovery** |

The reactions were read back from the isolated product database and seen in the browser; they were not simulated tool recordings or human clicks. The complex chain used the actual API, worker, SDK, product tools, PostgreSQL and Redis. Each control was sent once through the owner composer without mentioning delegation or reactions. These few observations establish the observed paths, not a general success rate or a guarantee that the model always chooses the same behavior.

![Real child completion automatically returned a useful owner notification](/tmp/duet-behavior-live-v2-20261028/real-child-result-owner-notification.jpg)

![Natural gratitude produced a persisted Duet reaction](/tmp/duet-behavior-live-v2-20261028/natural-gratitude-persisted-reaction.jpg)

## Version, evidence and cleanup

The V2 recursive product-source hashes were identical at startup and completion (`source_hash_delta: []`), and matched `/tmp/duet-delegation-runtime-v2-frozen-hashes.json`:

| Product file | SHA-256 |
|---|---|
| `backend/app/twin_prompts.py` | `8c449605a07f6b6cc292053fce5995b568f847c19a8243a5f7dd78ed200479dc` |
| `backend/app/soul_file.py` | `fa77b3cbab8cfd91b95357aa889bee61ce346aa0f46298cf53e406857282b0c0` |
| `backend/app/runtime_bridge.py` | `9d3c693b60f3f96267793f7feeb9ff2604d304af1b393d68c25fc5f80474d615` |

Runtime's separate offline evidence was 75/75 tests for V1 and 34/34 affected tests for the V2 revision. Independent integration checks: **7/7**, zero failures or skips; verified directly against `/tmp/duet-social-delegation-v2-independent.xml` and its summary JSON, with no product hash delta. Those regressions establish mechanics and prompt contracts. The live observations above establish actual model choices for these inputs. Changing the default SOUL remains create-once behavior: it does not overwrite an existing owner's customized SOUL or already frozen task configuration.

V2 private artifacts are retained under `/tmp/duet-behavior-live-v2-20261028/`: `final-canonical.json`, `final-freeze-boundary.json`, `final-summary.json`, `budget-change.json`, all HTTP/tool/declaration/model-input journals, stage snapshots, screenshots, and actual output files in `v2-deliverables/`. V1 evidence remains separately preserved. No private production conversations or credentials are included in the repository JSON; the local journals contain only synthetic fixture data and redact provider configuration.

At the final snapshot there were no active requests or SDK runs. Both owned stacks were gracefully stopped. V2 cleanup removed its disposable PostgreSQL schema, five namespace Redis keys and temporary root; launcher/worker/API processes were gone and its loopback port was closed. `cleanup.json` records these checks. Browser fixture tabs were closed. The isolated acceptance itself did not restart a production service or make a deployment, commit or push. The later authorized release is recorded below.


## Authorized deployment and read-only production smoke

After Root accepted the complete V2 evidence, runtime performed the authorized backend-only deployment, finishing **2026-10-28 01:08:44 CST**. The verified worker and two APIs exited gracefully after SIGTERM and restarted with the frozen V2 source. There was no migration, initialization, seed/reset, frontend publication, configuration write or SOUL rewrite. Runtime’s `/tmp/duet-delegation-rollout.json` verifies exact preservation of all 218 message rows, eight chat sequences, existing SOUL files/markers, environment files, SDK checkout, frontend files and FRPC process. New SDK commands and product requests were both zero.

The rollout’s 12 HTTP checks passed: health, index, JS and CSS at each local API and the public HTTPS entry. Public TLS verification passed; existing `index-BhuwDfMT.js` and `index-o3WCLiE0.css` hashes remained exact. The full frontend manifest is retained only in local rollout evidence.

The subsequent CUA browser smoke opened `http://127.0.0.1:8000/` and `https://154.217.234.218/`. Both rendered the **Welcome to Duet** sign-in page without a browser security interstitial. No sign-in, account switch, message, reaction or provider call was performed. Thus browser coverage is page reachability and rendering only; authenticated behavior remains the isolated acceptance evidence above. Both smoke tabs were closed. `/tmp/duet-delegation-production-browser-smoke.json` records the observations.

![Public production sign-in screen after authorized deployment](/tmp/duet-delegation-public-production-smoke.jpg)

The repository report and JSON contain synthetic acceptance data and allowlisted rollout results, with no private conversation bodies, SOUL contents or credentials. Deployment did not erase the V1 behavioral failure, the V2 initial transport failure, or the shell/web acceptance limits. No additional model testing occurred after the 29-attempt ledger closed.
