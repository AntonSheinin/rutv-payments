# Current verification

Last verified: 2026-10-25. Source branch: `refactor/solid-simplification`.
The service runs in Linux Docker containers on Ubuntu through Docker Compose.
Source changes and review images have not deployed or migrated the running service.

## Passed checks

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
