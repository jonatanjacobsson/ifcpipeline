#!/usr/bin/env bash
# Start control plane only (no workers) on the primary host, with LAN publish overlay.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "error: missing .env in $ROOT (required on primary host)" >&2
  exit 1
fi

echo "==> Syncing reboot-safe host-LAN bindings"
bash "$ROOT/scripts/apply-host-lan-access.sh"

echo ""
echo "==> Starting control plane (no workers)"
echo "    WARNING: never run 'docker compose down -v' on the primary host."

docker compose \
  -f docker-compose.control-plane.yml \
  -f docker-compose.host-lan.yml \
  up -d "$@"

echo "==> Status"
docker compose -f docker-compose.control-plane.yml -f docker-compose.host-lan.yml ps
