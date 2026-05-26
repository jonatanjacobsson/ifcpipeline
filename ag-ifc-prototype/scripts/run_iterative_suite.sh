#!/usr/bin/env bash
# Iterative clash → fix → AG verify → re-clash until pass
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

# Models + ifcopenshell
if ! "${PYTHON}" -c "import ifcclash" 2>/dev/null; then
  pip install -r requirements-ifc.txt
fi
./scripts/fetch_ifc_models.sh >/dev/null 2>&1 || true

echo "[run_iterative_suite] starting iterative clash resolution evaluation..."
"${PYTHON}" -m ag_ifc.run_iterative_suite "$@"
