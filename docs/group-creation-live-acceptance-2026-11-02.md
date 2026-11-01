# Human group creation live browser acceptance — 2026-11-02

The final scenario passed in 69.3 seconds with Chrome, three separate development-account browser sessions, the real Duet worker, and the configured `gpt-6-luna` provider. All 18 bounded provider attempts returned HTTP 200. The [sanitized evidence](group-creation-live-acceptance-2026-11-02.json) records the synthetic inputs, memory and tool calls, approval, membership, messages, and cleanup. Screenshots show the [proposal and approval](../design-artifacts/acceptance/group-creation-live/proposal.png), [AAA's view of the conversation](../design-artifacts/acceptance/group-creation-live/messages-aaa.png), and [CCC's view](../design-artifacts/acceptance/group-creation-live/messages-ccc.png).

AAA first asked about an unrelated Borealis review without identifying any participants. Duet gave general advice; it neither created a group nor proposed an exact group action. AAA then told Duet to remember that BBB owned Atlas settlement implementation, CCC owned Atlas release QA, and AAA coordinated the launch. Duet wrote those roles to `projects/atlas`. In a later task AAA described scattered Atlas readiness discussions and asked for a suggestion without naming participants or directing creation. Duet searched memory and received those same project roles, checked existing chats, resolved BBB and CCC, and requested approval for a human group containing exactly AAA, BBB, and CCC with no prior history. The earlier owner conversation was also available to the model, so this run does not establish that memory was its exclusive source.

No group existed before approval. After AAA approved through the browser, the real continuation called `execute_approved_action` and created the group. It appeared in all three already-connected browser sidebars without reload; each browser opened it and showed all three members. AAA, BBB, and CCC each sent a human message, and all three browsers displayed all three messages. The new group initially had no messages, and after the exchange contained only those three human messages and no group Duet requests. A postcheck of the recorded tool calls found no successful Duet-collaboration request.

AAA then explicitly asked Duet to create a different named group with BBB and CCC. Duet used `create_chat` directly, with the exact three human members, no additional question or group approval, and no initial message. The five execution requests in the final run completed, and no tool calls failed. The isolated PostgreSQL schema, Redis namespace, processes, and temporary workspace were verified removed.

A separate earlier diagnostic used the real model and same disposable fixture to reject an exact proposal for the same role-grounded three-person group. The rejection persisted and no group existed at the following check; its sanitized provenance and cleanup are included in the evidence file. A later task in that diagnostic raced the rejection continuation and saw stale pending context, so it is not counted as a successful reconsideration. Another diagnostic selected the private chat because its preview contained the group title; the test was corrected to choose the group by its name and wait for its selected header before the final run. These observations are individual model runs, not a guarantee that every similarly worded task produces the same proposal.

To repeat from the TwinTeams root, with local PostgreSQL/Redis, the previously configured provider credentials and loopback proxy, Node 22.12+ and Chrome available:

```sh
TWINTEAMS_BROWSER_CHANNEL=chrome backend/.venv/bin/python backend/checks/browser_e2e.py \
  --live-model --scenario group-creation-live \
  --node /usr/local/n/versions/node/22.14.0/bin/node \
  --artifact-dir /tmp/twinteams-group-creation-live
```

The fixture permits only `gpt-6-luna`, at most 60 provider attempts, 4096 output tokens per attempt, and a 900-second deadline. It uses disposable PostgreSQL and Redis namespaces and cleans up its own processes and data. Live model calls incur their normal cost.
