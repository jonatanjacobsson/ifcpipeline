#!/usr/bin/env bash
# run-kernel-experiment.sh - run one labelled hunt round, snapshot
# kernel/THP/MGLRU state, then tally attempts vs crashes.
#
# Usage:
#   bash run-kernel-experiment.sh <label> [N] [M] [ROUNDS]
# Env passed through to host-parallel-hunt.sh:
#   HUNT_PR_SET_THP_DISABLE
#   HUNT_MLOCK_WRAPPER
#   HUNT_MADV_NOHUGEPAGE_WRAPPER
#   HUNT_PIN_BLAS_THREADS
#   HUNT_PIN_MALLOC_ARENA
#
# Result row is appended to /var/crash/cores/kernel-experiment-results.tsv

set -uo pipefail

LABEL=${1:?usage: $0 <label> [N M ROUNDS]}
N=${2:-8}
M=${3:-3}
ROUNDS=${4:-2}

HUNT=/home/bimbot-ubuntu/apps/ifcpipeline/scripts/host-parallel-hunt.sh
RESULTS=/var/crash/cores/kernel-experiment-results.tsv

echo "========================================"
echo "EXPERIMENT: ${LABEL}"
echo "  N=${N} M=${M} ROUNDS=${ROUNDS}  (total attempts = $((N * M * ROUNDS)))"
echo "  HUNT_PR_SET_THP_DISABLE=${HUNT_PR_SET_THP_DISABLE:-0}"
echo "  HUNT_MLOCK_WRAPPER=${HUNT_MLOCK_WRAPPER:-0}"
echo "  HUNT_MADV_NOHUGEPAGE_WRAPPER=${HUNT_MADV_NOHUGEPAGE_WRAPPER:-0}"
echo "  HUNT_PIN_MALLOC_ARENA=${HUNT_PIN_MALLOC_ARENA:-0}"
echo "  HUNT_PIN_BLAS_THREADS=${HUNT_PIN_BLAS_THREADS:-0}"
echo "host kernel state:"
echo "  THP enabled  = $(cat /sys/kernel/mm/transparent_hugepage/enabled)"
echo "  THP defrag   = $(cat /sys/kernel/mm/transparent_hugepage/defrag)"
echo "  MGLRU        = $(cat /sys/kernel/mm/lru_gen/enabled 2>/dev/null)"
echo "  swappiness   = $(sysctl -n vm.swappiness)"
echo "  AnonHugePages= $(awk '/AnonHugePages:/{print $2,$3}' /proc/meminfo)"

# Capture core inventory before
pre_cores=$(ls /var/crash/cores/core-* 2>/dev/null | sort)

# Run the hunt
start=$(date +%s)
bash "${HUNT}" "${N}" "${M}" "${ROUNDS}" > "/tmp/exp-${LABEL}.log" 2>&1
hunt_rc=$?
end=$(date +%s)

# Parse the run's OUT dir from the log
OUT=$(grep '^  OUT' "/tmp/exp-${LABEL}.log" | awk '{print $NF}')
if [ -z "${OUT}" ]; then
    echo "could not find OUT dir, see /tmp/exp-${LABEL}.log"
    exit 1
fi

# Tally
post_cores=$(ls /var/crash/cores/core-* 2>/dev/null | sort)
new_cores=$(comm -13 <(echo "${pre_cores}") <(echo "${post_cores}") | wc -l)
attempts=$(find "${OUT}" -name '*.start' | wc -l)
oks=$(find "${OUT}" -name '*.ok' | wc -l)
fhs=$(find "${OUT}" -name '*.faulthandler' -size +0c | wc -l)
total_planned=$((N * M * ROUNDS))
elapsed=$((end - start))

# Tag OUT dir with label so we can find it later
TARGET=/var/crash/cores/exp-${LABEL}-$(basename "${OUT}" | sed 's/host-hunt-//')
mv "${OUT}" "${TARGET}"

# Write a tiny stamp file into the dir
{
    echo "label=${LABEL}"
    echo "ts=$(date -u +%FT%TZ)"
    echo "N=${N} M=${M} ROUNDS=${ROUNDS}"
    echo "HUNT_PR_SET_THP_DISABLE=${HUNT_PR_SET_THP_DISABLE:-0}"
    echo "HUNT_MLOCK_WRAPPER=${HUNT_MLOCK_WRAPPER:-0}"
    echo "HUNT_MADV_NOHUGEPAGE_WRAPPER=${HUNT_MADV_NOHUGEPAGE_WRAPPER:-0}"
    echo "HUNT_PIN_MALLOC_ARENA=${HUNT_PIN_MALLOC_ARENA:-0}"
    echo "HUNT_PIN_BLAS_THREADS=${HUNT_PIN_BLAS_THREADS:-0}"
    echo "THP_enabled=$(cat /sys/kernel/mm/transparent_hugepage/enabled)"
    echo "MGLRU=$(cat /sys/kernel/mm/lru_gen/enabled 2>/dev/null)"
    echo "attempts=${attempts} oks=${oks} faulthandlers=${fhs} new_cores=${new_cores}"
    echo "elapsed_s=${elapsed}"
} > "${TARGET}/EXPERIMENT.stamp"

# Append to results table
mkdir -p "$(dirname "${RESULTS}")"
if [ ! -f "${RESULTS}" ]; then
    echo -e "label\tdate\tN\tM\tR\tattempts\tok\tcores\tcrash_rate\tTHP\tMGLRU\tprctl_off\tmlock\tmadv_nohuge\tmalloc_arena\telapsed_s" > "${RESULTS}"
fi
crash_rate="-"
if [ "${attempts}" -gt 0 ]; then
    crash_rate=$(awk -v c=${new_cores} -v a=${attempts} 'BEGIN{printf "%.1f%%", 100*c/a}')
fi
echo -e "${LABEL}\t$(date -u +%FT%TZ)\t${N}\t${M}\t${ROUNDS}\t${attempts}\t${oks}\t${new_cores}\t${crash_rate}\t$(cat /sys/kernel/mm/transparent_hugepage/enabled | tr -d '[]')\t$(cat /sys/kernel/mm/lru_gen/enabled 2>/dev/null)\t${HUNT_PR_SET_THP_DISABLE:-0}\t${HUNT_MLOCK_WRAPPER:-0}\t${HUNT_MADV_NOHUGEPAGE_WRAPPER:-0}\t${HUNT_PIN_MALLOC_ARENA:-0}\t${elapsed}" >> "${RESULTS}"

echo
echo "=== ${LABEL} result ==="
echo "  attempts=${attempts}  ok=${oks}  faulthandlers=${fhs}  cores=${new_cores}  rate=${crash_rate}"
echo "  output dir: ${TARGET}"
echo "  elapsed: ${elapsed}s"
echo
echo "=== results table so far ==="
column -t -s $'\t' "${RESULTS}"
