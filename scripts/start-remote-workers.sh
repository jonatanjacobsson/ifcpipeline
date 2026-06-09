#!/usr/bin/env bash
# Start ifctester / ifcpatch / ifcclash / ifcdiff workers on a worker host.
# Run from ifcpipeline repo root with .env.remote present (not primary .env).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ENV_REMOTE="${ENV_REMOTE:-.env.remote}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.remote-workers.yml}"
export COMPOSE_PROFILES="${COMPOSE_PROFILES:-remote}"

if [[ -f .env && ! -f "$ENV_REMOTE" ]]; then
  echo "error: $ENV_REMOTE missing — use primary .env only on the primary host." >&2
  echo "  On worker host: copy .env.remote.example to .env.remote" >&2
  exit 1
fi

if [[ ! -f "$ENV_REMOTE" ]]; then
  echo "error: missing $ENV_REMOTE (copy from .env.remote.example)" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_REMOTE"
set +a

: "${PIPELINE_HOST:?Set PIPELINE_HOST in $ENV_REMOTE}"
: "${POSTGRES_USER:?Set POSTGRES_USER in $ENV_REMOTE}"

redis_host() {
  if [[ -n "${PIPELINE_LAN_IP:-}" ]]; then
    echo "$PIPELINE_LAN_IP"
    return
  fi
  if [[ -n "${REDIS_URL:-}" ]]; then
    python3 -c 'import os, urllib.parse; u=urllib.parse.urlparse(os.environ["REDIS_URL"]); print(u.hostname or "")' 2>/dev/null && return
  fi
  echo "$PIPELINE_HOST"
}

preflight() {
  local rh
  rh="$(redis_host)"
  echo "==> Preflight Redis/Postgres/SeaweedFS at ${rh} (PIPELINE_HOST=${PIPELINE_HOST})"
  local ok=1
  if command -v redis-cli >/dev/null 2>&1; then
    if redis-cli -h "$rh" ping 2>/dev/null | grep -q PONG; then
      echo "Redis: PONG"
    else
      echo "error: Redis not reachable at ${rh}:6379" >&2
      echo "  On primary run: ./scripts/apply-host-lan-access.sh" >&2
      echo "  Then update .env.remote: REDIS_URL=redis://<primary-lan-ip>:6379/0" >&2
      ok=0
    fi
  else
    echo "warn: redis-cli not installed; skipping Redis ping"
  fi
  if command -v pg_isready >/dev/null 2>&1; then
    if pg_isready -h "$rh" -p "${POSTGRES_PORT:-5432}" -U "$POSTGRES_USER" >/dev/null 2>&1; then
      echo "Postgres: ready"
    else
      echo "error: Postgres not reachable at ${rh}:${POSTGRES_PORT:-5432}" >&2
      ok=0
    fi
  else
    echo "warn: pg_isready not installed; skipping Postgres check"
  fi
  if command -v nc >/dev/null 2>&1; then
    if nc -z "$rh" 8333 2>/dev/null; then
      echo "SeaweedFS S3: OK (${rh}:8333)"
    else
      echo "error: SeaweedFS S3 not reachable at ${rh}:8333" >&2
      ok=0
    fi
  elif curl -sf -o /dev/null "http://${rh}:8333" 2>/dev/null; then
    echo "SeaweedFS S3: OK (${rh}:8333)"
  else
    echo "error: SeaweedFS S3 health failed at http://${rh}:8333" >&2
    ok=0
  fi
  if [[ "$ok" != "1" ]]; then
    exit 1
  fi
  echo "preflight OK"
}

preflight

REMOTE_SERVICES=(ifctester-worker ifcpatch-worker ifcclash-worker ifcdiff-worker)

UP_FLAGS=(-d)
if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
  UP_FLAGS+=(--build)
  echo "==> Building and starting remote workers (profile=${COMPOSE_PROFILES})"
else
  echo "==> Starting remote workers (SKIP_BUILD=1, profile=${COMPOSE_PROFILES})"
fi

SCALE_ARGS=()
for svc_var in \
  "ifctester-worker:IFCTESTER_REMOTE_REPLICAS" \
  "ifcpatch-worker:IFCPATCH_REMOTE_REPLICAS" \
  "ifcclash-worker:IFCCLASH_REMOTE_REPLICAS" \
  "ifcdiff-worker:IFCDIFF_REMOTE_REPLICAS"; do
  svc="${svc_var%%:*}"
  var="${svc_var#*:}"
  n="${!var:-1}"
  SCALE_ARGS+=(--scale "${svc}=${n}")
done
echo "==> Scale: ${SCALE_ARGS[*]}"

docker compose -f "$COMPOSE_FILE" --env-file "$ENV_REMOTE" up "${UP_FLAGS[@]}" "${SCALE_ARGS[@]}" "${REMOTE_SERVICES[@]}"

echo "==> Status"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_REMOTE" ps

echo "==> Recent logs"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_REMOTE" logs --tail=25 "${REMOTE_SERVICES[@]}"
