#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
./scripts/setup_ag2.sh
./scripts/fetch_ifc_models.sh
python3 -m ag_ifc.run_ag_suitability_eval "$@"
