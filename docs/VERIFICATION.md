# Current verification

Last verified: 2026-10-29. Source branch: `master`.
The service runs in Linux Docker containers on Ubuntu through Docker Compose.
The local Compose API was rebuilt and recreated from this branch on 2026-10-25;
its existing database volume was preserved. No remote deployment was performed.

## Allowed IP setting rename, 2026-10-29

- Renamed the staff source restriction setting to ALLOWED_IP and the Python
  field to allowed_ip across settings, authentication, Compose, fixtures,
  deployment checks, both private profiles, the template and deployment docs.
  Production retains 62.90.52.94 and development retains its explicit empty
  value. Only the names changed; valid Bearer keys still bypass the staff
  source restriction. All other private profile bytes were preserved.
- Both private profiles validated and Compose injected the renamed setting
  with its existing value. Fifteen settings/authentication tests passed against
  a dedicated payments_test_allowed_ip database; all nine deployment tests,
  Ruff lint/format and mypy passed. The image build passed.
- A disposable Compose project loaded ALLOWED_IP and passed startup migrations,
  authenticated readiness and frontend access from the permitted source. Its
  resources and the separate backend-test database container were removed.
  No existing service restart, remote deployment or PayPal call occurred.

## One Compose file for development and production, 2026-10-29

- Removed docker-compose.production.yml. The shared docker-compose.yml now
  declares the fixed image tag, local build, restart policies and graceful API
  shutdown. Its explicit API environment list receives values from the selected
  --env-file, without an additional profile/path parameter. Development builds
  locally; deployment pulls/tags the selected digest and still uses --no-build.
- Updated deployment packaging, server commands, profile-isolation checks and
  setup documentation. Both actual profiles rendered all 18 expected parameters,
  the correct sandbox/live mode and matching API/database passwords. Only api/db
  services remain, the API stays loopback-bound and PostgreSQL has no host port.
- All nine deployment-helper tests, deployment Ruff lint/format and ShellCheck
  passed. Profile-isolation rendering succeeded with the development file absent
  for production. The image build through the shared Compose file passed.
- A disposable Compose project, using the shared file with an ephemeral loopback
  host port and dummy settings, passed startup migrations, authenticated readiness
  and frontend HTTP access with --no-build and no source checkout. Its database
  volume, containers and network were removed afterwards. Existing services and
  databases were not restarted or modified; private profiles were unchanged.
- No GitHub workflow run, remote deployment or PayPal calls occurred. Earlier
  verification entries describe the manifests as they existed at that time.

## Single PayPal environment selector, 2026-10-29

- PAYPAL_ENVIRONMENT now selects both the provider API and browser session mode;
  removed LIVE_ENABLED from Settings, fixtures, both private profiles and the
  documented template. Choosing live explicitly enables real payments. Existing
  requirements for secure cookies, staff source restrictions and configured
  merchant credentials remain enforced.
- Development and production Compose rendering confirmed sandbox/live
  respectively, without the removed flag. All three dotenv files have the same
  18 parameter names. No credential values were displayed or changed.
- Eleven settings tests, backend Ruff lint/format, mypy, frontend build and
  frontend formatting passed in Linux Docker. The runtime image build passed
  as rutv-payments:environment-check. The badge now uses Sandbox/Live text and
  retains environment-specific styling without forced uppercase.
- Both isolated Chromium/Playwright checks passed against the built frontend:
  sandbox session data displays Sandbox and live session data displays Live,
  with the matching CSS class and no uppercase transformation. API responses
  were mocked; these checks do not prove live provider behavior.
- No deployment, existing service restart or PayPal calls occurred. Earlier
  verification entries describe the configuration as it existed at that time.

## Frontend database credential isolation, 2026-10-29

- Reviewed frontend HTTP calls, Vite configuration, Docker build stages, static
  file routes, browser session contracts and database error responses. Database
  credentials remain backend configuration; no settings object is returned to
  the browser. Compose injects runtime secrets after the frontend build.
- Privately scanned 43 frontend files and all four built frontend files in the
  existing rutv-payments:password-check image for both profiles' actual database
  passwords, their URL-encoded forms and database credential references. None
  were found; password values were never printed. The runtime image contained
  no /service/.env* files.
- Executed browser-session serialization with a configured database password;
  neither that password nor the provider secret appeared in its JSON. This was
  a local source/artifact review and serialization check, not a production
  browser/network inspection. No deployment or financial calls occurred.

## Single database password setting, 2026-10-29

- Replaced the configurable DATABASE_URL with required POSTGRES_PASSWORD in
  Settings. The connection URL is built with SQLAlchemy URL.create using fixed
  host db, port 5432, username payments and database payments. Passwords are held
  as SecretStr, and URL/password representations are masked. No manual escaping
  or duplicated database password is needed.
- Removed DATABASE_URL from both private profiles and .env.example. All three
  retain the same 19 parameters. The existing development password was preserved;
  the undeployed production profile now has a cryptographically random 24-character
  alphanumeric password. No other private assignments were changed or displayed.
- Tests retain TEST_DATABASE_URL. CLI migration checks now use that explicit
  override only for databases named payments_test*; a regression verifies rejection
  of an application database before connection. Application startup migrations
  continue using their supplied singleton-owned database connection.
- All 194 backend tests passed in Linux Docker on payments_test_password. All nine
  deployment-helper tests, Ruff lint/format, mypy, Actionlint and ShellCheck passed.
  Alembic upgrade/schema comparison passed on payments_test_password_migrations.
  Actual local/production Compose rendering returned only api/db with the same
  selected POSTGRES_PASSWORD injected into both containers.
- The runtime image build passed. A separate disposable Compose project verified
  startup migrations, authenticated readiness and frontend access using only the
  shared password, including special password characters. Its fixed payments
  database was in a newly created, disposable cluster/volume, separate from backend
  test databases and all existing application data; the project and volume were
  removed after the smoke test.
- No deployment, existing database password rotation, existing application restart,
  server modification, PayPal read or financial action occurred. Tests use mocked
  provider responses and do not prove live merchant behavior.

## Fixed frontend layout and public API deployment, 2026-10-29

- Fixed the frontend directory to /service/frontend/dist in app.main and removed
  frontend_dir from Settings. Removed FRONTEND_DIR, PAYMENTS_SHARED_NETWORK and
  PAYMENTS_IMAGE from all profiles and the template. The three files retain the
  same 20 parameter names; every other private assignment was preserved exactly.
- Removed the production shared Docker network. Integrations are documented to
  use the public HTTPS API with Bearer keys, while browser login/cookies/static
  routes retain the staff IP restriction. Proxy instructions distinguish these
  paths so a server-wide staff allowlist does not block integration keys.
- Production Compose uses the fixed local image tag rutv-payments:production.
  Deployment pulls the workflow's digest, tags it locally and records the digest
  in release/image.txt. There is no image-selection env parameter or image.env.
  The loopback host port, private database and single API ownership are preserved.
- All 190 backend tests passed on payments_test_simplify in Linux Docker with
  Python 3.14.9/PostgreSQL 17. The new regression confirms a valid integration key
  works outside the staff IP while existing cookies and forged forwarding headers
  are rejected. All nine deployment-helper tests, Ruff lint/format, mypy,
  Actionlint and ShellCheck passed. Both actual profiles passed Compose rendering
  without a shared network or image-selection env file.
- Runtime image/frontend build and smoke checks passed on the separate
  payments_test_simplify_smoke database: authenticated readiness, integration
  identity, frontend HTML and built JavaScript. The fixed path and absence of the
  frontend settings field were verified inside the image. Provider credentials in
  the smoke fixture were empty; no PayPal read or financial action occurred.
- No GitHub workflow was dispatched and no server/proxy configuration changed.
  Public DNS/TLS/Nginx routing still requires server provisioning and has not been
  verified by these local/mocked tests. Existing application containers were not
  restarted or replaced.

## Development profile filename and comment cleanup, 2026-10-29

- Renamed the private development file to .env.dev and removed comment/blank lines
  from .env.dev and .env.prod. Every parameter assignment was preserved exactly;
  configured PayPal values remain populated in both profiles. Explanatory comments
  remain in .env.example. Both private files remain ignored by Git.
- Updated active Compose, settings comments, test fixtures and documentation to use
  .env.dev. Local Compose rendering returned only api/db. All nine deployment-helper
  tests passed, including production rendering without the development file present.
  Five focused settings tests and applicable Ruff lint/format checks passed.
- No application container restart, remote change, deployment or PayPal call occurred.

## Explicit local/live settings profiles, 2026-10-28

- Renamed app.config to app.settings and updated application, Alembic and test
  imports. All 19 application fields are required, including nullable/empty values;
  there are no setting value defaults or automatic dotenv-profile fallbacks.
  Compose injects the selected file; explicit Settings(_env_file=...) remains
  available for validation tooling. Missing fields fail validation.
- Renamed the private .env to .env.local and created a private .env.prod. Both and
  the commented .env.example have exactly the same 23 keys: 19 application fields
  plus PostgreSQL password, Uvicorn proxy trust, shared network and image selection.
  Local credentials were preserved, with local Sandbox/cookie/return settings and
  explicit empty staff filtering. Production has independent generated database
  and signing secrets, live flags and staff IP 62.90.52.94. Its actual merchant
  credentials, account hashes and HTTPS URLs remain unconfigured; validation
  correctly rejects the incomplete live profile. Secret contents were not printed.
- All 189 backend tests passed in Linux Docker, Python 3.14.9 and PostgreSQL 17,
  using payments_test_settings. Ruff lint/format, mypy, Actionlint and ShellCheck
  passed. Nine deployment-helper tests passed, including Compose profile isolation
  with the local file absent from the production fixture. Actual private profile
  rendering was also checked without printing resolved values. Both profiles keep
  the loopback API port and unpublished database; the production overlay uses only
  .env.prod, and server deployment scripts now require that filename.
- A fresh runtime image build passed. Disposable local/live container profiles
  passed startup migrations and authenticated readiness on separate
  payments_test_settings_smoke_local and payments_test_settings_smoke_prod databases.
  Local UI access returned 200; production's non-allowed staff peer returned 403.
  The live smoke profile used dummy provider credentials, never the real .env.prod.
  CI-style Alembic upgrade/schema comparison passed against the separate
  payments_test_settings_migrations database using explicit example settings.
- No GitHub deployment, remote changes, PayPal read or financial action occurred.
  Runtime startup/health tests do not certify live merchant eligibility or public
  production DNS/TLS/proxy behavior. The existing local application containers were
  not recreated; their previous configuration remains until an explicit rebuild.

## Manual production deployment workflow, 2026-10-27

- Added a workflow_dispatch-only deployment workflow, reusing Checks before image
  publication. Production deployment uses a GHCR digest, pinned SSH host keys,
  server-only runtime configuration, a stable Compose project, serialized updates
  and authenticated local readiness. Setup is documented in DEPLOYMENT.md.
- Actionlint 1.7.7 and ShellCheck 0.10.0 passed. Ruff lint/format and backend mypy
  passed. All 184 backend tests passed in Linux Docker with Python 3.14.9 and a
  dedicated PostgreSQL 17 `payments_test_deploy` database. Eight deployment-helper
  tests passed, covering deployment ordering, unchanged release pointers after
  failure, bounded authenticated readiness and rejection of redirects/wrong bodies.
  Deployment Python syntax was checked for compatibility with Ubuntu Python 3.10+.
- Production Compose validation with disposable configuration returned only api/db,
  removed the server build configuration, retained the loopback API port, kept the
  database unpublished/off the shared network, and applied the staff IP restriction.
- Built the runtime image locally with Python 3.14.10. Startup/migration smoke tests
  used the separate `payments_test_deploy_smoke` database. Authenticated readiness
  and API-key session reads passed; invalid keys were rejected, browser/login
  access from a non-allowed peer was denied, and forged forwarding headers could
  not impersonate the allowed staff IP. Runtime UID was 10001.
- No workflow dispatch, image publication, SSH deployment or server changes were
  performed. No PayPal read or financial action was performed. Backend provider
  responses and deployment shell commands in helper tests were mocked; these
  checks do not prove live merchant behavior or production DNS/TLS/proxy access.

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
