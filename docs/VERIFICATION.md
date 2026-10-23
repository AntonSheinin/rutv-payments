# Implementation verification

Checked locally on 2026-10-18:

- 58 backend tests passed against PostgreSQL 17 on Python 3.14, covering validation, authorization/CSRF, idempotency, concurrent checkout/refund reservations, provider timeouts, capture verification, refund events, imports, timezone boundaries and financial totals. Review regressions cover worker restarts, startup races, read-only refresh, capture queue priority, outgoing payments, updates to imported payments, missing approval URLs and changed provider plan terms. Logging tests cover INFO/DEBUG filtering, repeated configuration, invalid levels and exclusion of customer data, credentials and request/response payloads. Migration tests verify existing UUIDv4 requests alongside new UUIDv7 IDs and FastAPI schema generation with native deferred annotations.
- 6 Chromium tests passed against the running application, covering login, conditional cycles, saving/searching requests, filters, subscription navigation, mobile layout and updating an open detail panel after checkout completes. Desktop/mobile screenshots were inspected.
- Ruff lint/format checks and Python type checks passed. Strict TypeScript build, unused-code checks and Prettier checks passed.
- All backend/app modules, classes and functions have docstrings and complete function annotations. Mypy rejects untyped function definitions. Concrete sessions, ORM records, provider responses and SQL expression types were checked; comments explain reservations, retry boundaries, ledger reconciliation and timezone/fee semantics. The 58 backend tests, 6 browser tests and Alembic comparison passed after these additions.
- Alembic initial migration applied successfully; metadata comparison reported no schema drift.
- FastAPI lifecycle tests verify migration-before-processing ordering, singleton ownership, cancellation/client cleanup, lock release, and readiness/write protection after database or programming failures. Startup migrations are tested against an empty database and on repeated execution.
- The simplified Docker image passed a fresh-database startup smoke test on Python 3.14.8 as UID/GID 10001: automatic migrations, embedded processing, UI serving, API authentication and graceful shutdown. Compose discovers docker-compose.yml automatically and defines only db and api.
- Docker image built successfully. Linux-container imports, configuration and connection to PostgreSQL were smoke-tested as the non-root application user.
- After migrating to uv, the image was rebuilt from pyproject.toml/uv.lock and checked as UID/GID 10001. The installed application, Alembic entry point, frontend, database startup and unauthorized API response passed; uv and development tools are absent from the runtime. All 27 backend tests also passed using the uv-managed development environment.
- The Python 3.14 image was built and exercised on CPython 3.14.8 as UID/GID 10001. Native uvloop initialization, UUIDv7 generation, a real Uvicorn HTTP server, authentication protection and Alembic schema comparison passed. The full 58-test backend suite ran locally on CPython 3.14.0.
- A local PostgreSQL custom-format dump restored into a separate test database successfully.
- npm production dependency audit and the original Python dependency audit reported no known vulnerabilities at initial implementation. Python dependency management subsequently migrated to pyproject.toml and uv.lock.
- Git ignore checks exclude local secrets, dependencies and browser artifacts. No remote repository is configured and no commit/push was made.

PayPal responses in automated tests are deterministic mocks. Real Sandbox checkout, recurring guest eligibility, account-specific sale refunds, public webhook delivery and legacy-history completeness remain the explicit integration checks in INTEGRATION.md. Production encrypted backup storage and the organization's authentication/access policy still require deployment configuration. No live financial actions were performed.

## Simplification review ? 2026-10-18

Removed thin GET/close/UUID wrappers, the legacy naive-timestamp fallback, explicit IDs already supplied by ORM defaults, a single-use serialization helper and order-verification method, duplicate report-filter construction, and an extra order lookup after ORDER_ALREADY_CAPTURED. Stable provider idempotency keys, durable reservations, authorization and recovery gates are preserved. Provider v1/v2 money/status shapes remain necessary for supported sale and capture resources.

Removed three tests for UUID-version compatibility, Python annotation/OpenAPI migration, and basic log-level parsing. Retained financial, concurrency, authentication, lifecycle and log-redaction coverage. Extended the capture reconciliation test to cover ORDER_ALREADY_CAPTURED and require exactly GET/POST/GET; adapted provider mocks to use the shared HTTP boundary directly.

Verification: 56 backend cases passed against the dedicated PostgreSQL payments_test database; Ruff lint/format, mypy, the TypeScript/Vite build, Prettier and all 6 Chromium tests passed. Alembic upgrade/schema comparison passed without schema drift. Browser tests used the existing local API and freshly built frontend; backend changes were exercised by the backend suite. Automated PayPal responses are mocked; this cleanup does not establish live merchant behavior.

## Project structure review ? 2026-10-18

The current backend/frontend split, flat backend application package, colocated migrations/tests, frontend screen components, root build configuration and deployment documentation suit this single-service application. Backend and frontend local import graphs have no cycles; frontend local import targets resolve. Python package discovery finds app under backend; migration/static paths exist; Compose lists only db and api. No directory moves or additional architecture layers are needed for the current scope. The largest modules are worker.py (1,017 lines), main.py (585) and frontend/src/main.tsx (458); they are the places to revisit if their responsibilities grow, rather than adding subpackages solely for file size.

Corrected PLAN.md's stale pre-implementation status and separate-recovery-process description. Structure verification passed: Ruff, mypy, TypeScript/Vite build, Prettier, discovery of all 56 backend tests and Compose service discovery. This review did not rerun backend/browser execution or build a new container; their preceding simplification results remain recorded above.

## Authentication simplification ? 2026-10-18

Moved login, current-user lookup and logout into auth.py's router so the authentication flow is in one module. Main wires the router and initializes one shared session signer at startup. Removed the signer-construction and single-use login-limit helpers, reused Principal for login/current-user responses and defined the eight-hour session lifetime once. URLs, signed-cookie format, bearer keys, viewer/operator permissions, password-change invalidation, origin/CSRF checks and processor-failure protection are preserved.

All 59 backend cases passed against payments_test, including new checks for authenticated/CSRF-protected logout, current role lookup, password-change invalidation and the ten-attempt login limit. Test applications now isolate their in-memory login counters. Ruff lint/format and mypy passed. The frontend was unchanged; browser tests were not rerun for this change.

## FastAPI practices verification ? 2026-10-18

Updated to FastAPI 0.146.0 with Starlette 1.8.0; development TestClient uses httpx2 2.14.0. Refreshed uv.lock and synced development dependencies with uv. All 59 backend cases passed on the dedicated PostgreSQL payments_test database with DeprecationWarning treated as an error; that policy is now part of pytest configuration. Ruff lint/format, mypy and OpenAPI generation (26 paths, 9 component schemas) passed. Webhook tests confirm signature verification and persistence execute on different threads while retaining deduplication and redaction. Lifecycle, cancellation, resource cleanup, processor failure, authentication and database error handling remain covered. The frontend and deployment configuration were unchanged; browser tests and a fresh container build were not rerun. See REVIEW.md for official references and the unresolved worker synchronous-database concurrency finding.

## Lifespan annotation correction - 2026-10-18

Changed the asynccontextmanager-decorated lifespan return annotation from AsyncIterator[None] to AsyncGenerator[None], avoiding the deprecated typing overload reported by ty. The decorator itself remains supported. Runtime DeprecationWarning checks do not detect typing-only overload deprecations; the preceding audit did not catch this distinction. Ruff lint/format, mypy and all four lifecycle cases passed after the annotation change.

## Bootstrap utility removal - 2026-10-18

The existing local .env validates, uses Sandbox, contains an operator account and matches the saved local credentials; existing secrets were preserved. Both local configuration and credential files remain ignored by Git. Removed the one-time environment setup module and its README, example-configuration and CI invocation references. CI migration checks now declare disposable environment variables directly and do not generate a .env or staff password.

Reviewed backend modules, frontend/package scripts, deployment files and CI for other standalone setup/bootstrap scripts, CLI entry points and credential-generation utilities; none remain. Alembic migration files/templates and recurring build/test/deployment commands are retained as operational infrastructure. CI settings validate with .env loading disabled; Alembic upgrade/schema comparison passed against payments_test. All 59 backend cases, Ruff lint/format and mypy passed.

## Worker simplification and scheduling optimization - 2026-10-18

Replaced per-resource pending-operation queries with one distinct pending-target lookup. Scheduling loads only checkout request IDs and subscription IDs, skips database access when no cadence is due, uses one UTC snapshot for daily keys/date windows, and advances cadence timers after the scheduling transaction commits. The scheduler is now a normal def because it performs no asynchronous work. Removed the redundant purchase-unit loop after enforcing exactly one unit, a redundant original-record condition and nested retry-result expressions. Payment mutation request IDs, retry windows, reservations, manual read-only synchronization, singleton ownership and capture priority are retained.

All 60 backend cases passed, including the new PostgreSQL scheduling regression with 12 checkout requests and 12 subscriptions: at most four read queries during scheduling/restart, no scheduler queries on idle polls, no duplicate targets and reuse of manual refresh operations. After the final pending-target query adjustment, the scheduling/restart regressions and four lifecycle cases passed again (6 cases). Ruff lint/format and mypy passed. These are read-query-count improvements; no provider throughput or wall-clock speedup is claimed. Provider responses remain mocked. The previously documented synchronous-database/event-loop concurrency limitation remains unresolved.

## Worker nullable ORM typing fixes - 2026-10-18

Fixed the ensure_plan return diagnostic by retaining and returning the validated non-null provider ID. Running ty against worker.py identified two additional nullable-attribute diagnostics for coverage_end and retry_deadline; both now use non-null local values for subsequent operations. No casts, ignored diagnostics or weakened return annotations were introduced. ty check backend/app/worker.py, mypy, Ruff lint/format and all 60 backend cases passed.

## Endpoint necessity verification - 2026-10-18

Inventoried all main.py route decorators and mapped their callers/contracts in REVIEW.md. Removed the unreferenced duplicate frontend /app/{path:path} alias and its unused path parameter. Confirmed 23 distinct method/path endpoints remain, with the authenticated schema, private health endpoints and verified webhook retained. OpenAPI generation, Ruff lint/format, mypy and all 60 backend cases passed. No external usage telemetry was available; browser tests were not rerun for the unused route removal.

## Local origin correction - 2026-10-18

Reproduced localhost:8000 passing the origin check while 127.0.0.1:8000 returned 403 Invalid origin. Added explicit HTTP 127.0.0.1 origins for API port 8000 and Vite port 5173 to the ignored local .env, alongside the existing localhost entries, and restarted the local API. Actual login, session lookup and CSRF-protected logout passed for all four allowed origins using the existing local account; the frontend shell returned 200 and an untrusted origin still returned 403. No authentication code or production example origins were relaxed, and no PayPal operations were performed.

## Playlist dashboard visual alignment - 2026-10-18

Reused the sibling playlists-service RuTV logo and its slate/blue light palette, charcoal/amber dark palette and semantic status colors. Updated the existing payment screens, login, forms, tables, charts and detail panels through shared CSS tokens. Added an accessible theme switch with system preference detection and a remembered browser preference; switching does not reset the current form. Logo and favicon use the existing /assets route. Payment/API logic and backend code were unchanged.

Installed frontend dependencies with npm ci; TypeScript/Vite build, Prettier and all six existing Chromium staff tests passed against the local application. Additional temporary Chromium checks verified system preference changes, explicit preference persistence across login/reload, retained unsaved form input, theme switching with browser storage disabled, logo/favicon loading and no page overflow at 390px. Reviewed screenshots of the light/dark dashboard, dark login and payment form, and mobile dashboard. The in-app Browser had no available connection; verification used local Chromium automation. Existing staff tests use mocked provider responses for checkout/refund scenarios; these results do not establish live PayPal behavior, and no live financial actions were performed.


## Unauthorized remote deployment rolled back - 2026-10-18

The assistant deployed a Sandbox instance on hetzner2 without user permission. The user requires local deployment only. Rolled back the remote application, dedicated database/volume/network, image tags pulled or loaded for this deployment, transferred credentials/configuration, static return/cancel pages and Nginx snippet. The remote database had zero payment requests, checkout attempts, transactions, subscriptions and webhook events before removal. Restored the original website configuration, verified it against its pre-change backup, and passed nginx -t/reload. Deleted the Sandbox webhook created by the assistant through PayPal's API and confirmed GET by ID returns 404. Removed only the local URL/webhook settings the assistant added; retained the user's client credentials, merchant ID, local application/data and dashboard visual changes. Removed remote deployment files and instructions from the workspace. Local public HTTPS webhook/return/cancel URLs remain unconfigured; no replacement tunnel or remote service is authorized.

## Direct-flow provider checkpoint - 2026-10-19

Created and switched to `refactor/local-direct-checkout`, preserving the pre-existing staged, unstaged and untracked workspace changes. No commit or push was performed.

Real Chromium loaded Sandbox JS SDK v6 on `http://localhost:8000` using the configured client ID and the guest-payments component. Browser `isSecureContext` was true for localhost HTTP. SDK initialization and guest session method availability succeeded. Eligibility queries for ILS, USD and EUR at amount 1.00 each returned card=true and advanced_cards=false. These checks did not enter a card, approve or collect a payment, certify staff-assisted use, or verify subscriptions/refunds.

A single actual Sandbox order-creation probe used CAPTURE intent, ILS 1.00 and the configured merchant ID. OAuth authenticated, but Orders v2 returned HTTP 422 PAYEE_ACCOUNT_INVALID, PayPal debug ID f3852479c7fcf; no order ID was returned. There was no automatic retry, alternate payee, approval or capture. The rejection prevented the planned overlay-launch test from running. The merchant value passed only basic shape checks; neither its identity nor the cause of the provider rejection has been established. See INTEGRATION.md for the required configuration check.

The plan's first checkpoint blocks the broad runtime rewrite until a supported checkout is demonstrated. Runtime code, .env, credentials, application database, running local services and PayPal account configuration were not changed. Only branch creation and checkpoint documentation were completed; the requested refactor is not implemented. No remote access/deployment or live financial action occurred. Documentation whitespace validation passed; application tests were not run for these documentation-only changes.


## Direct local flow implemented - 2026-10-19

Branch: refactor/local-direct-checkout. This supersedes the blocked checkpoint above after the user corrected the Sandbox merchant configuration. No commit/push or remote deployment was performed. Pre-existing staged/untracked work and the earlier RuTV visual changes were retained.

Replaced worker/queue/webhook execution with direct request handlers and immutable payment attempts. Submit now creates checkout and opens PayPal in one action; backend approval verification may capture once. Added exact attempt/operator correlation, preserved submission identity through reload/response loss, and retained uncertain outcomes without replay. Added direct refunds with atomic reservations, 180-day eligibility and an inline button beside Client name. Client name and Agreement number replace the old combined reference. Currencies are ILS/USD/EUR. Removed roles, redundant auth lookups, obsolete endpoints/models, worker and Attention files, deployment examples and empty deploy/frontend/scripts directories. Updated API/frontend, clean baseline, AGENTS/README/operations/integration/configuration/CI documentation. No compatibility or old queue path remains.

Preserved .env client credentials, merchant ID, signing secret and credential hashes. Removed obsolete role/queue/webhook entries; set localhost success/fail URLs and retained JSON quoting so Compose does not interpolate Argon2 hashes. Before the authorized local schema replacement, verified the exact loopback Sandbox payments database and zero checkouts, transactions and subscriptions; removed 22 unprocessed requests and 22 old operation rows. No financial collections or subscriptions were deleted. Initialized the target schema and restarted only the local API. Newly created attempt identities/results remain durable.

Actual PayPal evidence: OAuth and Sandbox order creation succeeded after the merchant correction. SDK v6 guest-card eligibility was true for ILS/USD/EUR and advanced cards false. A real hosted guest overlay opened from the required PayPal basic-card launch component on localhost HTTP. The implemented dashboard then sent one actual Submit for ILS 1.00, opened the real overlay, and PayPal's Close control caused exactly one outcome POST and the persisted cancelled result. Chromium reported zero CSP errors. No card was entered, approval/capture performed or funds collected. The LOCAL-VERIFY-001 Sandbox attempt is retained. A read-only subscription-list request succeeded. A bounded Transaction Search read returned 403 NOT_AUTHORIZED; reporting access is an account prerequisite, not a working import capability claim. Guest recurring/subscription-refund gates remain false pending actual merchant verification.

Validation passed:

- uv sync --locked --extra dev; Ruff lint/format and mypy for backend/app.
- 57 PostgreSQL backend tests on payments_test only. Coverage includes duplicate/forged callbacks, explicit safe restart, uncertain capture/create, wrong merchant, post-capture verification rejection, failure of the database immediately after capture, read-only synchronization, actual concurrent approval/refund claims, partial/full/pending/uncertain refunds and deadline boundaries, strict currencies, subscription effective terms and no Orders capture, cancellation/idempotency, single-access auth/CSRF and ownership loss. Provider financial responses in these tests are mocked.
- npm ci, TypeScript/Vite build and Prettier checks.
- Eight Chromium staff tests passed against the running local app, with provider financial/browser contracts mocked: one Submit/callback, reload without mutations, cancellation/error stopping until explicit restart, refund button placement and exact confirmation, rejected refund without repeat, minimal auth/currencies, availability errors and subscription return identity.
- Clean Alembic initialization and schema comparison on dedicated test databases. Local Compose discovery returns only db and api, without interpolation warnings.
- Built paypal-payment-service:local-refactor. Initial smoke found a stale cached Python wheel; fixed the Docker package-install step with --reinstall-package paypal-payment-service. The final image's installed main/models/services/normalize/sync hashes match the working tree. Non-root UID 10001 and migration/schema equivalence passed against payments_test_container. Authenticated readiness, cookie/CSRF/login/logout, local callback pages, core reads, currency options and absence of obsolete routes passed. Removed only the temporary local smoke container and its ephemeral test login; the normal local app remains running at localhost:8000.

These checks do not establish real approved card collection, recurring guest eligibility, refund acceptance, merchant email delivery or complete reporting coverage. No live API, real card, public HTTPS/tunnel or remote server was used for this implementation.

## Review fixes - 2026-10-19

Fixed all four findings from the subsequent code review. Explicit checkout refresh now propagates a verified failed capture to its attempt and payment request, allowing only an operator-initiated fresh attempt. Subscription refresh uses the same correlation and effective-plan verification as checkout for subscriptions created here; mismatched amount/currency is rejected without updating the saved synchronization timestamp. Rejected Submit preserves the editable form and clears the unused submission identity; lost or uncertain responses retain their identity and cannot automatically start another attempt. Subscription import accepts an explicit page number; each button press reads only that page.

Validation: 62 PostgreSQL backend tests passed on the dedicated payments_test database, including pending-to-DECLINED/FAILED transitions and subscription amount/currency mismatches. All 15 Chromium staff tests passed, including rejected submissions, subscription validation rejection, retained uncertain submission identity and explicit import of pages one and two. Browser tests reuse one authenticated session to avoid the login rate limit. Ruff lint/format, mypy, frontend build and frontend format checks passed. Financial/provider responses were mocked; no charge or refund was performed.

Restarted only the loopback local API with the updated source and rebuilt frontend. Authenticated readiness, Sandbox environment, ILS/USD/EUR options, dashboard serving and login/logout smoke checks passed. No remote deployment, commit or push was performed.

## Payment form width and browser autofill - 2026-10-19

Widened the desktop payment form by allocating a fixed 300px summary column and the remaining width to the form. Overrode the PayPal basic-card container's default 225px host width with a responsive width capped at 560px. Existing mobile stacking remains in place. The real Sandbox SDK loaded in local Chromium without creating an order or entering card data. Measured form/card widths were 785/560px at a 1440px viewport, 764/560px at 1024px and 284/234px at 390px, without horizontal document overflow. Frontend build and format checks passed. These measurements verify the container layout, not a completed payment or a new hosted checkout submission.

Investigated the screenshot's insecure-connection autofill warning against Chromium source. ChromeAutofillClient::IsContextSecure checks the top-level navigation's cryptographic URL scheme as well as the browser security level. This is distinct from JavaScript isSecureContext, which was true for HTTP localhost in all three viewport checks. Consequently, saved-card autofill can be disabled on the HTTP local dashboard while the PayPal-hosted form itself uses HTTPS. Source: https://chromium.googlesource.com/chromium/src/+/HEAD/chrome/browser/ui/autofill/chrome_autofill_client.cc . PayPal guest-form isolation is documented at https://developer.paypal.com/sdk/js/reference/#guest-payments . Preserved the requested HTTP-only local setup; did not suppress warnings, change browser security settings, add certificates/tunnels or access any remote server. Sandbox testing uses manually entered test-card data.

## Default buyer country - 2026-10-19

Set the PayPal guest launch component's supported buyer-country attribute to IL (Israel), following PayPal's SDK release notes at https://github.com/paypal/paypal-js/releases . Rebuilt the local frontend; build and formatting checks passed. Loaded the actual Sandbox SDK and confirmed both the launch element attribute and SDK buyerCountry property were IL. No order was created, card entered or payment performed for this check; a newly opened hosted form's initial country selection was not independently exercised.

## Silent fallback review fixes - 2026-10-19

Fixed all five findings from the silent-fallback review. Subscription normalization requires a supported provider price before modifying the saved subscription, and never marks a retained old price freshly verified. Removed saved-request substitutions for subscription frequency/cycle metadata. Explicit imports require result lists and pagination with the expected types; malformed responses raise reconciliation errors without advancing the scope's successful-import timestamp. Explicit empty pages remain valid and never trigger another page automatically. Checked the current PayPal response examples at https://developer.paypal.com/api/subscriptions/v1/subscriptions-list , https://developer.paypal.com/api/subscriptions/v1/subscriptions-transactions and https://developer.paypal.com/api/transaction-search/v1/search-get .

Dashboard statistics are cleared when a new statistics read starts. Until that read succeeds, the dashboard shows Statistics unavailable instead of zero financial totals, an empty ledger, or data from the previous filters. Generic actions no longer substitute completed for a missing response status; synchronization messages require counts, pagination and a coverage note. Invalid action responses remain explicitly unconfirmed and do not cause automatic retries. Removed unused coverage_start/coverage_end/error fields from SyncState and the obsolete coverage/unclassified frontend fields and rendering. A versioned migration drops only those unused sync_state columns; existing payment data is retained and no legacy runtime adapter was introduced.

Validation passed: 80 PostgreSQL backend tests on payments_test, including imported subscriptions with absent/unsupported prices, malformed result lists and pagination, unchanged successful-import timestamps, and valid empty pages. All 19 Chromium staff tests passed, including initial statistics outages, outages after a filter change, malformed synchronization responses and cancellation responses missing status. Ruff lint/format, mypy, frontend build and formatting passed. On an isolated payments_test_migration database, verified baseline initialization, upgrade with a retained state row, downgrade/re-upgrade and schema comparison. Provider financial responses were mocked; no payment or refund was performed.

Restarted only the local loopback API. Startup applied revision c8a2d4f61790. Confirmed the removed columns are absent, authenticated readiness and statistics succeed, the Sandbox dashboard is served, and login/logout work. No .env changes, remote deployment, commit or push occurred.

## Playlist-style navigation and typography - 2026-10-19

Compared the sibling playlists-service frontend's NavBar, AppLayout, PageHeader, Button, table header and base font definitions. Matched its 248px desktop sidebar, 80px logo/title/theme header, 16px medium menu text, 24px semibold page headings, system sans-serif stack and standard control/body text sizing. Removed the payment-workspace box, workspace labels/wording, private-workspace box, breadcrumb header, account avatar and Staff subtitle. Theme switching now sits beside the logo and RuTV title. The bottom account area contains the username and a separate accessible Logout icon button. Mobile uses a menu drawer with close/backdrop/Escape handling; successful navigation and logout close the drawer. Existing light/dark color values and financial behavior were preserved.

Rebuilt the frontend and passed formatting checks. All 19 existing Chromium staff regression tests passed with mocked financial/provider responses. Additional local browser checks measured a 248px sidebar, 16px menu font, theme control to the right of the brand, and account footer aligned with the viewport bottom. Visually inspected light/dark desktop and mobile screenshots. Mobile navigation, Escape dismissal, theme switching and actual local logout passed, with no horizontal overflow at a 390px viewport and no remaining workspace wording. No payment or refund was performed; no backend, environment or remote deployment change was needed.

## Default Dashboard page - 2026-10-20

Renamed the Overview navigation entry and Financial overview page heading to Dashboard. Opening or reloading the frontend root now selects Dashboard even when a prior payment submission identity remains in the tab. Explicit PayPal success/fail URLs still display their checkout result and process their initial callback; selecting Create payment still inspects a retained submission instead of starting a duplicate attempt. Extended the existing checkout browser test to verify reopening/reloading the root with a completed submission, active Dashboard navigation, and read-only inspection afterward with no additional create/outcome calls. Frontend build, formatting and all 19 Chromium staff tests passed. Financial responses were mocked; no payment/refund, backend change or remote deployment occurred.

## Local port 8100 - 2026-10-20

Moved the loopback application from port 8000 to http://localhost:8100. Updated only the origin and return/cancel URL values in the ignored .env; preserved credentials and existing development-origin entries. Updated configuration defaults, .env.example, Vite proxy, Playwright login/return URLs, test configuration and active setup/product/operations documentation. Compose publishes 127.0.0.1:8100 to the existing internal container port 8000; no duplicate listener or redirect service was introduced. Historical verification entries above retain their original URLs.

Stopped only the verified project Uvicorn process on port 8000 and started its replacement on loopback port 8100. Verified Sandbox mode, configured port-8100 callback URLs/origin, actual local login/logout, authenticated readiness/statistics, dashboard serving and both callback page routes. These smoke checks did not create or approve a PayPal payment. All 80 backend tests and 19 Chromium staff tests passed; financial/provider effects were mocked. Ruff lint/format, mypy, frontend build/format and Compose service discovery passed.

Rebuilt the local Docker image and smoke-tested it with disposable API credentials, an ephemeral loopback host-port mapping and an isolated payments_test_port database. Verified startup migrations, private readiness/statistics, frontend/callback serving, port-8100 URL defaults and non-root UID 10001. Removed the temporary container, its test database and temporary configuration file. The normal service remains running only at http://localhost:8100. No remote deployment, real financial action, commit or push occurred.

## Verified hosted billing country - 2026-10-20

This supersedes the earlier default-country claim, which checked only the launch component attribute/property and did not exercise the hosted billing selector. The actual Sandbox form still selected US despite buyer-country=IL. A diagnostic test with the documented SDK testBuyerCountry=IL option also left billing country US; that ineffective test override was removed. The actual PayPal iframe received buyerCountry=IL but locale.x=en_US, and its public hosted-form code derives the initial billing country from the locale region.

Set the SDK initialization locale to en-IL, retaining the explicit buyer-country=IL launch hint and English UI. Rebuilt the local frontend. Opened the real PayPal Sandbox guest form from the dashboard at http://localhost:8100, then directly inspected its billingAddress.country selector: selected value IL, selected label Israel, and hosted locale en_IL. Closed the form through its own close button and verified the service recorded Cancelled. Three diagnostic Sandbox orders were created during this investigation; their test attempts were explicitly cancelled and retained. No card data was entered, approval/capture performed, charge/refund made, or live API used. No provider-frame DOM was modified to force a selection. Build and formatting passed. SDK locale support reference: https://developer.paypal.com/sdk/js/set-up/ and https://github.com/paypal/paypal-js/blob/main/packages/paypal-js/types/v6/index.d.ts .

All 19 browser regression tests passed after the locale correction, with financial/provider responses mocked. A final read of the three diagnostic payment requests confirmed cancelled status and no financial transaction rows for each.

## NO_SHIPPING for one-time checkout - 2026-10-20

Added shipping_preference=NO_SHIPPING to the Orders v2 payment_source.paypal.experience_context request, using the current field documented at https://developer.paypal.com/api/orders/v2/orders-create rather than the deprecated Orders application_context field. Subscriptions already request NO_SHIPPING through their separate Subscriptions API contract. Extended the existing submit/capture/idempotency contract test to assert the shipping preference on the outbound order.

Restarted only the local API at http://localhost:8100. Created one actual Sandbox order for ILS 1.00 with agreement LOCAL-NO-SHIPPING. The real hosted guest form opened successfully, its billing country remained IL, and the form contained no shipping-address controls or Ship to billing address option. Closed it through PayPal's close button and confirmed the service recorded Cancelled. No card data was entered and no approval, capture, charge or refund was performed. Billing fields remain PayPal-controlled. Ruff lint/format, mypy and all 80 backend tests passed; provider financial responses in backend tests were mocked. No remote deployment occurred.

## Explicit payment-attempt cancellation - 2026-10-20

Added authenticated, CSRF-protected cancellation for the latest open attempt awaiting approval, exposed on the create form, saved result, payment-request list and attempt details. One-time cancellation serializes with capture and prevents late approval from capturing. Duplicate cancellation returns the saved outcome without another provider mutation. Subscription checkout verifies provider identity/terms and requests cancellation once; an active subscription is not silently cancelled, and rejected/uncertain cancellation cannot permit a replacement. Subscription pre-approval cancellation eligibility remains merchant/provider-dependent and was not verified against an actual subscription.

Fixed navigation after safe cancellation/failure: an explicit new-payment action clears the saved tab submission and opens an empty form without submitting. Review initial form retains the intent for a deliberate restart. Opening the saved result after list/detail changes reads current persisted state rather than reusing an outdated awaiting-approval result. Navigation also clears unrelated synchronization notices.

All 86 backend tests passed against the dedicated payments_test database. Tests cover duplicate cancellation, late approval, an already claimed capture (including concurrent capture), confirmed/timed-out subscription cancellation, an already active subscription, and explicit new Submit. All 23 Chromium staff tests passed, including cancellation on all four surfaces, hosted-session destruction, fresh-form navigation and persisted-result refresh after list/detail cancellation. Provider/SDK effects in these regression tests are mocked. Ruff lint/format, mypy, frontend build and frontend formatting passed.

Separately created one real Sandbox order for ILS 1.00, agreement LOCAL-CANCEL-ATTEMPT, through the local application at http://localhost:8100. Used the new Cancel payment attempt button, verified checkout closed and saved status was cancelled with no transaction rows, then opened an empty form using Create a new payment. No card data was entered, approval/capture performed, charge/refund made or live API used. Existing operator payment attempts were not cancelled. Restarted only the local API and rebuilt the local frontend; no remote deployment, commit or push occurred.

## Split names, hosted prefill and refund placement - 2026-10-20

Moved the eligible-payment Refund action beside the payment amount. Its existing date/product/balance checks, event isolation, exact reviewed amount and single-dispatch behavior remain in place. Split Create payment into required First name and Last name fields. Submit now accepts first_name/last_name, preserves the name parts in immutable attempt terms and derives the combined client_name for existing record/search columns; no database schema change or name-splitting heuristic was introduced. Each part is trimmed and limited to 140 characters, with the combined name limited to the existing 255-character column. Review initial form retains the separate names; its next Submit sends only accepted input fields, excluding derived response fields.

Forwarded names through Orders v2 payment_source.paypal.name.given_name/surname and Subscriptions v1 subscriber.name, following https://developer.paypal.com/api/orders/v2/orders-create and https://developer.paypal.com/api/subscriptions/v1/subscriptions-create . The selected SDK's payment session, country/locale and NO_SHIPPING behavior remain unchanged. Subscription name forwarding is covered with mocked provider requests; an actual recurring checkout was not performed.

All 86 existing backend tests and all 23 Chromium staff tests passed after the change. Extended backend assertions verify distinct names in immutable terms and the outbound order/subscription; additional name validation cases cover empty last name, provider field length and combined storage length. The 14 relevant validation/subscription cases passed after those additions. Frontend tests verify Refund belongs to the amount cell, retains eligibility rules and opens only the intended refund form. Ruff lint/format, mypy, frontend build and frontend formatting passed. Automated provider/SDK financial responses are mocked.

Separately opened an actual Sandbox guest checkout for ILS 1.00, agreement LOCAL-NAME-PREFILL, with synthetic names Sandbox / Namecheck. Read the real PayPal-controlled first-name and last-name inputs and verified both were prefilled exactly. No provider-frame DOM was modified. Cancelled the verification attempt using the local Cancel payment attempt action. No card details were entered and no approval, capture, charge or refund occurred. Rebuilt the frontend and restarted only the local API at http://localhost:8100; no remote deployment, commit or push occurred.

## Refund button states - 2026-10-20

Changed the amount-adjacent Refund link to a bordered rectangular button with 16px logical spacing. Available refunds use a yellow background in both themes. Collection rows with unavailable eligibility, missing/expired deadlines or busy work show a grey disabled button; non-collection rows have no refund action. This supersedes the earlier rule that hid unavailable collection actions. Backend eligibility, refund limits and single-dispatch behavior are unchanged.

Both refund browser regressions passed with mocked financial responses. Computed-style checks verify yellow enabled and grey disabled states in light and dark themes, border and spacing; the existing refund flow verifies correct form selection and the exact reviewed amount. Frontend build and formatting passed. No provider financial action or remote deployment was performed.


## Refund primary color - 2026-10-20

Updated the enabled Refund background, border and text to use the same theme tokens as primary buttons (orange in the dark theme, blue in the light theme). This supersedes the fixed yellow styling above. Grey disabled styling and 16px spacing remain. Frontend build/format and both refund browser regressions passed; computed colors are checked in both themes. Provider responses are mocked; no financial action or remote deployment occurred.

## Payment collection/refund filter - 2026-10-20

Replaced Origin in the Payments page's expanded filters with Collected / Refunded: All records omits the kind parameter, Collected uses kind=collection and Refunded uses kind=refund. These distinguish collection and refund ledger rows; the separate Status filter remains independent. Reused the existing backend kind filter without changing financial behavior. Origin remains available on other pages. Filter changes retain the existing pagination reset and Clear filters behavior.

Frontend build and formatting passed. A local Chromium read-only smoke check selected all three options against the running backend, verified successful requests and matching kinds for every returned row, and confirmed Origin remains on Subscriptions. No provider mutation, financial action or remote deployment occurred.

## Unified payment register and schema - 2026-10-20

Removed the separate Payment requests navigation and request resource. All frontend checkout, cancellation, detail and association actions use /payments; the former /payment-requests API returns 404 with no compatibility alias. Payments lists unpaid, cancelled, pending, failed, completed, partially refunded and refunded payment identities. Refunds remain linked financial ledger entries and change the original payment's outcome rather than adding top-level list rows. Added a payment-status selector; Collected now selects completed/partially refunded payments and Refunded selects partial/full refunds. Ledger statistics still account for gross collections and adjustments separately.

Renamed PaymentRequest/payment_requests to Payment/payments and request_id foreign keys to payment_id on attempts, subscriptions and transactions. The migration preserves existing identities and financial facts, creates payment identities for unlinked imported collections, and gives each subscription installment its own payment. Imported payment metadata/intent fields remain nullable instead of fabricating operator input. The partial unique collection constraint permits one non-failed collection per payment while keeping failed capture observations for explicit restarts. The register's join excludes those failed observations from row counts, and details retain them. Verified refund/reversal facts determine the payment status while its original gross amount remains unchanged. Correlation by provider order/capture identity prevents duplicate imported/payment rows; a known completed imported capture cannot cause another capture through a late callback. No name/amount matching or provider fallback was introduced.

Migration tests cover a clean database and a populated prior schema with a collected payment, two failed captures, a partial refund, an unlinked import, subscription activation and two installments. Upgrade preserves financial IDs, gives installments separate payment IDs, derives the refund status, removes the old table and matches SQLAlchemy metadata. Regression coverage includes one row through checkout/partial/full refunds, cancelled payments without invented ledger collections, repeated imports, exact capture correlation, and a deliberate restart after a declined capture. Financial/provider responses in automated tests are mocked; these do not certify live PayPal behavior.

Applied the schema locally and compared the running application database against SQLAlchemy: zero differences. All 10 existing payment identities and 10 attempts remain; the single existing financial ledger record and its provider identity, kind, amount, currency and original link are unchanged (matching before/after SHA-256 of those facts). No unlinked collections or duplicate accepted collection links remain. Finalized the accepted-collection partial index during this local implementation without changing ledger data. Chromium read-only checks confirmed one Payments menu entry, all 10 existing records, cancelled/awaiting-approval/completed status filtering and the removed request API. Frontend build/format and all 23 staff browser regressions passed. Local only at http://localhost:8100; no provider financial action, remote deployment, commit or push occurred.

Final backend validation covers 97 test cases: the final full run passed all 96 non-failing cases, and all four lifecycle/migration cases passed after correcting only the populated-migration fixture's SQL parameter typing. This includes migration with multiple failed captures, preserved completed/refund facts and distinct installment identities. Ruff lint/format and mypy passed. No application database was used by backend tests.

## Unified register code review - 2026-10-20

Review-only probes reproduced five gaps not covered by the preceding regressions: migration can replace an open payment's status with an older failed capture, enabling another attempt; confirmed subscription cancellation leaves its payment active; a previously uncorrelated imported capture cannot be reconciled when its order ID arrives later; a correlated pending capture becoming declined updates the attempt but leaves the payment pending; and a stale COMPLETED capture response can overwrite REFUNDED provider evidence and re-enable refund eligibility when refund ledger details are unavailable. These are outstanding findings, not fixes or verified-correct behavior.

All five reproductions ran only against the dedicated payments_test database with mocked provider effects. Temporary review probes were removed afterward. No production code was changed, application database modified, provider financial action performed or remote deployment made during this review.

## Review fixes and cumulative partial refunds - 2026-10-21

Resolved the five findings above. Restart now checks the latest attempt as well as the payment summary, so an open/pending attempt blocks another submission even if the summary is incorrect. Corrected the original unified-register projection for fresh upgrades and added migration e61f28c904a7 to repair already-upgraded attempt-derived summaries and confirmed cancelled agreements without changing accepted collections. Migration tests exercise both fresh upgrade and a deliberately corrupted previously-applied summary.

Capture normalization retains confirmed full/partial refund evidence across older completed/pending/declined responses. A known full refund yields zero refundable balance even without individual refund ledger entries. Exact order correlation now merges an earlier imported capture and its refunds into the checkout payment while preserving collection/refund IDs and original links. A pending capture becoming definitively declined updates both its attempt and payment, permitting only an explicit operator restart. Confirmed subscription cancellation updates the latest matching activation attempt/payment, retains terminal cancellation on stale reads, and leaves collected installments unchanged. Added the suspended-subscription status to the existing selector.

Verified staged refunds against a 50.00 payment: a completed 20.00 refund leaves 30.00 available and the button eligible; a 30.01 request is rejected before provider HTTP; a further explicit 30.00 refund exhausts the balance and an additional 0.01 is rejected. Pending/uncertain reservations also reduce availability: after 20.00 completed and 10.00 reserved, only 20.00 remains. Unknown external partial-refund amounts stay unavailable rather than treating a pending reservation as completed evidence. These provider effects are mocked; no actual refund was issued by validation.

All 109 backend tests and 23 Chromium staff regressions passed. Ruff lint/format, mypy, frontend build/format, clean/populated migrations and schema comparison passed. Backend tests used only the dedicated payments_test database. Applied the repair migration locally and restarted only the loopback API at http://localhost:8100. The application schema matches SQLAlchemy; all 10 payments and the two existing financial ledger records remain, with an unchanged hash of financial IDs, provider identities, kind, amount, currency, original link and statuses. A separate authenticated read-only smoke check loaded the Payments page and checked the existing partially refunded payment's remaining balance and enabled eligibility. No actual charge/refund, new PayPal checkout, remote deployment, commit or push occurred.


## 2026-10-21: Remove subscription capability configuration

At the operator's request, removed both subscription capability settings from the
runtime model, browser options, subscription form, example configuration and local
`.env`. Subscription checkout and sale-based installment refunds are available
without configuration gates. The explicit live-environment gate remains intact.
Earlier entries describing disabled capability gates are historical and superseded
by this change. Updated product and operating documentation accordingly.

Removed the now-unused configuration dependency from refund eligibility and its
callers. Refund product, payment date, confirmed outcome, remaining balance,
idempotency and uncertain-reservation rules remain enforced. The partial-refund
regression now covers both capture and sale APIs: refund 30 of 100, reject 71,
refund the remaining 70, and then disable further refunds. Provider responses are
mocked, including the v1 sale-refund money and status shapes.

Frontend build, formatting, backend Ruff/type checks and all 23 browser tests
passed. Browser financial responses are mocked. The local API was restarted on
127.0.0.1:8100. No PayPal financial mutation, remote deployment, commit or push was
performed; removing an application switch is not evidence of merchant acceptance.

Backend verification: the initial full suite had 109 passing tests and one obsolete
assertion expecting subscription submission to be blocked. Replaced that assertion
with default-enabled subscription coverage. All 70 tests in the changed payment,
review-fix and register modules then passed against the dedicated test database.


## 2026-10-22: Subscription progress verification and empty-list investigation

All 118 backend tests and all 24 Chromium staff tests passed. Ruff lint/format,
mypy, frontend build/format and diff whitespace checks passed. Subscription list
and detail tests cover successful recorded collections, pending/failed exclusion,
refunded collection history, finite/unlimited/unknown remaining counts, trial
exclusion, cancellation/expiry and suspension. Remaining counts use saved provider
cycle metadata; charged counts use recorded completed collections rather than
assuming completed billing cycles prove collection. The browser regression
verifies the new columns and unknown/unlimited labels. Financial/provider results
are mocked; neither local application database currently contains subscriptions.
No live subscription billing behavior is certified by these checks.

Read-only native database queries found 10 payments and two transactions. The
active Compose database has zero payments/transactions/subscriptions, matching
HTTP 200 authenticated API list responses with total=0. Compose uses its own db
service/volume instead of the native configuration's local database. No data was
moved, recreated or removed. The current Docker image includes subscription
progress and excludes the two removed feature gates. No container configuration
was changed or container restarted by this review.

A separate mocked browser probe confirmed stale list rows remain after a failed
filter request (ILS record still shown with USD selected after HTTP 503). Recorded
as an open P2 finding in REVIEW.md. No PayPal financial action, remote deployment,
commit or push was performed. Tests recreated only the dedicated payments_test
database; the two application databases were inspected read-only.


## 2026-10-22: Fix unavailable lists and restore Compose records

Fixed the stale-list finding: a new list query clears the previous result;
failed queries show Records unavailable with no previous rows, zero-count claim
or pagination. Successful empty lists still show No matching records. Added
payment and subscription browser regressions for filter failure, stale-row
removal and explicit successful refresh. All 26 browser tests passed, along with
frontend build/format and whitespace checks. Provider mutations in these tests
are mocked.

Restored the original local Sandbox data into the already configured Compose
payments-db volume. Before the restore, verified equal merchant/environment
identity, equal migration head/PostgreSQL major version, no source database owner
and an empty destination. Stored pg_dump custom-format backups of both databases
in a private temporary directory outside the repository. Stopped the API, restored
in one PostgreSQL transaction, and compared every table's row count and full-row
fingerprint. All tables matched the source, including stable attempt, financial,
audit and idempotency records. The original database remains unchanged. Compose
now has all 10 payments and both financial ledger records.

Built the updated container and restarted only the local Compose API. It runs as
UID 10001; authenticated readiness and list reads return HTTP 200 with 10 payments
and two transactions. An unmocked Chromium smoke check displays all 10 payment
rows without an error banner. Compose still contains only api/db, binds the API
to loopback and leaves PostgreSQL unpublished. README now documents Compose as
this workspace's standard local startup and explicitly distinguishes the separate
native development database. No provider payment/refund, remote deployment,
commit or push was performed.


## 2026-10-22: Remove sync log/history control/footer; use operator timezone

Removed the dashboard sync-history/coverage panel and successful synchronization
log messages from the UI, following the operator's clarification. Errors and
invalid synchronization responses remain visible. Removed the Import existing
PayPal history control and its unused component/styles, plus empty-state text
suggesting that control. The separate subscription import action and backend
read-only history API remain available. Removed the currency/timezone footer and
its unused CSS. No financial behavior, payment records or provider actions changed.

All UI timestamp formatting now uses the browser timezone. Reporting/filter
requests and explicit subscription installment date windows send that IANA
timezone. Trusted calendar API requests without a timezone use UTC; invalid names
are rejected. Fixed the bounded installment window to count calendar days so a
31-day range spanning a repeated daylight-saving hour is accepted. Financial
storage and provider instants retain their original timezone-aware representation.

All 122 backend tests and 29 Chromium staff tests passed. New tests cover missing
sync/import/footer UI, no successful-sync log, payment dates and reporting requests
in Tokyo and Los Angeles, calendar-window UTC conversion, invalid timezones and
31/32-day windows across daylight saving. Provider financial responses are mocked.
Ruff lint/format, mypy, frontend build/format, container build and whitespace checks
passed. Restarted only the local Compose API; authenticated readiness and payment
reads succeed, all 10 records remain available, and reporting returns the requested
operator timezone. No actual charge/refund, remote deployment, commit or push.


## 2026-10-22: Pagination comparison with sibling playlist service

Verified without application changes. Payments and Subscriptions support working
previous/next controls with a fixed frontend page size of 25. Browser probes with
mocked 26-record responses verified navigation 1 -> 2 -> 1 and disabled controls
at the boundaries for both lists. Actual authenticated Payments API reads with
page_size=3 returned page lengths 3,3,3,1, covering all 10 distinct saved payment
IDs with no overlap; page_size=101 was rejected with HTTP 422. No data was changed.

The full playlist-style pagination is not implemented everywhere. Its shared
Pagination supports Page X of Y, optional 10/20/50/100 page-size selection and
First/Previous/Next/Last buttons when page-size selection is enabled. PayPal's
main lists currently expose only Previous/Next and Page X/total-records text.
Payment and subscription detail financial histories are capped at 100 rows with
no pagination; payment history also lacks explicit ordering on that limited
query. The attempt history is returned and displayed without pagination. These
are remaining gaps, not fixed or approved as compliant by this verification.
Dashboard currency/status summaries have at most three/five rows and do not need
pagination. No actual PayPal action, service restart or deployment was performed.


## 2026-10-22: Focused pagination review reproductions

A mocked browser list reproduced an out-of-range page after shrinkage from 26
records to 25: Page 2 remains selected and No matching records is shown. A
separate temporary PostgreSQL test, restricted by the existing fixture to the
dedicated payments_test database, confirmed that a payment with 102 ledger rows
returns only 100 in detail, while the paginated ledger API correctly counts 102;
101 attempt rows are returned unbounded in that same detail response. The probe
passed and its temporary test file was removed. Source inspection confirmed no
ordering on the limited payment-history query despite the latest-100 label.
Recorded three open P2 findings in REVIEW.md. No application changes, provider
financial calls, application-database writes, deployment, commit or push.


## 2026-10-22: Complete list and detail pagination fixes

Replaced the fixed financial-history cutoff with bounded paginated results and
stable provider-effective date/ID sorting, reusing the existing ledger query.
Payment attempts use bounded, independently selectable pages with creation-date
and ID ordering. Latest-attempt cancellation eligibility is independent of the
selected page. Both detail reads use one repeatable-read snapshot for metadata,
counts and rows. Refund balance/date/reservation checks still use every relevant
ledger entry, independent of history page size. The canonical detail response
uses page objects for attempts/transactions; no alternate legacy array contract
or fallback was retained. No schema or financial-operation behavior changed.

Added one shared pagination component matching the sibling playlist control set:
First, Previous, Next, Last, Page X of Y, and 10/20/50/100 rows per page (default 20).
Main lists and independent detail histories correct the selected page when totals
shrink. Loading or failed detail reads hide stale data/actions. Date formatting
continues using the operator's browser timezone.

All 128 backend tests passed against the dedicated payments_test database. New
coverage walks 102 payment-history entries, 101 subscription-history entries and
101 attempts with tied timestamps without gaps/duplicates, verifies bounds and
out-of-range responses, confirms the full refund balance on every history page,
and ensures old attempt pages cannot offer cancellation. All 33 browser tests
passed, including both lists shrinking from two pages to one, page-size and
First/Last navigation, and independent detail paging beyond 100 rows. Browser
financial/provider responses are mocked. One older subscription-detail mock was
updated to include the new query parameters before the complete passing run.

Ruff lint/format, mypy, frontend build/format, whitespace checks and local container
build passed. Restarted only the local Compose API; it runs as UID 10001 with
only api/db services. Actual read-only API and Chromium checks verified main-list
page-size/last-page navigation and both paginators in the existing partially
refunded payment detail, including its remaining refund balance. The application
now has 11 saved payments, including the existing 10; review did not reset or
replace either database. No real charge/refund or mutation replay was performed
by this work. No remote deployment, commit or push.


## 2026-10-23: Investigate INVALID_RESOURCE_ID on subscription refresh

Inspected the current local Sandbox record read-only. The uncertain subscription
checkout was created on 2026-10-22 and its attempt is unknown/cancel_dispatched
with INVALID_RESOURCE_ID saved from the earlier cancellation. Its linked
subscription retains the last verified APPROVAL_PENDING snapshot. No financial
ledger entries are linked to this payment locally.

One actual GET to the configured Sandbox subscription-details endpoint using the
saved provider subscription ID returned HTTP 404 INVALID_RESOURCE_ID, PayPal debug
ID f96201867653f. This reproduces the operator's error directly at PayPal; the
route selects the subscription API, not the Orders API. The 404 alone does not
distinguish missing provider data from caller access/context issues and does not
confirm cancellation or absence of collection. No subscription lookup result or
financial status was fabricated. No provider mutation, replacement checkout,
application-database write, deployment, commit or push was performed.


Follow-up to the 2026-10-23 investigation: the operator confirmed no credential or
account changes. A single read of the original saved billing plan succeeded and
returned ACTIVE. Stored subscription IDs agree between the checkout attempt and
subscription row. Audits show submission followed by a cancellation request.
Reviewed PayPal's official REST specification, which explicitly restricts the
cancel action to active/suspended subscriptions. Found that our pre-approval
cancellation branch and its mocked success test violate that contract. Recorded
an open defect in REVIEW.md. The code defect does not prove why a later GET
returns 404, and no provider cancellation outcome or expiry was inferred. No
provider mutation, application-state change or deployment occurred in this
follow-up.


## 2026-10-23: Fix unsupported pre-approval subscription cancellation

Removed the subscription provider cancellation branch and its now-unused PayPal
client dependency from cancel_attempt. The eligibility flag is false for
subscription attempts; direct endpoint calls return a clear 409 before changing
audit/attempt state or making provider calls. One-time cancellation retains its
capture-claim lock and stable latest-attempt check. Active/suspended subscription
cancellation remains a separate explicit, idempotent financial action. Added a
shared UI explanation to subscription forms, checkout results and detail panels.
Corrected the provider mock to reject cancellation outside ACTIVE/SUSPENDED.

All 132 backend tests and 35 browser tests passed. Regressions verify no provider
call or durable-state change when rejecting subscription checkout cancellation,
no unsupported UI action on Payments/Subscriptions, active/suspended cancellation
uncertainty and duplicate protection, and preservation of an UNKNOWN cancellation
when the subscription read returns 404. Tests use a dedicated payments_test
database and mocked provider mutations; they do not prove real merchant-side
cancellation acceptance. Ruff lint/format, mypy, frontend build/format, whitespace
checks, container build and local readiness passed.

Restarted only the local Compose API at localhost:8100. Read-only API and actual
Chromium checks confirmed the existing UNKNOWN record and INVALID_RESOURCE_ID
remain intact, with its cancellation restriction explained and unsupported action
absent. No provider mutation, new checkout, forced cancellation/reclassification,
automatic retry, application-schema change, remote deployment, commit or push.
The provider's 404 for the old subscription remains unresolved; this fix prevents
our unsupported cancellation call and does not claim provider cancellation.


## 2026-10-23: Terminal checkout results and independent new payments

Supersedes the earlier product rule that pending/uncertain checkout outcomes
block replacement. Every explicit new Submit now has a fresh payment, attempt,
intent key and stable provider mutation identities. Create payment is available
on every page, including the active form and result; it always opens an empty
form immediately. Removed initial-form restart/reuse and the can_restart API
contract. No navigation, reload or late response automatically submits a payment.

Added terminal checkout_status success/failed independently of provider/financial
status. Verified approval/completion ends checkout successfully; cancellation,
error, abandonment and unsuccessful creation end it as failed. Late callbacks
and explicit financial reads cannot promote failed checkout results. Local close
performs no provider cancellation, capture recovery or financial reclassification.
Leaving an embedded checkout destroys its session once. Late creation responses
retain their provider identity but never reopen the abandoned checkout.

Migration f72b38a015d9 adds the constrained checkout outcome column and closes
pre-existing local checkouts. The existing UNKNOWN subscription checkout is now
Failed in the operator UI; its financial UNKNOWN/INVALID_RESOURCE_ID evidence is
preserved. A private pre-migration backup was created outside the repository.
Before/after whole-record hashes match for all 11 payments, both ledger entries
and subscriptions; existing financial records were not rewritten.

All 150 backend tests pass on a dedicated payments_test database, including clean
and populated Alembic upgrades and schema comparison. Regressions cover both
products, independent Submit while an old checkout is open, cancellation/error,
creation/capture timeouts, late approval, closure during creation/capture,
immutable failed outcomes after provider reads, retained collection evidence,
idempotency and no automatic replay. All 35 browser tests pass, including a
new explicit subscription Submit after unsuccessful creation and direct new-form
navigation while an embedded checkout is still active. An additional actual
Chromium smoke check loaded the existing UNKNOWN record through the local API,
saw Failed, opened an empty new form and confirmed zero payment-creation POSTs.
Ruff lint/format and mypy pass. Frontend build/format and the local container
build pass. Compose still contains only api
and db; only the local API at localhost:8100 was restarted. Authenticated
readiness and local record checks pass. No real PayPal checkout, charge, refund,
cancellation, remote deployment, commit or push was performed. Provider mutations
in regression tests are mocked; they do not prove merchant-side behavior.


## 2026-10-23: Subscription Submit opens a PayPal popup

The operator selected a subscription popup after the provider contract review.
Loaded paypal-guest-payments and paypal-subscriptions in one SDK instance;
subscription eligibility uses RECURRING_PAYMENT before enabling Submit. The
existing one-time guest overlay remains in place. Subscription Submit starts
createPayPalSubscriptionPaymentSession with explicit popup presentation and an
asynchronous subscription-ID promise inside the original user gesture. Both
products share approval/cancel/error handling, terminal checkout outcomes,
callback correlation and session cleanup. No application redirect fallback,
mutation retry, polling or failed-session resume was added.

Removed the full-page approval-URL navigation, approval URL response field,
provider link parser and dependency on finding an approval link after creation.
Backend subscriptions still use the Subscriptions API and verify their own terms;
no Orders capture is issued for subscription approval. Signed local return state
remains available for provider-controlled navigation. No database migration or
financial data change is needed for this presentation change.

Actual read-only Sandbox SDK initialization from localhost verified both session
factories. PayPal recurring eligibility returned true for ILS, USD and EUR on the
configured app. This created no order/subscription and proves SDK availability
and eligibility only; it does not prove approval, guest-card availability or
recurring billing on the account. The connected browser was unavailable; the
project Chromium runner performed these checks.

All 150 backend tests and 40 browser tests pass. Popup regressions cover
approval, cancellation, error, blocked popup without redirect fallback/retry,
recurring ineligibility before creation, launch before asynchronous creation
resolves, and abandonment during creation. Existing one-time flows also pass.
Ruff lint/format, mypy, frontend build/format and the local container build pass.
Financial/provider regression tests are mocked. Only the
local Compose API at localhost:8100 was rebuilt/restarted; no remote deployment,
actual checkout, charge, refund, cancellation, commit or push was performed.


A final actual-SDK presentation smoke check used the built local dashboard and
real Sandbox SDK, while intercepting POST /api/v1/payments before it reached the
backend. Subscription Submit opened a real popup before creation resolved; the
dashboard stayed on localhost. PayPal places its own overlay over the dashboard
while the popup is active. Closing the popup and aborting the held request allowed
Create payment to open an empty form. Exactly one creation request was intercepted;
no order/subscription was created at PayPal and no funds were collected. This
proves launch/presentation only, not actual approval or recurring card support.


## 2026-10-23: Callback race review reproductions

Reviewed the shared popup/guest checkout implementation without modifying or
redeploying application code. Three isolated Chromium reproductions used the
existing test helper and mocked all checkout creation/outcome/closure responses:

1. Held an old approval outcome POST, opened and edited a new empty form, then
   delivered the old response. The old finally handler navigated to /paypal/fail
   and removed the new form.
2. Aborted Submit, held its saved-submission lookup, opened and edited a new form,
   then returned the old failed record. creationFailed's delayed onSaved replaced
   the new form with that old failed result, despite the originating component
   having unmounted.
3. Held the first approval POST, explicitly submitted a second checkout and kept
   its SDK session awaiting operator action, then delivered the old response.
   The old redirect selected the second intent from session storage and closed
   that new checkout as Failed. Observed two explicit mock creations and two
   local mock closures (old abandonment and unintended new closure).

The reproductions confirm frontend state/navigation defects, not actual PayPal
behavior. No real checkout creation, capture, refund or subscription cancellation
was sent, and application financial data was not changed. Recorded both open
findings in REVIEW.md; the previously passing 40 browser tests omit these races.


## 2026-10-23: Wait for answers, supersede unfinished operator checkouts

The operator clarified the rule: no new attempt while the current request is
open; every accepted new Submit closes all unfinished local checkouts belonging
to that operator. The backend now rejects overlapping checkout mutations per
operator with 409, without queueing/retry and without a database lock over HTTP.
The guard is released on response/exception; another operator remains independent.
New Submit closes unfinished local checkout permission in the same transaction
that saves the new payment/attempt. Idempotent replays and invalid submissions
never dispatch new provider actions or supersede checkouts. Financial evidence,
other operators and terminal checkout results are preserved.

Frontend request tracking disables new-attempt/navigation controls until open
API responses are consumed, including response-loss lookup. There is no background
polling. Removed the saved-record lookup from new-form navigation. Async callbacks
check component lifetime after awaits, and approval navigation selects the
original signed attempt state. Blocked/cancelled popup handling waits for any
outstanding creation response rather than aborting it to navigate early.

All 154 backend tests pass on a dedicated payments_test database. Added regressions
exercise open creation/capture requests through real ASGI routes with mocked
provider HTTP, cross-tab rejection, another operator's independent Submit,
lock release after rejection, and closure of multiple unfinished local attempts
without changing verified financial status. Ruff lint/format, mypy, frontend
build/format, container build and authenticated local readiness pass. No schema
migration, actual PayPal creation/charge/refund/cancellation, remote deployment,
commit or push was performed. Only the local Compose API was restarted. Mocked
tests do not certify merchant-side outcomes.


All 43 browser tests pass on the final built local version. Regressions verify
new-attempt/navigation controls remain disabled until creation, approval and
saved-submission lookup responses finish; a blocked popup waits for its creation
answer before showing failure; after completion an empty new form is available.
Existing payment, subscription, cancellation, refund and pagination checks pass.
Browser financial scenarios use mocked HTTP/SDK responses and create no real
PayPal resources. The deployed service remains loopback-only at localhost:8100.
