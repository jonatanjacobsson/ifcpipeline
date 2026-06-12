#!/usr/bin/env bash
# Publish Redis, Postgres, and SeaweedFS on reboot-safe host ports and sync the
# detected primary LAN IP into .env for remote worker configuration.
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

WORKER_HOST="${WORKER_HOSTNAME:-bimbotw1}"
DETECTED_LAN="$(bash "$ROOT/scripts/detect-pipeline-lan-ip.sh" 2>/dev/null || true)"
DETECTED_WORKER="$(getent hosts "$WORKER_HOST" 2>/dev/null | awk '{print $1}' | head -1 || true)"
DETECTED_WORKER="${DETECTED_WORKER:-${WORKER_VM_IP:-}}"

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
    echo "==> Syncing PIPELINE_LAN_IP for workers (${PIPELINE_LAN_IP:-unset} -> ${DETECTED_LAN})"
    sync_env_ip PIPELINE_LAN_IP "$DETECTED_LAN"
  fi
fi
if [[ -n "$DETECTED_WORKER" && "${WORKER_VM_IP:-}" != "$DETECTED_WORKER" ]]; then
  echo "==> Syncing WORKER_VM_IP (${WORKER_VM_IP:-unset} -> ${DETECTED_WORKER})"
  sync_env_ip WORKER_VM_IP "$DETECTED_WORKER"
fi

WORKER_VM_IP="${WORKER_VM_IP:-$DETECTED_WORKER}"

echo "==> Ensuring redis, postgres, seaweedfs use reboot-safe host port bindings"
docker compose \
  -f docker-compose.control-plane.yml \
  -f docker-compose.host-lan.yml \
  up -d redis postgres seaweedfs

echo ""
echo "==> Listening ports (6379, 5432, 8333 on all interfaces; filer UI loopback-only):"
ss -tlnp 2>/dev/null | grep -E ':6379|:5432|:8333' || true

if [[ -n "${PIPELINE_LAN_IP:-}" ]]; then
  echo ""
  echo "==> Worker reachability check (current primary LAN IP ${PIPELINE_LAN_IP}):"
  echo "redis-cli -h ${PIPELINE_LAN_IP} ping"
  echo "nc -z ${PIPELINE_LAN_IP} 8333 && echo SeaweedFS OK"
fi

if [[ -n "$WORKER_VM_IP" ]]; then
  echo ""
  echo "==> After primary IP changes, refresh worker config:"
  echo "  ./scripts/deploy-remote-workers-from-primary.sh"
fi

if [[ $EUID -eq 0 ]]; then
  bash "$ROOT/scripts/apply-host-lan-firewall.sh"
elif sudo -n true 2>/dev/null; then
  sudo -n bash "$ROOT/scripts/apply-host-lan-firewall.sh"
else
  echo ""
  echo "==> Run manually (needs root): sudo ./scripts/apply-host-lan-firewall.sh"
fi
