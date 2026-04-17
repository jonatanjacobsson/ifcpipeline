#!/usr/bin/env bash
# Prod-scale test for the object-storage build.
#
# Uploads 7 real-world IFC files (~190 MiB total) from prod-fixtures/,
# enqueues a large batch of jobs (ifccsv, ifc2json, ifcconvert, ifcpatch,
# ifcdiff, ifctester, ifcclash), polls them to completion, and then
# summarizes what landed in MinIO + Postgres (audit trail).
#
# Intentionally does NOT use the `shared/uploads`/`shared/output` bind
# mounts: every file moves through the `/upload` endpoint so the S3-only
# path is exercised end-to-end, identical to a real deployment.
#
# Requires: docker, docker compose v2, curl, python3.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

: "${IFC_PIPELINE_API_KEY:=pocsecret}"
: "${IFC_PIPELINE_ALLOWED_IP_RANGES:=127.0.0.1/32,172.16.0.0/12}"
: "${POSTGRES_PASSWORD:=pocpass}"
: "${S3_BUCKET:=ifcpipeline}"
: "${S3_ACCESS_KEY:=minioadmin}"
: "${S3_SECRET_KEY:=minioadmin}"
export IFC_PIPELINE_API_KEY IFC_PIPELINE_ALLOWED_IP_RANGES POSTGRES_PASSWORD
export S3_BUCKET S3_ACCESS_KEY S3_SECRET_KEY
export IFC_PIPELINE_EXTERNAL_URL="http://localhost:8100"
export IFC_PIPELINE_PREVIEW_EXTERNAL_URL="http://localhost:8101"
export N8N_WEBHOOK_URL="http://localhost:5778"
export N8N_COMMUNITY_PACKAGES_ENABLED=true

API="http://localhost:8100"
AUTH=(-H "X-API-Key: ${IFC_PIPELINE_API_KEY}")
FIXTURES="$ROOT/prod-fixtures"

if [ ! -d "$FIXTURES" ]; then
  echo "missing fixture directory: $FIXTURES" >&2
  exit 2
fi

SERVICES=(
  minio minio-setup redis postgres
  api-gateway
  ifccsv-worker ifctester-worker ifcconvert-worker
  ifcdiff-worker ifc5d-worker ifc2json-worker ifcpatch-worker
  ifcclash-worker
)

jqp()  { python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps(d, indent=2))'; }
jqget() { python3 -c 'import json, sys
d=json.load(sys.stdin)
for k in sys.argv[1].split("."):
    d = d.get(k) if isinstance(d, dict) else None
    if d is None: break
print("" if d is None else d)' "$1"; }

echo ">>> Building & starting services"
docker compose up -d --build "${SERVICES[@]}" >/dev/null

echo ">>> Waiting for api-gateway"
for _ in $(seq 1 60); do
  if curl -fsS "${AUTH[@]}" "$API/health" >/dev/null 2>&1; then break; fi
  sleep 2
done
curl -sS "${AUTH[@]}" "$API/health" | jqp

# The IDS sample shipped with upstream is a toy but good enough to exercise
# the tester with a real IFC; most rules will not apply and we only assert
# the job finishes + is audited.
IDS="IDS-example.ids"
cp -f "$ROOT/shared/examples/$IDS" "$FIXTURES/$IDS"

FILES=(Arch-v1.ifc Arch-v2.ifc Arch-B.ifc Struct.ifc Elec.ifc Pipe.ifc Sprinkler.ifc)

echo ">>> Uploading ${#FILES[@]} IFC fixtures + IDS"
for f in "${FILES[@]}"; do
  size=$(stat -c%s "$FIXTURES/$f")
  resp=$(curl -fsS "${AUTH[@]}" -F "file=@$FIXTURES/$f" "$API/upload/ifc")
  sha=$(echo "$resp" | jqget sha256)
  printf '  up  %-16s %9s bytes  sha=%s…\n' "$f" "$size" "${sha:0:12}"
done
curl -fsS "${AUTH[@]}" -F "file=@$FIXTURES/$IDS" "$API/upload/ids" >/dev/null
echo "  up  $IDS"

enqueue() {
  local endpoint="$1" body="$2"
  curl -fsS "${AUTH[@]}" -H 'Content-Type: application/json' -d "$body" "$API$endpoint" | jqget job_id
}

# Track (job_id, label) so we can poll them together.
declare -a JOB_IDS JOB_LABELS
add_job() { JOB_IDS+=("$1"); JOB_LABELS+=("$2"); }

echo ">>> Enqueue jobs"

# --- ifccsv: one per IFC, varied queries to exercise different element sets
idx=0
for f in "${FILES[@]}"; do
  q=$([ $((idx % 2)) = 0 ] && echo "IfcWall" || echo "IfcBuildingElement")
  jid=$(enqueue /ifccsv "{\"filename\":\"$f\",\"output_filename\":\"${f%.ifc}.csv\",\"format\":\"csv\",\"query\":\"$q\",\"attributes\":[\"Name\",\"Description\",\"GlobalId\"]}")
  add_job "$jid" "csv/${f%.ifc}"
  idx=$((idx + 1))
done

# --- ifc2json: one per IFC
for f in "${FILES[@]}"; do
  jid=$(enqueue /ifc2json "{\"filename\":\"$f\",\"output_filename\":\"${f%.ifc}.json\"}")
  add_job "$jid" "json/${f%.ifc}"
done

# --- ifcconvert: every file to .obj  (heavy, but we want prod-scale proof)
for f in "${FILES[@]}"; do
  jid=$(enqueue /ifcconvert "{\"input_filename\":\"/uploads/$f\",\"output_filename\":\"/output/converted/${f%.ifc}.obj\",\"log_file\":\"/output/converted/${f%.ifc}.log\"}")
  add_job "$jid" "conv/${f%.ifc}"
done

# --- ifcpatch: three recipes × three files → 9 patches
PATCH_FILES=(Arch-v1.ifc Struct.ifc Pipe.ifc)
for f in "${PATCH_FILES[@]}"; do
  for recipe in "ExtractElements|IfcWall" "ExtractElements|IfcSpace" "ExtractElements|IfcBuildingElementProxy"; do
    r="${recipe%%|*}"; a="${recipe##*|}"
    jid=$(enqueue /patch/execute "{\"input_file\":\"$f\",\"output_file\":\"${f%.ifc}.patch-${a}.ifc\",\"recipe\":\"$r\",\"arguments\":[\"$a\"],\"use_custom\":false}")
    add_job "$jid" "patch/${f%.ifc}-${a}"
  done
done

# --- ifcdiff: a few version/variant pairs
DIFF_PAIRS=(
  "Arch-v1.ifc|Arch-v2.ifc|v1-vs-v2"
  "Arch-v1.ifc|Arch-B.ifc|arch1-vs-archB"
  "Struct.ifc|Arch-v1.ifc|struct-vs-arch1"
)
for pair in "${DIFF_PAIRS[@]}"; do
  IFS='|' read -r oldf newf tag <<<"$pair"
  jid=$(enqueue /ifcdiff "{\"old_file\":\"$oldf\",\"new_file\":\"$newf\",\"output_file\":\"diff-${tag}.json\"}")
  add_job "$jid" "diff/${tag}"
done

# --- ifctester: run against each arch file using the shipped IDS
for f in Arch-v1.ifc Arch-v2.ifc Arch-B.ifc; do
  jid=$(enqueue /ifctester "{\"ifc_filename\":\"$f\",\"ids_filename\":\"$IDS\",\"output_filename\":\"ids-${f%.ifc}.json\",\"report_type\":\"json\"}")
  add_job "$jid" "ids/${f%.ifc}"
done

# --- ifcclash: several multi-discipline sets
CLASH_JOB=$(enqueue /ifcclash "$(cat <<EOF
{
  "clash_sets": [
    {"name":"arch_vs_struct","a":[{"file":"Arch-v1.ifc"}],"b":[{"file":"Struct.ifc"}]},
    {"name":"arch_vs_mep",   "a":[{"file":"Arch-v1.ifc"}],"b":[{"file":"Pipe.ifc"},{"file":"Sprinkler.ifc"}]},
    {"name":"struct_vs_mep", "a":[{"file":"Struct.ifc"}], "b":[{"file":"Pipe.ifc"},{"file":"Sprinkler.ifc"},{"file":"Elec.ifc"}]}
  ],
  "output_filename": "clash-prod.json",
  "tolerance": 0.01,
  "mode": "intersection",
  "check_all": false,
  "allow_touching": false,
  "clearance": 0,
  "smart_grouping": false,
  "max_cluster_distance": 5.0
}
EOF
)")
add_job "$CLASH_JOB" "clash/multi"

# --- ifc5d (qto): best-effort, may fail due to upstream library mismatch
for f in Arch-v1.ifc Struct.ifc; do
  jid=$(enqueue /calculate-qtos "{\"input_file\":\"$f\",\"output_file\":\"qto-${f%.ifc}.ifc\"}")
  add_job "$jid" "qto/${f%.ifc}"
done

total=${#JOB_IDS[@]}
echo "  enqueued $total jobs"

# Poll every job, count outcomes. Known-soft (ifc5d + ifcclash) get counted
# separately so they can't fail the overall run on their own.
fail=0
soft_fail=0
ok=0
declare -A FINAL_STATUS
start=$(date +%s)
pending=$total
while [ "$pending" -gt 0 ]; do
  pending=0
  for i in "${!JOB_IDS[@]}"; do
    jid="${JOB_IDS[$i]}"
    label="${JOB_LABELS[$i]}"
    if [ -n "${FINAL_STATUS[$jid]:-}" ]; then continue; fi
    body=$(curl -fsS "${AUTH[@]}" "$API/jobs/${jid}/status" 2>/dev/null || true)
    [ -z "$body" ] && { pending=$((pending + 1)); continue; }
    status=$(echo "$body" | jqget status)
    case "$status" in
      finished)
        FINAL_STATUS[$jid]="$status"
        ok=$((ok + 1))
        et=$(echo "$body" | jqget execution_time_seconds)
        printf '  [%s] OK   %-28s %s  %6ss\n' "$(date +%H:%M:%S)" "$label" "$jid" "${et:-?}"
        ;;
      failed|stopped|canceled)
        FINAL_STATUS[$jid]="$status"
        # ifcclash + ifc5d are treated as soft failures (upstream lib bugs).
        if [[ "$label" == qto/* || "$label" == clash/* ]]; then
          soft_fail=$((soft_fail + 1))
          printf '  [%s] SOFT %-28s %s  (%s)\n' "$(date +%H:%M:%S)" "$label" "$jid" "$status"
        else
          fail=$((fail + 1))
          err=$(echo "$body" | python3 -c 'import json,sys; d=json.load(sys.stdin); e=(d.get("error") or "").splitlines(); print(e[-1] if e else "")')
          printf '  [%s] FAIL %-28s %s  %s\n' "$(date +%H:%M:%S)" "$label" "$jid" "$err"
        fi
        ;;
      *) pending=$((pending + 1));;
    esac
  done
  if [ "$pending" -gt 0 ]; then
    now=$(date +%s)
    printf '  ...waiting, %d pending (elapsed %ds)\n' "$pending" "$((now - start))"
    sleep 5
  fi
  # Hard stop: 25 minutes.
  now=$(date +%s)
  if [ $((now - start)) -gt 1500 ]; then
    echo "  TIMEOUT: $pending jobs still pending after 25 minutes"
    fail=$((fail + pending))
    break
  fi
done
elapsed=$(($(date +%s) - start))
echo ">>> Jobs done in ${elapsed}s   ok=$ok  fail=$fail  soft=$soft_fail  total=$total"

echo ">>> Bucket summary"
docker compose run --rm --entrypoint /bin/sh minio-setup -c "
  mc alias set local http://minio:9000 ${S3_ACCESS_KEY} ${S3_SECRET_KEY} >/dev/null
  echo '  - uploads/'   ; mc ls --recursive local/${S3_BUCKET}/uploads   | tail -n +1 | wc -l   | xargs printf '    objects: %s\n'
  echo '  - output/'    ; mc ls --recursive local/${S3_BUCKET}/output    | tail -n +1 | wc -l   | xargs printf '    objects: %s\n'
  echo '  - total size:'; mc du local/${S3_BUCKET} | awk '{print \"   \", \$0}'
" 2>/dev/null || true

echo ">>> Audit trail summary"
docker compose exec -T postgres psql -U ifcpipeline -d ifcpipeline -t -A -c "
SELECT 'roots=' || COUNT(*) FROM object_versions WHERE kind='root';
" 2>/dev/null
docker compose exec -T postgres psql -U ifcpipeline -d ifcpipeline -t -A -c "
SELECT 'derived=' || COUNT(*) FROM object_versions WHERE kind='derived';
" 2>/dev/null
docker compose exec -T postgres psql -U ifcpipeline -d ifcpipeline -t -A -c "
SELECT 'edges=' || COUNT(*) FROM object_lineage;
" 2>/dev/null
echo "  per-operation counts:"
docker compose exec -T postgres psql -U ifcpipeline -d ifcpipeline -c "
SELECT operation, COUNT(*) AS objects, SUM(size_bytes) AS bytes
  FROM object_versions
 GROUP BY operation
 ORDER BY operation;
" 2>/dev/null

echo ">>> Sample lineage queries"
for k in "output/csv/Arch-v1.csv" "output/diff/diff-v1-vs-v2.json" "output/patch/Arch-v1.patch-IfcWall.ifc"; do
  echo "  -- /lineage/$k"
  body=$(curl -fsS "${AUTH[@]}" "$API/lineage/$k" 2>/dev/null || true)
  if [ -z "$body" ]; then
    echo "     (missing)"
    continue
  fi
  echo "$body" | python3 -c "
import json, sys
d = json.load(sys.stdin)
s = d.get('self', {}) or {}
anc = d.get('ancestors', []) or []
print('     sha256    :', (s.get('sha256') or '')[:16], '...')
print('     size_bytes:', s.get('size_bytes'))
print('     worker    :', s.get('worker'))
print('     op        :', s.get('operation'))
print('     parents   :', ', '.join(a.get('object_key','') for a in anc) or '-')
"
done

echo ">>> Root uploads (audit)"
curl -fsS "${AUTH[@]}" "$API/audit/roots?limit=20" \
  | python3 -c '
import json, sys
d = json.load(sys.stdin).get("roots", [])
for r in d:
    sha = (r.get("sha256") or "")[:12]
    print("  {ts}  {sha}...  {size:>10} bytes  {key}".format(
        ts=r.get("created_at",""), sha=sha, size=r.get("size_bytes",0), key=r.get("object_key","")))
' || true

if [ "$fail" -gt 0 ]; then
  echo "PROD TEST FAILED ($fail hard failures, $soft_fail soft failures)"
  exit 1
fi
if [ "$soft_fail" -gt 0 ]; then
  echo "PROD TEST OK  (with $soft_fail known-soft failure(s): see OBJECT_STORAGE.md)"
else
  echo "PROD TEST OK"
fi
