#!/usr/bin/env bash
# Pull -> migrate -> up --wait -> caddy reload -> prune. Shared by CD and `make deploy`.
# Usage: IMAGE_TAG=<tag> ./deploy.sh
set -euo pipefail
cd "$(dirname "$0")"

: "${IMAGE_TAG:?IMAGE_TAG must be set}"

dc() { docker compose -f compose.prod.yaml "$@"; }

# Replace or append KEY=VALUE in .env using a temp file + mv.
set_env() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp .env.XXXXXX)"
  if grep -q "^${key}=" .env; then
    grep -v "^${key}=" .env > "$tmp" || true
  else
    cat .env > "$tmp"
  fi
  printf '%s=%s\n' "$key" "$value" >> "$tmp"
  chmod 600 "$tmp"
  mv "$tmp" .env
}

PREVIOUS_IMAGE_TAG="$(grep '^IMAGE_TAG=' .env | tail -n1 | cut -d= -f2- || true)"
if [ -n "$PREVIOUS_IMAGE_TAG" ]; then
  set_env PREVIOUS_IMAGE_TAG "$PREVIOUS_IMAGE_TAG"
fi
set_env IMAGE_TAG "$IMAGE_TAG"

dc pull
dc up -d --wait db
# Migrate BEFORE the new api starts so readyz passes on first boot.
dc run --rm api alembic upgrade head
dc up -d --wait --wait-timeout 240 --remove-orphans
# Bind-mounted Caddyfile edits are otherwise never applied.
dc exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
docker image prune -f

echo "Deployed IMAGE_TAG=${IMAGE_TAG}. Rollback: IMAGE_TAG=${PREVIOUS_IMAGE_TAG:-<previous>} ./deploy.sh"
