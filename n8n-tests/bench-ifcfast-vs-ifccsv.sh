#!/usr/bin/env bash
# Benchmark ifcfast-worker vs ifccsv-worker on the same IFC export job.
#
# Usage:
#   IFC_BENCH_FILE=/path/to/model.ifc ./n8n-tests/bench-ifcfast-vs-ifccsv.sh
#   IFC_BENCH_QUERY=IfcElement IFC_BENCH_RUNS=2 ./n8n-tests/bench-ifcfast-vs-ifccsv.sh
#
# Requires: curl, python3, stack with ifcfast-worker + ifccsv-worker + api-gateway.
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
RUNS="${IFC_BENCH_RUNS:-2}"
QUERY="${IFC_BENCH_QUERY:-IfcProduct}"
ATTRS="${IFC_BENCH_ATTRIBUTES:-Name,Description}"

DEFAULT_IFC="${ROOT}/ifc-coord/reports/nobel_mep_eval/ifc_coord_elec_vs_plumb_rerun/work/nobel_elec_vs_plumb/P1_2b_BIM_XXX_5000_00.ifc"
IFC_PATH="${IFC_BENCH_FILE:-$DEFAULT_IFC}"

if [ ! -f "$IFC_PATH" ]; then
  echo "error: IFC not found: $IFC_PATH" >&2
  echo "Set IFC_BENCH_FILE to a ~100MB model." >&2
  exit 1
fi

IFC_BYTES=$(stat -c%s "$IFC_PATH")
IFC_MB=$(python3 -c "print(f'{$IFC_BYTES / (1024*1024):.1f}')")
BASENAME=$(basename "$IFC_PATH")
# Must match the key the upload API stores (original basename, not bench_-prefixed).
UPLOAD_NAME="${BASENAME}"
export ATTRS QUERY UPLOAD_NAME

AUTH=(-H "X-API-Key: ${IFC_PIPELINE_API_KEY}")

pyjson() {
  python3 -c "$1"
}

enqueue() {
  local endpoint="$1" body="$2"
  local resp http
  resp=$(curl -sS -w '\n%{http_code}' "${AUTH[@]}" -H 'Content-Type: application/json' -d "$body" "$API$endpoint")
  http=$(echo "$resp" | tail -n1)
  resp=$(echo "$resp" | sed '$d')
  if [ "$http" -lt 200 ] || [ "$http" -ge 300 ]; then
    echo "enqueue $endpoint failed HTTP $http: $resp" >&2
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

job_seconds() {
  echo "$1" | pyjson 'import json,sys; d=json.load(sys.stdin); print(d.get("execution_time_seconds") or "")'
}

job_rows() {
  echo "$1" | pyjson '
import json, sys
d = json.load(sys.stdin)
r = d.get("result") or {}
if r.get("element_count") is not None:
    print(r["element_count"])
elif isinstance(r.get("metadata"), dict) and r["metadata"].get("element_count") is not None:
    print(r["metadata"]["element_count"])
else:
    print("")
'
}

job_status() {
  echo "$1" | pyjson 'import json,sys; print(json.load(sys.stdin).get("status",""))'
}

run_one() {
  local label="$1" endpoint="$2" out_suffix="$3"
  local body
  body=$(python3 - "$label" "$endpoint" "$out_suffix" <<'PY'
import json, os, sys
label, endpoint, out_suffix = sys.argv[1:4]
attrs = [a.strip() for a in os.environ["ATTRS"].split(",") if a.strip()]
d = {
    "filename": os.environ["UPLOAD_NAME"],
    "output_filename": f"bench_{label}_{out_suffix}.csv",
    "query": os.environ["QUERY"],
    "attributes": attrs,
}
if endpoint == "/ifccsv":
    d["format"] = "csv"
if endpoint == "/ifcfast":
    d["mmap"] = True
print(json.dumps(d))
PY
)

  local t0 jid body_out
  t0=$(date +%s.%N)
  jid=$(enqueue "$endpoint" "$body")
  body_out=$(poll_until_done "$jid")
  local t1 wall
  t1=$(date +%s.%N)
  wall=$(python3 -c "print(f'{$t1 - $t0:.2f}')")

  local status secs rows
  status=$(job_status "$body_out")
  secs=$(job_seconds "$body_out")
  rows=$(job_rows "$body_out")

  if [ "$status" != "finished" ]; then
    echo "FAIL $label run wall=${wall}s status=$status" >&2
    echo "$body_out" | pyjson 'import json,sys; print(json.dumps(json.load(sys.stdin), indent=2))' >&2
    return 1
  fi
  printf '%s|%s|%s|%s|%s\n' "$label" "$secs" "$wall" "$rows" "$jid"
}

echo "========================================"
echo "ifcfast vs ifccsv benchmark"
echo "========================================"
echo "API:        $API"
echo "IFC:        $IFC_PATH"
echo "Size:       ${IFC_MB} MiB"
echo "Job input:  $UPLOAD_NAME (must exist in uploads/ after upload)"
echo "Query:      $QUERY"
echo "Attributes: $ATTRS"
echo "Runs:       $RUNS per worker"
echo ""

echo ">>> Health (ifcfast queue)"
curl -fsS "${AUTH[@]}" "$API/health" | pyjson '
import json, sys
d = json.load(sys.stdin)
svc = d.get("services") or {}
print("  ifcfast_queue:", svc.get("ifcfast_queue", "(missing — recreate api-gateway + start ifcfast-worker)"))
print("  ifccsv_queue:", svc.get("ifccsv_queue"))
' || { echo "  (health request failed)"; }

echo ""
echo ">>> Upload IFC (may take a few minutes for large files)"
curl -fsS "${AUTH[@]}" -F "file=@${IFC_PATH}" "$API/upload/ifc" | pyjson '
import json,sys
d=json.load(sys.stdin)
print("  stored:", d.get("filename") or d.get("key") or d)
' || {
  echo "  retrying with upload name override if gateway expects basename only"
  curl -fsS "${AUTH[@]}" -F "file=@${IFC_PATH};filename=${UPLOAD_NAME}" "$API/upload/ifc"
}

FAST_WALL=()
FAST_RQ=()
CSV_WALL=()
CSV_RQ=()

for run in $(seq 1 "$RUNS"); do
  echo ""
  echo ">>> Run $run / $RUNS — ifcfast"
  line=$(run_one ifcfast /ifcfast "r${run}")
  echo "    ${line//|/ }"
  IFS='|' read -r _ rq wall rows jid <<<"$line"
  FAST_RQ+=("$rq")
  FAST_WALL+=("$wall")

  echo ">>> Run $run / $RUNS — ifccsv"
  line=$(run_one ifccsv /ifccsv "r${run}")
  echo "    ${line//|/ }"
  IFS='|' read -r _ rq wall rows jid <<<"$line"
  CSV_RQ+=("$rq")
  CSV_WALL+=("$wall")
done

echo ""
echo "========================================"
echo "Summary (${IFC_MB} MiB, query=$QUERY)"
echo "========================================"
python3 <<PY
import statistics as stats

def summarize(label, rq, wall):
    rq = [float(x) for x in rq if x]
    wall = [float(x) for x in wall if x]
    if not wall:
        print(f"{label}: no data")
        return
    rq_med = stats.median(rq) if rq else None
    wall_med = stats.median(wall)
    print(f"{label}:")
    print(f"  wall clock (median of {len(wall)}): {wall_med:.2f}s  (all: {', '.join(f'{w:.2f}' for w in wall)})")
    if rq_med is not None:
        print(f"  RQ execution_time_seconds (median): {rq_med:.2f}s  (all: {', '.join(f'{r:.2f}' for r in rq)})")

fast_rq = """${FAST_RQ[*]}""".split()
fast_wall = """${FAST_WALL[*]}""".split()
csv_rq = """${CSV_RQ[*]}""".split()
csv_wall = """${CSV_WALL[*]}""".split()

summarize("ifcfast", fast_rq, fast_wall)
summarize("ifccsv", csv_rq, csv_wall)

def med(lst):
    lst = [float(x) for x in lst if x]
    return stats.median(lst) if lst else None

fw, cw = med(fast_wall), med(csv_wall)
fr, cr = med(fast_rq), med(csv_rq)
if fw and cw:
    print(f"Speedup (wall median): {cw/fw:.2f}x faster with ifcfast")
if fr and cr:
    print(f"Speedup (RQ median):   {cr/fr:.2f}x faster with ifcfast")
PY
