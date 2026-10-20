#!/usr/bin/env bash
# Print the primary host IP used to reach the worker VM (for PIPELINE_LAN_IP / .env.remote).
# Never prints a Docker bridge address (see scripts/lib/host-lan.sh); exits 1 instead,
# so callers keep the previous value.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/host-lan.sh
source "$ROOT/scripts/lib/host-lan.sh"

WORKER_HOST="${WORKER_HOSTNAME:-worker-host}"
worker_ip="${WORKER_VM_IP:-}"
if [[ -z "$worker_ip" ]]; then
  worker_ip="$(getent hosts "$WORKER_HOST" 2>/dev/null | awk '{print $1}' | head -1 || true)"
fi
if [[ -z "$worker_ip" ]]; then
  echo "error: cannot resolve worker host ($WORKER_HOST); set WORKER_VM_IP" >&2
  exit 1
fi
if ! lan_ip_toward "$worker_ip"; then
  echo "error: no non-Docker LAN address toward $worker_ip" >&2
  exit 1
fi
