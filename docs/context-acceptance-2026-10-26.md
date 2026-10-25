# Context acceptance — 2026-10-26

Status: **accepted and locally deployed**. The final backend suite passed **512/512 in 412.91 seconds**; real-model and browser acceptance passed. Both local APIs and the public entry point are available. No further acceptance model calls are planned.

The frozen backend and provider-guard source-set SHA-256 is `7b10613f0544e8461ee4718fe4f4028242d4898bd1ccb907ce7e893a54a743b6`. Per-file hashes are recorded in the evidence below; the real run verified identical hashes before and after execution. Tests use disposable PostgreSQL schemas, temporary workspaces and secrets directories, and separate Redis namespaces. Existing application tables and services were not changed by these checks.

## Real model evidence

The unmodified SDK/provider path used **gpt-6-luna** against synthetic journal inputs. All **6 HTTP attempts returned 200**, within the single 12-attempt campaign: one smoke, two private checkpoints, one ordinary private reply, one group checkpoint, and one router decision. The remaining six attempts were unused.

- Private checkpoints covered original messages through sequence **512**, exceeding 500 cumulative sources. The ordinary reply returned the exact release code established at the start of the 540-message synthetic history.
- The group checkpoint retained the earlier assignment of release validation to BBB. The real router selected only `owner-maya` and required a response.
- Every group-summary and router provider payload excluded the private canary. Original private and group journal rows remained unchanged.
- Replaying the accepted command and checking the same checkpoint boundary made **zero additional provider calls**.
- The disposable schema was removed; cleanup found zero keys in the run's Redis namespace.

[Real provider report and attempt ledger](/var/folders/qb/r26c76pj22bfqvvm8cwg4bjh0000gp/T/twinteams-context-live-jnnqp34v/report.json)

## SQL workload and limits

The final real run read a checkpoint through sequence 512 and the contiguous **29-message tail, sequences 513–541**: **33 SELECTs**, **58 message rows fetched**, and at most **29 rows in any body-bearing message SELECT**. An earlier intermediate implementation, using mocked model transport with the same synthetic journal fixture and 29-message tail, recorded 150 SELECTs and 175 message rows fetched. These are context-read SQL measurements, **not an end-to-end platform latency benchmark**.

An existing long journal needs checkpoints built in bounded batches before model admission. History-permission changes can retire a checkpoint cache and require rebuilding it. These paths have a one-time latency cost and can page through denied-message metadata to find authorized history; they do not load an unbounded historical body window. Original messages remain the authority, and explicit history pagination remains available. This evidence does not claim constant work for initial bootstrap or permission-driven rebuilding.

[Earlier mocked-transport baseline, explicitly labelled as controlled](/var/folders/qb/r26c76pj22bfqvvm8cwg4bjh0000gp/T/twinteams-context-live-6cfnbjmn/report.json)

## Controlled regressions

[Independent context acceptance](../backend/tests/test_context_acceptance.py) covers private/group coverage beyond 500 sources, canonical history and exact tails, bounded SQL reads including legacy Duet dependencies, compact provenance on new outputs, pending questions outside the tail, and idempotent checkpoint publication. It verifies source edits, withdrawal and deletion; history boundaries; membership loss and gain; nested private sources and reset generations; unchanged legacy hash resumes; and router privacy. An unrelated new membership preserves already-published output and identical checkpoint rebuilding terminates.

[Runtime tests](../backend/tests/test_conversation_summary.py) use controlled provider responses with the real SDK to verify ordinary and recovered main/child tool-loop compaction, retained journals, no duplicate tools, checkpoint failure/truncation preservation, incomplete-context admission blocking, and failed/stopped checkpoint recovery. This does not authorize restarting a command with an explicit SDK `cancel_requested` flag. Legacy model/tool recovery retains the exact old **14-tool** frozen declaration, including `save_summary`; fresh runs use the current tool contract.

The real Chrome browser regression passed **1/1** with an offline deterministic provider. Its initial failure was a harness snapshot race: the poll saw request completion before reading the expected reply in the same snapshot. The fixture now waits for both, retaining the exact-one-reply assertion. The original failed evidence is preserved.

- [Passing browser report](/tmp/duet-context-browser-20261026/report.json)
- [Initial browser snapshot-race evidence](/tmp/duet-context-browser-20261026-failed-snapshot/report.json)

The first full backend run reported 506 passed and six stale-fixture failures. Four expected the former router context list; two built a legacy tool fixture from today's declarations. The corrected six cases passed targeted checks, with the 14-tool recovery coverage preserved. The final full rerun passed **512/512 in 412.91 seconds**; its frozen source hashes match the real-model run and were unchanged throughout the suite.

- [First full run and frozen-source result](/var/folders/qb/r26c76pj22bfqvvm8cwg4bjh0000gp/T/twinteams-context-suite-final-4q111pyx/result.json)
- [Passing final rerun and frozen-source result](/var/folders/qb/r26c76pj22bfqvvm8cwg4bjh0000gp/T/twinteams-context-suite-pass-rhcqwx5h/result.json)
- [Final full backend log](/var/folders/qb/r26c76pj22bfqvvm8cwg4bjh0000gp/T/twinteams-context-suite-pass-rhcqwx5h/pytest.log)

## Local rollout

The authorized rollout completed after all acceptance gates passed. Preflight found zero active replies, pending routes, active candidates and SDK leases. The specified incremental schema transaction committed three summary columns and four indexes. The existing routing `command_id` column required no further migration.

The managed worker and both local APIs were restarted once; the previous managed processes stopped and the new processes are alive. The FRP process remained unchanged. Both local APIs and the public entry point returned **200** for health and the home page; each entry point served the three checked static assets byte-for-byte equal to the current `dist`.

The original **210 canonical messages** retained the same journal digest. The **94 human-message count** and every chat's `next_message_seq` remained unchanged. Postflight still found zero active replies, pending routes and SDK leases. The rollout created no production messages and made no deliberate model calls.

[Local preflight, migration, restart and postflight evidence](/tmp/duet-context-rollout-20261026.json)
