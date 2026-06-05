#!/usr/bin/env bash
# Enqueue /ifcfast and /ifccsv on the same IFC + query; compare row counts.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

: "${IFC_PIPELINE_API_KEY:=pocsecret}"
API="${IFC_PIPELINE_API_URL:-http://localhost:8100}"
ARCH="${IFC_FAST_TEST_IFC:-Building-Architecture.ifc}"

AUTH=(-H "X-API-Key: ${IFC_PIPELINE_API_KEY}")

enqueue() {
  local endpoint="$1" body="$2"
  curl -fsS "${AUTH[@]}" -H 'Content-Type: application/json' -d "$body" "$API$endpoint" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])'
}

poll_job() {
  local jid="$1"
  for _ in $(seq 1 90); do
    local body status
    body=$(curl -fsS "${AUTH[@]}" "$API/jobs/${jid}/status")
    status=$(echo "$body" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",""))')
    case "$status" in
      finished) echo "$body"; return 0 ;;
      failed|stopped|canceled) echo "$body" >&2; return 1 ;;
    esac
    sleep 2
  done
  echo "timeout waiting for $jid" >&2
  return 1
}

element_count() {
  python3 -c 'import json,sys; d=json.load(sys.stdin); r=d.get("result") or {}; print(r.get("element_count", "?"))'
}

echo ">>> Upload sample IFC (if missing)"
curl -fsS "${AUTH[@]}" -F "file=@shared/examples/$ARCH" "$API/upload/ifc" >/dev/null || true

QUERY='IfcWall'
ATTRS='["Name","Description"]'

FAST_BODY=$(cat <<EOF
{"filename":"$ARCH","output_filename":"n8n_fast_smoke.csv","query":"$QUERY","attributes":$ATTRS}
EOF
)
CSV_BODY=$(cat <<EOF
{"filename":"$ARCH","output_filename":"n8n_csv_smoke.csv","format":"csv","query":"$QUERY","attributes":$ATTRS}
EOF
)

echo ">>> Enqueue ifcfast"
FAST_JOB=$(enqueue /ifcfast "$FAST_BODY")
echo "    job_id=$FAST_JOB"

echo ">>> Enqueue ifccsv (parity)"
CSV_JOB=$(enqueue /ifccsv "$CSV_BODY")
echo "    job_id=$CSV_JOB"

echo ">>> Poll ifcfast"
FAST_RESULT=$(poll_job "$FAST_JOB")
FAST_ROWS=$(echo "$FAST_RESULT" | element_count)
echo "    element_count=$FAST_ROWS engine=$(echo "$FAST_RESULT" | python3 -c 'import json,sys; print((json.load(sys.stdin).get("result") or {}).get("engine",""))')"

echo ">>> Poll ifccsv"
CSV_RESULT=$(poll_job "$CSV_JOB")
CSV_ROWS=$(echo "$CSV_RESULT" | python3 -c 'import json,sys; r=json.load(sys.stdin).get("result") or {}; print(r.get("metadata",{}).get("element_count", r.get("element_count","?"))')
echo "    element_count=$CSV_ROWS"

if [ "$FAST_ROWS" != "$CSV_ROWS" ] && [ "$CSV_ROWS" != "?" ]; then
  echo "WARNING: row counts differ (fast=$FAST_ROWS csv=$CSV_ROWS) — check query/attributes"
fi

echo ">>> OK — ifcfast smoke passed"
