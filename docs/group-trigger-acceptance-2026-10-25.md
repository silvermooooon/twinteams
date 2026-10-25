# Group routing fix acceptance — 2026-10-25

Status: the required behaviors were verified across the eight-phase campaign and a separately authorized targeted follow-on check on identical application code. The first report correctly remains failed because a real `ConnectError` interrupted the follow-on; the targeted check passed. The offline failure/retry browser case also passed. Post-fix real-provider usage was **29 of 40 allowed attempts**. The earlier investigation remains preserved in `group-trigger-review-2026-10-25.md` and its original artifact directories.

## Frozen live verification

One fresh isolated application stack, three real Chrome sessions, three synthetic owners and three uniquely named Duets. Use the actual SDK, PostgreSQL, Redis and `gpt-6-luna`, with the existing credential connection. Every mutation remains an ordinary browser UI action; inspectors and API reads are read-only. No production writes, forced provider failure, interception, route settlement, artificial clock or TTL override.

The frozen ceiling is **40 provider HTTP attempts**, including smoke and failed HTTP attempts, with the existing 900-second deadline and 4096-output-token ceiling. The root approved the eight-phase campaign after product freeze and successful offline browser verification. It used 23 attempts: 22 HTTP 200 responses and one `ConnectError` with no HTTP status. The error occurred while classifying Beacon's actual newly published question to Cedar, before a CCC request existed. The script completed the remaining independent read/offline phases and correctly failed its final assertion for the unresolved follow-on. It did not relabel that failure as a model decision to remain silent.

The root then explicitly authorized one targeted fresh follow-on campaign capped at the remaining **17 attempts**, preserving `23 + targeted <= 40`. This was a separate reported campaign, not an implicit retry or a rerun of all eight phases. It reused the same live helpers with three fresh browser sessions and UI-renamed Duets. A single source-owner BBB Retry click was permitted only if an incidental `ConnectError` recurred on Beacon's message; it was **not needed or used**. No failure was forced and no retry loop ran. The targeted campaign used six HTTP 200 requests, including smoke, for a combined **23 + 6 = 29 attempts**. The same deadline/output limits applied. No further campaign ran.

The harness writes `group-routing-source-manifest.json` and checks the same source hashes at completion. This covers changed application/fixture/browser source files and the live provider guard. It preserves the source-commit reference while identifying uncommitted fixes actually tested.

## Desired behavior

| Phase | Acceptance |
| --- | --- |
| First message in fresh three-owner group addresses custom `@Beacon Duet` only | Exactly one completed BBB Duet request and one visible answer containing the requested marker and `12`; no terminal follow-on |
| Equivalent human `@BBB` question while owner is online in Profile and unread | Exactly one BBB request, accepted after at least ten real seconds; one visible requested answer; no inherited read receipt |
| Fresh two-owner chat receives an ordinary arithmetic question with no mention or name | Exactly one peer BBB Duet request, accepted after at least ten real seconds while the peer stays online in Profile; one visible requested `17` answer; terminal stop |
| Two-owner chat addresses only sender's own `@Atlas Duet`, explicitly excludes BBB | Zero requests, candidates and reservations; zero spent allowance; no unsolicited Duet message |
| BBB addresses human `@AAA` and owner ID `@owner-jordan` | Exactly two intended requests and one requested visible answer from each target; source-owner Duet excluded; terminal answers stop |
| Beacon Duet poses a new plain-name question to Cedar Duet | Genuine Duet-authored source creates the CCC request in the same causal chain; Cedar publishes the requested `54`; exactly two intended tasks total and terminal stop |
| CCC truly reads a new targeted question then navigates to Profile | Normal browser-generated per-message receipt within ten real seconds; router still recognizes a question; CCC candidate durably suppressed with `owner_read` and no accepted request |
| Close CCC context, wait actual original presence TTL, then address owner ID | Read-only Redis proves actual offline transition; exactly one accepted CCC candidate/request before the bounded online ten-second threshold; one requested visible answer; terminal stop |

Every phase also checks no same-source-owner automatic request, no failed/pending route, no queued/running/failed request and no waiting candidate when settled. A final audit checks that no later request appeared in any earlier chain. The six-task cap is observed only if naturally reached; deterministic cap tests remain separate from this live evidence.

## Failure/recovery coverage

Provider failures will not be forced in the real-provider campaign. A separate offline browser fixture case uses a deterministic SDK classifier failure, followed by normal Retry and Dismiss UI actions. It proves a failed durable SDK run and visible source-message notice without fabricating an accepted reply request; the peer sees failure without Retry; source-owner dismissal persists after reload while durable failure remains; a later source's normal UI Retry submits its current command identity, creates a fresh durable classifier command, and produces exactly one completed peer reply and visible answer. Reload removes the recovered failure notice.

The first offline run reached successful actual recovery but timed out because its polling helper incorrectly expected completed requests in chat detail, which intentionally returns only active/failed requests. That failed report remains at `/tmp/twinteams-group-route-recovery-20261025`. After the helper was corrected to use the guarded read-only inspector for terminal completion, **1/1 browser case passed in 15.494 seconds** at `/tmp/twinteams-group-route-recovery-20261025-verified`. Both runs used zero real provider calls and fully cleaned their schema, Redis namespace, processes and temporary root.

The first post-fix live campaign incidentally exercised real failure visibility: its final screenshot shows “Automatic reply could not start” below Beacon's actual question. AAA sees Dismiss and no Retry, correctly reflecting that BBB owns this Duet-authored source. The projected message error is sanitized; the provider ledger separately records `ConnectError`. Successful explicit Retry was verified in the offline SDK/browser case. The targeted live follow-on succeeded without Retry, so live Retry execution is not claimed.

## Eight-phase observations

The original post-fix report is `/tmp/duet-group-routing-fixed-live-20261025/report.json`, deliberately **failed** for the follow-on connection error. All eight phases executed in 227.323 seconds. Its source-manifest equality check passed.

| Phase | Observed result |
| --- | --- |
| Fresh custom alias | Correct raw owner target and bounded mention; one real requested `12` answer; accepted after 10.038 seconds; terminal stop |
| Human-name control | One correct real answer; accepted after 10.032 seconds; terminal stop |
| Ordinary fresh pair question, no mention | Model selected the peer; one real `17` answer; accepted after 10.033 seconds; terminal stop |
| Own-Duet-only pair address | Model selected the author's owner; source-owner filtering left zero candidates, requests, reservations and spent allowance; no Duet message |
| Multiple recipients | Exactly Atlas and Cedar requests and separate requested visible answers; terminal stop |
| Genuine Duet follow-on | First campaign: Beacon published the requested CCC question; its classifier encountered a real `ConnectError`; no CCC request/answer. Separate targeted campaign: genuine Beacon question triggered Cedar's actual `54` answer and terminal stop |
| Read then Profile | Specific browser read receipt after 252.060 ms; `owner_read` candidate suppression; no request |
| Real presence expiry and offline owner-ID input | CCC became offline 35.765 seconds after context close; one offline answer accepted after 3.262 seconds; terminal stop |

The failed campaign's schema was removed, Redis namespace had zero remaining keys, every owned process stopped and the temporary root was removed. Its final screenshot was visually inspected: it shows three human/Duet pairs, the newly visible real failure notice, the actual CCC read receipt, and the offline answer.

## Targeted live follow-on result

`/tmp/duet-group-routing-fixed-follow-on-20261025/report.json` reports **1/1 passed in 55.613 seconds**. The fresh group had three real human sessions and the UI-renamed Atlas/Beacon/Cedar Duets. AAA's initial message selected only BBB. Beacon's actual conversational question then selected CCC through the model's inferred target, with no deterministic mention on that Duet message. Cedar published the requested answer `54`. Its terminal route selected no owner and required no further response. Exactly two completed requests shared one causal chain, spending two of six allowance units. No live Retry click was needed.

The target scenario recorded six provider attempts, all HTTP 200 and all `gpt-6-luna`. The original failed eight-phase report and its failed connection row remain intact. The final targeted screenshot was visually inspected by this agent and the root and visibly shows the genuine Beacon-to-Cedar exchange and all three human/Duet pairs. The final inspector agrees with the report: two completed target requests and three completed routing decisions, ending with `needs_response=false`. Its schema, Redis namespace, owned processes and temporary root were all cleaned. No live campaign remains running. The six-task exhaustion boundary was not naturally reached; its verification remains deterministic.

## Code and deterministic verification

The updated live scenario passes Node syntax checking, Python compilation and `git diff --check`. It checks actual answer markers, request identity, durable suppression metadata, no waiting work and no late automatic requests. The 16 existing fixture/provider-budget guard tests passed again in 0.15 seconds before targeted verification.

The root's full backend verification passed **466 tests in 304.87 seconds**. Frontend typecheck and build passed with Node 22. An isolated legacy-column upgrade check proved startup rejects the missing routing-command column, and running initialization DDL twice preserves existing records. These are separate code/schema checks; they are not real-provider acceptance results.

The eight-phase browser scenario SHA-256 was `f7ce60a522928ee5f6135a8e81a6b77dea21d5895d499ccc3521d4000eab373f`. The targeted variant's scenario SHA-256 is `89e268b5a6518745c03aa7dad1c6ff63d7875071c330833ca741676ee15b8185`. All eight changed application source hashes under `backend/app/` and `frontend/src/` were compared before targeted launch and between the two saved live manifests after completion; every hash matched. Each campaign also passed its own start/end source-manifest equality assertion. Only the test harness/fixture scenario selection changed for the targeted verification; product code stayed frozen.

## Local rollout

After checking that production had no active work, the root applied the additive routing-command column while preserving existing records and restarted the managed worker and both APIs. The root verified both local health endpoints and public health were healthy with the model configured, and all three served index responses matched the current built UI bytes. The rollout generated no provider calls or production messages. This verification agent operated only disposable stacks; no commit or push was made.
