#!/usr/bin/env bash
# Build remote worker images on the primary host (if needed) and load them on the worker host.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

REMOTE_SSH="${REMOTE_SSH:-deploy@worker-host}"
SSH_OPTS=(-o RemoteCommand=none -o RequestTTY=no)

WORKERS=(ifctester-worker ifcpatch-worker ifcclash-worker ifcdiff-worker ifccoord-worker topologicpy-worker)

missing=()
for w in "${WORKERS[@]}"; do
  if ! docker image inspect "ifcpipeline-${w}:latest" >/dev/null 2>&1; then
    missing+=("$w")
  fi
done
if ((${#missing[@]})); then
  echo "==> Building on primary: ${missing[*]}"
  if ! docker compose -f docker-compose.yml build "${missing[@]}"; then
    echo "warn: build failed for: ${missing[*]} — will push only images already on primary" >&2
  fi
fi

IMAGES=()
for w in "${WORKERS[@]}"; do
  if docker image inspect "ifcpipeline-${w}:latest" >/dev/null 2>&1; then
    IMAGES+=("ifcpipeline-${w}:latest")
  else
    echo "warn: skipping push for ${w} (not on primary; worker keeps existing image if any)" >&2
  fi
done
if ((${#IMAGES[@]} == 0)); then
  echo "error: no worker images on primary to push" >&2
  exit 1
fi

echo "==> Stream images to ${REMOTE_SSH}: ${IMAGES[*]}"
echo "    (~2 GB uncompressed; may take a few minutes)"
ssh "${SSH_OPTS[@]}" "$REMOTE_SSH" 'docker load' < <(docker save "${IMAGES[@]}")

echo "==> Images on worker:"
ssh "${SSH_OPTS[@]}" "$REMOTE_SSH" \
  "docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' | grep -E 'ifcpipeline-(ifctester|ifcpatch|ifcclash|ifcdiff|ifccoord|topologicpy)-worker'"

echo "==> Done. On worker: SKIP_BUILD=1 ./scripts/start-remote-workers.sh"
