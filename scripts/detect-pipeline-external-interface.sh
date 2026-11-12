#!/usr/bin/env bash
# Print the NIC used to reach the worker VM (for DOCKER-USER firewall scoping).
# Never prints a Docker bridge (see scripts/lib/host-lan.sh): firewall rules scoped
# to one would leave the LAN NIC unfiltered.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/host-lan.sh
source "$ROOT/scripts/lib/host-lan.sh"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

WORKER_HOST="${WORKER_HOSTNAME:-bimbotw1}"
worker_ip="${WORKER_VM_IP:-}"
if [[ -z "$worker_ip" ]]; then
  worker_ip="$(getent hosts "$WORKER_HOST" 2>/dev/null | awk '{print $1}' | head -1 || true)"
fi

if [[ -n "$worker_ip" ]]; then
  lan_dev_toward "${worker_ip}/32"
  exit 0
fi

# Fallback: default route interface.
dev="$(default_route_dev)"
if [[ -z "$dev" ]] || is_docker_bridge_dev "$dev"; then
  exit 1
fi
echo "$dev"
