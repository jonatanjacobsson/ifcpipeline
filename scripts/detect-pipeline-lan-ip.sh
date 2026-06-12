#!/usr/bin/env bash
# Print the primary host IP used to reach the worker VM (for PIPELINE_LAN_IP / .env.remote).
set -euo pipefail

WORKER_HOST="${WORKER_HOSTNAME:-worker-host}"
worker_ip="${WORKER_VM_IP:-}"
if [[ -z "$worker_ip" ]]; then
  worker_ip="$(getent hosts "$WORKER_HOST" 2>/dev/null | awk '{print $1}' | head -1 || true)"
fi
if [[ -z "$worker_ip" ]]; then
  echo "error: cannot resolve worker host ($WORKER_HOST); set WORKER_VM_IP" >&2
  exit 1
fi
ip route get "$worker_ip" 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "src") { print $(i + 1); exit }}'
