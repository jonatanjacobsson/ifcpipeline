#!/usr/bin/env bash
# Publish Redis, Postgres, and SeaweedFS S3 on PIPELINE_LAN_IP for remote workers.
# Run on the primary host from the ifcpipeline repo root.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "error: missing .env in $ROOT" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

WORKER_HOST="${WORKER_HOSTNAME:-worker-host}"
DETECTED_LAN="$(bash "$ROOT/scripts/detect-pipeline-lan-ip.sh" 2>/dev/null || true)"
DETECTED_WORKER="$(getent hosts "$WORKER_HOST" 2>/dev/null | awk '{print $1}' | head -1)"

sync_env_ip() {
  local key="$1" val="$2"
  [[ -z "$val" ]] && return 0
  if grep -q "^${key}=" .env 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" .env
  else
    echo "${key}=${val}" >>.env
  fi
  export "$key=$val"
  echo "==> ${key}=${val}"
}

if [[ -n "$DETECTED_LAN" ]]; then
  if [[ "${PIPELINE_LAN_IP:-}" != "$DETECTED_LAN" ]]; then
    echo "==> Updating stale PIPELINE_LAN_IP (${PIPELINE_LAN_IP:-unset} -> ${DETECTED_LAN})"
    sync_env_ip PIPELINE_LAN_IP "$DETECTED_LAN"
  fi
fi
if [[ -n "$DETECTED_WORKER" && "${WORKER_VM_IP:-}" != "$DETECTED_WORKER" ]]; then
  echo "==> Updating WORKER_VM_IP (${WORKER_VM_IP:-unset} -> ${DETECTED_WORKER})"
  sync_env_ip WORKER_VM_IP "$DETECTED_WORKER"
fi

: "${PIPELINE_LAN_IP:?Set PIPELINE_LAN_IP in .env (or ensure worker hostname resolves)}"
WORKER_VM_IP="${WORKER_VM_IP:-$DETECTED_WORKER}"

if ! ip -4 addr show | grep -q "inet ${PIPELINE_LAN_IP}/"; then
  echo "error: ${PIPELINE_LAN_IP} is not assigned on this host (LAN IP may have changed)." >&2
  echo "  Detected route IP: ${DETECTED_LAN:-unknown}" >&2
  echo "  Run: ./scripts/detect-pipeline-lan-ip.sh" >&2
  exit 1
fi

echo "==> Recreating redis, postgres, seaweedfs with host-lan bindings on ${PIPELINE_LAN_IP}"
docker compose \
  -f docker-compose.control-plane.yml \
  -f docker-compose.host-lan.yml \
  up -d redis postgres seaweedfs

echo ""
echo "==> Listening ports (expect ${PIPELINE_LAN_IP} for 6379, 5432, 8333):"
ss -tlnp 2>/dev/null | grep -E ':6379|:5432|:8333' || true

echo ""
if [[ -n "$WORKER_VM_IP" ]]; then
  echo "==> Suggested ufw rules (worker only):"
  echo "sudo ufw allow from ${WORKER_VM_IP} to any port 6379 proto tcp"
  echo "sudo ufw allow from ${WORKER_VM_IP} to any port 5432 proto tcp"
  echo "sudo ufw allow from ${WORKER_VM_IP} to any port 8333 proto tcp"
  echo ""
  echo "==> Regenerate worker .env.remote (from primary):"
  echo "  ./scripts/deploy-remote-workers-from-primary.sh"
  echo "  # or set REDIS_URL=redis://${PIPELINE_LAN_IP}:6379/0 on the worker"
else
  echo "==> Set WORKER_VM_IP in .env for ufw hints."
fi

echo ""
echo "==> From worker host:"
echo "redis-cli -h ${PIPELINE_LAN_IP} ping"
echo "nc -z ${PIPELINE_LAN_IP} 8333 && echo SeaweedFS OK"
