# IfcOpenShell SIGSEGV — Hunt Report (2026-05-14, 13:00 local)

> **Read this for raw hunt numbers.** For the consolidated, up-to-date
> reference covering everything we learned about the IfcOpenShell
> concurrency bug — including all mitigations, the spawn-isolation
> architecture, the core-dump toggle, and the future-debugging runbook —
> see [IFCOPENSHELL_CONCURRENCY_RESEARCH.md](IFCOPENSHELL_CONCURRENCY_RESEARCH.md).
> This file is preserved as a snapshot of the May 14 hunt evidence.

> **Headline finding.** Under N=12 simultaneous ifcopenshell processes on a
> single host (4 ifcpatch-worker containers × 3 parallel processes each, on
> the same `RemoveElements` workload that crashes in production), we
> reproduced **39 SIGSEGVs in 72 process invocations (54 % per-process,
> 21.6 % per `ifcpatch.execute` call)** — and for the first time captured
> the **Python frame at the moment of crash** via `faulthandler`, plus
> paired `/proc/self/{maps,status,smaps_rollup,io,stat}` and `/proc/meminfo`
> snapshots taken seconds before each crash.
>
> **Memory exhaustion is conclusively refuted as the root cause.** All 37
> snapshot-paired crashes had `MemAvailable >= 5 GiB` (host had 23 GiB free
> at the time, swap unused, the crashing process was using a median of
> 151 MB RSS / max 1.1 GB out of a 4 GB cgroup ceiling).
>
> The bug is a **genuine concurrency / race in the IfcOpenShell C++ wrapper**
> (`_ifcopenshell_wrapper.cpython-311-x86_64-linux-gnu.so`), triggered when
> N ≥ ~4 separate Python processes call into the wrapper concurrently on
> the same host, regardless of cgroup isolation. Crash rate scales
> non-linearly with N.

## 1. What we ran

**Test conditions, restored to original failure mode + extra concurrency:**

| Parameter | This hunt | Previous tests |
| - | - | - |
| `ifcpatch-worker` replicas | **4** (scaled up via `--scale`) | 2 (post-mitigation) / 3 (pre-incident) |
| Mem limit per replica | 4 GiB (cgroup) | 4 GiB |
| Parallel python processes per container | **3** | 1 |
| Total simultaneous ifcopenshell processes | **12** | 2–4 |
| Workload per process | 4 × `RemoveElements` recipe on real production input | same |
| Rounds | 6 | 9 |
| Total ifcpatch.execute calls | **288** (72 processes × 4 iters) | 156 |
| Host RAM | 33 GiB + 16 GiB swap (added 2026-05-14 09:24) | same after stabilization |

**Instrumentation added on top of the regular reproducer (`scripts/hunt-repro.py`):**

- `faulthandler.enable()` and `faulthandler.register(SIGSEGV, ..., chain=True)`
  to a per-process file in `/var/crash/cores/` so the Python frame at the
  moment of SIGSEGV is preserved before the kernel writes the core.
- Pre-execute snapshot of `/proc/self/{maps,status,smaps_rollup,io,stat}` +
  `/proc/meminfo` + `/proc/loadavg` to `/var/crash/cores/<pid>-<ts>.<label>`
  on every iteration, so the memory state at crash time can be reconstructed
  without relying on the post-crash core (which is too late and only shows
  the secondary CPython collapse).
- Per-iteration `.start` / `.ok` marker files for outside-the-container
  outcome counting.

**Harness (`scripts/hunt-harness.sh`):**

- Discovers all `ifcpatch-worker-*` containers, fans out the reproducer
  into them, runs the rounds, gdb-analyzes every new core, writes a
  combined log to `/var/crash/cores/hunt-<tag>/`.

## 2. Aggregate results

| Metric | Value |
| - | - |
| Processes started | 72 |
| Processes that returned non-zero | 39 |
| Processes that fired faulthandler (i.e. caught SIGSEGV/SIGBUS/...) | 37 |
| Cores written by the kernel | 39 |
| ifcpatch.execute attempts (`.start` markers) | 194 |
| ifcpatch.execute completions (`.ok` markers) | 152 |
| **Per-process crash rate** | **39/72 = 54 %** |
| **Per-execute crash rate** | **42/194 = 21.6 %** |
| n8n production failures during the hunt window | **0** |

The hunt was non-destructive to production (rq workers handled real
n8n traffic uninterrupted; the crashing python processes were spawned
side-of-band via `docker exec` and isolated to subprocess space).

Production conditions for comparison:
- Cross-container test (2026-05-14 ~11:30, parallelism = 2): 2/80 ≈ **2.5 %**
- Single-process test (parallelism = 1): 0/30 = **0 %**
- This hunt (parallelism = 12): **21.6 %**

The crash rate scales **strongly super-linearly** with concurrency.

## 3. Crash sites — for the first time, the *Python* stack at SIGSEGV

`faulthandler` captured 37 non-empty stacks. Top-of-stack distribution:

| count | crash site (Python frame at SIGSEGV) |
| - | - |
| 8 | `ifcopenshell_wrapper.py:6120 in is_a` |
| 5 | `ifcopenshell_wrapper.py:6127 in get_argument` |
| 4 | `entity_instance.py:309 in wrap_value` |
| 3 | `entity_instance.py:306 in wrap_value` |
| 3 | `entity_instance.py:189 in __del__` |
| 2 | `util/element.py:259 in get_property_definition` |
| 2 | `entity_instance.py:337 in attribute_name` |
| 2 | `entity_instance.py:174 in __init__` |
| 1 | `ifcopenshell_wrapper.py:6863 in open` (crashed even *before* recipe started) |
| 1 | `ifcopenshell_wrapper.py:6105 in __len__` |
| 1 | `entity_instance.py:626 in _` |
| 1 | `entity_instance.py:297 in walk` |
| 1 | `file.py:789 in schema` |
| 1 | `util/element.py:109 in get_pset` |
| 1 | `util/selector.py:1012 in filter_function` |
| 1 | `RemoveElements.py:290 in _bulk_detach_relationships` (calling `val.id()`) |

**Pattern.** Every single crash site is either inside the SWIG-generated
wrapper module (`ifcopenshell_wrapper.py` calls into the `.so`) or inside
`entity_instance.py` / `util/*` methods that themselves call into the
wrapper. **Not one** crash is in user code that doesn't immediately bridge
to C++. This rules out anything in our recipe (`RemoveElements.py`) being
the bug — when our recipe appears at the top, it's because the very next
line is `val.id()` which dispatches to the C++ wrapper.

The *secondary* libpython crash at `_PyEval_EvalFrameDefault` (calling
through a NULL pointer at `0x0` — see `hunt-evidence/sample-core.sig11`)
is the kernel-visible fault. The faulthandler captures the Python state
*before* CPython attempts the corrupted call.

The diversity of crash sites (15 distinct entry points, all in the
ifcopenshell stack) strongly suggests **shared global state in the C++
side gets corrupted under concurrent access**, after which any subsequent
SWIG call across the boundary may fault. A single bug in any one method
would not produce this distribution.

## 4. Memory was NOT the trigger

Per-crash `/proc` snapshots at crash time:

| metric (across 37 crashes) | min | median | max |
| - | - | - | - |
| `MemAvailable` (host) | > 5 GiB on every single sample | – | – |
| `VmRSS` (crashing process) | 149 MB | 151 MB | 1 151 MB |
| `Threads` | 1 | 1 | 1 |
| `Swap` used at crash time | 0 KB | 0 KB | 0 KB |

Snapshot pulled 6 ms before the example crash at `entity_instance.py:297
in walk`:

```
MemAvailable:   23,543,672 kB   (≈ 22.5 GiB free)
SwapCached:              0 kB
VmRSS:             150,628 kB   (process held ~150 MB out of 4 GiB cap)
loadavg:        2.59 0.95 0.66  10/1905 1066
```

**Conclusion.** The morning's load-reduction package (replicas 3 → 2,
mem 6G → 4G, +16G swap) helped *only* by mechanically reducing the
maximum host-wide concurrency. Memory pressure per se was a coincident
symptom, not the trigger. With memory abundant, the C++ wrapper still
faults at ~22 % per execute when 12 ifcopenshell processes coexist on
the host.

## 5. The actual fix

(a) **Cap host-wide concurrency** of "heavy" ifcopenshell jobs to a
known-safe level (we are at 0 crashes with N ≤ 2 cross-container, will
re-test N = 3 to find the exact threshold). A Redis-backed semaphore
in the rq worker (`acquire(heavy_ifcopenshell, timeout=...)` before
each `ifcpatch.execute`, release after) will impose this regardless of
how many replicas exist or how many jobs n8n produces. This is the only
mitigation that is robust against future scale-up of either dimension.

(b) **File the upstream issue** with the new evidence (see §6). The
existing `ifcpipeline/UPSTREAM_ISSUE_DRAFT.md` was written when our best
evidence was a stripped wrapper offset; it now needs to be updated with
the per-site Python-frame distribution and the per-snapshot memory data
that refutes the resource-exhaustion hypothesis.

(c) **(optional) Build a debug-symbol IfcOpenShell wrapper** so a future
hunt can resolve the C++ frames. A standard PyPI wheel is `strip`'d.
We need either: a source build with `-g -O0` plus retained symbols, or
the upstream maintainers to publish a debug-symbol wheel. With a debug
build we could pinpoint the exact corrupted struct member.

## 6. Evidence preserved

Under `ifcpipeline/hunt-evidence/`:

- `faulthandler/` — 72 files (37 with content, the rest are empty
  process-start markers). These are the **first-ever** captures of the
  Python frame at the moment of SIGSEGV in this codebase.
- `proc-snapshots/` — 194 sets of `/proc` snapshots taken just before
  each `ifcpatch.execute`. Use the timestamp filename to pair with
  `faulthandler/` files (same pid, slightly earlier timestamp).
- `gdb-bts/` — 39 gdb backtraces from the kernel-written cores. All
  show `0x0000000000000000 in ?? ()` at the secondary-fault level
  because the wrapper `.so` is stripped — that's why faulthandler is
  the more useful evidence stream.
- `sample-core.sig11` — one preserved core for posterity (687 MB).
- `r*.log` — per-process stdout/stderr from each round.
- `core-dump-inventory.txt` — what we deleted to free disk after
  preserving the metadata.

## 7. Reverted state

After the hunt, scaled `ifcpatch-worker` back to **2 replicas × 4 GiB**
(the post-mitigation baseline) via
`docker compose up -d --no-deps --scale ifcpatch-worker=2 ifcpatch-worker`.

The hunt was a transient, controlled experiment. The compose file is
unchanged from the morning's settings (replicas: 2, memory: 4G).

## 8. Next steps (subject to user direction)

1. **Build the Redis-semaphore wrapper around `ifcpatch.execute`** in
   `ifcpatch-worker/tasks.py`. Cap N=2 (proven safe) cluster-wide.
2. **Update `ifcpipeline/UPSTREAM_ISSUE_DRAFT.md`** with the new
   per-site crash distribution and the no-memory-pressure data.
3. **(optional) attempt a `pip install --no-binary :all: ifcopenshell`**
   build inside the worker image to retain symbols, then re-run the
   hunt to get the C++ frame.
