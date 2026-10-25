# Current verification

Last verified: 2026-10-25. Source branch: `feat/dashboard-balance-and-metrics`.
The service runs in Linux Docker containers on Ubuntu through Docker Compose.
The local Compose API was rebuilt and recreated from this branch on 2026-10-25;
its existing database volume was preserved. No remote deployment was performed.

## Subscription table and import follow-up, 2026-10-25

- Local Compose API rebuild/recreation passed with Sandbox readiness. HTTP `/`
  returned 200 serving `/assets/index-BH2eifQ3.js`, containing the charged-total
  column and omitting the old paragraph and Import page control. Existing data
  volumes were preserved; no actual provider import was triggered.
- Removed the subscription table's explanatory paragraph and Import page field;
  Import subscriptions now sits beside Create payment. Its single request scans
  all returned ACTIVE/SUSPENDED agreement pages, using the documented status
  filter and 20 rows per page. No installment history is fetched by this action.
- Added total_charged to list/detail contracts and a Total charged (recorded)
  table column. It sums actual completed collection amounts before refunds/fees,
  independent of displayed history pagination. Empty recorded history is zero;
  mixed currencies produce an unavailable total rather than a combined amount.
- Full backend suite: 183 passed. An additional cross-currency/zero-total
  regression passed separately afterward. Tests cover multi-page filtered imports,
  duplicate IDs, skipped states, later-page failures preserving committed facts
  without claiming complete coverage, and actual charge sums distinct from the
  current billing amount multiplied by counts. Ruff, formatting and mypy passed.
- All 45 Chromium tests, frontend build and Prettier checks passed. Browser tests
  used a disposable Linux app with provider requests forbidden. Backend tests used
  mocked provider responses and a dedicated payments_test_subscriptions database;
  browser verification used payments_test_subscriptions_browser. No actual PayPal
  read or financial action was performed, so merchant-wide coverage remains
  account-dependent as documented in INTEGRATION.md.

## Dashboard and balance verification, 2026-10-25

- Follow-up: removed the Create payment plus icon, matched both heading action
  buttons to 42px, and removed the subscription period caption. Subscription health
  now ignores both date boundaries while retaining other applicable filters.
  Four reporting tests and two dashboard Chromium tests passed on isolated
  `payments_test*` databases, including empty collection windows with unchanged
  health counts and equal action-button heights. Ruff, formatting, mypy and the
  frontend build/format checks passed. No actual provider calls were made.
  Rebuilt/recreated the local Compose API and confirmed HTTP 200 serving
  `/assets/index-CoeyXmdu.js` with the old period caption absent; startup reported
  Sandbox readiness.
- A read-only current-month ILS aggregate found one completed collection with a
  known fee and one completed refund with an unknown fee. This explains the
  unavailable after-fee net without assuming a missing refund fee credit is zero.
- Local rollout: `docker compose up -d --build api` passed. Startup reported
  `api_ready environment=sandbox`; the database remained healthy. HTTP `/` returned
  200 and served `/assets/index-Doq9zIxs.js`, containing the new balance sync,
  combined collection card and last-sync labels. No provider action was performed.
- Full backend suite: 181 passed in Linux Docker, Python 3.14.9 and PostgreSQL 17.
  Ruff lint, formatting and mypy passed. Dependencies and database schema are unchanged.
- Frontend TypeScript/Vite build and Prettier checks passed. All 45 Chromium
  browser tests passed against an isolated Linux application with disposable
  credentials. Its provider transport rejected outbound provider calls.
- Browser checks covered ILS/current-month defaults and reset, filter parameters,
  gross/net display and missing fees, separate currencies, four cards in one
  desktop row, responsive wrapping, day/month labels across a month boundary in
  a negative UTC offset, balance-only action/CSRF, snapshot retention on error,
  and local sync/provider timestamps without seconds. Desktop and mobile
  screenshots were also visually inspected.
- Backend checks covered merchant balance identity, unsupported/duplicate
  currencies, malformed money/timestamps, permission failures, older snapshots,
  authentication/CSRF, filter-independent saved balances, excluded balance
  coverage, refund fee credits for capture and sale products, filtered health
  counts, completed one-time/installment aggregation and repeated-window deduplication.
- A read-only status/count query against the existing local database confirmed
  the original mismatch: one ACTIVE and one APPROVAL_PENDING agreement. No
  customer data was read and no application records were changed.
- Tests used dedicated `payments_test_dashboard` and
  `payments_test_dashboard_browser` databases on a disposable PostgreSQL server.
  No actual PayPal calls or financial actions were performed. Merchant permission
  for List Balances remains unverified; mocked tests do not establish access or
  actual fee reimbursement. See the current provider references in INTEGRATION.md.

## Earlier baseline verification


| Check | Result |
| --- | --- |
| Backend suite | 169 tests passed in Linux Docker on Python 3.14.9 using the default event loop. |
| Backend lint, formatting and types | Ruff and mypy passed. |
| Database migrations | Clean upgrade/schema comparison and populated-data migration cases passed against dedicated test databases. |
| Frontend suite | 43 Chromium browser tests passed against an isolated test app. |
| Frontend build and formatting | TypeScript/Vite and Prettier passed. |
| Linux image | Build passed; the API imported as UID 10001 without a native launcher. Compose defines only api/db. |
| Dependencies and source checks | Lock consistency and diff whitespace checks passed; existing locked package versions were unchanged. |

Database tests used only dedicated `payments_test*` databases. Browser verification
used disposable credentials and mocked SDK/provider responses; its backend
transport rejected unexpected provider calls. Disposable servers, containers and
databases were cleaned up. No actual PayPal action occurred during these checks.

## Important regression coverage

- Immutable identities, changed-input rejection, concurrent callbacks, operator
  isolation, lost responses, late approval/closure and no mutation replay.
- Capture/refund/cancellation timeouts and interrupted requests retaining financial
  claims; persistence/output errors after collection; ownership loss blocking
  mutations while preserving reads; startup and shutdown cleanup.
- Refund limits, partial/full/pending results, original-timestamp expiry boundaries,
  concurrent reservations and mismatched provider facts retaining holds.
- Merchant/payment correlation, finite amounts and currencies; changed effective
  subscription quantity, tax, shipping, setup fee, frequency, cycles and price.
- Independent currency totals, unknown versus zero fees, calendar/DST boundaries,
  partial imports, malformed pages and stable histories beyond 100 rows.
- Authentication, password integrity, CSRF/origin checks and private diagnostics.
  Tests use disposable in-memory identities rather than personal configuration files.
- SDK gesture timing, abandonment, stale UI responses, signed returns independent
  of tab storage, dashboard reload and no repeated financial action after errors.
- Safe logging without customer fields, credentials, raw payloads, query values or
  SQL parameters; no repeated terminal/result events for duplicate requests.

An interrupted subscription-cancellation regression exposed internal `dispatched`
state in a duplicate response. The API now returns Failed while retaining its
reservation and original action identity. Provider debug IDs remain available
for failed-cancellation investigation. Expected-error boundaries stay specific;
the shared database HTTP handler preserves safe, visible server failures.

## Limits and ongoing evidence

Mocked tests establish local behavior, not merchant eligibility, actual collection,
refund acceptance or recurring billing. See [integration](INTEGRATION.md) for
actual Sandbox evidence, reporting-permission limitations and remaining account
verification. See [operations](OPERATIONS.md) for financial and diagnostic rules.

Completed plans/review transcripts and obsolete local logs were removed. The
verification record is intentionally a current summary; operational rules and
merchant evidence remain in their dedicated documents.
