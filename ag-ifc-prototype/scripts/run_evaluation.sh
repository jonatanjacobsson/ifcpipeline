#!/usr/bin/env bash
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

WITH_IFC=0
for arg in "$@"; do
  case "${arg}" in
    --with-ifc) WITH_IFC=1 ;;
  esac
done

mkdir -p reports
ARGS=(--report reports/eval_report.json)
if [[ "${WITH_IFC}" -eq 1 ]]; then
  ARGS+=(--with-ifc)
fi

echo "[run_evaluation] starting harness..."
"${PYTHON}" -m ag_ifc.evaluate "${ARGS[@]}"
echo "[run_evaluation] report → reports/eval_report.json"
