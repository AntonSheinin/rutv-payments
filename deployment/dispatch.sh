#!/usr/bin/env bash
# Run on the GitHub runner; runtime credentials never leave the server.
set -euo pipefail

: "${DEPLOY_HOST:?Set DEPLOY_HOST}"
: "${DEPLOY_USER:?Set DEPLOY_USER}"
: "${DEPLOY_PORT:?Set DEPLOY_PORT}"
: "${DEPLOY_PATH:?Set DEPLOY_PATH}"
: "${DEPLOY_KNOWN_HOSTS:?Set DEPLOY_KNOWN_HOSTS}"
: "${DEPLOY_SSH_PRIVATE_KEY:?Set DEPLOY_SSH_PRIVATE_KEY}"
: "${IMAGE_REF:?Set IMAGE_REF}"

# SSH constructs a remote shell command. Validate every interpolated argument.
[[ "$DEPLOY_HOST" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]]
[[ "$DEPLOY_USER" =~ ^[a-zA-Z_][a-zA-Z0-9_-]*$ ]]
[[ "$DEPLOY_PORT" =~ ^[0-9]{1,5}$ ]]
(( 10#$DEPLOY_PORT >= 1 && 10#$DEPLOY_PORT <= 65535 ))
[[ "$DEPLOY_PATH" =~ ^/[a-zA-Z0-9_/-]+$ && "$DEPLOY_PATH" != / ]]
[[ "$IMAGE_REF" =~ ^ghcr\.io/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$ ]]
[[ "$GITHUB_SHA" =~ ^[a-f0-9]{40}$ ]]
[[ "$GITHUB_RUN_ID" =~ ^[0-9]+$ && "$GITHUB_RUN_ATTEMPT" =~ ^[0-9]+$ ]]

umask 077
credentials=$(mktemp -d)
trap 'rm -rf -- "$credentials"' EXIT
printf '%s\n' "$DEPLOY_SSH_PRIVATE_KEY" > "$credentials/key"
printf '%s\n' "$DEPLOY_KNOWN_HOSTS" > "$credentials/known_hosts"
unset DEPLOY_SSH_PRIVATE_KEY
ssh_options=(
  -p "$DEPLOY_PORT" -i "$credentials/key"
  -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes
  -o "UserKnownHostsFile=$credentials/known_hosts"
  -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=3
)
release="$DEPLOY_PATH/releases/$GITHUB_SHA-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"

# Send only deployment code/manifests, never the checkout's dotenv or login files.
# These client-side expansions are intentional; every remote argument was validated above.
# shellcheck disable=SC2029
tar -czf - docker-compose.yml deployment/deploy.sh deployment/readiness.py |
  ssh "${ssh_options[@]}" "$DEPLOY_USER@$DEPLOY_HOST" \
    "set -eu; umask 077; test -f '$DEPLOY_PATH/.env.prod'; test -f '$DEPLOY_PATH/.health-api-key'; mkdir -p '$release'; tar -xzf - --no-same-owner -C '$release'; bash '$release/deployment/deploy.sh' '$DEPLOY_PATH' '$IMAGE_REF'"
