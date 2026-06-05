#!/usr/bin/env bash
# End-to-end test: remote workers consume ifctester / ifcpatch / ifcclash / ifcdiff jobs.
# Run on the primary host from repo root (combined compose must be up).
#
# Modes (TEST_MODE):
#   smoke       — one job per queue (default phase)
#   concurrency — N jobs per queue, polled in parallel; checks max in-flight + distinct workers
#   full        — smoke then concurrency (default)
#
# Optional: ISOLATE_REMOTE=1 stops primary workers for those queues so only remote workers run jobs.
#
# Concurrency env:
#   CONCURRENCY_JOBS=3          jobs per queue in concurrency phase
#   CONCURRENCY_QUEUES=ifcpatch,ifctester,ifcclash,ifcdiff   subset of queues
#   MIN_PARALLEL=2              require this many simultaneous "started" when enough workers exist
#   CONCURRENCY_TIMEOUT_SEC=600 poll timeout for concurrency batch
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ISOLATE_REMOTE="${ISOLATE_REMOTE:-1}"
TEST_MODE="${TEST_MODE:-full}"
CONCURRENCY_JOBS="${CONCURRENCY_JOBS:-2}"
CONCURRENCY_QUEUES="${CONCURRENCY_QUEUES:-ifctester,ifcpatch,ifcclash,ifcdiff}"
MIN_PARALLEL="${MIN_PARALLEL:-2}"
CONCURRENCY_TIMEOUT_SEC="${CONCURRENCY_TIMEOUT_SEC:-600}"
API="${API:-http://127.0.0.1:8000}"
ARCH="${ARCH:-Building-Architecture.ifc}"
STRUCT="${STRUCT:-Building-Structural.ifc}"
IDS="${IDS:-IDS-example.ids}"
REMOTE_HOST_PATTERN="${REMOTE_HOST_PATTERN:-worker}"

if [[ ! -f .env ]]; then
  echo "error: missing .env" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1091
source .env
set +a

AUTH=(-H "X-API-Key: ${IFC_PIPELINE_API_KEY:?IFC_PIPELINE_API_KEY not set}")

jqp() { python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin), indent=2))'; }
jqget() {
  python3 -c 'import json,sys; d=json.load(sys.stdin);
for k in sys.argv[1].split("."): d=d.get(k) if isinstance(d,dict) else None
print("" if d is None else d)' "$1"
}

# Prints:
#   line0: queues with >=1 worker (0-3)
#   rest:  queue:worker_count:id1,id2,...
count_workers_for_queues() {
  local want="${1:-ifctester,ifcpatch,ifcclash,ifcdiff}"
  docker compose exec -T -e "WANT_QUEUES=${want}" api-gateway python3 <<'PY'
from redis import Redis
from rq import Worker
from rq.exceptions import NoSuchJobError
import os

r = Redis.from_url("redis://redis:6379/0")
want = set(os.environ.get("WANT_QUEUES", "").split(","))
found = {q: [] for q in want}

for raw in list(r.smembers("rq:workers")):
    name = raw.decode() if isinstance(raw, bytes) else raw
    key = name if name.startswith("rq:worker:") else f"rq:worker:{name}"
    if not r.exists(key):
        r.srem("rq:workers", raw)
        continue
    try:
        w = Worker.find_by_key(key, connection=r)
    except ValueError:
        r.srem("rq:workers", raw)
        continue
    try:
        w.get_current_job()
    except NoSuchJobError:
        pass
    for q in w.queue_names():
        if q in want:
            found[q].append(w.name)

covered = sum(1 for q in want if found[q])
print(covered)
for q in sorted(want):
    ids = found[q]
    print(f"{q}:{len(ids)}:" + (",".join(ids) if ids else ""))
PY
}

parse_worker_registry() {
  # Reads count_workers_for_queues output into associative arrays:
  #   WORKER_COUNT[queue], WORKER_IDS[queue] (comma-separated)
  declare -gA WORKER_COUNT WORKER_IDS
  WORKER_COUNT=()
  WORKER_IDS=()
  local coverage="$1"
  shift
  QUEUE_COVERAGE="$coverage"
  for line in "$@"; do
    [[ "$line" == *:* ]] || continue
    local q="${line%%:*}"
    local rest="${line#*:}"
    local n="${rest%%:*}"
    local ids="${rest#*:}"
    WORKER_COUNT["$q"]="$n"
    WORKER_IDS["$q"]="$ids"
  done
}

enqueue() {
  local endpoint="$1" body="$2"
  curl -fsS "${AUTH[@]}" -H 'Content-Type: application/json' -d "$body" "$API$endpoint" | jqget job_id
}

enqueue_ifctester() {
  local tag="$1"
  enqueue /ifctester "{\"ifc_filename\":\"$ARCH\",\"ids_filename\":\"$IDS\",\"output_filename\":\"remote-test-ids-${tag}.json\",\"report_type\":\"json\"}"
}

enqueue_ifcpatch() {
  local tag="$1"
  enqueue /patch/execute "{\"input_file\":\"$ARCH\",\"output_file\":\"remote-test-patch-${tag}.ifc\",\"recipe\":\"ExtractElements\",\"arguments\":[\"IfcWall\"],\"use_custom\":false}"
}

enqueue_ifcclash() {
  local tag="$1"
  enqueue /ifcclash "{\"clash_sets\":[{\"name\":\"t\",\"a\":[{\"file\":\"$ARCH\"}],\"b\":[{\"file\":\"$STRUCT\"}]}],\"output_filename\":\"remote-test-clash-${tag}.json\",\"tolerance\":0.01,\"mode\":\"intersection\",\"check_all\":false,\"allow_touching\":false,\"clearance\":0,\"smart_grouping\":false,\"max_cluster_distance\":5.0}"
}

enqueue_ifcdiff() {
  local tag="$1"
  enqueue /ifcdiff "{\"old_file\":\"$ARCH\",\"new_file\":\"$STRUCT\",\"output_file\":\"remote-test-diff-${tag}.json\"}"
}

poll_job() {
  local jid="$1" label="$2"
  for _ in $(seq 1 120); do
    local body status
    body=$(curl -fsS "${AUTH[@]}" "$API/jobs/${jid}/status" || true)
    status=$(echo "$body" | jqget status)
    printf '  [%s] %-10s %s -> %s\n' "$(date +%H:%M:%S)" "$label" "$jid" "$status"
    case "$status" in
      finished) echo "$body" | jqp; return 0 ;;
      failed|stopped|canceled) echo "$body" | jqp; return 1 ;;
    esac
    sleep 3
  done
  echo "  timed out: $jid"
  return 1
}

worker_for_job() {
  local jid="$1"
  docker compose exec -T redis redis-cli HGET "rq:job:${jid}" worker_name 2>/dev/null | tr -d '\r\n'
}

ensure_samples() {
  curl -fsS "${AUTH[@]}" -F "file=@shared/examples/$ARCH" "$API/upload/ifc" >/dev/null
  curl -fsS "${AUTH[@]}" -F "file=@shared/examples/$STRUCT" "$API/upload/ifc" >/dev/null
  curl -fsS "${AUTH[@]}" -F "file=@shared/examples/$IDS" "$API/upload/ids" >/dev/null
}

check_queue_coverage() {
  local want_queues="$1"
  mapfile -t queue_lines < <(count_workers_for_queues "$want_queues")
  parse_worker_registry "${queue_lines[0]:-0}" "${queue_lines[@]:1}"
  local nqueues
  nqueues=$(echo "$want_queues" | tr ',' '\n' | wc -l)
  echo "Queues with at least one worker: ${QUEUE_COVERAGE}/${nqueues}"
  for line in "${queue_lines[@]:1}"; do
    local q="${line%%:*}"
  local rest="${line#*:}"
  local wc="${rest%%:*}"
  local ids="${rest#*:}"
  if [[ -n "$ids" ]]; then
    echo "  $q: ${wc} worker(s) — $ids"
  else
    echo "  $q: NONE"
  fi
  done
  if [[ "${QUEUE_COVERAGE:-0}" -lt "$nqueues" ]]; then
    echo ""
    echo "Not all target queues have a worker (remote stack not up or primary still stopped)."
    echo "Start remote workers on worker host:"
    echo "  ssh -o RemoteCommand=none \${REMOTE_SSH:-deploy@worker-host} \\"
    echo "    'cd \${REMOTE_REPO:-/home/deploy/apps/ifcpipeline} && SKIP_BUILD=1 ./scripts/start-remote-workers.sh'"
    return 1
  fi
  return 0
}

# Poll many jobs together; sets globals CONCURRENCY_FAIL, CONCURRENCY_MAX_IN_FLIGHT,
# and associative CONCURRENCY_QUEUE_MAX_IN_FLIGHT, CONCURRENCY_QUEUE_WORKERS_USED.
poll_jobs_parallel() {
  local -a jids=("$@")
  local -a labels=("${PARALLEL_LABELS[@]}")
  local -a queues=("${PARALLEL_QUEUES_ARR[@]}")
  local deadline=$(( $(date +%s) + CONCURRENCY_TIMEOUT_SEC ))
  declare -A job_status job_reported
  declare -gA CONCURRENCY_QUEUE_MAX_IN_FLIGHT CONCURRENCY_QUEUE_WORKERS_USED
  CONCURRENCY_QUEUE_MAX_IN_FLIGHT=()
  CONCURRENCY_QUEUE_WORKERS_USED=()
  CONCURRENCY_MAX_IN_FLIGHT=0
  CONCURRENCY_FAIL=0

  for q in "${queues[@]}"; do
    CONCURRENCY_QUEUE_MAX_IN_FLIGHT["$q"]=0
    CONCURRENCY_QUEUE_WORKERS_USED["$q"]=""
  done

  while true; do
    local in_flight=0
    local all_terminal=1
  declare -A queue_in_flight

    for i in "${!jids[@]}"; do
      local jid="${jids[$i]}"
      local label="${labels[$i]}"
      local queue="${queues[$i]}"
      local body status
      body=$(curl -fsS "${AUTH[@]}" "$API/jobs/${jid}/status" 2>/dev/null || echo '{}')
      status=$(echo "$body" | jqget status)
      job_status["$jid"]="$status"

      case "$status" in
        started)
          in_flight=$((in_flight + 1))
          queue_in_flight["$queue"]=$((${queue_in_flight[$queue]:-0} + 1))
          all_terminal=0
          ;;
        queued|deferred|scheduled)
          all_terminal=0
          ;;
        finished|failed|stopped|canceled)
          if [[ -z "${job_reported[$jid]:-}" ]]; then
            printf '  [%s] %-16s %s -> %s\n' "$(date +%H:%M:%S)" "$label" "$jid" "$status"
            job_reported["$jid"]=1
            if [[ "$status" != "finished" ]]; then
              CONCURRENCY_FAIL=1
              echo "$body" | jqp
            fi
            local wn
            wn=$(worker_for_job "$jid")
            if [[ -n "$wn" ]]; then
              if [[ ",${CONCURRENCY_QUEUE_WORKERS_USED[$queue]}," != *",$wn,"* ]]; then
                if [[ -n "${CONCURRENCY_QUEUE_WORKERS_USED[$queue]:-}" ]]; then
                  CONCURRENCY_QUEUE_WORKERS_USED["$queue"]+=",$wn"
                else
                  CONCURRENCY_QUEUE_WORKERS_USED["$queue"]="$wn"
                fi
              fi
            fi
          fi
          ;;
        *)
          all_terminal=0
          ;;
      esac
    done

    if (( in_flight > CONCURRENCY_MAX_IN_FLIGHT )); then
      CONCURRENCY_MAX_IN_FLIGHT=$in_flight
    fi
    for q in "${!queue_in_flight[@]}"; do
      local qif="${queue_in_flight[$q]}"
      if (( qif > ${CONCURRENCY_QUEUE_MAX_IN_FLIGHT[$q]:-0} )); then
        CONCURRENCY_QUEUE_MAX_IN_FLIGHT["$q"]=$qif
      fi
    done

    if [[ "$all_terminal" == "1" ]]; then
      break
    fi
    if (( $(date +%s) >= deadline )); then
      echo "  concurrency poll timed out after ${CONCURRENCY_TIMEOUT_SEC}s"
      CONCURRENCY_FAIL=1
      break
    fi
    sleep 2
  done
}

run_smoke_test() {
  echo ""
  echo "=== Smoke test (one job per queue) ==="
  local IDS_JOB PATCH_JOB CLASH_JOB DIFF_JOB
  IDS_JOB=$(enqueue_ifctester smoke)
  PATCH_JOB=$(enqueue_ifcpatch smoke)
  CLASH_JOB=$(enqueue_ifcclash smoke)
  DIFF_JOB=$(enqueue_ifcdiff smoke)
  echo "Enqueued: ifctester=$IDS_JOB ifcpatch=$PATCH_JOB ifcclash=$CLASH_JOB ifcdiff=$DIFF_JOB"
  local fail=0
  poll_job "$IDS_JOB" ifctester || fail=1
  poll_job "$PATCH_JOB" ifcpatch || fail=1
  poll_job "$CLASH_JOB" ifcclash || fail=1
  poll_job "$DIFF_JOB" ifcdiff || fail=1
  for jid in "$IDS_JOB" "$PATCH_JOB" "$CLASH_JOB" "$DIFF_JOB"; do
    local wn
    wn=$(worker_for_job "$jid")
    echo "Job $jid -> worker_name=${wn:-unknown}"
  done
  if [[ "$fail" -ne 0 ]]; then
    echo "=== FAIL: smoke test ==="
    return 1
  fi
  echo "=== PASS: smoke test ==="
  return 0
}

run_concurrency_test() {
  echo ""
  echo "=== Concurrency test (${CONCURRENCY_JOBS} job(s) per queue, parallel poll) ==="
  local -a want_list=()
  IFS=',' read -ra want_list <<< "$CONCURRENCY_QUEUES"
  local -a jids=() PARALLEL_LABELS=() PARALLEL_QUEUES_ARR=()
  local i tag q

  ensure_samples

  for q in "${want_list[@]}"; do
    for ((i = 0; i < CONCURRENCY_JOBS; i++)); do
      tag="${q}-${i}"
      case "$q" in
        ifctester) jids+=("$(enqueue_ifctester "$tag")") ;;
        ifcpatch)  jids+=("$(enqueue_ifcpatch "$tag")") ;;
        ifcclash)  jids+=("$(enqueue_ifcclash "$tag")") ;;
        ifcdiff)   jids+=("$(enqueue_ifcdiff "$tag")") ;;
        *)
          echo "error: unknown queue in CONCURRENCY_QUEUES: $q" >&2
          return 1
          ;;
      esac
      PARALLEL_LABELS+=("${q}[${i}]")
      PARALLEL_QUEUES_ARR+=("$q")
    done
  done

  echo "Enqueued ${#jids[@]} jobs ($(printf '%s ' "${want_list[@]}")× ${CONCURRENCY_JOBS})"
  poll_jobs_parallel "${jids[@]}"

  echo ""
  echo "Concurrency summary:"
  echo "  global max in-flight (started): ${CONCURRENCY_MAX_IN_FLIGHT}"
  local fail=0
  if [[ "${CONCURRENCY_FAIL:-0}" -ne 0 ]]; then
    fail=1
  fi

  for q in "${want_list[@]}"; do
    local wc="${WORKER_COUNT[$q]:-0}"
    local qmax="${CONCURRENCY_QUEUE_MAX_IN_FLIGHT[$q]:-0}"
    local used="${CONCURRENCY_QUEUE_WORKERS_USED[$q]:-}"
    local used_n=0
    if [[ -n "$used" ]]; then
      used_n=$(echo "$used" | tr ',' '\n' | grep -c . || true)
    fi
    echo "  $q: max in-flight=$qmax, workers used=$used_n (${used:-none})"
    local need=$(( MIN_PARALLEL < wc ? MIN_PARALLEL : wc ))
    need=$(( need < CONCURRENCY_JOBS ? need : CONCURRENCY_JOBS ))
    if (( wc >= 2 && CONCURRENCY_JOBS >= 2 && need >= 2 )); then
      if (( qmax < need )); then
        echo "    FAIL: expected max in-flight >= $need ($wc worker(s) registered, ${CONCURRENCY_JOBS} jobs)"
        fail=1
      else
        echo "    OK: parallel execution observed (need >= $need)"
      fi
      if (( used_n < need && used_n < wc )); then
        echo "    WARN: only $used_n distinct worker(s) ran jobs (expected up to $need)"
      fi
    else
      echo "    note: $wc worker(s) — parallel assertion skipped (scale remote/primary workers to test overlap)"
    fi
  done

  for jid in "${jids[@]}"; do
  local wn
  wn=$(worker_for_job "$jid")
  echo "  job $jid -> ${wn:-unknown}"
  done

  if [[ "$fail" -ne 0 ]]; then
    echo "=== FAIL: concurrency test ==="
    return 1
  fi
  echo "=== PASS: concurrency test ==="
  return 0
}

# --- main ---
echo "=== Remote worker test (mode=${TEST_MODE}) ==="
curl -fsS "${AUTH[@]}" "$API/health" >/dev/null && echo "API OK" || { echo "API not reachable at $API"; exit 1; }

scaled_down=0
restore_primary() {
  if [[ "$scaled_down" == "1" ]]; then
    echo "==> Restore primary workers (start only, no recreate)"
    docker compose start ifctester-worker ifcclash-worker ifcpatch-worker ifcdiff-worker 2>/dev/null || \
      docker compose up -d --no-recreate ifctester-worker ifcclash-worker ifcpatch-worker ifcdiff-worker
  fi
}
trap restore_primary EXIT

if [[ "$ISOLATE_REMOTE" == "1" ]]; then
  echo "==> Stopping primary ifctester/ifcpatch/ifcclash/ifcdiff (ISOLATE_REMOTE=1)"
  docker compose stop ifctester-worker ifcclash-worker ifcdiff-worker 2>/dev/null || true
  docker compose stop ifcpatch-worker 2>/dev/null || true
  scaled_down=1
  sleep 5
fi

want_all="ifctester,ifcpatch,ifcclash,ifcdiff"
case "$TEST_MODE" in
  concurrency) want_all="$CONCURRENCY_QUEUES" ;;
esac
check_queue_coverage "$want_all" || exit 1

ensure_samples

overall_fail=0
case "$TEST_MODE" in
  smoke)
    run_smoke_test || overall_fail=1
    ;;
  concurrency)
    run_concurrency_test || overall_fail=1
    ;;
  full)
    run_smoke_test || overall_fail=1
    run_concurrency_test || overall_fail=1
    ;;
  *)
    echo "error: TEST_MODE must be smoke, concurrency, or full (got: $TEST_MODE)" >&2
    exit 1
    ;;
esac

if [[ "$overall_fail" -eq 0 ]]; then
  echo ""
  echo "=== PASS: remote worker test (${TEST_MODE}) ==="
else
  echo ""
  echo "=== FAIL: remote worker test (${TEST_MODE}) ==="
  exit 1
fi
