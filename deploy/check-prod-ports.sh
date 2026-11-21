#!/usr/bin/env bash
# Asserts only caddy publishes ports in compose.prod.yaml and db uses the PG18 volume path.
set -euo pipefail

src_dir="$(cd "$(dirname "$0")" && pwd)"
tmp="$(mktemp -d)"
cleanup() {
  rm -f "$tmp/compose.prod.yaml" "$tmp/.env"
  rmdir "$tmp"
}
trap cleanup EXIT
cp "$src_dir/compose.prod.yaml" "$tmp/compose.prod.yaml"
: > "$tmp/.env"

rendered="$(GHCR_OWNER=x IMAGE_TAG=t PUBLIC_HOSTNAME=h ACME_EMAIL=e \
  POSTGRES_USER=u POSTGRES_PASSWORD=p POSTGRES_DB=d \
  docker compose -f "$tmp/compose.prod.yaml" --env-file /dev/null config --format json)"

owners="$(jq -r '.services | to_entries[] | select(.value.ports != null and (.value.ports|length) > 0) | .key' <<<"$rendered")"
if [ "$owners" != "caddy" ]; then
  echo "FAIL: services publishing ports: '${owners}' (expected exactly 'caddy')" >&2
  exit 1
fi
if ! jq -e '.services.db.volumes[0].target == "/var/lib/postgresql"' <<<"$rendered" >/dev/null; then
  echo "FAIL: db volume target is not /var/lib/postgresql" >&2
  exit 1
fi
echo "OK: only caddy publishes ports; db volume is /var/lib/postgresql"
echo "$owners"
