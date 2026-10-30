# Manual production deployment

`.github/workflows/deploy.yml` runs only through **Actions → Deploy production →
Run workflow**. It does not deploy on pushes, tags, or completion of Checks.
The workflow must exist on the default branch before GitHub exposes manual runs.
It deploys the commit selected in the Run workflow branch selector. The existing
Checks workflow runs first, including backend tests, migration checks and the
frontend build/format checks; it does not certify live PayPal behavior.

The workflow publishes `ghcr.io/antonsheinin/rutv-payments:sha-<commit>` and deploys
its resolved SHA-256 digest. All deployment commands are defined inline in this
workflow, including validation, image publishing, SSH transfer, container
replacement and readiness. Only the Compose file is copied to the release;
server commands are streamed over SSH. Production credentials are not build arguments, image contents, or
GitHub deployment artifacts.

## GitHub configuration

Create the `production` environment. Its deployment job needs the following settings:

| Name | Kind | Value |
| --- | --- | --- |
| `DEPLOY_SSH_PRIVATE_KEY` | Secret | Dedicated unencrypted CI SSH private key; authorize its public key on the server |
| `DEPLOY_HOST` | Variable | Reachable server IPv4 address or DNS hostname; the local `hetzner2` SSH alias is not available on GitHub |
| `DEPLOY_USER` | Variable | Server deployment user |
| `DEPLOY_PORT` | Variable | SSH port; defaults to `22` |
| `DEPLOY_PATH` | Variable | Dedicated directory; defaults to `/home/anton/rutv-payments-service` |
| `DEPLOY_KNOWN_HOSTS` | Variable | Verified OpenSSH known-hosts entry for the host/port |

Host verification is mandatory. Obtain the host key through your existing trusted
server connection; do not accept an unverified key discovered during deployment.
For a nonstandard port, use the `[host]:port` known-hosts form. Deployment paths
support letters, digits, underscores, hyphens and slashes, without spaces.
Only the deployment job receives the SSH secret. The publisher uses the automatic
`GITHUB_TOKEN` with `packages: write`; no publishing PAT is required.

## Server provisioning (separate from deployment)

Provision the following once on Ubuntu. The workflow does not create users, change
SSH/firewalls/Nginx, configure DNS/TLS, or
generate/replace production secrets.

- Install Docker Engine, current Docker Compose (2.24.4 or newer),
  Bash and flock. Allow the dedicated user to run the deployment's Docker
  commands. Docker access grants control over the host; choose this user accordingly.
- Create the dedicated deployment directory, writable by that user. Store its
  production `.env.prod` with mode `0600` and keep backups separately protected.
  The shared Compose file uses the settings selected by `--env-file .env.prod`;
  no development profile or server-side source checkout is needed. Deployment
  always uses `--no-build` to run the prebuilt image.
- Authenticate that user's Docker client to GHCR if the package is private,
  using an account with package read access and a PAT classic with `read:packages`.
  The server retains these pull credentials; GitHub does not send a registry token
  over SSH. Public images can be pulled anonymously.
- Configure host Nginx HTTPS access to `127.0.0.1:8100`. Other services use this
  public HTTPS API with their own Bearer keys; no shared Docker network is needed.
  Do not apply a server-wide staff IP allowlist to `/api/v1/` or `/health/`:
  their authentication is enforced by the application, with valid API keys allowed
  from any source. Browser login, cookies and static routes retain the staff IP
  restriction. Nginx may additionally restrict UI paths to the staff IP.
  Restore the real visitor IP first if Cloudflare proxies the hostname. Overwrite
  forwarded headers; configure only the actual proxy peer address as trusted.
- Establish PostgreSQL backups and verify a restore before using live payments.

The shared certificate on hetzner2 covers `rutv.co.il` and `*.rutv.co.il`.
Certbot renewal uses Cloudflare DNS verification with the root-only token file
`/etc/letsencrypt/cloudflare.ini`; keep this credential outside Git and the
payment service's environment. `certbot.timer` schedules renewal, and
`/etc/letsencrypt/renewal-hooks/deploy/rutv-reload-nginx` validates and reloads
Nginx after success. To test saved renewal settings and the reload hook:

```sh
sudo certbot renew --cert-name rutv.co.il --dry-run --run-deploy-hooks \
  --no-random-sleep-on-renew --non-interactive
```

Required server `.env.prod` secrets are `POSTGRES_PASSWORD`, `APP_SECRET`,
`PAYPAL_CLIENT_SECRET`, `AUTH_USERS` (Argon2 hashes) and `API_KEYS` (SHA-256 hashes).
Keep production database storage and signing secrets separate from Sandbox. Keep
single quotes around JSON values containing Argon2 dollar signs.

`POSTGRES_PASSWORD` is the only database parameter. The API builds its connection
to `db:5432` as user `payments`, database `payments`, using that same password.
There is no `DATABASE_URL` env parameter to maintain or escape. A randomly
generated 24-character alphanumeric password is sufficient for this deployment.
Preserve the password after database initialization; changing a populated
database requires an explicit PostgreSQL password change as well as the env update.

Every field in `backend/app/settings.py` is required. `.env.example` documents
the complete parameter set, shared by `.env.dev` and `.env.prod`. Compose
injects the selected file; the application does not implicitly load a profile or
provide fallback values. Private profiles contain parameter assignments only;
explanatory comments stay in `.env.example`. The prepared `.env.prod` has separate
random database and signing secrets, the live environment and the staff IP. The configured
PayPal credentials are populated in both profiles as requested. Fill production
account hashes, HTTPS URLs and trusted proxy settings privately before deployment;
validation rejects the incomplete profile. The development profile explicitly
disables staff IP filtering for local development.

Production settings also include:

```dotenv
ALLOWED_IPS=62.90.52.94
COOKIE_SECURE=true
PAYPAL_ENVIRONMENT=live
LOG_LEVEL=INFO
FORWARDED_ALLOW_IPS=the-verified-proxy-peer-ip
```

`ALLOWED_IPS` accepts comma-separated IPv4/IPv6 addresses. Every listed address
may access the staff interface. An explicitly empty list disables this restriction
in Sandbox; live staff access requires at least one address.
`ALLOWED_ORIGINS` and `FORWARDED_ALLOW_IPS` use the same comma-separated format.
Origins must include scheme, host and optional port, for example
`ALLOWED_ORIGINS=https://payments.rutv.co.il,http://localhost:8100`.
At least one origin is required; each origin is validated individually.

Set the production PayPal client ID, merchant ID, exact HTTPS `ALLOWED_ORIGINS`,
and return/cancel URLs. PAYPAL_ENVIRONMENT is the single mode selector: sandbox
uses the test API and live enables real payments. Verify the merchant integration
before using live mode. The frontend always lives at
`/service/frontend/dist`; this image layout is fixed in code.
Do not use `*` or trust other application containers as proxy peers.

The staff IP restriction needs the original visitor address. Behind Nginx, the
container sees the proxy's connection address, not 62.90.52.94. Nginx must replace
`X-Forwarded-For` with its verified `$remote_addr`, and set `X-Forwarded-Proto` to
`$scheme`; Uvicorn accepts those headers only from `FORWARDED_ALLOW_IPS`. This setting
is the proxy peer IP as seen inside Docker, not the allowed staff IP. Empty is
correct for development without a proxy, but production browser access needs the
verified proxy address. API-key authentication does not depend on the staff IP.

Deployment readiness needs no API key or credential file. It runs
`docker compose exec -T api python -m app.readiness` inside the API container.
The probe calls `/internal/ready` on container loopback, checking the running
process's database access and singleton ownership. Non-loopback connections and
forwarded requests are rejected; `/health/ready` remains authenticated.
`API_KEYS='{}'` is valid when staff accounts are configured and integrations are
unused. When needed, peer services receive their own raw API tokens and call
`https://<production-hostname>/api/v1/...`. Use the same HTTPS endpoint from any
Docker network; an unauthenticated public request never gains business API access.

## Deployment and investigation

GitHub and a server-side flock serialize deployments. Each run creates
`releases/<commit>-<run-id>-<attempt>` containing the Compose manifest and
image digest in `image.txt`. Deployment pulls that digest and tags it locally as
`rutv-payments:production`, the fixed image name in production Compose. No image
selection env parameter or generated env file is needed. Compose uses the fixed
project name `rutv-payments`, preserving
`rutv-payments_payments-db` between deployments. Do not rename this project or
adopt an existing differently named database volume without an explicit data
migration. The workflow never deletes volumes or prunes images.

The server validates Compose without printing resolved secrets, pulls the image,
waits for PostgreSQL health, and replaces the single API container. Application
startup applies migrations under singleton ownership. There is a short API outage
during replacement, with up to 150 seconds allowed for graceful API shutdown.
Both services restart after a host reboot. The container probe confirms
database/ownership readiness; it never calls PayPal. Success updates the `current`
symlink to the release. The workflow does not verify public DNS/TLS/Nginx access
or merchant capabilities.

Failure stops the workflow without automatically rolling back migrations or
replaying financial actions. The running API may already be the new release even
if readiness failed; `current` remains the last successful release. Identify the
failed run's release directory and inspect it explicitly. Keep logs/configuration
private; never print `docker compose config` without `--quiet`/`--services`.

To inspect a successful deployment on the server:

```sh
cd /home/anton/rutv-payments-service
docker compose --project-name rutv-payments --project-directory "$PWD" \
  --env-file .env.prod \
  -f current/docker-compose.yml ps
```

Use the corresponding failed release's paths instead of `current` after failure.
Rollback requires reviewing database migration compatibility and manually running
the GitHub Actions workflow for the chosen compatible commit. Container replacement
remains owned by the workflow. Adding or editing this workflow does not deploy.
