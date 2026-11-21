#!/usr/bin/env bash
# Memory and cold-start measurement for docs/decisions/0000-hosting-and-memory.md.
# Run on the host from /srv/ranker.
# Usage: measure-memory.sh {snapshot <label>|embeddings [N]|coldstart|images|all}
set -euo pipefail
cd "$(dirname "$0")"

dc() { docker compose -f compose.prod.yaml "$@"; }

# Read only PUBLIC_HOSTNAME from .env, never other variables.
public_host() {
  grep '^PUBLIC_HOSTNAME=' .env | tail -n1 | cut -d= -f2-
}

snapshot() {
  local label="${1:-snapshot}"
  echo "== ${label} =="
  docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}'
  echo
  echo "-- host RSS (MB) --"
  ps -eo rss,args | grep -E 'uvicorn|ranker\.worker|next-server|node server\.js|postgres|caddy' \
    | grep -v grep | awk '{printf "%.1f MB  ", $1/1024; $1=""; print substr($0,1,100)}' || true
  echo
  echo "-- cgroup memory.peak (MB) --"
  local id peak path
  for id in $(docker ps -q); do
    path="/sys/fs/cgroup/system.slice/docker-$(docker inspect -f '{{.Id}}' "$id").scope/memory.peak"
    if [ -r "$path" ]; then
      peak="$(cat "$path")"
      printf '%s  %.1f MB\n' "$(docker inspect -f '{{.Name}}' "$id")" "$(awk -v p="$peak" 'BEGIN{print p/1048576}')"
    fi
  done
  echo
  free -m
  echo
}

embeddings() {
  local n="${1:-100}" host i
  host="$(public_host)"
  for ((i = 0; i < n; i++)); do
    curl -fsS "https://${host}/api/readyz" >/dev/null
  done
  echo "ran ${n} readyz calls (one embedding each)"
}

wait_200() {
  local url="$1"
  until [ "$(curl -s -o /dev/null -w '%{http_code}' "$url")" = "200" ]; do
    sleep 0.25
  done
}

coldstart() {
  local host start t_health t_ready
  host="$(public_host)"
  start="$(date +%s.%N)"
  dc up -d --force-recreate api
  wait_200 "https://${host}/api/healthz"
  t_health="$(date +%s.%N)"
  wait_200 "https://${host}/api/readyz"
  t_ready="$(date +%s.%N)"
  awk -v s="$start" -v h="$t_health" -v r="$t_ready" \
    'BEGIN{printf "cold start: healthz %.2f s, readyz %.2f s\n", h-s, r-s}'
}

images() {
  docker image ls --format '{{.Repository}}:{{.Tag}} {{.Size}}' | grep ranker || true
}

cmd="${1:-all}"
case "$cmd" in
  snapshot) snapshot "${2:-snapshot}" ;;
  embeddings) embeddings "${2:-100}" ;;
  coldstart) coldstart ;;
  images) images ;;
  all)
    snapshot "after-startup"
    embeddings 100
    snapshot "after-100-embeddings"
    coldstart
    images
    ;;
  *) echo "Usage: $0 {snapshot <label>|embeddings [N]|coldstart|images|all}" >&2; exit 2 ;;
esac
