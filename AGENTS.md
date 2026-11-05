# Duet project guidance

Follow the current [product requirements](docs/product-requirements.md),
[interaction rules](docs/interaction-rules.md) and
[visual identity and interface principles](docs/visual-identity.md).
Dated acceptance records describe past verification, not current design rules.

For every UI change, apply the art of whitespace: show only what helps the user
understand the conversation or make the next decision. Keep implementation status
out of the main chat; active replies need only a simple typing indicator. These
are product development rules, not instructions to add to each Duet's prompt.

Non-AI HTTP APIs have a 3,000 ms successful-response budget. Run the normal tests
with `-p checks.api_budget --api-budget-report /tmp/duet-api-budget.json` and the
isolated `python -m checks.api_performance --report /tmp/duet-api-load.json` load
check before accepting query changes. Errors/timeouts or missing data do not
satisfy the budget. Report the tested data volume and concurrency; file-transfer
timings cover local test payloads, not an arbitrary remote connection. See
[API performance acceptance](docs/api-performance-budget.md).
