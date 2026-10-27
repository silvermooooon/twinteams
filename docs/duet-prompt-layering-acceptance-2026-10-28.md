# Prompt layering acceptance — 2026-10-28

This separate campaign evaluates the condensed role/tool instructions with the previous six unchanged natural inputs. It does not replace the [previous behavior history](duet-delegation-behavior-acceptance-2026-10-28.md). Only GPT-6 Luna / OpenAI Responses and the existing authorized provider/proxy configuration were used. The hard aggregate limit was **24 actual HTTP attempts**, including any failures/retries. Round 1 used 21 and the separately revised Round 2 used 2: **23/24 total, all HTTP 200, no provider failures or retries**. No further model testing is planned.

## Design reference

The structural reference was the local [nanoMuse desktop preset at commit `bfbe5edf67e2804802faccfdaad7c85c49694a79`](https://github.com/nano-muse/nanoMuse/blob/bfbe5edf67e2804802faccfdaad7c85c49694a79/harness/dsh-nanomuse/presets/nanomuse.patch.yml), together with its pinned DSH `0.2.0-rc.2` tool packages inspected under `/tmp/nanomuse-prompt-audit-20261028/`. Its background tool descriptions carry handoff, completion notification and advice against status polling, separately from the persona. This change borrows that organization, not its implementation. It does not claim nanoMuse has Duet's strict front-desk mission or equate typing/status emoji with emotional message reactions.

## Round 1 results

| Original case | HTTP attempts | Observed result |
|---|---:|---|
| Complex three-file reconciliation | 14 | **PASS** early delegation, real child work, durable automatic main return and correct data files; **FAIL** owner-summary accuracy |
| Natural gratitude | 2 | **PASS** real ❤️ reaction persisted as `twin-alex`, with brief warm acknowledgment |
| Tiny file write | 2 | **PASS** one direct main write, correct actual `reminder.md`, no delegation |
| Tiny factual question | 1 | **PASS** direct correct answer, no tools |
| Natural good news | 1 | **PASS** warm congratulations; **FAIL** spontaneous persisted-reaction subcriterion: no tool call or reaction row |
| Distress | 1 | **PASS** substantive empathetic care, no delegation or emoji-only dismissal |

The complex case naturally spawned a child before the main read the three inputs or wrote outputs. The main acknowledged and yielded; the real SDK child wrote both files. Its durable result was accepted and automatically resumed the main, which reread the three inputs and both outputs, published the files and delivered the owner summary. Both product requests and all three SDK runs reached completed states. The child wrote both deliverables; the main did not rewrite them. This does not establish zero duplicate internal analysis.

The actual CSV contains eight unique paid orders totaling 790; September/October totals are 370/420 and customer totals are North 380 / South 300 / West 110. The actual report correctly records draft corrections and traceable IDs. **The owner summary nevertheless says “9 月 450（应为 370，少报差额 80）”.** The direction word is wrong: the draft overstates the amount by 80. File correctness and this failed summary subcriterion remain separate. The material-review and original-goal instructions remained present, and the main actually reread all five files; this observation does not establish that layering caused the error.

Good news received appropriate congratulatory prose but no product reaction. Neither failed observation was resent, induced with extra instructions, or erased. Root authorized the remaining original controls after each reported issue. These are particular live samples, not a frequency estimate or deterministic behavioral guarantee; this is **not six fully passing cases**.

## Scope and version evidence

The existing behavior fixture supplied fresh disposable PostgreSQL, Redis, workspaces and a loopback API/worker. Model choices were not scripted. Function declarations remained intact; actual file tools, product reactions and SDK callbacks executed. Bash remained declared but its executor explicitly rejected the child's verification command: no command ran. The child had also read its actual outputs. Native provider web search was omitted because its execution cannot be fenced by the local executor. This is not shell or web end-to-end coverage.

Round 1's original manifest is preserved inside its artifact directory. Each round matched its own frozen product hashes at startup and completion (`source_hash_delta: []`). All seven requests and eight SDK runs in Round 1, and the one request/run in Round 2, reached completed states. Both owned stacks were stopped gracefully; each schema, five Redis keys and temporary root were removed, owned processes exited, loopback ports closed and browser tabs closed. The isolated acceptance made no production data/service change, external message, configuration change, commit or push. Any later authorized deployment is recorded separately.

Private synthetic evidence is retained at `/tmp/duet-prompt-layering-live-20261028/`: `first-campaign-final.json`, `first-campaign-freeze-boundary.json`, `first-campaign-live-budget.json`, actual files in `deliverables/`, tool/declaration/model-input journals and screenshots. The [repository JSON](duet-prompt-layering-acceptance-2026-10-28.json) contains only necessary results, frozen hashes and evidence indexes, not raw logs or private production context.

## Size measurements

`/tmp/duet-prompt-layering-warmth-size-comparison.json` preserves baseline, Round 1 and final-warmth measurements. It measures **characters and English whitespace-separated words, not tokens**, in the same synthetic empty-history context without SOUL/Skills payload. Unchanged schemas and remaining tool descriptions are excluded from all sides.

| Measured text | Baseline chars / words | Round 1 chars / words | Final chars / words | Final character reduction |
|---|---:|---:|---:|---:|
| Main platform role | 2117 / 294 | 1444 / 200 | 1466 / 202 | 30.75% |
| Assembled main plus four related tool descriptions | 8612 / 1245 | 8155 / 1166 | 8177 / 1168 | 5.05% |

The four descriptions are spawn/get/wait/reaction. The main role shortened while those descriptions grew. These numbers do **not** support a claim that whole-system tokens fell 32%. Versioning and representative evaluation follow the official [OpenAI prompting guide](https://developers.openai.com/api/docs/guides/prompting).

## Round 2: separate revised-prompt check

After Round 1 was sealed and cleaned, the general main-role reaction mission was broadened beyond acknowledgment-only situations. Root authorized exactly one good-news check with at most two HTTP attempts on the separately frozen source. The same natural input was sent once in a fresh independent fixture, without mentioning reactions. **Round 2 PASS:** the real `set_message_reaction` persisted 🎉 as `twin-alex`, followed by natural congratulations and completed request/SDK states. It used two HTTP 200 attempts, with no retry. The changed prompt and fresh history make this a separate observation, not a controlled causal estimate.

This does not replace Round 1's missing-reaction result or repair/retest the complex summary error. Other cases were not rerun on the final wording. Evidence is `/tmp/duet-prompt-layering-warmth-live-20261028/`: `good-news-final.json`, `final-freeze-boundary.json`, `live-budget.json`, journals, screenshot and cleanup record.

Final frozen source:

| File | SHA-256 |
|---|---|
| `backend/app/twin_prompts.py` | `21561cc83e90af3c66e0bd58a410750fb2beb9196c38ad40264c5b9a6141f187` |
| `backend/app/runtime_bridge.py` | `aca82f24d6f9fd7ab14dc5d915f73fa9bd86f39d7a62cb2c3e9ac063873ceb00` |
| `backend/app/soul_file.py` | `fa77b3cbab8cfd91b95357aa889bee61ce346aa0f46298cf53e406857282b0c0` |

Direct JUnit inspection confirms **42 distinct targeted offline checks passed**: 35 focused plus 7 independent integration checks. The final wording's 24 prompt checks passed again; these overlap the 35 and are not added to the distinct total. Offline scripted checks prove contracts/wiring, not spontaneous future model choices. Default SOUL remains create-once; existing owner files and frozen tasks are not overwritten.

![Round 2 actual persisted Duet reaction](/tmp/duet-prompt-layering-warmth-live-20261028/good-news-persisted-reaction.jpg)

## Release status

After Root authorization, runtime completed the backend-only deployment at **2026-10-28 02:21:20 CST** using graceful SIGTERM and the final frozen source. `/tmp/duet-prompt-layering-rollout.json` verifies preservation of all 218 message rows (94 human), eight chat sequences, SOUL files/markers, environment files, SDK checkout, frontend files and FRPC. No migration, frontend/configuration/SOUL write, production message or provider test occurred; new requests and SDK commands were zero. All 12 local/public health/index/JS/CSS checks passed, including public TLS and exact existing asset hashes.

The subsequent read-only CUA smoke reached **Welcome to Duet** at both `http://127.0.0.1:8000/` and `https://154.217.234.218/`, without a security interstitial. Both required sign-in; no login, account switch, message, reaction or model call was performed. Browser coverage is therefore page rendering/reachability, not authenticated behavior. Smoke tabs were closed. Evidence is `/tmp/duet-prompt-layering-production-smoke.json` and the local/public screenshots.

![Read-only public production smoke](/tmp/duet-prompt-layering-public-smoke.jpg)

The known summary-accuracy failure remains a delivery limitation. Repository evidence contains only synthetic fixture data and allowlisted rollout results, with no private production conversation bodies, SOUL contents or credentials. The 23-attempt ledger was not extended after acceptance.
