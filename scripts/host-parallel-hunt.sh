#!/usr/bin/env bash
# host-parallel-hunt.sh - Fan out N copies of repro-local-wrapper.py
# on the host (no docker), each running M iterations, using the
# locally-built debug-symbol ifcopenshell.
#
# Usage:
#   bash host-parallel-hunt.sh [N=8] [M=4] [ROUNDS=3]
#
# Output: per-process logs + faulthandler frames + /proc snapshots
# under /var/crash/cores/host-hunt-<tag>/ and any kernel cores in
# /var/crash/cores/.
#
# Environment:
#   IFCOPENSHELL_TREE  path to locally-built IfcOpenshell repo (defaults
#                      to /home/bimbot-ubuntu/apps/IfcOpenshell)
#   PYTHON             default python3.10
#
# When a kernel core appears, gdb-analyse it with the wrapper .so so
# symbols resolve, save the backtrace next to the python frame.

set -uo pipefail

N=${1:-8}
M=${2:-4}
ROUNDS=${3:-3}

IFCOPENSHELL_TREE=${IFCOPENSHELL_TREE:-/home/bimbot-ubuntu/apps/IfcOpenshell}
PYTHON=${PYTHON:-python3.10}
INPUT_PATH=${INPUT_PATH:-/tmp/repro-ifcpatch/input.ifc}
REPRO_SCRIPT=${REPRO_SCRIPT:-/home/bimbot-ubuntu/apps/ifcpipeline/scripts/repro-local-wrapper.py}

TAG="$(date -u +%Y%m%d-%H%M%S)"
OUT=/var/crash/cores/host-hunt-${TAG}
mkdir -p "${OUT}"

WRAPPER_SO_GLOB="${IFCOPENSHELL_TREE}/build/ifcwrap/_ifcopenshell_wrapper.cpython-*.so"
WRAPPER_SO=$(ls ${WRAPPER_SO_GLOB} 2>/dev/null | head -1 || true)
if [ -z "${WRAPPER_SO}" ]; then
    echo "ERROR: no built wrapper found at ${WRAPPER_SO_GLOB}" >&2
    exit 2
fi
if ! file "${WRAPPER_SO}" | grep -q "not stripped"; then
    echo "WARNING: wrapper appears stripped: ${WRAPPER_SO}" >&2
fi

if [ ! -f "${INPUT_PATH}" ]; then
    echo "ERROR: input file missing: ${INPUT_PATH}" >&2
    echo "  download once with: ${PYTHON} ${REPRO_SCRIPT%/repro-local-wrapper.py}/fetch-input-from-minio.py" >&2
    exit 2
fi

echo "=== host-parallel-hunt config ==="
echo "  IFCOPENSHELL_TREE = ${IFCOPENSHELL_TREE}"
echo "  WRAPPER_SO        = ${WRAPPER_SO}"
echo "  PYTHON            = ${PYTHON} ($(${PYTHON} --version))"
echo "  INPUT_PATH        = ${INPUT_PATH} ($(stat -c %s "${INPUT_PATH}") bytes)"
echo "  N (parallel)      = ${N}"
echo "  M (iters/proc)    = ${M}"
echo "  ROUNDS            = ${ROUNDS}"
echo "  OUT               = ${OUT}"
echo "  core_pattern      = $(cat /proc/sys/kernel/core_pattern)"
echo

mark_cores_before=$(ls -1 /var/crash/cores/core-* 2>/dev/null | sort)

ulimit -c unlimited

PYPATH="${IFCOPENSHELL_TREE}/src/ifcopenshell-python"

# --- Phase 6 hypothesis ---
# Numpy is loaded transitively (shapely <- ifcopenshell.util.selector) and
# starts an OpenBLAS thread pool of `ncpu` background threads per process.
# Under N=8 fan-out that is 80+ threads competing for glibc allocator and
# TLS canary slots.  HUNT_PIN_BLAS_THREADS=1 forces those pools to a
# single thread, isolating that variable.
if [ "${HUNT_PIN_BLAS_THREADS:-0}" = "1" ]; then
    echo "  HUNT_PIN_BLAS_THREADS=1 ==> pinning OPENBLAS/OMP/MKL/NUMEXPR/BLIS thread pools to 1"
    export OPENBLAS_NUM_THREADS=1
    export OMP_NUM_THREADS=1
    export MKL_NUM_THREADS=1
    export NUMEXPR_NUM_THREADS=1
    export VECLIB_MAXIMUM_THREADS=1
    export BLIS_NUM_THREADS=1
fi

# Glibc malloc arena hypothesis -- under multi-thread / multi-process
# pressure glibc spawns up to 8 per-thread arenas which all live in the
# same address space.  HUNT_PIN_MALLOC_ARENA=1 forces a single arena.
if [ "${HUNT_PIN_MALLOC_ARENA:-0}" = "1" ]; then
    echo "  HUNT_PIN_MALLOC_ARENA=1 ==> MALLOC_ARENA_MAX=1"
    export MALLOC_ARENA_MAX=1
fi

for r in $(seq 1 "${ROUNDS}"); do
    echo "--- ROUND ${r}/${ROUNDS} ---"
    pids=()
    for w in $(seq 1 "${N}"); do
        LOG="${OUT}/r${r}-w${w}.log"
        HUNT_DUMP_DIR="${OUT}/r${r}-w${w}" \
        HUNT_ITERATIONS="${M}" \
        INPUT_PATH="${INPUT_PATH}" \
        PYTHONPATH="${PYPATH}" \
            "${PYTHON}" "${REPRO_SCRIPT}" "${M}" > "${LOG}" 2>&1 &
        pids+=("$!")
    done

    # Wait for all workers and capture exit codes
    fail=0
    ok=0
    for pid in "${pids[@]}"; do
        if wait "${pid}"; then
            ok=$((ok + 1))
        else
            ec=$?
            fail=$((fail + 1))
            echo "    pid ${pid} exited ${ec}"
        fi
    done
    echo "  round ${r}: ok=${ok}/N=${N}  fail=${fail}"
done

# inventory new cores
echo
echo "=== new kernel cores in /var/crash/cores ==="
mark_cores_after=$(ls -1 /var/crash/cores/core-* 2>/dev/null | sort)
new_cores=$(comm -13 <(echo "${mark_cores_before}") <(echo "${mark_cores_after}") || true)
if [ -n "${new_cores}" ]; then
    echo "${new_cores}" | sed 's/^/  /'
    # gdb each
    echo
    echo "=== gdb backtraces (debug-symbol wrapper => primary C++ frame) ==="
    while read -r core; do
        [ -z "${core}" ] && continue
        BT="${OUT}/$(basename "${core}").bt"
        echo "  ${core} -> ${BT}"
            # Pass the python executable (so libpython frames resolve too)
            # plus solib-search-path pointing at the built .so dir, so the
            # core's mapping of _ifcopenshell_wrapper resolves to OUR debug
            # build.  The wrapper is mmap'd from the source-tree symlink
            # path (which is what the kernel records in the core), so we
            # also tell gdb to honour the executable's "set pagination off"
            # before any potentially-blocking command.
            gdb -batch \
                -ex "set pagination off" \
                -ex "set sysroot /" \
                -ex "set solib-search-path $(dirname "${WRAPPER_SO}"):${IFCOPENSHELL_TREE}/src/ifcopenshell-python/ifcopenshell" \
                -ex "core-file ${core}" \
                -ex "bt 50" \
                -ex "info registers" \
                -ex "thread apply all bt 30" \
                /usr/bin/python3.10 > "${BT}" 2>&1 || true
    done <<< "${new_cores}"
else
    echo "  (none)"
fi

echo
echo "=== faulthandler frames captured ==="
find "${OUT}" -name '*.faulthandler' -size +0 -printf '%p (%s bytes)\n'

echo
echo "=== summary ==="
total_start=$(find "${OUT}" -name '*.start' | wc -l)
total_ok=$(find "${OUT}" -name '*.ok' | wc -l)
total_fh=$(find "${OUT}" -name '*.faulthandler' -size +0 | wc -l)
new_core_count=$(echo "${new_cores}" | grep -c '/core-' || true)
echo "  ifcpatch.execute attempts    : ${total_start}"
echo "  ifcpatch.execute completions : ${total_ok}"
echo "  faulthandler frames captured : ${total_fh}"
echo "  kernel cores written         : ${new_core_count}"
echo "  output dir                   : ${OUT}"
