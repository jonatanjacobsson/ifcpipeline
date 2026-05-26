#!/usr/bin/env bash
# Download open-source IFC models listed in scenarios/ifc_models/manifest.json
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

PYTHON="${PYTHON:-python3}"
if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

echo "[fetch_ifc_models] Downloading PCERT building + infrastructure models..."
"${PYTHON}" << 'PY'
from ag_ifc.ifc_models import load_manifest, resolve_model_path

manifest = load_manifest()
for model_set in manifest["model_sets"]:
    if model_set.get("optional") and model_set.get("lfs_required"):
        print(f"  skip optional LFS set: {model_set['id']} (clone + git lfs pull locally)")
        continue
    print(f"  set: {model_set['id']}")
    for entry in model_set["files"]:
        path = resolve_model_path(model_set, entry["filename"], fetch=True)
        if path:
            print(f"    OK {entry['filename']} ({path.stat().st_size} bytes)")
        else:
            print(f"    FAIL {entry['filename']}")
PY

echo "[fetch_ifc_models] Model status:"
"${PYTHON}" -m ag_ifc.run_ifc_scenarios --list-models
