#!/usr/bin/env bash
# Clone AlphaGeometry2 into vendor/ for local DDAR evaluation.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR="${ROOT}/vendor/alphageometry2"
REF="${AG2_GIT_REF:-main}"

if [[ -d "${VENDOR}/.git" ]]; then
  echo "[setup_ag2] vendor/alphageometry2 already present"
  exit 0
fi

mkdir -p "${ROOT}/vendor"
echo "[setup_ag2] cloning google-deepmind/alphageometry2 (${REF})..."
git clone --depth 1 --branch "${REF}" \
  https://github.com/google-deepmind/alphageometry2.git "${VENDOR}"

echo "[setup_ag2] done → ${VENDOR}"
