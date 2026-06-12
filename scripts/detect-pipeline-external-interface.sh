#!/usr/bin/env bash
# Print the NIC used to reach the worker VM (for DOCKER-USER firewall scoping).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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
  ip route get "${worker_ip}/32" 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "dev") { print $(i + 1); exit }}'
  exit 0
fi

# Fallback: default route interface.
ip route show default 2>/dev/null | awk '{print $5; exit}'
