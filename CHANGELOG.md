# Changelog

Changes are grouped by commit date, newest first.

## 2026-10-25 — Dashboard balances and subscription reporting

### Added

- PayPal balance card and an explicit balance-only sync button beside Create payment. The saved account snapshot shows its last successful sync and provider refresh times, and remains visible after sync errors.
- Total charged (recorded) on the Subscriptions table, calculated from actual successful collections before refunds and fees, with currencies kept separate.

### Changed

- Combined collected and net collected amounts into one card, separated by a slash. Net deducts refunds and fees and includes only provider-confirmed fee credits; missing fee information keeps net unavailable.
- Subscription health now shares the top card row and shows only Active and Suspended counts across all dates, retaining other applicable filters. Removed the duplicate Active subscriptions card.
- Dashboard defaults and reset now use ILS and the current browser-local calendar month. All currencies displays separate amounts.
- Import subscriptions moved beside Create payment and reads all returned Active/Suspended agreement pages in one explicit request. Removed the Import page control and the table's explanatory paragraph.
- Collection activity uses the full width, shows day/month labels and includes recorded one-time and subscription collections. Removed the timezone caption and the Financial summary badge.
- Removed the plus icon from Create payment and matched the heading action button heights.

### Fixed

- Subscription health no longer shows an agreement total that includes hidden approval-pending states.
- Checkout-result filters now apply to financial reporting, and chart scaling uses only the displayed collection buckets.
- Balance synchronization validates the merchant, preserves the last verified snapshot on failures and prevents older provider data from replacing newer balances.

### Verification

- Full backend suite: 183 tests passed; an additional mixed-currency/zero-total regression passed separately. All 45 Chromium tests, lint, formatting, typing, frontend/container builds and local service smoke checks passed.
- Provider tests use mocked responses. Balance permission and complete merchant-wide subscription coverage still require actual account verification; see [integration](docs/INTEGRATION.md).

## 2026-10-25 — Payment and API simplification

### Changed

- Payments now own their checkout state directly; removed the separate payment-attempt table while preserving existing financial identities through migrations.
- Close checkout is the single local checkout-ending action. Removed the redundant payment-cancellation and options endpoints; login/session responses include public configuration.
- Unconfirmed financial outcomes display Failed while unresolved refund and cancellation reservations remain protected against duplicate actions.
- Database requests run asynchronously, keeping unrelated staff requests responsive during database waits. Public API responses explicitly expose supported fields.
- The service runs exclusively through Docker Compose on Linux. Removed native startup support, completed plans, obsolete logs and redundant tests; reorganized workflows, UI code and styles.

### Fixed

- Signed checkout returns work independently of malformed tab storage. Returning to Dashboard restores its default URL so reloading keeps the dashboard open.
- Interrupted subscription cancellations return Failed instead of exposing an internal dispatch state, preserve their reservation and retain provider support diagnostics.
- Financial activity and synchronization logs include useful result/reservation context without repeating terminal events or exposing customer data and credentials.

### Verification

- 169 backend tests passed in Linux Docker; 43 browser tests passed. Lint, formatting, type checks, frontend/container builds and migration/schema checks passed.
- Financial tests use mocked provider responses; remaining merchant-specific verification is documented in [integration](docs/INTEGRATION.md).

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
