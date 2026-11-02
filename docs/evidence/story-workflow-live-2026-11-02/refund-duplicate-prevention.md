# Prevent Duplicate Refund Effects

## User story

As a customer requesting a refund, I want repeated submissions of the same refund request to be handled idempotently, so that retries or accidental resubmissions do not create duplicate refund effects.

## Context / description

Customers may submit the same refund more than once, including when a request times out or the result is unclear. Identify a refund request by the combination of its transaction identity and refund-request identity. Repeated submissions with that same identity must not create additional refund effects. A confirmed failure must allow a safe retry. If the outcome is still in flight or uncertain, retain and resolve the existing request outcome rather than issuing another refund.

Distinct refund-request identities for the same transaction must not be treated as duplicates solely because the transaction matches; apply any separate business eligibility or transaction limits without silently merging those requests. Keep the refund idempotency key for 24 hours from the initial request so repeated submissions and safe retries within that window resolve against the same logical request. This story defines behavior, not a particular storage, API, provider, or implementation design.

## Acceptance criteria

1. **Repeated submission is idempotent**  
   **Given** a refund request identified by transaction `T` and request identity `R` has already been accepted or has produced a refund effect  
   **When** the same transaction-and-request-identity pair is submitted again  
   **Then** the system does not create an additional refund effect  
   **And** it returns or presents the existing request's appropriate outcome rather than representing the repeat as a new refund.

2. **Concurrent duplicates are safe**  
   **Given** two submissions with the same transaction identity and refund-request identity arrive before either has a final outcome  
   **When** both are processed  
   **Then** they resolve to one logical refund request  
   **And** at most one refund effect can be created for that request.

3. **Retry after confirmed failure is safe**  
   **Given** a request with transaction `T` and request identity `R` has a confirmed failure and no refund effect was created  
   **When** the customer or an authorized caller retries that same request  
   **Then** the retry can be processed safely  
   **And** processing cannot create more than one refund effect if the retry succeeds.

4. **Uncertain or in-flight outcome is not reissued**  
   **Given** the system cannot yet determine whether a request with identity `(T, R)` succeeded, or it remains in flight  
   **When** the same request is submitted again  
   **Then** the existing request is retained for outcome resolution (including any necessary reconciliation)  
   **And** no second refund is issued merely because the first outcome is unknown.

5. **Different request identities remain distinct**  
   **Given** two refund submissions have the same transaction identity but different refund-request identities  
   **When** the system evaluates them  
   **Then** it does not classify them as duplicates based only on the shared transaction identity  
   **And** it evaluates each under applicable refund eligibility and business rules.

6. **Identity is consistently applied**  
   **Given** a refund submission or retry is received  
   **When** duplicate detection and outcome resolution are performed  
   **Then** both the transaction identity and refund-request identity are used together to identify the logical request.

7. **Idempotency key is retained for 24 hours**  
   **Given** a refund request with transaction identity `T` and refund-request identity `R` is first received  
   **When** the request is processed  
   **Then** its idempotency key is retained and recognized for 24 hours from that initial receipt  
   **And** repeat submissions using the same `(T, R)` during that window resolve to the existing logical request and cannot create an additional refund effect.

## Assumptions / open questions

- The transaction identity and refund-request identity are available and stable across retries; the source, format, scope, and uniqueness guarantees for each need confirmation.
- Define what constitutes a **confirmed failure** with certainty that no refund effect occurred, and how late or contradictory provider outcomes are reconciled.
- Confirm whether a retry after confirmed failure reuses the original request identity (assumed here) and which actors may initiate it; safe retries within the 24-hour idempotency-key window must not create duplicate effects.
- Define what should happen to replays after the 24-hour key-retention window expires, including whether the original request identity remains separately protected from duplicate effects.
- Specify behavior and customer-visible status for in-flight/uncertain requests, including any resolution or escalation process; no timing or status names are prescribed here.
- Define applicable eligibility, partial-refund, amount, currency, and transaction-level limits for distinct request identities. This story does not assume that distinct identities are always independently refundable.
- Define the response shown for a duplicate submission and the audit/retention requirements.
