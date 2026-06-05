#!/usr/bin/env bash
# List compose project volumes for a before/after migration check on the primary host.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PROJECT="${COMPOSE_PROJECT_NAME:-ifcpipeline}"
echo "==> Compose project: ${PROJECT}"
docker compose ls 2>/dev/null || true
echo ""
echo "==> Named volumes (expect postgres-data, minio-data, redis-data under ${PROJECT}_*)"
docker volume ls --format '{{.Name}}' | grep -E "${PROJECT}|postgres|minio|redis" | sort || true
echo ""
echo "==> Postgres container name"
docker compose -f docker-compose.control-plane.yml config 2>/dev/null \
  | grep -E 'container_name:|postgres-data' || true
