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

# shellcheck source=lib/images.sh
source "$ROOT/scripts/lib/images.sh"

REMOTE_SSH="${REMOTE_SSH:-deploy@worker-host}"
SSH_OPTS=(-o RemoteCommand=none -o RequestTTY=no)

WORKERS=(ifctester-worker ifcpatch-worker ifcclash-worker ifcdiff-worker ifccoord-worker topologicpy-worker)

missing=()
for w in "${WORKERS[@]}"; do
  if ! docker image inspect "$(image_ref "$w")" >/dev/null 2>&1; then
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
  if docker image inspect "$(image_ref "$w")" >/dev/null 2>&1; then
    IMAGES+=("$(image_ref "$w")")
  else
    echo "warn: skipping push for ${w} (not on primary; worker keeps existing image if any)" >&2
  fi
done
if ((${#IMAGES[@]} == 0)); then
  echo "error: no worker images on primary to push" >&2
  exit 1
fi

# Skip what the worker already has: the same tag created at the same moment is the same
# image. Image IDs cannot be compared -- a worker on Docker's containerd image store
# reports a different ID for an identical image -- so the creation time is the key.
TO_PUSH=()
for ref in "${IMAGES[@]}"; do
  local_created="$(docker image inspect --format '{{.Created}}' "$ref")"
  remote_created="$(ssh "${SSH_OPTS[@]}" "$REMOTE_SSH" "docker image inspect --format '{{.Created}}' '${ref}' 2>/dev/null" || true)"
  if [[ -n "$remote_created" && "$remote_created" == "$local_created" ]]; then
    echo "==> ${ref}: already on the worker (created ${local_created}), not pushed"
  else
    TO_PUSH+=("$ref")
  fi
done

if ((${#TO_PUSH[@]} == 0)); then
  echo "==> The worker has every image already"
else
  # Refuse up front rather than let `docker load` stop halfway with "no space left on
  # device": the image sizes plus 1 GiB must fit on the worker's Docker disk.
  # FORCE_PUSH=1 skips the check (shared layers can make the real need smaller).
  need=$((1024 * 1024 * 1024))
  for ref in "${TO_PUSH[@]}"; do
    need=$((need + $(docker image inspect --format '{{.Size}}' "$ref")))
  done
  avail="$(ssh "${SSH_OPTS[@]}" "$REMOTE_SSH" \
    'df -P -B1 "$(docker info --format "{{.DockerRootDir}}")" | awk "NR==2 {print \$4}"' || true)"
  if [[ "${FORCE_PUSH:-0}" != "1" && "$avail" =~ ^[0-9]+$ ]] && ((avail < need)); then
    echo "error: the worker has $((avail / 1024 / 1024)) MiB free for Docker, about $((need / 1024 / 1024)) MiB is needed for: ${TO_PUSH[*]}" >&2
    echo "  Free space there first (e.g. 'docker image prune -f'), or set FORCE_PUSH=1 to try anyway." >&2
    exit 1
  fi
  echo "==> Stream images to ${REMOTE_SSH}: ${TO_PUSH[*]}"
  echo "    (may take a few minutes)"
  ssh "${SSH_OPTS[@]}" "$REMOTE_SSH" 'docker load' < <(docker save "${TO_PUSH[@]}")
fi

echo "==> Images on worker:"
ssh "${SSH_OPTS[@]}" "$REMOTE_SSH" \
  "docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' | grep -E 'ifcpipeline-(ifctester|ifcpatch|ifcclash|ifcdiff|ifccoord|topologicpy)-worker'"

echo "==> Done. On worker: SKIP_BUILD=1 ./scripts/start-remote-workers.sh"
