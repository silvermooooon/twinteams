# Show Customers Safe, Useful Refund Failure Reasons

## User story

As a customer whose refund request fails, I want a clear explanation of the failure and a useful next step where possible, so that I understand what I can do without being exposed to internal or sensitive technical details.

## Context / description

Translate refund failures into approved, customer-safe explanations. Messages should help the customer understand the issue at an appropriate level and provide a meaningful next step when one is known. Do not expose raw internal errors, implementation details, sensitive data, or security-sensitive information. If a failure cannot be mapped confidently to an approved explanation, use a safe fallback rather than displaying internal details.

## Acceptance criteria

1. **Mapped customer-safe reason is displayed**  
   **Given** a refund failure is associated with an approved customer-facing reason mapping  
   **When** the failure is shown to the customer  
   **Then** the customer sees the mapped explanation in clear, non-technical language  
   **And** the explanation accurately reflects the approved meaning of that failure category.

2. **Internal details are not exposed**  
   **Given** the failure record contains internal codes, raw error text, stack traces, provider details, or other non-customer-safe data  
   **When** a customer-facing failure message is generated or displayed  
   **Then** none of those internal or sensitive details are disclosed in the explanation.

3. **Useful next step is included when approved and applicable**  
   **Given** an approved next step exists for the mapped failure reason  
   **When** the customer sees the failure explanation  
   **Then** the message includes that next step in a clear and actionable form.

4. **No unsupported next step is invented**  
   **Given** no approved next step is defined or applicable for a failure reason  
   **When** the message is shown  
   **Then** the system does not invent a remedy or promise an outcome  
   **And** it uses the approved explanation or safe fallback.

5. **Unknown or unmapped failure uses a safe fallback**  
   **Given** a failure cannot be mapped to an approved customer-facing reason  
   **When** the customer is shown the result  
   **Then** the message uses a generic, customer-safe explanation and an approved support or follow-up route if one is configured  
   **And** it does not reveal the underlying raw failure details.

6. **Customer-facing content is reviewed against the mapping**  
   **Given** a customer-facing reason and next step are configured  
   **When** they are presented to a customer  
   **Then** the displayed content matches the approved customer-facing content for that reason and locale, where localization is supported.

## Assumptions / open questions

- Product, support, risk, and/or compliance owners must approve the mapping from failure categories to customer-facing explanations and next steps.
- Define the failure categories, customer wording, safe fallback wording, and any categories that should not be disclosed beyond a generic message.
- Confirm which next steps are allowed (for example, retry, contact support, or take no action); no specific action is assumed here.
- Define supported channels and locales, and whether messaging differs by customer, region, or refund method.
- Confirm where the explanation appears and whether it is also returned through customer-facing APIs or notifications.
- Define accessibility, content governance, and logging requirements while ensuring internal diagnostic data remains protected.
