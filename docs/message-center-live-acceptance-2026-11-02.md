# Message Center live browser acceptance — 2026-11-02

The final run passed in 95.1 seconds using Chrome, the actual Duet UI, the configured `gpt-6-luna` provider, and real Duet and Message Center workers. All 25 bounded provider requests returned HTTP 200. The [sanitized evidence](message-center-live-acceptance-2026-11-02.json) contains the inputs, questions, tool calls, per-owner deliveries, responses, and request completion states. Selected screenshots show the [scope question](../design-artifacts/acceptance/message-center-live/scope-question.png), [Maya's invitation](../design-artifacts/acceptance/message-center-live/delivery-maya.png), and [Jordan's subset invitation](../design-artifacts/acceptance/message-center-live/subset-jordan.png).

Alex asked to organize a lunch and invite signups without naming an audience. Duet asked who to invite; the Center had no message at that point. Alex answered Alex, Maya, and Jordan in the browser question card. A real model tool call published an invitation with exactly those three owner IDs; the Center worker delivered it to all three, each Duet asked privately about participation, and no attendance response existed before the owners answered. All three browser answers led to structured `attending` responses in the Center.

Alex then requested a book club invitation for only Maya and Jordan. The Center created exactly two deliveries, both recipients received a participation question, and Alex received no delivery for that invitation. The second invitation's questions remained awaiting their owners' answers; no response was recorded for it. All five deliveries were processed and notified without claiming `human_read`, and all 11 execution requests completed. There were no failed tool calls in this run. The isolated schema, Redis namespace, processes, and temporary workspace were verified removed.

Earlier diagnostic runs found that an empty `options` list was rejected before the model retried, that recipient questions could be repeated in final text, and that an already-answered question could trigger a redundant rejected `answer_question` call before recovery. The final run followed the schema and prompt changes and did not reproduce those failures. This single live run demonstrates observed behavior, not a guarantee for every model response. The older `message_center_acceptance.py` uses a scripted offline provider and is separate from this live acceptance.

To repeat from the TwinTeams root, with the sibling `message-center` checkout and its backend virtual environment, local PostgreSQL/Redis, configured provider credentials and loopback proxy available, Node 22.12+ and Chrome installed:

```sh
TWINTEAMS_BROWSER_CHANNEL=chrome backend/.venv/bin/python backend/checks/browser_e2e.py \
  --live-model --scenario message-center-live \
  --node /usr/local/n/versions/node/22.14.0/bin/node \
  --artifact-dir /tmp/twinteams-center-live-acceptance
```

The live fixture permits at most 60 model attempts with at most 4096 output tokens each, and only its isolated Center loopback origin in addition to the configured provider. It uses disposable PostgreSQL and Redis namespaces and cleans up its own processes and data. The run uses live model calls and incurs their normal cost.
