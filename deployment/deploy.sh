#!/usr/bin/env bash
# Run on Ubuntu via SSH, using the existing server-only production configuration.
set -euo pipefail
umask 077

deploy_path=$(realpath -e "${1:?Supply deployment directory}")
image_ref=${2:?Supply immutable image reference}
release=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
[[ "$release" == "$deploy_path/releases/"* ]]
[[ "$image_ref" =~ ^ghcr\.io/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$ ]]
test -f "$deploy_path/.env.prod"
test -s "$deploy_path/.health-api-key"
[[ ! -e "$deploy_path/current" || -L "$deploy_path/current" ]]

# Serialize manual server invocations as well as GitHub dispatches.
exec 9>"$deploy_path/.deploy.lock"
flock -n 9 || { echo 'Another deployment is running' >&2; exit 1; }
printf '%s\n' "$image_ref" > "$release/image.txt"
compose=(docker compose --project-name rutv-payments --project-directory "$deploy_path"
  --env-file "$deploy_path/.env.prod"
  -f "$release/docker-compose.yml")

# Validate without emitting the resolved configuration, which contains secrets.
"${compose[@]}" config --quiet
"${compose[@]}" config --services
docker pull "$image_ref"
docker tag "$image_ref" rutv-payments:production
"${compose[@]}" up -d --no-build --wait --wait-timeout 90 db
# Compose stops/replaces the old API; never start a parallel owner or run migrations twice.
"${compose[@]}" up -d --no-build --no-deps api
python3 "$release/deployment/readiness.py" "$deploy_path/.health-api-key"

# Keep the last successful release pointer on failure. Never automatically roll back migrations.
ln -sfnT "$release" "$deploy_path/.current-next"
mv -Tf "$deploy_path/.current-next" "$deploy_path/current"
printf 'Deployment ready: %s\n' "$image_ref"
