# Changelog

Changes are grouped by commit date, newest first.

## 2026-10-30 — Staff access and deployment simplification

### Changed

- Staff browser access supports multiple IPv4/IPv6 addresses through `ALLOWED_IPS`, replacing `ALLOWED_IP`. Allowed IPs, browser origins and trusted proxy addresses all use comma-separated lists.
- Pydantic validates every explicit profile parameter, including callback URLs, browser origins, credential entries and trusted proxy IPs; unknown settings are rejected.
- Deployment readiness runs inside the API container without an integration key or `.health-api-key` file. Public health routes remain authenticated, and integrations can stay disabled when only staff use the service.
- The manual GitHub deployment workflow now contains the complete transfer and server deployment process. Removed the separate deployment folder and moved relevant checks into the backend test suite.
- Removed redundant configuration and UI styling tests, refreshed setup documentation and expanded ignore rules for generated test and coverage artifacts.

### Verification

- All 210 backend tests and five subtests, migration/schema checks, lint, formatting, type checks, frontend build, workflow lint and the disposable container readiness/frontend smoke test passed.
- Validation details are recorded in [verification](docs/VERIFICATION.md). Provider responses in automated financial tests are mocked; these checks do not certify live merchant behavior.
- No production deployment or live financial action was performed.

## 2026-10-29 — Production configuration and controlled deployment

### Added

- Staff browser access restricted by `ALLOWED_IP`, while trusted integrations can use valid Bearer API keys independently of the staff address.
- A manually triggered GitHub deployment workflow that checks the selected commit, publishes its image and deploys an immutable digest through verified SSH. Pushes do not deploy.
- Deployment readiness checks, profile-isolation tests and production setup documentation.

### Changed

- Replaced `config.py` with `settings.py`; all configuration values are explicit and validated from the selected environment. `.env.example` documents every parameter, while private development and production profiles stay outside Git.
- Development and production share one Docker Compose file, selecting `.env.dev` or `.env.prod`. Production runs the prebuilt image, keeps PostgreSQL private and preserves its data volume.
- Simplified database configuration to one `POSTGRES_PASSWORD`; the backend constructs the fixed Compose database connection without exposing credentials to the frontend.
- `PAYPAL_ENVIRONMENT` alone selects Sandbox or live payments, and the frontend displays the matching Sandbox/Live badge. Subscription billing policies are explicitly configured.
- Fixed the frontend image path in code and retained explicit proxy trust, secure-cookie and browser-origin checks.

### Verification

- Backend settings/authentication and deployment tests, Chromium checks for both environment labels, lint, formatting and type checks passed. Earlier full-suite and migration results are recorded in [verification](docs/VERIFICATION.md).
- Container builds and disposable Compose smoke tests passed, including startup migrations, authenticated readiness and permitted-source frontend access. No production deployment or live financial action was performed.

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
