#!/usr/bin/env bash
# run-phase10-block.sh - Run K campaigns of run-kernel-experiment.sh with a
# common <label_base> and a per-campaign suffix (1..K).  Optionally clears
# kernel cores before each campaign so the new_cores tally is honest.
#
# Usage:
#   bash run-phase10-block.sh <label_base> <K> [N=8] [M=3] [ROUNDS=2]
#
# Env passed through to run-kernel-experiment.sh / host-parallel-hunt.sh:
#   HUNT_PIN_BLAS_THREADS=0|1   (runtime-r1 etc.)
#   PHASE10_CLEAR_CORES=1       (rm /var/crash/cores/core-* before each)

set -uo pipefail

LABEL_BASE=${1:?usage: $0 <label_base> <K> [N M ROUNDS]}
K=${2:?usage: $0 <label_base> <K> [N M ROUNDS]}
N=${3:-8}
M=${4:-3}
ROUNDS=${5:-2}

RESULTS=/var/crash/cores/kernel-experiment-results.tsv
RUNNER=/home/bimbot-ubuntu/apps/ifcpipeline/scripts/run-kernel-experiment.sh
SUDO_PASS=${SUDO_PASS:-rusksele}

total_attempts=0
total_cores=0

for i in $(seq 1 "${K}"); do
    if [ "${PHASE10_CLEAR_CORES:-1}" = "1" ]; then
        echo "${SUDO_PASS}" | sudo -S sh -c 'rm -f /var/crash/cores/core-*' || true
    fi
    LABEL="${LABEL_BASE}-${i}"
    echo "######## ${LABEL} (campaign ${i}/${K}) ########"
    bash "${RUNNER}" "${LABEL}" "${N}" "${M}" "${ROUNDS}" 2>&1 | tail -8

    last_row=$(grep -P "^${LABEL}\t" "${RESULTS}" | tail -1)
    if [ -n "${last_row}" ]; then
        attempts=$(echo "${last_row}" | awk -F'\t' '{print $6}')
        cores=$(echo "${last_row}" | awk -F'\t' '{print $8}')
        rate=$(echo "${last_row}" | awk -F'\t' '{print $9}')
        echo "  >> ${LABEL}: attempts=${attempts} cores=${cores} rate=${rate}"
        total_attempts=$((total_attempts + attempts))
        total_cores=$((total_cores + cores))
    fi
done

echo
echo "================= BLOCK SUMMARY: ${LABEL_BASE} ================="
echo "  total_attempts=${total_attempts}  total_cores=${total_cores}"
if [ "${total_attempts}" -gt 0 ]; then
    python3 -c "import math; c=${total_cores}; n=${total_attempts}; p=c/n; z=1.96; d=1+z**2/n; ce=(p+z**2/(2*n))/d; sp=z/d*math.sqrt(p*(1-p)/n+z**2/(4*n*n)); print(f'  pooled rate = {100*p:.1f}% (95% Wilson CI [{100*max(0,ce-sp):.1f}, {100*min(1,ce+sp):.1f}])')"
fi
