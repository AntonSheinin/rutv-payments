# Internal PayPal payments

Standalone local FastAPI + PostgreSQL + React staff workspace. Customers never access it. Card data stays in PayPal-controlled frames/pages. The dashboard retains the RuTV logo, light/dark themes, search and per-currency statistics.

See [CHANGELOG.md](CHANGELOG.md) for the dated project history.

## Run locally

This workspace now uses **Docker Compose at http://localhost:8100** as its local runtime. Start it with `docker compose up --build -d`. Its `payments-db` volume contains the restored existing records. Compose uses the `db` service for PostgreSQL and overrides the host-development `DATABASE_URL`. Native Python/Uvicorn with the host-development `DATABASE_URL` is a separate development setup; switching runtimes does not copy or share database records. Do not use the native runtime as an alternative start command for the Compose installation.

Use Python 3.14, uv, Node 22+ and PostgreSQL 17/Docker Desktop. The ignored `.env` and `.local-access.json` contain this workspace's existing configuration/login; never commit them. New installations use `.env.example`. `AUTH_USERS` contains username-to-password_hash objects; `API_KEYS` contains name-to-sha256 objects. There are no access roles. Quote JSON environment values with single quotes so Compose preserves dollar signs in Argon2 hashes.

```powershell
docker compose up --build -d
```

Open **http://localhost:8100**. Use localhost consistently for the app and PayPal returns. API startup applies migrations and obtains singleton database ownership. Run one Uvicorn process. There is no worker, periodic polling, webhook, scheduled retry or startup replay.

For a fresh Compose installation, configure `.env` and `POSTGRES_PASSWORD`, then run `docker compose up --build -d`. Only `api` and `db` are defined. The API binds to loopback; the database port is unpublished. The image runs as UID/GID 10001. Its build explicitly reinstalls the local package to avoid stale cached application wheels, following [uv's local-package caching rules](https://docs.astral.sh/uv/concepts/cache/). No Nginx, remote deployment or tunnel is needed.

Alembic applies the baseline and subsequent schema migrations at startup. The unified register migration preserves existing payment/attempt/ledger identities and links. Tests and container smoke checks use separate `payments_test*` databases.

## Payment register

Payments is the single list for awaiting approval, pending, completed, cancelled, failed, partially refunded and refunded payments, including imports. A refund updates the original payment status; its exact amount and provider outcome remain linked ledger entries in payment details. Collected selects completed/partially refunded payments; Refunded selects partially/fully refunded payments. Checkout result filters Success/Failed independently of provider/financial status. Created-here rows show the terminal checkout result and their financial status separately. There is no separate request page or request API.

Lists and detail histories use First/Previous/Next/Last controls, Page X of Y, and 10/20/50/100 rows per page (default 20). Main lists return to the last valid page when results shrink. Payment details page attempts and financial history independently; subscription details page financial history. Detail API `attempts` and `transactions` are page objects (`items`, `total`, `page`, `page_size`), with `attempt_page`, `attempt_page_size`, `history_page`, and `history_page_size` query parameters as applicable. Refund balances use the full ledger, independent of the displayed page.

## Staff flow

1. Sign in. Enter **First name**, **Last name**, **Agreement number**, amount, currency and payment terms; comments are optional/internal.
2. The form's single PayPal card Submit control opens the hosted guest-card overlay immediately. PayPal requires its own card-button component as the launch element. No separate Save or Open PayPal action exists.
3. PayPal approval invokes the backend once. The backend verifies merchant, attempt, amount and currency, captures once, verifies provider collection and records the result.
4. Checkout ends as **Success** after verified approval/completion, or **Failed** after unsuccessful creation, cancellation, error or abandonment. Failed results are terminal. **Create a new payment** is disabled until open API requests finish, including Submit, approval and saved-result lookup. After their answers arrive it opens an empty form, regardless of previous saved results. Each accepted new Submit closes all this operator's unfinished local checkouts as Failed, then creates a fresh payment/attempt and identifiers. Other operators and verified financial records are unaffected. Overlapping checkout mutations from the same operator are rejected, without queueing or retrying. Reloads and duplicate callbacks never retry or reopen old attempts.

Only **ILS (default), USD and EUR** are supported. One-time amount is charged once; period information never multiplies it. Subscription amount is per billing interval, with a finite cycle count or explicit Until cancelled. Agreement numbers are text and preserve leading zeros.

One-time checkout selects SDK v6 `paypal-guest-payments`, with no alternative card/wallet implementation. Subscriptions use the v6 paypal-subscriptions session in explicit popup mode. Submit opens the popup immediately while the backend creates the agreement; the dashboard stays open. The same approval/cancellation/error handlers return both products to local result pages. The short guest-card overlay remains one-time-only; PayPal controls the separate subscription form. No full-page approval redirect or application fallback chain exists. PayPal owns recurring charges, installment retries and outcome emails; this service performs none of that work. See [integration status](docs/INTEGRATION.md) for actual evidence and remaining verification.

Local redirect pages:

- `http://localhost:8100/paypal/success`
- `http://localhost:8100/paypal/fail`

Embedded SDK callbacks POST the outcome to the backend and navigate to a result page. Subscription popup callbacks POST the exact subscription ID using the same authenticated, CSRF-protected outcome route. Signed return URLs remain available for PayPal-controlled navigation during checkout. GET never captures. A success-named URL is not proof of collection. Browser closure can prevent callback delivery; use explicit read-only synchronization for later provider facts.

## Refunds and explicit provider reads

All displayed timestamps, calendar filters and chart day boundaries use the operator browser's local timezone. Installment date windows send that timezone explicitly. API calendar requests without a timezone use UTC; invalid timezone values are rejected. The UI has no sync log, history-import control or date/currency footer. Read-only refresh errors remain visible.

Collection rows have a rectangular **Refund** button with extra spacing beside the payment amount: the primary button color when eligible, grey and disabled when unavailable. The form shows payment date, original/refunded/remaining amounts and the deadline; review and confirm a positive partial refund or the full remaining amount. Refund currency is fixed. Eligibility uses the original provider timestamp plus 180 days, supported product capability and remaining balance after completed/pending/uncertain reservations. The backend rechecks every confirmation. The exact reviewed amount is sent, with no automatic adjustment, retry or subscription cancellation. PayPal's current acceptance decision remains authoritative.

Lists/statistics read local data only and show verification time. Explicit Refresh from PayPal is read-only. Imports read one page or one requested date window; another page requires another click/request. History windows are at most 31 days; unsupported/ambiguous entries are excluded with coverage information. The configured app currently returns 403 NOT_AUTHORIZED for Transaction Search; enable/obtain that reporting permission before account-wide history import can work. Subscription details provide an explicit installment date-window action. No automatic backfill or alternate-API lookup loop exists. Imported payments without internal metadata remain unassociated until staff explicitly associate them.

## API and authentication

Authenticated OpenAPI: `/api/v1/openapi.json`. Browser auth exposes login/me/logout, one signed HttpOnly SameSite=Lax cookie (local HTTP uses COOKIE_SECURE=false), CSRF and exact allowed origins. No role management, account administration, refresh tokens or recurring session checks. Trusted integrations use a configured Bearer token hash with the same access.

- `POST /api/v1/payments`: direct Submit. Required name fields are `first_name` and `last_name` (trimmed, at most 140 characters each and 255 combined including the separator); the service derives `client_name` for local lists and search. It supplies the separate names to PayPal checkout. Include a fresh `Idempotency-Key` and `intent_key` for each new explicit payment. Repeating the original identical submission returns its saved result; reusing an intent for a different submission is rejected. The browser preserves only these non-sensitive identifiers in tab session storage before dispatch.
- `POST /api/v1/payments/{id}/checkout-outcome`: exact attempt_id, signed proof, outcome and provider identity. Browser-supplied outcome is not financial evidence.
- `POST /api/v1/payments/{id}/attempts/{attempt_id}/cancel`: explicitly close the latest unclaimed one-time attempt awaiting approval. Available in the create form, payment list and attempt details. It closes checkout as Failed without capturing. Starting a new payment never requires this action.
- `POST /api/v1/payments/{id}/attempts/{attempt_id}/close`: freeze an unfinished checkout as Failed without PayPal calls; used when abandoning either product. It does not cancel/erase provider financial activity. Success/Failed never changes afterward.
- `GET /api/v1/checkout-submissions/{intent_key}`: read-only result lookup after response loss. Never resubmit a mutation to inspect it.
- Direct refunds and subscription cancellation require an Idempotency-Key and return their actual observed result. A reused key with different input is rejected. Unknown results remain reserved.
- `/payments`, `/transactions`, `/subscriptions`, `/statistics`: local search/reporting. `/synchronizations` performs explicit bounded provider reads. Association PATCH endpoints accept client_name and agreement_number; no external_reference compatibility alias exists.

See [operations](docs/OPERATIONS.md) for uncertain outcomes, singleton ownership and safe diagnostics. LOG_LEVEL=INFO records meaningful actions; DEBUG adds safe route/provider timing. No secrets, raw provider payloads, card/customer details, callback queries or approval URLs are logged.

## Verify

```powershell
uv run --locked --extra dev ruff check backend
uv run --locked --extra dev ruff format --check backend
uv run --locked --extra dev mypy backend/app
uv run --locked --extra dev pytest -q
cd frontend
npm.cmd run build
npm.cmd run format:check
npm.cmd test
```

Backend tests require a dedicated database named `payments_test*`, configured with TEST_DATABASE_URL; they recreate its tables. Browser tests need the running local app and ignored local login; all financial/provider scenarios in those tests are mocked. They do not certify merchant capability or actual collection. See [verification evidence](docs/VERIFICATION.md).


Subscription checkout cancellation follows PayPal's product rules: the subscription
cancel API supports only ACTIVE or SUSPENDED agreements. An unapproved subscription
checkout cannot be cancelled by that API, so it has no Cancel payment attempt
action and direct attempts are rejected before changing state or contacting
PayPal. Closing its hosted checkout does not prove provider cancellation. An
uncertain provider result or unavailable ID is preserved as financial evidence;
the local checkout is Failed and never blocks an independent new attempt. One-time checkout
cancellation and explicit active/suspended agreement cancellation remain available.
