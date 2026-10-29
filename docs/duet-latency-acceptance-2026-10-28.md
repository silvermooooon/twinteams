# Runtime authorization latency acceptance — 2026-10-28

> Historical verification record. This describes the tested revision on its stated date, not the current peer-message contract. Current guidance: [interaction rules](interaction-rules.md) and [architecture](architecture.md).

The deployed change reuses visibility results within one authorization check and fetches missing message hash fields together only when a hash is required. Metadata-only reads remain deferred; already loaded or edited values are preserved. Each subsequent authorization still checks current permissions and content. The SDK and private configuration were unchanged.

The actual implementation's read-only replay of the earlier fifth request used **71 SQL reads**, with a three-run median of **49.081 ms**. This is the shipped implementation, not the earlier 43-query experiment.

Three sequential questions were sent through the real public Chrome UI to DEV-AAA's own Duet. Each reply was visible and correct, used one successful `gpt-6-luna` Responses call through the existing proxy, and used no tools or retries. Total provider usage was **3 of the 6-attempt ceiling**.

| Question → answer | Message → model start | Model adapter | Model finish → saved reply | Backend total | Click → visible reply bracket |
| --- | ---: | ---: | ---: | ---: | ---: |
| 17 + 25 → 42 | 1.198 s | 3.983 s | 0.186 s | 5.367 s | 6.550–6.571 s |
| 3 metres → 300 centimetres | 1.159 s | 3.111 s | 0.177 s | 4.447 s | 5.593–5.611 s |
| 90 minutes → 1.5 hours | 1.218 s | 3.301 s | 0.225 s | 4.745 s | 5.953–5.968 s |

The preparation median was **1.198 s**, versus **4.241 s** in the earlier fifth sample, a 71.8% observed reduction. Continuous read-only DOM probes bounded first visible replies within 21/18/15 ms. The owner's bubble was first observed by 0.337/0.330/0.355 s; preparing status by 1.163/1.092/1.156 s; running status by 1.699/1.630/1.679 s after the click. Durable source/reply IDs matched the observed DOM rows.

Validation included 11 new PostgreSQL cases. The full backend run produced 562 passes and one stale prompt-location assertion failure; after correcting that assertion, the complete affected seven-case file passed. All 563 unique cases therefore have verified final outcomes; this was not a second complete-suite run. Original failure evidence is retained.

Release waited for zero active work, then gracefully replaced only the managed worker and two APIs using SIGTERM. One public TLS probe returned an unexpected EOF; the original failed rollout report is preserved. A read-only recheck passed all six local/public health and index checks with normal TLS verification, without another restart or infrastructure change. All 232 original message rows and chat sequences were unchanged through release. Browser acceptance then added exactly six expected private-chat messages and three requests/commands/model calls; original message hashes remained identical. SOUL files, environment files, SDK checkout, frontend assets and FRPC remained unchanged. Final active-work counts were zero. Chrome remains on the completed private conversation with an empty draft. Existing unrelated failure notices were left intact.

These are sequential production observations, not a controlled benchmark or latency guarantee. Adapter time includes network/proxy/provider time; first provider-token timing is unavailable. Message timestamps precede commit. Browser intervals include click dispatch and DOM sampling. Three Memory review failure notices were already present before deployment and their request timestamps predate this release; the three successful chats do not establish that those older Memory tasks are repaired. A post-reply diagnostic briefly selected an older reply; its selector was corrected without resending, and the continuous timing probes were unaffected.

Product source SHA-256: `ab04c94462c74cc41ba891d81dd1b11766ab0a8866eb690850add03afd1afc6b`. Full sanitized evidence, timestamps, probes and screenshots: `/tmp/duet-latency-fix-20261028/` (`aggregate-report.json`, `browser-observations.json`, `sample-*-metadata.json`, `rollout.json`, `rollout-readonly-recheck.json`, `final-preservation.json`). Offline and microbenchmark evidence remains in `/tmp/duet-latency-20261028/`.
