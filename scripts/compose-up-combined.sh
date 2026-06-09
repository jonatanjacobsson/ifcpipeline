#!/usr/bin/env bash
# Start full stack (control plane + all workers) on the primary host.
# Never use: docker compose down -v  (deletes postgres-data, seaweedfs-data, redis-data).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "error: missing .env in $ROOT (required on primary host)" >&2
  exit 1
fi

echo "==> Starting combined ifcpipeline stack (control plane + workers)"
echo "    WARNING: never run 'docker compose down -v' on the primary host."

docker compose up -d "$@"

echo "==> Status"
docker compose ps
