# Current verification

Last verified: 2026-10-30. Source branch: `main`.
The service runs in Linux Docker containers on Ubuntu through Docker Compose.
The local Compose API was rebuilt and recreated from this branch on 2026-10-25;
its existing database volume was preserved. No remote deployment was performed.

## Repository deployment secrets, 2026-10-30

- Updated DEPLOY_HOST and DEPLOY_USER to read the GitHub secrets context, matching
  the repository secret names shown by the operator. DEPLOY_SSH_PRIVATE_KEY already
  uses that context. Optional port/path overrides remain variables with defaults.
- Actionlint and whitespace checks passed. Secret values and actual server SSH
  connectivity were not inspected; no deployment was performed.

## Automatic SSH host-key acceptance, 2026-10-30

- Removed the required DEPLOY_KNOWN_HOSTS deployment variable at the operator's
  request. SSH now uses StrictHostKeyChecking=accept-new with a temporary
  known-hosts file removed with the runner credentials. Each fresh runner trusts
  the first key it receives, without checking a previously trusted server identity.
- All six deployment workflow tests passed in Linux Docker without a supplied
  known-hosts variable. Ruff lint/format checks, actionlint and whitespace checks
  passed. Tests use command doubles and do not establish actual SSH connectivity
  or server identity. No remote deployment or provider call was performed.

## Deployment ports and migration review, 2026-10-30

- Confirmed Compose publishes API container port 8000 only at host
  127.0.0.1:8100. The Dockerfile declares port 8000 and starts one Uvicorn process
  on 0.0.0.0:8000; the internal readiness probe uses 127.0.0.1:8000. PostgreSQL
  remains unpublished and the API connects to db:5432. No port changes needed.
- Confirmed the runtime image contains alembic.ini and backend/migrations.
  FastAPI startup retains singleton ownership while calling Alembic upgrade head
  before serving requests. Deployment's container-local readiness check follows
  startup; migration failures prevent successful readiness. A separate workflow
  migration command is unnecessary.
- In Linux Docker against a disposable PostgreSQL 17 database named
  payments_test_deployment_review, locked Alembic upgrade and schema comparison
  passed, followed by all 13 lifecycle, populated-migration, Compose-profile and
  deployment workflow tests. Removed the test database container, its anonymous
  volume and network. No remote deployment or provider calls were performed.
- The existing combined-Compose-startup availability finding below remains
  unresolved; this review made no runtime or workflow changes.

## Direct Compose environment-file import, 2026-10-30

- Replaced the API's explicit setting list with `env_file`, using the single
  profile path selected by `COMPOSE_ENV_FILES`. Updated local commands and the
  inline deployment workflow. PostgreSQL still receives only its three database
  settings; application credentials are not injected into that container.
- All seven affected Compose/profile and deployment workflow tests passed in a
  Linux Docker container, including production without a development file,
  quoted dollar-containing values and the database environment boundary. Ruff
  lint/format checks and whitespace checks passed.
- Built the runtime image and smoke-tested the updated manifest under a unique
  disposable Compose project with dummy Sandbox settings and a fresh database.
  `config --services` returned only api/db; startup migrations, internal readiness,
  frontend HTTP 200 and literal dollar preservation inside the API passed.
  Removed the disposable containers, network and database volume. No provider
  call, live financial action or remote deployment was performed. The existing
  deployment review finding below remains unresolved.

## Single-step backend image installation, 2026-10-30

- Removed the dependency-only uv sync from the Dockerfile. One locked,
  non-editable production sync now installs dependencies and the application,
  explicitly reinstalling the local package to avoid stale cached application
  code. Builder stages, the runtime user and Uvicorn command are unchanged.
- The Docker build passed. A disposable Compose project with dummy Sandbox
  settings passed fresh database initialization, startup migrations, internal
  readiness and frontend HTTP 200. The running API retained UID/GID 10001 and
  contained neither the uv nor Node executable. Both real profile renders still
  contain only api/db; whitespace checks passed.
- Removed the disposable containers, network and database volume. No production
  deployment, server change or provider call was performed. The separately
  documented deployment review finding remains unresolved.

## Deployment review finding, 2026-10-30

- Confirmed a regression in the simplified combined Compose startup: Compose
  recreates the API before waiting for its database dependency to become healthy.
  A disposable two-service reproduction started an old API, then changed the API
  configuration and made the replacement database health check fail. Compose
  exited with status 1 and left the new API in created state; the old API had
  already been removed. The existing command-double tests and successful initial
  startup smoke test do not cover this upgrade failure.
- Restore the separate database up/wait and API up/no-deps steps so database
  startup failure does not replace the API. This is a review finding; the workflow
  has not yet been corrected. The reproduction used sleeping Python processes,
  no application data, and no provider calls. Its containers and network were
  removed; no server or deployment workflow was accessed.

## Simplified manual deployment workflow, 2026-10-30

- Reduced the workflow to checks followed by one production-environment job for
  build, publishing and deployment. Environment approval, when configured,
  precedes the build. The successful run summary records commit and image digest.
- Removed release directories, image.txt and current symlinks. One verified SSH
  session holds the server flock while validating the incoming manifest in a
  temporary file, pulling/tagging the immutable image, installing the single
  docker-compose.yml, starting Compose and checking internal readiness. Manifest
  replacement happens only after validation and image pull succeed. Temporary
  manifests and runner credentials are cleaned up. No automatic rollback occurs.
- Updated workflow tests for one-session transfer, deployment order, preflight
  failure, readiness failure, manual triggering and server lock contention.
  All 11 affected tests and five subtests passed in Linux Docker. Ruff lint and
  formatting, mypy, actionlint and whitespace checks passed.
- Ran the actual inline remote commands against a disposable local Compose
  project with dummy Sandbox settings and the existing built runtime image.
  Registry pull/tag were replaced with the existing local image and the project
  name/loopback port were isolated. Real Compose startup, database health,
  migrations, manifest installation and internal readiness passed. Removed all
  disposable containers, the network, database volume and temporary files.
  No GitHub deployment, SSH connection, server change or live financial action
  was performed. This local smoke check does not verify GHCR or SSH access.

## Commit validation and repository cleanup, 2026-10-30

- Updated README and changelog for multiple staff addresses, consistent list
  formatting, Pydantic validation, container-local readiness and deployment steps
  contained in the manual GitHub workflow. Removed the empty deployment/tests
  and deployment directories after their tracked scripts/tests were moved or removed.
- Reviewed both ignore files. Private profiles, local access credentials and old
  health keys remain excluded; .env.example remains tracked. Coverage artifacts
  are excluded from Git and Docker. Docker also excludes frontend browser tests
  and nested dependency/test-result directories. A private-value scan of all
  pending changes passed without displaying credentials.
- All 210 backend tests and five subtests passed against the dedicated
  payments_test_commit database. Ruff lint/format, mypy, Alembic upgrade/schema
  comparison, npm clean install/build/format, actionlint and the final Docker build
  passed. Both profile renders contain only api/db.
- A disposable Compose project started the rebuilt API and database, applied
  migrations, passed container-local readiness without an API key and served the
  frontend with HTTP 200. Its containers, network and volume were removed. No
  live provider calls, server changes or production deployments were performed.

## Consistent comma-separated lists, 2026-10-30

- ALLOWED_ORIGINS now uses the same comma-separated parsing as ALLOWED_IPS and
  FORWARDED_ALLOW_IPS. Pydantic still validates and normalizes each origin and
  requires at least one. Updated both local private profiles without changing
  their permitted origins, plus the example and deployment documentation.
- Both private profiles passed validation. One-off checks confirmed multiple
  origins, whitespace trimming and trailing-slash normalization, and rejection
  of empty lists, paths, empty entries and the previous JSON-array format.
  Ruff lint/format, mypy and the five existing settings/Compose tests passed in
  Linux Docker. Compose renders only api/db for both profiles. No deployment or
  remote profile changes were performed.

## Multiple allowed staff addresses, 2026-10-30

- Renamed ALLOWED_IP to ALLOWED_IPS in settings, authentication, Compose, local
  private profiles, the example and current deployment documentation. The value
  accepts comma-separated IPv4/IPv6 addresses; Pydantic validates every address.
  Empty still disables staff IP filtering only in Sandbox. Live staff access
  requires a nonempty list. The production profile retains 62.90.52.94.
- Verified staff login from both configured IPv4 addresses and a configured IPv6
  address, and rejection of an unlisted source with a spoofed forwarding header.
  Existing integration-key independence and private readiness checks passed.
- Ruff lint/format, mypy and all 19 affected authentication/settings/Compose
  tests passed in Linux Docker against payments_test_allowed_ips. Both private
  profiles passed Pydantic validation and contain exactly the 18 model fields;
  Compose renders only api/db for each profile. The disposable database and
  network were removed. No server files, proxy settings or deployments changed.

## Pydantic owns application configuration validation, 2026-10-29

- All 18 application profile parameters are now fields of Settings, including
  comma-separated Uvicorn proxy peers validated as IP addresses. Pydantic handles
  required fields, types, numeric bounds, HTTP callback URLs, normalized browser
  origins and credential-entry schemas. Staff hashes are parsed as encoded Argon2
  values, API-key digests require lowercase SHA-256 format and credential entries
  forbid extra fields. Unknown dotenv settings are rejected.
- Removed manual credential-shape loops and redundant callback-presence checks.
  Subscription payload construction explicitly serializes validated URL objects
  to strings, preserving the provider contract. Application-specific anonymous
  access and live-mode security rules remain Pydantic model validators.
- Removed tests for required fields, simple numeric/empty-value bounds, template
  metadata and the SQLAlchemy/SecretStr URL wrapper. The remaining settings tests
  exercise application-specific IP/cookie gates and environment selection.
  Compose profile-isolation and workflow failure tests remain because Pydantic
  cannot verify which file Compose injects or the order of deployment actions.
- Privately loaded both existing profiles through Settings and verified all 18
  names match the model/template. One-off malformed credential, URL, origin,
  proxy wildcard and unknown-setting checks were rejected without introducing
  field-by-field unit tests. No private assignment values were changed/displayed.
- All 206 backend tests and five subtests passed against the dedicated
  payments_test_settings_validation database. Ruff lint/format, mypy and the
  runtime/frontend image build passed. A disposable Compose project verified
  startup migrations, a database password with special characters, internal
  readiness and staff login with the validated settings. All test containers,
  networks and volumes were removed. No deployment, server change or PayPal
  call occurred; mocked tests do not establish live provider behavior.

## Redundant and cosmetic test review, 2026-10-29

- Inspected the backend test inventory and compared normalized bodies of all
  115 remaining test functions; no exact duplicate bodies were found. Reviewed
  overlapping settings, ownership, deployment and frontend scenarios separately,
  since structural uniqueness alone does not establish useful coverage.
- Removed the redundant ordinary-password URL case while retaining special
  character handling and secret masking. Folded internal readiness after ownership
  loss into the existing lifecycle test instead of repeating its setup.
- Removed comment-placement/style checks, field metadata assertions duplicated
  by missing-input validation, obsolete removed-setting/file assertions and
  redundant shell parsing already exercised by execution of the inline workflow.
  Kept manual-only workflow enforcement, failure ordering, profile isolation,
  loopback security, timeouts and provider/security boundary checks.
- Removed exact refund colors/margins/borders, duplicate visibility assertions,
  dashboard icon/button-size/card-position checks and obsolete caption checks.
  Environment checks now assert rendered Sandbox/Live text instead of CSS classes
  or a particular text-transform rule. Financial UI actions and data assertions
  remain covered.
- The full backend suite passed: 212 tests and five subtests against the dedicated
  payments_test_review database. Ruff lint/format and mypy passed. Its database
  container/network were removed. No production or application code changed in
  this review, and provider tests remain mocked rather than live verification.
- All six affected Chromium scenarios passed: dashboard filters/balances, refund
  eligibility/exact amounts/failure handling, and Sandbox/Live display. Frontend
  build and formatting passed. An initial run through an HTTP container hostname
  failed refund setup because crypto.randomUUID requires a secure context; the
  successful rerun used the supported localhost origin without application changes.
  The browser application used a fresh disposable database and dummy credentials;
  its containers/network/volume and temporary source copy were removed. No PayPal
  or production-server actions occurred.

## Deployment owned entirely by GitHub Actions, 2026-10-29

- Moved runner validation, temporary SSH credential handling, manifest transfer
  and all remote deployment commands inline into .github/workflows/deploy.yml.
  Removed standalone deployment scripts and moved their regression tests into
  backend/tests. Only the Compose manifest is transferred; remote commands are
  streamed directly from the workflow. The deployment folder is no longer part
  of the repository. The default target is /home/anton/rutv-payments-service.
- Preserved manual-only dispatch, CI gating, immutable image digests, pinned SSH
  host verification, workflow/server locks, private server settings, singleton API
  replacement and internal readiness before updating the successful-release link.
  Updated checks and deployment docs; removed the obsolete Ruff target override.
- All six workflow/Compose regression tests passed under pytest in Linux Docker.
  Tests execute the actual inline shell with Docker/SSH command doubles and verify
  success, readiness failure, preflight failure, manifest-only transfer, private
  SSH file cleanup and manual-only triggering. They never contact a real server.
  Actionlint, ShellCheck for both inline shell bodies, Ruff lint/format and mypy
  passed. The image build and disposable Compose smoke passed startup readiness
  and frontend HTTP access. Test resources were removed.
- No GitHub workflow was dispatched, no server files/services were changed and
  no payment deployment or PayPal calls occurred.

## Cloudflare automatic renewal verified, 2026-10-29

- Verified the supplied root-owned /etc/letsencrypt/cloudflare.ini token file
  without displaying its value and enforced mode 0600. Certbot reconfigure
  successfully tested staging issuance and saved dns-cloudflare authentication,
  the credential path and a 60-second DNS propagation wait for rutv.co.il.
- A second test using the saved settings passed with certbot renew --cert-name
  rutv.co.il --dry-run --run-deploy-hooks --no-random-sleep-on-renew
  --non-interactive. Its successful deploy hook validated and gracefully reloaded
  Nginx. An initial dry run had entered a 401-second random scheduling delay;
  that test process was gracefully stopped and rerun without the test delay.
- Confirmed certbot.timer is enabled/active and uses certbot renew. Future due
  renewals now automate DNS verification and reload Nginx without manual TXT
  entry. Archived and removed the redundant old lampac reload hook after both
  hooks passed the dry run; only the shared rutv-reload-nginx hook remains.
- Verified trusted TLS for root/www/lampac/payments after the test. The live
  certificate remains unchanged, expiring 2027-01-27 10:08:13 UTC; no staging
  certificate was installed. Nginx remained active. No payment deployment,
  container restart or PayPal call occurred. Earlier manual-renewal limitations
  below describe the previous setup and have now been resolved.

## Cloudflare automatic renewal preparation, 2026-10-29

- The user subsequently selected Cloudflare API authentication. Reinstalled
  python3-certbot-dns-cloudflare and created the empty root-owned credential file
  /etc/letsencrypt/cloudflare.ini with mode 0600. Existing certbot.timer and the
  successful-renewal Nginx reload hook remain configured.
- No DNS token is currently stored in the inspected credential locations.
  Renewal still uses manual authentication until the scoped token is supplied
  and Certbot reconfigure passes staging verification. No production certificate
  replacement, DNS change, payment deployment or container restart occurred.

## Automatic renewal preparation, 2026-10-29

- Confirmed certbot.timer is already enabled and active on hetzner2. The wildcard
  renewal configuration still uses manual authentication without a DNS auth hook,
  so the timer cannot currently renew it unattended.
- Installed /etc/letsencrypt/renewal-hooks/deploy/rutv-reload-nginx, mode 0750,
  to validate Nginx configuration and reload it after successful renewal. Shell
  syntax validation passed; the hook was not executed and no renewal was run.
- Automatic DNS verification remains pending the user's choice between a scoped
  Cloudflare DNS token and DNS challenge delegation, preserving the earlier
  instruction to avoid Cloudflare API use until explicitly changed. Renewal is
  not yet fully automatic; the existing certificate remains valid.

## Wildcard certificate issued and installed, 2026-10-29

- Issued a production Let's Encrypt certificate for rutv.co.il and *.rutv.co.il
  using manual DNS-01 verification, without the Cloudflare API/plugin. Both TXT
  challenge values were verified at the authoritative nameservers and public
  resolvers before submission. The certificate expires 2027-01-27 10:08:13 UTC.
  Manual DNS verification is required again for renewal; unattended renewal is
  not configured. The temporary challenge TXT records can now be removed.
- Stored protected configuration/certificate rollback backups in
  /root/rutv-tls-backup-20261029T110749Z and changed the existing rutv.co.il/www
  and lampac Nginx sites to the new shared Let's Encrypt certificate/key.
  An immediate initial post-reload TLS check failed and triggered configuration
  rollback. The new certificate chain independently verified against system CAs;
  a second switch with bounded reload-settling checks passed for all hostnames.
- Verified trusted TLS and hostname matching for rutv.co.il, www.rutv.co.il,
  lampac.rutv.co.il and payments.rutv.co.il. Existing root/www HTTP 200 and lampac
  HTTP 403 responses were preserved, including direct external HTTPS checks
  against 46.224.52.226 with hostname verification enabled.
- After checking active Nginx/systemd references and Docker bind mounts,
  removed the superseded lampac Certbot certificate/renewal and the unused
  Cloudflare certificate/key from their original paths. Protected rollback
  copies remain. An older inactive Nginx backup was retained unchanged.
  Nginx syntax validation passed after cleanup; Certbot now lists only the
  root/wildcard certificate. Removed the unused Cloudflare DNS plugin.
- No payment-service deployment, container restart or PayPal call occurred.
  A payment-specific Nginx route is still required before deployment, and the
  production application URLs currently remain configured with the server IP.

## Wildcard certificate preparation, 2026-10-29

- Authoritative DNS for rutv.co.il is hosted by Cloudflare. No Certbot DNS
  credentials were found in the inspected Certbot/secrets directories, and
  the existing lampac certificate renews through HTTP webroot validation.
- Installed Ubuntu's python3-certbot-dns-cloudflare plugin and its dependencies
  on hetzner2; Certbot confirmed the plugin is available. Nginx configuration
  syntax validation still passed. No certificate was issued or removed and
  no Nginx configuration was changed. Issuance/automated renewal of rutv.co.il
  plus *.rutv.co.il awaits a restricted Cloudflare DNS token for that zone.
  Existing certificates must stay in place until their replacement is installed
  and verified. The payment service remains undeployed.

## Container-local deployment readiness, 2026-10-29

- Replaced the host's authenticated health probe with docker compose exec -T api
  python -m app.readiness. Removed the deployment readiness-key requirement and
  host-side Python helper. API_KEYS may remain empty when staff is configured;
  no additional credential file or environment parameter is needed.
- The container probe calls /internal/ready through 127.0.0.1:8000, sharing the
  existing database and sticky singleton-ownership checks in the running API.
  Only container loopback connections without forwarding headers may use this
  route. The existing /health/ready route still requires authentication, and
  the internal route is excluded from the API schema. No second app process,
  migration run, financial dispatch or ownership reacquisition is introduced.
- Thirty relevant backend tests and five subtests passed against the dedicated
  payments_test_readiness database. All four deployment-helper tests, Ruff
  lint/format, mypy and ShellCheck passed. Both Compose profiles still contain
  only api/db, and the image build passed.
- A disposable Docker Compose project with API_KEYS={} and a staff restriction
  passed the actual container readiness command. Public health access, peer
  container access and forged forwarded-loopback requests were rejected.
  Terminating its database ownership connection caused /internal/ready to return
  503 and the deployment probe to fail. All disposable smoke/test resources
  were removed. No existing service restart, remote change, deployment or
  PayPal call occurred. Earlier readiness-key requirements below are historical.

## Hetzner2 provisioning and ingress inspection, 2026-10-29

- Created /home/anton/rutv-payments-service with mode 0700 and copied the private
  production profile there with mode 0600. Local/remote SHA-256 comparison
  confirmed an identical copy; no secret values or hashes were printed.
- Read-only inspection confirmed Docker Engine 29.3.1, Compose v5.1.1, the
  deployment user's Docker access and an unused loopback port 8100. The payment
  service is not running. Host Nginx configuration syntax validation passed,
  but no payment upstream/site is configured.
- Existing configured certificates serve lampac.rutv.co.il and rutv.co.il,
  not the configured payment IP. A verified TLS connection for 46.224.52.226
  failed with an IP mismatch; both configured certificate files also failed
  OpenSSL's check for that IP. Existing HTTPS setup is unsuitable for the
  payment profile's IP-based origin and return/cancel URLs.
- A subsequent read-only certificate/SNI inventory confirmed the Let's Encrypt
  certificate covers only lampac.rutv.co.il and expires 2026-12-20 08:59:01 UTC.
  The certificate configured for rutv.co.il/www.rutv.co.il has subject CN=Cloudflare,
  no subject alternative names, and extended key usage TLS Web Client
  Authentication. It expires 2041-03-28 08:55:00 UTC and fails hostname checks
  for rutv.co.il, www.rutv.co.il and payments.rutv.co.il. It is unsuitable as a
  matching HTTPS server certificate for those names. Actual local Nginx SNI
  handshakes confirmed these configured certificates are served. Neither has
  an IP subject alternative name; an IP-address handshake serves the lampac
  certificate. No Nginx or certificate changes were made.
- The copied profile still has no trusted proxy peers or integration API keys.
  Before deployment, configure the payment Nginx site, a matching trusted TLS
  certificate, verified forwarded-header handling and explicit proxy peer trust.
  Preserve application authentication and the staff IP restriction.
- The new directory has no readiness API-key file and the deployment user has
  no Docker registry configuration; private GHCR pulls need authentication.
  GitHub DEPLOY_PATH must target /home/anton/rutv-payments-service, overriding
  the workflow's older default path.
- Only the requested directory and private profile were created remotely.
  Nginx, certificates, Docker services and local profiles were unchanged; no
  workflow, deployment or PayPal call was performed.

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
