#!/usr/bin/env bash
# HTTPS smoke test: readyz, pgvector/embedding checks, cookie round-trip, redirect.
# Usage: smoke.sh <host>
set -euo pipefail

host="${1:?usage: smoke.sh <host>}"
fail=0
tmp="$(mktemp -d)"
cleanup() {
  rm -f "$tmp/readyz.json" "$tmp/jar" "$tmp/ping1.json" "$tmp/ping2.json"
  rmdir "$tmp"
}
trap cleanup EXIT

check() {
  local name="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "PASS: $name"
  else
    echo "FAIL: $name"
    fail=1
  fi
}

curl -fsS --retry 12 --retry-delay 5 --retry-all-errors "https://$host/api/readyz" -o "$tmp/readyz.json" || true
check "readyz status ok" jq -e '.status == "ok"' "$tmp/readyz.json"
check "pgvector ok and embedding dim 384" jq -e '.checks.pgvector.ok and .checks.embedding.dim == 384' "$tmp/readyz.json"

curl -fsS -c "$tmp/jar" -b "$tmp/jar" "https://$host/api/session/ping" -o "$tmp/ping1.json" || true
curl -fsS -c "$tmp/jar" -b "$tmp/jar" "https://$host/api/session/ping" -o "$tmp/ping2.json" || true
check "cookie round-trip" jq -e '.cookie_received == true' "$tmp/ping2.json"

http_code="$(curl -sS -o /dev/null -w '%{http_code}' "http://$host/" || true)"
if [ "$http_code" = "301" ] || [ "$http_code" = "308" ]; then
  echo "PASS: http redirects to https ($http_code)"
else
  echo "FAIL: http redirect (got $http_code)"
  fail=1
fi

https_code="$(curl -sS -o /dev/null -w '%{http_code}' "https://$host/" || true)"
if [ "$https_code" = "200" ]; then
  echo "PASS: https / returns 200"
else
  echo "FAIL: https / (got $https_code)"
  fail=1
fi

exit "$fail"
