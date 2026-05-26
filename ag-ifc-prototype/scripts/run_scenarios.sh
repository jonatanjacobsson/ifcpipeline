#!/usr/bin/env bash
# Run the full AEC × AlphaGeometry2 scenario matrix.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

if [[ ! -d vendor/alphageometry2 ]]; then
  ./scripts/setup_ag2.sh
fi

PYTHON="${PYTHON:-python3}"
if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

ARGS=(--generate)
for arg in "$@"; do
  ARGS+=("$arg")
done

echo "[run_scenarios] generating + running scenario matrix..."
"${PYTHON}" -m ag_ifc.run_scenarios "${ARGS[@]}"
