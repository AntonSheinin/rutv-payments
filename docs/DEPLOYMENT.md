# Manual production deployment

`.github/workflows/deploy.yml` runs only through **Actions → Deploy production →
Run workflow**. It does not deploy on pushes, tags, or completion of Checks.
The workflow must exist on the default branch before GitHub exposes manual runs.
It deploys the commit selected in the Run workflow branch selector. The existing
Checks workflow runs first, including backend tests, migration checks and the
frontend build/format checks; it does not certify live PayPal behavior.

The workflow publishes `ghcr.io/antonsheinin/rutv-payments:sha-<commit>` and deploys
its resolved SHA-256 digest. It sends only the Compose file and deployment scripts
over SSH. Production credentials are not build arguments, image contents, or
GitHub deployment artifacts.

## GitHub configuration

Create the `production` environment. Its deployment job needs the following settings:

| Name | Kind | Value |
| --- | --- | --- |
| `DEPLOY_SSH_PRIVATE_KEY` | Secret | Dedicated unencrypted CI SSH private key; authorize its public key on the server |
| `DEPLOY_HOST` | Variable | Reachable server IPv4 address or DNS hostname; the local `hetzner2` SSH alias is not available on GitHub |
| `DEPLOY_USER` | Variable | Server deployment user |
| `DEPLOY_PORT` | Variable | SSH port; defaults to `22` |
| `DEPLOY_PATH` | Variable | Dedicated directory; defaults to `/home/anton/paypal-payment-service` |
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

- Install Docker Engine, current Docker Compose (2.24.4 or newer), Python 3.10+,
  Bash, tar and flock. Allow the dedicated user to run the deployment's Docker
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
ALLOWED_IP=62.90.52.94
COOKIE_SECURE=true
PAYPAL_ENVIRONMENT=live
LOG_LEVEL=INFO
FORWARDED_ALLOW_IPS=the-verified-proxy-peer-ip
```

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

Create a separate high-entropy deployment health API token. Add its SHA-256 hash
as a named entry in `API_KEYS`, and put the raw token alone in
`<DEPLOY_PATH>/.health-api-key` with mode `0600`. This credential is used only by
the host's authenticated readiness GET, never transferred to GitHub or printed.
It has the same access as other integration keys; there are no special roles.
Peer services receive their own raw API tokens and call
`https://<production-hostname>/api/v1/...`. Use the same HTTPS endpoint from any
Docker network; an unauthenticated public request never gains business API access.

## Deployment and investigation

GitHub and a server-side flock serialize deployments. Each run creates
`releases/<commit>-<run-id>-<attempt>` containing the exact manifests/scripts and
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
Both services restart after a host reboot. The probe waits for authenticated `/health/ready` to confirm
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
cd /home/anton/paypal-payment-service
docker compose --project-name rutv-payments --project-directory "$PWD" \
  --env-file .env.prod \
  -f current/docker-compose.yml ps
```

Use the corresponding failed release's paths instead of `current` after failure.
Rollback requires reviewing database migration compatibility, pulling a compatible
previous digest from its release's `image.txt`, and explicitly tagging it as
`rutv-payments:production` before recreating the API. No deployment has been run merely by
adding this workflow.
