#!/usr/bin/env bash
# Build remote worker images on the primary host (if needed) and load them on the worker host.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

REMOTE_SSH="${REMOTE_SSH:-deploy@worker-host}"
SSH_OPTS=(-o RemoteCommand=none -o RequestTTY=no)

WORKERS=(ifctester-worker ifcpatch-worker ifcclash-worker ifcdiff-worker)
IMAGES=()
for w in "${WORKERS[@]}"; do
  IMAGES+=("ifcpipeline-${w}:latest")
done

missing=()
for w in "${WORKERS[@]}"; do
  if ! docker image inspect "ifcpipeline-${w}:latest" >/dev/null 2>&1; then
    missing+=("$w")
  fi
done
if ((${#missing[@]})); then
  echo "==> Building on primary: ${missing[*]}"
  docker compose -f docker-compose.workers.yml build "${missing[@]}"
fi

echo "==> Stream images to ${REMOTE_SSH} (~2.2 GB uncompressed; may take a few minutes)"
ssh "${SSH_OPTS[@]}" "$REMOTE_SSH" 'docker load' < <(docker save "${IMAGES[@]}")

echo "==> Images on worker:"
ssh "${SSH_OPTS[@]}" "$REMOTE_SSH" \
  "docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' | grep -E 'ifcpipeline-(ifctester|ifcpatch|ifcclash|ifcdiff)-worker'"

echo "==> Done. On worker: SKIP_BUILD=1 ./scripts/start-remote-workers.sh"
