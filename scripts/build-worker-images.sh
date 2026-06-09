#!/usr/bin/env bash
# Build worker images on the primary host (repo root, .env required).
# guid-index-worker is defined in docker-compose.control-plane.yml — build with:
#   docker compose -f docker-compose.control-plane.yml build guid-index-worker
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "error: missing .env in $ROOT" >&2
  exit 1
fi

ALL_WORKERS=(
  ifc5d-worker ifcpatch-worker ifcconvert-worker ifcclash-worker
  ifccsv-worker ifcfast-worker ifctester-worker ifcdiff-worker ifc2json-worker
  ifcfrag-worker ifccoord-worker topologicpy-worker
)
PRIMARY_LOCAL_WORKERS=(
  ifc5d-worker ifcconvert-worker ifccsv-worker ifcfast-worker ifc2json-worker
  ifcfrag-worker ifccoord-worker topologicpy-worker
)
REMOTE_WORKERS=(ifctester-worker ifcpatch-worker ifcclash-worker ifcdiff-worker)

TARGET="${WORKER_BUILD_TARGET:-all}"
case "$TARGET" in
  all) WORKERS=("${ALL_WORKERS[@]}") ;;
  primary) WORKERS=("${PRIMARY_LOCAL_WORKERS[@]}") ;;
  remote) WORKERS=("${REMOTE_WORKERS[@]}") ;;
  *)
    echo "error: WORKER_BUILD_TARGET must be 'all', 'primary', or 'remote' (got: $TARGET)" >&2
    exit 1
    ;;
esac

echo "==> Building worker images: ${WORKERS[*]}"
docker compose -f docker-compose.workers.yml build "${WORKERS[@]}"
echo "==> Done"
