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
