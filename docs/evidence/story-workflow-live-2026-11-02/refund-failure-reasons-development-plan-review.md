# Customer-Safe Refund Failure Reasons — Development Plan

**Document due:** November 20, 2026  
**Status:** Story accepted; implementation remains open. This is a planning document only—not implementation approval or evidence that work has started.

## Goal
Give customers clear, accurate, safe explanations when a refund fails. Show only approved reason wording, applicable next steps, and supported localized wording. Never disclose internal codes, raw errors, stack traces, provider or implementation details, sensitive data, or other unsafe information.

## Scope and guardrails
- Map only failure categories and meanings approved by the designated owners. Do not infer categories or promise a remedy.
- Use approved, nontechnical customer wording that accurately represents each category. Include next steps only when approved and applicable.
- For unknown or unmapped failures, use a generic safe explanation and an approved support/follow-up route if configured.
- Riley's confirmed guidance for unknown failures: show a generic safe message, include an incident reference for support, and never expose the internal error. **The exact message and how the incident reference is presented are undecided.** This guidance does not approve exact wording, retry advice, or a distinction between confirmed-no-effect and uncertain/in-progress outcomes.
- Keep the prior suggestion to distinguish confirmed-no-effect from uncertain/in-progress cases, and to avoid retry advice when uncertain, as **candidate decisions only**. Obtain explicit approval before adopting either.
- Do not assume an architecture, existing category taxonomy, supported channel, locale, or customer-facing surface; discover and document these first.
- **No code or implementation work before Riley reviews this plan and the open decisions below.**

## Decisions and inputs needed before implementation
| Decision / input | Owners to identify or consult | Required outcome |
|---|---|---|
| Approval authority and sign-off | Riley to name the product/content, support, and technical approvers as applicable | Named approvers and approval path for mappings, copy, and release |
| Failure categories and mappings | Technical/domain owner with product/content review | Approved category list, meaning, and handling for each; gaps explicitly identified |
| Customer wording and next steps | Product/content and support owners | Approved, clear explanation per category; only approved, accurate next steps; no invented remedies or promises |
| Unknown-failure behavior and support route | Riley, product/content, support, and technical owner | Approved fallback copy; whether/how a support or follow-up route is configured and shown |
| Incident reference | Riley and support/technical owners | What reference is safe and useful, when available, and approved format/presentation; fallback if unavailable |
| Status and retry language | Riley and relevant product/technical approvers | Decide whether confirmed-no-effect vs uncertain/in-progress handling is needed and whether retry guidance is allowed; existing suggestions remain unapproved candidates |
| Channels, locales, and variations | Product/content and channel/locale owners | In-scope customer surfaces and notification types; supported locales and approved variants; localization review process |
| Customer-facing contract and presentation surface | Technical owner with product owner | Identify where refund outcomes and wording are delivered/displayed (e.g. API response, UI, notification); confirm existing contract and compatibility constraints, without assuming them |
| Accessibility | Product/design and accessibility owner | Applicable requirements for the actual surfaces and content presentation |
| Content governance | Product/content owner | Source of truth, versioning, review/translation approval, change control, and retirement process |
| Safe logging | Security/privacy and technical owner | Permitted diagnostic fields, redaction/retention/access rules, and how support uses references without exposing internal details to customers |

## Proposed sequence and gates
1. **Review and authorize the plan (before any code).** Riley reviews this document, names approvers, resolves or assigns the decisions above, and confirms scope and surfaces. Record deferred decisions and safe behavior while unresolved.
2. **Inventory and map.** With the responsible technical owner, discover current refund failure outcomes, customer-facing surfaces, locale support, and relevant constraints. Produce an owner-approved category-to-meaning-to-copy/next-step mapping. Mark unknowns and unmapped outcomes explicitly; do not make up a taxonomy.
3. **Approve content and safety rules.** Obtain approval for each customer-facing string, locale, applicable next step, fallback, and incident-reference treatment. Confirm status/retry language and safe behavior where the outcome is ambiguous. Complete content, accessibility, privacy/security, and support review as applicable.
4. **Design against the discovered system.** The technical owner proposes how the approved mapping reaches identified customer surfaces and how unknown/unmapped outcomes safely fall back. Review contract compatibility, localization, observability, and failure behavior. This is a design gate, not authorization to implement.
5. **Implement only after explicit review.** After Riley reviews the plan and required decisions and approvals are recorded, proceed under the separately authorized implementation task. Keep customer copy sourced from approved content and avoid exposing internal diagnostics.
6. **Verify, stage, and release.** Run the tests below, have designated owners review results and customer-visible wording, then use the existing release process for a controlled rollout. Roll back or disable the change if safety, accuracy, or delivery criteria fail; the technical owner must identify an available mechanism rather than this plan assuming one.

## Test strategy
- **Mapping and accuracy:** For every approved category, verify wording reflects its approved meaning, is clear/nontechnical, and includes only approved applicable next steps. Verify no unapproved category or remedy is introduced.
- **Unknown/unmapped failures:** Exercise an unknown outcome, absent mapping, and any configured fallback path. Confirm generic safe wording; include the approved support/follow-up route only if configured; verify incident-reference behavior follows approved format/availability rules; ensure no internal error is shown.
- **Leakage tests:** Use representative diagnostic values and assert customer-facing API/UI/notification outputs contain no raw errors, internal codes, stacks, provider/implementation details, or sensitive data. Cover known, unknown, malformed, missing, and unexpected failure inputs on every in-scope surface. Ensure diagnostics are not copied into localized copy or customer-visible references.
- **Status and retry behavior:** Once explicitly decided, test approved handling for confirmed-no-effect and uncertain/in-progress outcomes, including permitted or prohibited retry language. Until approved, do not treat candidate distinctions or retry wording as requirements.
- **Localization and presentation:** For each agreed locale/surface, verify the approved localized variant is selected, no fallback leaks untranslated/internal text, and truncation/layout preserves meaning and any support reference.
- **Accessibility and compatibility:** Validate agreed accessibility criteria and existing customer-facing contract/consumer compatibility on actual identified surfaces.
- **Logging and support:** Verify permitted diagnostics remain useful to authorized support while customer-visible content stays safe; confirm redaction and reference correlation follow approved rules.
- **Regression and release checks:** Cover successful refunds, unrelated refund behavior, and agreed notification/API contracts; run the project's existing checks and staged-release verification once identified.

## Risks and mitigations
- **Incomplete or misleading mappings:** Require named category owners and explicit sign-off; keep unmapped cases on the generic safe path.
- **Overpromising or unsafe instructions:** Approve every next step and retry/status statement; omit anything not approved and applicable.
- **Internal-detail leakage:** Treat failure text and diagnostics as untrusted for customer display; add surface-wide leakage tests and review logging separately.
- **Reference or fallback unavailable:** Decide approved behavior for missing incident references and unconfigured support routes before release; do not invent a contact path.
- **Localization drift:** Maintain approved source copy and require locale review/version alignment before enabling each locale.
- **Scope/contract surprises:** Discover actual surfaces and contracts before design; assess compatibility with their owners rather than presuming architecture.
- **Approval or content delays:** Name approvers early, track undecided items, and keep implementation blocked until required content and safety decisions are approved.

## Acceptance and rollout criteria
Release readiness requires all of the following:
1. Named approvers have approved in-scope categories, meanings, wording, applicable next steps, fallback, incident-reference behavior, locales, and status/retry behavior (or explicitly excluded unresolved variants).
2. Every in-scope known category maps to approved accurate wording; unknown/unmapped cases safely use the approved generic path.
3. Leakage tests pass on every in-scope customer surface; no prohibited internal or sensitive detail is exposed.
4. Support/follow-up and incident-reference behavior matches approved configuration, including unavailable-reference handling.
5. Agreed localization, accessibility, compatibility, logging/privacy, regression, and staged-release checks pass with results reviewed by designated owners.
6. A support/monitoring owner and rollback or disable procedure are identified for rollout; proceed only through the existing release process.

**Current disposition:** Plan drafted for Riley's review. No code has been written, no source files changed, no implementation started, and no implementation approval is implied. Riley's review and the decisions/approvals above remain prerequisites.
