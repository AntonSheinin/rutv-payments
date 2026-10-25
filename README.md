# Internal PayPal payments

Standalone local FastAPI + PostgreSQL + React staff workspace. Customers never access it. Card data stays in PayPal-controlled frames/pages. The dashboard retains the RuTV logo, light/dark themes, search and per-currency statistics.

Open `/` to start on the dashboard after signing in. Returning to Dashboard from a checkout result restores `/`, so reloading opens the dashboard too.

The dashboard defaults to ILS and the current calendar month in the operator's
browser timezone. Search and applicable filters scope local collection totals,
activity and financial summary. Subscription health shows only Active and
Suspended agreements across all dates, retaining other applicable filters.
Reset filters restores the dashboard defaults; All currencies keeps amounts separate.
The four summary cards share one responsive row. Collection activity shows recorded
one-time and subscription collections beneath them, with day/month labels for the
last 30 available collection buckets in each selected currency.

Collected / Net collected shows gross collections and the net after completed
refunds, reversals, restorations and known fees. Missing fee data leaves net
unavailable. PayPal balance is a separate account snapshot: only currency selection
affects it. **Sync PayPal balance**, beside Create payment on Dashboard, reads only
balances and saves the last successful snapshot. Last synced and Balance as of
show local date/time to the minute. Provider errors retain the previous snapshot.
No dashboard load or filter change calls PayPal. Balance reporting requires the
merchant's separate List Balances permission and may lag by up to three hours.

See [CHANGELOG.md](CHANGELOG.md) for the dated project history.

See [operations](docs/OPERATIONS.md) for financial rules, [integration](docs/INTEGRATION.md) for provider contracts and remaining merchant checks, and [verification](docs/VERIFICATION.md) for current validation results.

## Run locally

The service runs only in **Linux Docker containers on Ubuntu**, using Docker Compose at **http://localhost:8100**. Start it with `docker compose up --build -d`. Its `payments-db` volume contains the restored existing records. Compose uses the `db` service for PostgreSQL and configures the API's database connection.

Install Docker Engine and Docker Compose on Ubuntu. Development checks use Python 3.14, uv and Node 22+; PostgreSQL 17 runs in the database container. The ignored `.env` and `.local-access.json` contain this workspace's existing configuration/login; never commit them. New installations use `.env.example`. `AUTH_USERS` contains username-to-password_hash objects; `API_KEYS` contains name-to-sha256 objects. There are no access roles. Quote JSON environment values with single quotes so Compose preserves dollar signs in Argon2 hashes.

```sh
docker compose up --build -d
```

After changing the source, rebuild and recreate the API to serve the updated
frontend and backend:

```sh
docker compose up -d --build api
```

A container restart alone reuses the existing image and its previously built UI.
Refresh the browser after the rebuilt API starts. The database volume is preserved.

Open **http://localhost:8100**. Use localhost consistently for the app and PayPal returns. API startup applies migrations and obtains singleton database ownership. Run one Uvicorn process. There is no worker, periodic polling, webhook, scheduled retry or startup replay.

For a fresh Compose installation, configure `.env` and `POSTGRES_PASSWORD`, then run `docker compose up --build -d`. Only `api` and `db` are defined. The API binds to loopback; the database port is unpublished. The image runs as UID/GID 10001. Its build explicitly reinstalls the local package to avoid stale cached application wheels, following [uv's local-package caching rules](https://docs.astral.sh/uv/concepts/cache/). No Nginx, remote deployment or tunnel is needed.

Alembic applies the baseline and subsequent schema migrations at startup. The unified register migration preserves existing payment/provider/ledger identities and links. Tests and container smoke checks use separate `payments_test*` databases.

Application database access uses SQLAlchemy AsyncSession; Alembic CLI
migration tooling remains synchronous and startup adapts it through run_sync.

## Payment register

Payments own their checkout identity, immutable name/terms, provider references, phase, browser state, diagnostics and terminal checkout result. There is no separate checkout-attempt model or table. Imports and subscription installments have no hosted-checkout execution fields.

Payments is the single list for pending, completed, cancelled, failed, partially refunded and refunded payments, including imports. A refund updates the original payment status; its exact amount and provider outcome remain linked ledger entries in payment details. Collected selects completed/partially refunded payments; Refunded selects partially/fully refunded payments. Checkout result filters Success/Failed independently of provider/financial status. Created-here rows show the terminal checkout result and their financial status separately. There is no separate request page or request API.

Lists and detail histories use First/Previous/Next/Last controls, Page X of Y, and 10/20/50/100 rows per page (default 20). Main lists return to the last valid page when results shrink. Payment details show checkout state directly on the payment. Payment and subscription details paginate financial history through the `transactions` page object (`items`, `total`, `page`, `page_size`) and `history_page`/`history_page_size` parameters. Refund balances use the full ledger, independent of the displayed page.

## Staff flow

1. Sign in. Enter **First name**, **Last name**, **Agreement number**, amount, currency and payment terms; comments are optional/internal.
2. The form's single PayPal card Submit control opens the hosted guest-card overlay immediately. PayPal requires its own card-button component as the launch element. No separate Save or Open PayPal action exists.
3. PayPal approval invokes the backend once. The backend verifies merchant, payment correlation, amount and currency, captures once, verifies provider collection and records the result.
4. Checkout ends as **Success** after verified approval/completion, or **Failed** after unsuccessful creation, cancellation, error or abandonment. Failed results are terminal. **Create a new payment** is disabled until open API requests finish, including Submit, approval and saved-result lookup. After their answers arrive it opens an empty form, regardless of previous saved results. Each accepted new Submit closes all this operator's unfinished local checkouts as Failed, then creates a fresh payment and its immutable provider correlation. Other operators and verified financial records are unaffected. Overlapping checkout mutations from the same operator are rejected, without queueing or retrying. Reloads and duplicate callbacks never retry or reopen old checkouts.

Only **ILS (default), USD and EUR** are supported. One-time amount is charged once; period information never multiplies it. Subscription amount is per billing interval, with a finite cycle count or explicit Until cancelled. Agreement numbers are text and preserve leading zeros. Client names and agreement numbers are fixed at creation; record details display them without editing controls, and no metadata-editing API is exposed.

One-time checkout selects SDK v6 `paypal-guest-payments`, with no alternative card/wallet implementation. Subscriptions use the v6 paypal-subscriptions session in explicit popup mode. Submit opens the popup immediately while the backend creates the agreement; the dashboard stays open. The same approval/cancellation/error handlers return both products to local result pages. The short guest-card overlay remains one-time-only; PayPal controls the separate subscription form. No full-page approval redirect or application fallback chain exists. PayPal owns recurring charges, installment retries and outcome emails; this service performs none of that work. See [integration status](docs/INTEGRATION.md) for actual evidence and remaining verification.

Local redirect pages:

- `http://localhost:8100/paypal/success`
- `http://localhost:8100/paypal/fail`

Embedded SDK callbacks POST the outcome to the backend and navigate to a result page. Subscription popup callbacks POST the exact subscription ID using the same authenticated, CSRF-protected outcome route. Signed return URLs remain available for PayPal-controlled navigation during checkout. GET never captures. A success-named URL is not proof of collection. Browser closure can prevent callback delivery; use explicit read-only synchronization for later provider facts.

## Refunds and explicit provider reads

All displayed timestamps, calendar filters and chart day boundaries use the operator browser's local timezone. Installment date windows send that timezone explicitly. API calendar requests without a timezone use UTC; invalid timezone values are rejected. The UI has no sync log, history-import control or date/currency footer. Read-only refresh errors remain visible.

Collection rows have a rectangular **Refund** button with extra spacing beside the payment amount: the primary button color when eligible, grey and disabled when unavailable. The form shows payment date, original/refunded/remaining amounts and the deadline; review and confirm a positive partial refund or the full remaining amount. Refund currency is fixed. Eligibility uses the original provider timestamp plus 180 days, supported product capability and remaining balance after completed/pending/uncertain reservations. The backend rechecks every confirmation. The exact reviewed amount is sent, with no automatic adjustment, retry or subscription cancellation. PayPal's current acceptance decision remains authoritative.

Lists/statistics read local data only and show verification time. Explicit Refresh from PayPal is read-only. **Import subscriptions**, beside Create payment on the Subscriptions page, reads all returned ACTIVE/SUSPENDED agreement pages in one explicit request, independent of local table filters. It does not fetch installment history. The total charged column sums recorded successful collections at their actual amounts, before refunds and fees; it never multiplies the current billing amount by cycle counts or combines currencies. History imports still read one page or one requested date window; another history page requires another click/request. History windows are at most 31 days; unsupported/ambiguous entries are excluded with coverage information. The configured app currently returns 403 NOT_AUTHORIZED for Transaction Search; enable/obtain that reporting permission before account-wide history import can work. Subscription details provide an explicit installment date-window action. No automatic backfill or alternate-API lookup loop exists. Imported payments without internal metadata remain unassociated; the service does not offer manual metadata editing.

## API and authentication

Authenticated OpenAPI: `/api/v1/openapi.json`. Login and session restoration return identity, CSRF and complete browser-safe configuration together; there is no separate options endpoint. Browser auth exposes login/me/logout, one signed HttpOnly SameSite=Lax cookie (local HTTP uses COOKIE_SECURE=false), CSRF and exact allowed origins. No role management, account administration, refresh tokens or recurring session checks. Trusted integrations use a configured Bearer token hash with the same access.

- `POST /api/v1/payments`: direct Submit. Required name fields are `first_name` and `last_name` (trimmed, at most 140 characters each and 255 combined including the separator); the service derives `client_name` for local lists and search. It supplies the separate names to PayPal checkout. Include a fresh `Idempotency-Key` and `intent_key` for each new explicit payment. Repeating the original identical submission returns its saved result; reusing an intent for a different submission is rejected. The browser preserves only these non-sensitive identifiers in tab session storage before dispatch.
- `POST /api/v1/payments/{id}/checkout-outcome`: the payment ID in the path, its signed proof, outcome and provider identity. Browser-supplied outcome is not financial evidence.
- `POST /api/v1/payments/{id}/close`: freeze an unfinished checkout as Failed without PayPal calls; used by the Close checkout controls and when abandoning either product. It does not cancel/erase provider financial activity. Success/Failed never changes afterward.
- `GET /api/v1/checkout-submissions/{intent_key}`: read-only result lookup after response loss. Never resubmit a mutation to inspect it.
- Direct refunds and subscription cancellation require an Idempotency-Key and return their actual observed result. A reused key with different input is rejected. Unconfirmed results are Failed; active financial reservations remain until resolved.
- `/payments`, `/transactions`, `/subscriptions`, `/statistics`: local search/reporting. `/synchronizations` performs explicit bounded provider reads. Client names and agreement numbers cannot be edited after creation; no association PATCH endpoints are exposed.
- `POST /api/v1/balance/synchronize`: authenticated balance-only provider read;
  validates the configured merchant and supported-currency totals, then saves and
  returns the snapshot. `/statistics` includes that saved snapshot independently
  of reporting filters. No payment or subscription history is synchronized.
- `POST /api/v1/synchronizations` with `scope: "subscriptions"`: reads all returned
  ACTIVE/SUSPENDED agreement pages, starting at page 1, within the request timeout.
  It does not use local search/date/currency filters or import installment history.
  A failed scan retains already committed facts without claiming a complete scan.
- Subscription list and detail responses expose `total_charged` as decimal text
  in the agreement currency, or null when currencies cannot be combined safely.
  This is the gross recorded collection sum, independent of history pagination.

Public output is declared in `backend/app/responses.py` and the matching frontend
contracts in `frontend/src/types.ts`. Payment responses retain names/terms,
provider references, financial and terminal checkout results, safe diagnostics,
action eligibility and paginated history. Internal intent/submission keys,
input hashes, provider-correlation seeds and execution phases are not response
fields. They remain persisted for exact matching and idempotency. Nullable names,
IDs, fees and cycle counts are explicit; money is decimal text. Adding an ORM
column never automatically expands public output. Existing lookup and financial
request identities are unchanged; no old-payload or old-route aliases are added.

See [operations](docs/OPERATIONS.md) for uncertain outcomes, singleton ownership and safe diagnostics. LOG_LEVEL=INFO records meaningful actions; DEBUG adds safe route/provider timing. No secrets, raw provider payloads, card/customer details, callback queries or approval URLs are logged.

## Verify

```sh
uv run --locked --extra dev ruff check backend
uv run --locked --extra dev ruff format --check backend
uv run --locked --extra dev mypy backend/app
uv run --locked --extra dev pytest -q
cd frontend
npm run build
npm run format:check
npm test
```

Backend tests require a dedicated database named `payments_test*`, configured with TEST_DATABASE_URL; they recreate its tables. Browser tests are organized by feature with shared staff/SDK fixtures in `frontend/tests/fixtures.ts`. Set E2E_BASE_URL to select an isolated test app and set the required E2E_USERNAME and E2E_PASSWORD to its disposable login. Tests never read `.local-access.json`. Browser tests need that running app; all financial/provider scenarios in those tests are mocked. They do not certify merchant capability or actual collection. See [verification evidence](docs/VERIFICATION.md).


The single Close checkout action is available for unfinished one-time and
subscription checkouts. It records local failure without PayPal calls, financial
reclassification or releasing reservations. Late approvals cannot start capture;
a capture already dispatched can still finish. Future subscription billing is
cancelled only through the separate Cancel subscription action for eligible
ACTIVE/SUSPENDED agreements. There is no payment cancellation endpoint.
