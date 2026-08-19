#!/usr/bin/env bash
# One-shot migration: adopt existing locally built images under their new names.
#
# Before prebuilt GHCR images existed, Compose tagged every built image
# `ifcpipeline-<service>:latest` (project name + service). Now that the services
# carry an explicit `image:` key, Compose looks for
# `${IFCPIPELINE_REGISTRY}/ifcpipeline-<service>:${IFCPIPELINE_TAG}` instead.
#
# On a host that has been building from source, those two names point at the same
# work but Compose cannot see it — so the first `docker compose up` after pulling
# this change would rebuild all 12 images from scratch (10-30 minutes) even with
# IFCPIPELINE_PULL_POLICY unset/`build`. Retagging is instant and makes the
# transition a no-op.
#
# Safe to re-run; it only adds tags, never deletes or overwrites source images.
# Run once on the primary (and on the worker VM) after pulling this change.
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

SERVICES=(
  ifc5d-worker ifcpatch-worker ifcconvert-worker ifcclash-worker
  ifccsv-worker ifcfast-worker ifctester-worker ifcdiff-worker
  ifc2json-worker topologicpy-worker api-gateway ifc-classifier
)

adopted=0
for svc in "${SERVICES[@]}"; do
  old="ifcpipeline-${svc}:latest"
  new="$(image_ref "$svc")"
  if [[ "$old" == "$new" ]]; then
    continue
  fi
  if ! docker image inspect "$old" >/dev/null 2>&1; then
    echo "skip ${svc}: no local ${old}"
    continue
  fi
  if docker image inspect "$new" >/dev/null 2>&1; then
    echo "skip ${svc}: ${new} already present"
    continue
  fi
  docker tag "$old" "$new"
  echo "adopted ${old} -> ${new}"
  adopted=$((adopted + 1))
done

# ifccoord-worker keeps its original name (never published) — nothing to do.
echo "==> Done (${adopted} retagged). Verify with: docker compose config | grep image:"
