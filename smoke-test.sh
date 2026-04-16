#!/usr/bin/env bash
# Smoke-test the object-storage PoC.
# - Boots minio + redis + postgres + api-gateway + ifccsv-worker + ifctester-worker
# - Uploads a sample IFC and IDS
# - Enqueues an ifccsv export and an ifctester validation
# - Verifies the output objects exist in MinIO
#
# Requires: docker, docker compose v2, curl, python3
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

: "${IFC_PIPELINE_API_KEY:=pocsecret}"
: "${IFC_PIPELINE_ALLOWED_IP_RANGES:=127.0.0.1/32,172.16.0.0/12}"
: "${POSTGRES_PASSWORD:=pocpass}"
: "${S3_BUCKET:=ifcpipeline}"
: "${S3_ACCESS_KEY:=minioadmin}"
: "${S3_SECRET_KEY:=minioadmin}"
export IFC_PIPELINE_API_KEY IFC_PIPELINE_ALLOWED_IP_RANGES POSTGRES_PASSWORD S3_BUCKET S3_ACCESS_KEY S3_SECRET_KEY
export IFC_PIPELINE_EXTERNAL_URL="http://localhost:8100"
export IFC_PIPELINE_PREVIEW_EXTERNAL_URL="http://localhost:8101"
export N8N_WEBHOOK_URL="http://localhost:5778"
export N8N_COMMUNITY_PACKAGES_ENABLED=true

API="http://localhost:8100"
AUTH=(-H "X-API-Key: ${IFC_PIPELINE_API_KEY}")

jqp() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps(d, indent=2))'; }
jqget() { python3 -c 'import json,sys; d=json.load(sys.stdin)
for k in sys.argv[1].split("."):
    d = d.get(k) if isinstance(d, dict) else None
    if d is None: break
print("" if d is None else d)' "$1"; }

SERVICES=(minio minio-setup redis postgres ifccsv-worker ifctester-worker api-gateway)

echo ">>> Building & starting services: ${SERVICES[*]}"
docker compose -f docker-compose.yml -f docker-compose.test.yml up -d --build "${SERVICES[@]}"

echo ">>> Waiting for api-gateway to answer /health ..."
for i in $(seq 1 60); do
  if curl -fsS "${AUTH[@]}" "$API/health" >/dev/null 2>&1; then break; fi
  sleep 2
done
curl -sS "${AUTH[@]}" "$API/health" | jqp

SAMPLE_IFC="shared/examples/Building-Architecture.ifc"
SAMPLE_IDS="shared/examples/IDS-example.ids"

echo ">>> Uploading sample IFC"
curl -fsS "${AUTH[@]}" -F "file=@${SAMPLE_IFC}" "$API/upload/ifc" | jqp

echo ">>> Uploading sample IDS"
curl -fsS "${AUTH[@]}" -F "file=@${SAMPLE_IDS}" "$API/upload/ids" | jqp

echo ">>> Enqueue ifccsv export"
CSV_JOB=$(curl -fsS "${AUTH[@]}" -H 'Content-Type: application/json' \
  -d '{"filename":"Building-Architecture.ifc","output_filename":"arch.csv","format":"csv","query":"IfcWall","attributes":["Name","Description"]}' \
  "$API/ifccsv" | jqget job_id)
echo "  job_id=$CSV_JOB"

echo ">>> Enqueue ifctester validation"
IDS_JOB=$(curl -fsS "${AUTH[@]}" -H 'Content-Type: application/json' \
  -d '{"ifc_filename":"Building-Architecture.ifc","ids_filename":"IDS-example.ids","output_filename":"report.json","report_type":"json"}' \
  "$API/ifctester" | jqget job_id)
echo "  job_id=$IDS_JOB"

poll_job() {
  local jid="$1"
  for i in $(seq 1 90); do
    local body status
    body=$(curl -fsS "${AUTH[@]}" "$API/jobs/${jid}/status" || true)
    status=$(echo "$body" | jqget status)
    echo "  [$(date +%H:%M:%S)] $jid -> $status"
    case "$status" in
      finished|failed|stopped|canceled)
        echo "$body" | jqp
        [ "$status" = "finished" ]
        return $?
        ;;
    esac
    sleep 2
  done
  echo "  timed out polling $jid"
  return 1
}

echo ">>> Polling ifccsv job"
poll_job "$CSV_JOB"

echo ">>> Polling ifctester job"
poll_job "$IDS_JOB"

echo ">>> Listing objects in bucket ${S3_BUCKET}"
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm \
  --entrypoint /bin/sh minio-setup -c \
  "mc alias set local http://minio:9000 ${S3_ACCESS_KEY} ${S3_SECRET_KEY} >/dev/null && mc ls --recursive local/${S3_BUCKET}"

echo "OK"
