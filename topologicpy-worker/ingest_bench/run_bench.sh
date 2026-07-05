#!/usr/bin/env bash
# Host-side driver: sync worktree ingest code into the shared volume and run the
# benchmark inside the live topologicpy-worker container (prod cgroup limits).
#
#   ./run_bench.sh <tag> [bench.py extra args...]
#   ./run_bench.sh baseline --profile
#   ./run_bench.sh opt-spaceadj --only SpaceAdjacency_A1spaces --repeat 3
#
# BENCH_NS (env, default "main") namespaces the synced code + results so several
# agents can iterate concurrently without overwriting each other's code copy:
#   BENCH_NS=kgexport ./run_bench.sh kg-opt1 --only KnowledgeGraphExport_A1spaces
#
# Results land in shared/output/bench/<ns>/results/<tag>.json (host path).
# NOTE: timing runs contend for the container's 2 CPUs — only compare timings
# from serial runs; fingerprints are valid regardless.
set -euo pipefail

TAG="${1:?usage: run_bench.sh <tag> [extra bench.py args]}"
shift || true

NS="${BENCH_NS:-main}"
WORKER_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHARED_BENCH="/home/bimbot-ubuntu/apps/ifcpipeline/shared/output/bench/${NS}"
CONTAINER="ifcpipeline-topologicpy-worker-1"

mkdir -p "$SHARED_BENCH/src" "$SHARED_BENCH/results"
rsync -a --delete \
  --exclude '__pycache__' --exclude '*.pyc' \
  "$WORKER_SRC/ingest_scripts" "$WORKER_SRC/ingest_bench" "$WORKER_SRC/space_cache.py" \
  "$SHARED_BENCH/src/"

docker exec -e PYTHONPATH="/output/bench/${NS}/src" \
  ${INGEST_TGRAPH_CACHE+-e INGEST_TGRAPH_CACHE="$INGEST_TGRAPH_CACHE"} \
  "$CONTAINER" \
  python "/output/bench/${NS}/src/ingest_bench/bench.py" \
  --matrix "/output/bench/${NS}/src/ingest_bench/matrix.json" \
  --out "/output/bench/${NS}/results/${TAG}.json" \
  "$@"

echo "results: $SHARED_BENCH/results/${TAG}.json"
