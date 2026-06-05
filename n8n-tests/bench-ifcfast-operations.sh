#!/usr/bin/env bash
# Benchmark POST /ifcfast operations on a large IFC (wall clock + RQ execution time).
#
# Usage:
#   ./n8n-tests/bench-ifcfast-operations.sh
#   IFC_BENCH_SKIP_HEAVY=1 ./n8n-tests/bench-ifcfast-operations.sh   # skip mesh/extract_all
#   IFC_BENCH_FILE=/path/to/model.ifc ./n8n-tests/bench-ifcfast-operations.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

: "${IFC_PIPELINE_API_KEY:?Set IFC_PIPELINE_API_KEY in .env or env}"
API="${IFC_PIPELINE_API_URL:-http://localhost:8000}"
SKIP_HEAVY="${IFC_BENCH_SKIP_HEAVY:-0}"

DEFAULT_IFC="${ROOT}/ifc-coord/reports/nobel_mep_eval/ifc_coord_elec_vs_plumb_rerun/work/nobel_elec_vs_plumb/P1_2b_BIM_XXX_5000_00.ifc"
IFC_PATH="${IFC_BENCH_FILE:-$DEFAULT_IFC}"

if [ ! -f "$IFC_PATH" ]; then
  echo "error: IFC not found: $IFC_PATH" >&2
  exit 1
fi

BASENAME=$(basename "$IFC_PATH")
IFC_BYTES=$(stat -c%s "$IFC_PATH")
IFC_MB=$(python3 -c "print(f'{$IFC_BYTES / (1024*1024):.1f}')")
UPLOAD_NAME="${BASENAME}"

AUTH=(-H "X-API-Key: ${IFC_PIPELINE_API_KEY}")

pyjson() { python3 -c "$1"; }

enqueue() {
  local body="$1"
  local resp http
  resp=$(curl -sS -w '\n%{http_code}' "${AUTH[@]}" -H 'Content-Type: application/json' \
    -d "$body" "$API/ifcfast")
  http=$(echo "$resp" | tail -n1)
  resp=$(echo "$resp" | sed '$d')
  if [ "$http" -lt 200 ] || [ "$http" -ge 300 ]; then
    echo "enqueue failed HTTP $http: $resp" >&2
    return 1
  fi
  echo "$resp" | pyjson 'import json,sys; print(json.load(sys.stdin)["job_id"])'
}

poll_until_done() {
  local jid="$1"
  while true; do
    local body status
    body=$(curl -fsS "${AUTH[@]}" "$API/jobs/${jid}/status")
    status=$(echo "$body" | pyjson 'import json,sys; print(json.load(sys.stdin).get("status",""))')
    case "$status" in
      finished|failed|stopped|canceled) echo "$body"; return 0 ;;
    esac
    sleep 2
  done
}

run_op() {
  local label="$1" body="$2"
  local t0 jid out status wall rq rows extra
  t0=$(date +%s.%N)
  jid=$(enqueue "$body")
  out=$(poll_until_done "$jid")
  local t1
  t1=$(date +%s.%N)
  wall=$(python3 -c "print(f'{$t1 - $t0:.2f}')")
  status=$(echo "$out" | pyjson 'import json,sys; print(json.load(sys.stdin).get("status",""))')
  rq=$(echo "$out" | pyjson 'import json,sys; d=json.load(sys.stdin); print(d.get("execution_time_seconds") or "")')
  rows=$(echo "$out" | pyjson '
import json, sys
d = json.load(sys.stdin)
r = d.get("result") or {}
if r.get("element_count") is not None:
    print(r["element_count"])
elif r.get("rows") is not None:
    print(r["rows"])
elif isinstance(r.get("artifacts"), list):
    print(len(r["artifacts"]))
elif isinstance(r.get("inline"), dict) and r["inline"].get("product_count") is not None:
    print(r["inline"]["product_count"])
else:
    print("")
')
  extra=$(echo "$out" | pyjson '
import json, sys
d = json.load(sys.stdin)
r = d.get("result") or {}
inline = r.get("inline") or {}
if inline.get("parse_seconds") is not None:
    print(f"parse={inline[\"parse_seconds\"]:.2f}s")
elif r.get("artifacts"):
    print(f"artifacts={len(r[\"artifacts\"])}")
else:
    print("")
' 2>/dev/null || true)
  if [ "$status" != "finished" ]; then
    err=$(echo "$out" | pyjson 'import json,sys; d=json.load(sys.stdin); print((d.get("result") or {}).get("error") or d.get("exc_info") or d)' 2>/dev/null || echo "$out")
    printf '%s|FAIL|%s|%s||%s\n' "$label" "$wall" "$rq" "$err"
    return 0
  fi
  printf '%s|OK|%s|%s|%s|%s\n' "$label" "$wall" "$rq" "$rows" "$extra"
}

echo "========================================"
echo "ifcfast operations benchmark"
echo "========================================"
echo "API:     $API"
echo "IFC:     $IFC_PATH (${IFC_MB} MiB)"
echo "Upload:  $UPLOAD_NAME"
echo "Skip heavy: $SKIP_HEAVY"
echo ""

curl -fsS "${AUTH[@]}" "$API/health" | pyjson '
import json, sys
d = json.load(sys.stdin)
print("ifcfast_queue:", (d.get("services") or {}).get("ifcfast_queue", "?"))
' || true

echo ""
echo ">>> Upload (skip if already present)"
if ! curl -fsS "${AUTH[@]}" -o /dev/null -w '%{http_code}' "$API/files/${UPLOAD_NAME}" 2>/dev/null | grep -q 200; then
  curl -fsS "${AUTH[@]}" -F "file=@${IFC_PATH};filename=${UPLOAD_NAME}" "$API/upload/ifc" | pyjson '
import json,sys; d=json.load(sys.stdin); print("  stored:", d.get("filename") or d)
' || curl -fsS "${AUTH[@]}" -F "file=@${IFC_PATH}" "$API/upload/ifc" >/dev/null
else
  echo "  (assuming $UPLOAD_NAME already in uploads/)"
fi

# Sample GUID for traverse (first product from summary job)
echo ""
echo ">>> Warm-up: summary (for traverse guid)"
SUM_BODY=$(python3 -c "import json; print(json.dumps({'filename': '$UPLOAD_NAME', 'operation': 'summary'}))")
SUM_OUT=$(poll_until_done "$(enqueue "$SUM_BODY")")
TRAVERSE_GUID=$(echo "$SUM_OUT" | pyjson '
import json, sys
d = json.load(sys.stdin)
r = d.get("result") or {}
# try inline sample_guid or first product from prior export — fallback empty
inline = r.get("inline") or {}
g = inline.get("sample_product_guid") or inline.get("sample_guid")
print(g or "")
' 2>/dev/null || true)

if [ -z "$TRAVERSE_GUID" ]; then
  TRAVERSE_GUID=$(python3 <<PY
import json, os, sys
try:
    import ifcfast
    m = ifcfast.open("$IFC_PATH")
    df = m.products_df
    print(str(df.iloc[0]["guid"]) if len(df) else "")
except Exception:
    print("")
PY
)
fi
echo "  traverse guid: ${TRAVERSE_GUID:-<none>}"

RESULTS_FILE=$(mktemp)
FN="$UPLOAD_NAME"

body() { python3 -c "import json; print(json.dumps($1))"; }

# label|json body fragments built in python for escaping
run_and_log() {
  local label="$1"
  shift
  local b
  b=$(python3 - "$FN" "$@" <<'PY'
import json, sys
fn = sys.argv[1]
args = sys.argv[2:]
# args: key=value pairs
d = {"filename": fn}
for a in args:
    k, v = a.split("=", 1)
    if v in ("true", "false"):
        d[k] = v == "true"
    elif v.isdigit():
        d[k] = int(v)
    else:
        d[k] = v
print(json.dumps(d))
PY
)
  echo ">>> $label"
  run_op "$label" "$b" | tee -a "$RESULTS_FILE"
}

# Core ops
run_and_log "export_products" operation=export_products output_filename=bench_export_products.csv output_format=csv
run_and_log "summary" operation=summary
run_and_log "schemas" operation=schemas
run_and_log "types" operation=types
run_and_log "type_summary" operation=type_summary
run_and_log "type_bank" operation=type_bank
run_and_log "export_layer:psets" operation=export_layer layer=psets output_filename=bench_psets.parquet output_format=parquet
run_and_log "export_layer:quantities" operation=export_layer layer=quantities output_filename=bench_quantities.parquet output_format=parquet
run_and_log "export_layer:materials" operation=export_layer layer=materials output_filename=bench_materials.csv output_format=csv
run_and_log "preview:products" operation=preview preview_table=products preview_n=50
run_and_log "by_type:IfcWall" operation=by_type entity_type=IfcWall output_filename=bench_walls.csv output_format=csv
run_and_log "filter_products" operation=filter_products filter_entity=IfcWall filter_mode=type output_filename=bench_filter_walls.csv output_format=csv

if [ -n "$TRAVERSE_GUID" ]; then
  b=$(python3 -c "import json; print(json.dumps({'filename':'$FN','operation':'traverse','traverse':'parent','guid':'$TRAVERSE_GUID'}))")
  echo ">>> traverse:parent"
  run_op "traverse:parent" "$b" | tee -a "$RESULTS_FILE"
fi

if [ "$SKIP_HEAVY" = "1" ]; then
  echo ""
  echo "(Skipped heavy ops: extract_all, mesh_qto, point_cloud, meshes_summary — set IFC_BENCH_SKIP_HEAVY=0 to include)"
else
  run_and_log "extract_all" operation=extract_all output_format=parquet output_prefix=bench_all
  run_and_log "meshes_summary" operation=meshes_summary output_filename=bench_meshes_summary.json output_format=json
  run_and_log "mesh_qto" operation=mesh_qto output_filename=bench_mesh_qto.parquet output_format=parquet
  run_and_log "point_cloud" operation=point_cloud output_filename=bench_point_cloud.parquet output_format=parquet point_cloud_max_points=50000
fi

echo ""
echo "========================================"
echo "Results (${IFC_MB} MiB) — wall | RQ exec | rows/artifacts"
echo "========================================"
column -t -s'|' < "$RESULTS_FILE" 2>/dev/null || cat "$RESULTS_FILE"
rm -f "$RESULTS_FILE"
