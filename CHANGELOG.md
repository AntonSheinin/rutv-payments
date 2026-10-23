# Changelog

Changes are grouped by commit date, newest first.

## 2026-10-23 — Initial commit

This repository had no earlier project commits when this changelog was created.
This entry covers the complete initial implementation and its uncommitted fixes.

### Added

- Standalone internal payment service running locally at `http://localhost:8100`, with FastAPI, PostgreSQL and a React dashboard.
- RuTV branding, light/dark themes, responsive navigation and operator login/logout.
- One-time payments through PayPal's short guest-card form and subscriptions through a PayPal popup opened directly by Submit.
- Separate client first/last names, agreement numbers and internal comments. Supported currencies are ILS, USD and EUR.
- A unified Payments list for checkout, collection and refund outcomes, plus a subscription list showing recorded charges and remaining installments.
- Search, filters, per-currency statistics, browser-local dates and pagination throughout lists and detail histories.
- Full and partial refunds from the payment amount, subject to the original payment date, remaining balance and PayPal eligibility.
- Explicit, read-only PayPal refresh and bounded integration APIs for inspecting financial records and subscription installments.
- Staff authentication with a single access level, signed sessions, CSRF/origin checks and protected health/API endpoints.
- Local Docker Compose with only API/database services, startup schema migrations, merchant/environment ownership checks and separate Sandbox/live database protection.
- Backend and browser regression tests, CI checks and setup, operations, integration and verification documentation.

### Reliability and checkout behavior

- Checkout results are terminal Success/Failed, separate from verified collection, subscription and refund status.
- Failed or abandoned checkouts cannot be resumed or approved by late callbacks. Only an explicit new Submit starts another attempt.
- New attempts wait for open requests to finish. Overlapping checkout mutations from the same operator are rejected, including across tabs.
- Each accepted new Submit closes all unfinished local checkout attempts belonging to that operator while preserving other operators and financial evidence.
- Late responses cannot replace a fresh form or select a newer attempt's result. Blocked/closed popups wait for outstanding creation responses before showing failure.
- Durable action identities and provider idempotency keys prevent duplicate payment mutations; refund reservations preserve cumulative partial-refund limits and uncertain outcomes.
- Financial actions run directly within requests in one API process, without polling, background workers, automatic payment retries, webhook processing or startup replay.
- Removed obsolete request pages, recovery/restart flows, role management and unused deployment components.

### Verification

- 154 backend tests and 43 browser tests passed before this commit, together with lint, formatting, type checks, frontend/container builds and local readiness checks.
- Financial regression tests use mocked provider responses. Actual Sandbox SDK checks verified guest-form/popup launch and eligibility without creating a subscription or collecting funds; see [verification details](docs/VERIFICATION.md).
