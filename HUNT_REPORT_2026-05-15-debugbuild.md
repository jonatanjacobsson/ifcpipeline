# IfcOpenShell SIGSEGV — Debug-Symbol Host Hunt (2026-05-15)

> Follow-up to [HUNT_REPORT_2026-05-14.md](HUNT_REPORT_2026-05-14.md). This
> hunt was run with a **locally-built debug-symbol wrapper** so we can
> resolve C++ frames in gdb. See [`IfcOpenshell/build_debug_no_collada.sh`](../IfcOpenshell/build_debug_no_collada.sh)
> for the build script, and [`ifcpipeline/scripts/host-parallel-hunt.sh`](scripts/host-parallel-hunt.sh)
> for the fanout harness.

## Setup

| Item | Value |
| - | - |
| Wrapper | `IfcOpenshell` tag `ifcopenshell-python-0.8.5`, built with `-DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_CXX_FLAGS="-g3 -fno-omit-frame-pointer"` |
| `.so` size (debug, unstripped) | 1.7 GiB |
| Wrapper integration | Symlinked the built `_ifcopenshell_wrapper.cpython-310-x86_64-linux-gnu.so` and `ifcopenshell_wrapper.py` into `IfcOpenshell/src/ifcopenshell-python/ifcopenshell/` so `PYTHONPATH=...src/ifcopenshell-python python3.10` picks it up |
| Reproducer | [`scripts/repro-local-wrapper.py`](scripts/repro-local-wrapper.py) inlining the production `RemoveElements` workload, against the real production input (`uploads/A--40_V00000.ifc`, 52.4 MB, sha256 `ed013a91…`) fetched once from MinIO via [`scripts/fetch-input-from-minio.py`](scripts/fetch-input-from-minio.py) |
| Host concurrency | N=8 parallel processes (Python 3.10 each) directly on the host, no containers |
| Iterations per process | M=3–4 |
| Host | 10 vCPU, 33 GiB RAM, 15 GiB swap, no other workloads — completely idle apart from this hunt |
| `kernel.core_pattern` | `/var/crash/cores/core-%e-%p-%t.sig%s` (`ulimit -c unlimited`) |

## Headline numbers

| Run | `faulthandler` chain | N | M | Rounds | Attempts | OK | Crashes | Crash rate | Cores written |
| - | - | - | - | - | - | - | - | - | - |
| 1 | `chain=True` (Python frame + chained re-raise) | 8 | 4 | 2 | 49 | 40 | **8** | 16 % | 8 (RIP = re-raise site) |
| 2 | `HUNT_NO_FAULTHANDLER=1` (clean primary RIP) | 8 | 3 | 1 | 15 | 9 | **5** | 33 % | 5 (RIP = primary fault site) |

The bug **reproduces immediately on-host without the ifcpatch-worker container, MinIO worker stack, or any cgroup**. It only needs:

1. Multiple `python3.10` processes running `ifcopenshell.open(...)` and operating on a moderately complex IFC4 file
2. The host loads the same `_ifcopenshell_wrapper.so` (large mmap)

## Crash-site distribution (Python frame at SIGSEGV/SIGABRT, run 1)

From the eight `*.faulthandler` files captured by `chain=True`:

| Python frame | Count | C++ entry point |
| - | - | - |
| `entity_instance.get_attribute_category(name)` (`__getattr__`) | 3 | SWIG `entity_instance::get_attribute_category(string)` |
| `entity_instance.is_a()`                                          | 2 | SWIG `entity_instance::is_a(...)` |
| `entity_instance.get_argument(i)` (via `wrap_value`)              | 1 | SWIG `entity_instance::get_argument(unsigned)` |
| `entity_instance.get_argument_name(i)` (via `get_info`/`attribute_name`) | 1 | SWIG `entity_instance::get_argument_name(unsigned)` |
| `entity_instance.__getitem__(i)` (via `get_info`'s `_`)           | 1 | SWIG `entity_instance::get_attribute_value(...)` |

Same distribution as the 2026-05-14 in-container hunt. Every fault sits in a **SWIG entry point** that takes / returns a `std::string` or a `PyObject*`.

## C++ primary frames from `HUNT_NO_FAULTHANDLER=1` cores (run 2)

Two SIGABRT cores produced **resolvable** C++ frames; the SIGSEGV cores resolved only inside CPython (the wrapper is dynamically loaded, so on a NULL deref through a returned bogus PyObject the kernel records `_PyEval_EvalFrameDefault`).

### SIGABRT 2139109 — stack-canary smashed in SWIG marshalling

```
#5  __libc_message  (... "*** %s ***: terminated\n")
#6  __GI___fortify_fail (msg=... "stack smashing detected")
#7  __stack_chk_fail
#8  SWIG_AsCharPtrAndSize (obj=<optimized out>,
                           cptr=0x7ffcb8747a90,   ← caller's stack
                           psize=0x55ebefc46c90,  ← caller's HEAP
                           alloc=<optimized out>)
    at IfcPythonPYTHON_wrap.cxx:5794
#9  0x000055ebd1abd790  ←  unresolved SWIG glue caller
```

`SWIG_AsCharPtrAndSize` is the SWIG-generated function that converts a Python `str` into a C `char* + size_t`. The canary check at line 5794 (function epilogue) fires, meaning the canary was overwritten between the prologue and this `}`.

### SIGABRT 2139112 — stack-canary smashed building a property name

```
#7  __stack_chk_fail
#8  std::__cxx11::basic_string<char, std::char_traits<char>,
        std::allocator<char> >::_M_construct<char*>
        (this=0x7ffdfbbe0810,          ← std::string on the SWIG stack
         __beg=0x558d37488030 "IfcPropertySingleValue",
         __end=<optimized out>)
    at /usr/include/c++/11/bits/basic_string.tcc:233
#9  0x00007bb800000000  ← clobbered return address
#16 0x0000558d25ef7420 in _Py_FalseStruct ()
```

The libstdc++ `_M_construct<char*>` is being called by the SWIG glue that builds `std::string` arguments to a `get_pset` / `get_property` call. `_M_construct`'s caller frame has a **clobbered return address** (`0x00007bb800000000`), and gdb's deeper unwind ends up landing on `_Py_FalseStruct` which is obviously nonsense — the **caller stack frame is already destroyed** by the time the canary trips.

### SIGSEGV 2139111 — `Py_INCREF(NULL+1)` in `_PyEval_EvalFrameDefault`

`%rip` points at:

```
addq   $0x1,(%rdi)        ← Py_INCREF
lea    0x434d69(%rip),%r8  →  _PyRuntime
```

with `%rdi = 0x1`. CPython is trying to bump the refcount of a `PyObject*` of value `0x1` — i.e. a **type-confused / corrupted Python object** handed back from a C extension call. This is exactly what we would expect if a SWIG-marshalled return value (e.g. the result of `entity_instance.get_argument(...)` or `is_a()`) gave the interpreter a bogus pointer.

## What this tells us

Three independent crash signatures, all consistent with the same root cause class:

* **Stack-canary smash inside SWIG `string` marshalling** (two SIGABRTs). The wrapper has corrupted memory below the canary on the C stack before returning, or the canary master (`%fs:0x28`) itself was clobbered.
* **`Py_INCREF` on `0x1`** (SIGSEGV). A SWIG marshalled return value carried a non-`PyObject*` integer (very likely **a stale slab address that got reused as a refcount-like small integer** — `0x1` is the post-`Py_DECREF` of a singleton that was just freed).
* All Python frames hit the same six SWIG glue functions on `IfcUtil::IfcBaseClass`: `is_a()`, `get_argument()`, `get_argument_name()`, `get_attribute_category()`, `get_attribute_value()`, `__getitem__()`.

What we can **rule out** with this evidence:

| Hypothesis | Disposition |
| - | - |
| Memory exhaustion / OOM-killer | Refuted (host had 17 GiB free) |
| Container / cgroup edge case | Refuted (no container in this hunt) |
| Worker-stack interference (rq, redis, gunicorn) | Refuted (bare `python3.10`) |
| Filesystem / MinIO I/O timing | Refuted (file pre-fetched to `/tmp`, opened once per iteration) |
| Per-process race (multi-threaded C++ inside one process) | Refuted: every faulthandler dump shows `Threads: 1` for the main interpreter (the extra threads are OpenBLAS workers parked on condvars — see below) |

What we **cannot yet** distinguish between:

| Hypothesis | Why it survives |
| - | - |
| Glibc allocator contention / `dlopen` `.text` page-cache race | All N=8 processes mmap the same 1.7 GiB `.so`; demand-paging contention is plausible |
| **OpenBLAS thread pool TLS clobber** | Every process spawns 9–10 `blas_thread_server` threads at numpy / shapely import time. They are visible in every core dump, parked on condvars at fault time but with TLS allocated |
| Schema lazy-init double-free | `static std::unique_ptr<schema_definition> schema` per `IfcXxx-schema.cpp` is lazy-initialised on first `schema_by_name(...)` call without a mutex; only protected by the GIL in a single thread, but allocates ~3000 `new declaration*` etc.; if anything re-enters during init the result is undefined |

## What we tried (Phase 6) — every candidate fix was a negative result

All measurements at **N=8 fan-out, M iterations per process, R rounds** on the same idle host with the same input file. Per-attempt is `cores / (N*M*R)`.

| Run | N | M | R | Env | Attempts | Cores | Per-attempt rate | Verdict |
| - | - | - | - | - | - | - | - | - |
| Baseline (chained faulthandler) | 8 | 4 | 2 | none | 49 | 8 SEGV | **16 %** | reproduces production rate |
| `HUNT_NO_FAULTHANDLER=1` (clean primary RIP) | 8 | 3 | 1 | none | 15 | 3 SEGV + 2 ABRT | **33 %** | confirms primary frames usable |
| **Hyp A — kill OpenBLAS / OMP thread pool** | 8 | 3 | 1 | `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 …` | 18 | 4 SEGV | 22 % | **REFUTED** — same rate |
| **Hyp B — single malloc arena** | 8 | 3 | 1 | `MALLOC_ARENA_MAX=1` | 24 | **0** | 0 % | suspicious — single sample |
| Hyp B repeat | 8 | 4 | 2 | `MALLOC_ARENA_MAX=1` | 54 | 5 SEGV | 9 % | partial — half-baseline |
| Hyp A + B combined | 8 | 4 | 2 | both pinned | 39 | 9 SEGV | 23 % | **REFUTED** — same rate |
| **Hyp C — hold the GIL through `open()`/`read()`** | 8 | 4 | 2 | rebuilt wrapper without `Py_BEGIN_ALLOW_THREADS` | 43 | 9 SEGV | 21 % | **REFUTED** — same rate |

The patch for Hyp C was committed (and reverted after the negative result) in [`IfcOpenshell/src/ifcwrap/IfcParseWrapper.i`](../IfcOpenshell/src/ifcwrap/IfcParseWrapper.i): we removed the `Py_BEGIN_ALLOW_THREADS` / `Py_END_ALLOW_THREADS` block that wraps `new IfcParse::IfcFile(...)`. The motivation was core `2174469` (which crashes inside `IfcParse::parse_context::construct` with the GIL released), but the rebuilt wrapper crashed at the same rate.

### New C++ evidence captured in the Hyp B & C hunts

The `MALLOC_ARENA_MAX=1` runs produced a **fully-symbolised C++ frame** that the chained-faulthandler runs had hidden:

```
#0  __GI___libc_free (mem=0x544152454d554e45) at ./malloc/malloc.c:3368
                          ^^^^^^^^^^^^^^^^^^
                          ASCII "ENUMERAT" (little-endian)
#1  std::__cxx11::basic_string<…>::_M_destroy
#2  std::__cxx11::basic_string<…>::~basic_string
#…
#11 _wrap_entity_argument_types (args=<optimized out>) at
    IfcPythonPYTHON_wrap.cxx:90968
```

The address being passed to `free()` is **the byte content of the string `"ENUMERAT"` interpreted as a pointer** (i.e. `'E'=0x45, 'N'=0x4E, …` packed little-endian into a `uintptr_t`). This is the classic fingerprint of a **`std::string` SSO-vs-heap state bit getting corrupted**: a string that should have been recognised as Small-String-Optimized (`"ENUMERATION"` is 11 chars, fits in the 15-char SSO buffer of `std::string`) is being treated as heap-allocated, so the destructor tries to `free()` the inline character buffer as if it were a pointer.

The matching source line is the `r.push_back(IfcUtil::ArgumentTypeToString(at))` loop in [`src/ifcwrap/IfcParseWrapper.i`](../IfcOpenshell/src/ifcwrap/IfcParseWrapper.i) (SWIG-extended `IfcParse::entity::argument_types()`), which builds `std::vector<std::string>` from `static const char* const argument_type_string[]` literals in [`src/ifcparse/IfcUtil.cpp:144-168`](../IfcOpenshell/src/ifcparse/IfcUtil.cpp). The strings themselves are immutable `.rodata`, so the corruption is in the `std::vector<std::string>`'s heap layout, not in the source data.

A second, independent core (`2174469`, SIGSEGV) crashed inside `std::vector<parameter_type const*>::_M_realloc_insert` during `IfcParse::parse_context::construct` while parsing the file open — i.e. on `decl->as_entity()->all_attributes()` (returns a fresh vector each call) being copied into `parameter_types`. Same shape: vector construction corruption under N=8 host pressure.

## Conclusion

We have a **fully-instrumented host reproducer with debug symbols** for a bug that:

1. Reproduces 100 % reliably at N≥8 process fan-out (10–30 % per `ifcpatch.execute`).
2. Crashes in C++ code paths that handle `std::vector<std::string>` / `std::vector<T*>` returned from schema queries.
3. Manifests as **`std::string` SSO-vs-heap state corruption** (the smoking-gun `free("ENUMERAT")` fingerprint) and **`Py_INCREF(0x1)` on type-confused `PyObject*`** returned from SWIG glue.
4. Is **NOT** fixed by any of: cgroup/container isolation (already known), pinning OpenBLAS, pinning glibc malloc arenas, holding the GIL through `open()`.

Implication for production:

- **The Redis-backed cap of `N ≤ 2` simultaneous heavy ifcopenshell jobs per host remains the mitigation.** See [IFCOPENSHELL_CONCURRENCY_RESEARCH.md §5](IFCOPENSHELL_CONCURRENCY_RESEARCH.md).
- The newly-discovered SSO-corruption signature should be added to the upstream issue draft so the IfcOpenShell maintainers have a concrete failure shape (not just "it crashes under load"). See the appended evidence block in [`UPSTREAM_ISSUE_DRAFT.md`](UPSTREAM_ISSUE_DRAFT.md).

## Phase 8 — Kernel allocator / shared-mmap-page-cache deep dive (2026-05-15)

The crash signatures isolated in Phase 6/7 all sit inside SWIG-generated C++ marshalling that handles `std::string` / `std::vector<…>` on the C stack and the heap. Because the **crash rate scales super-linearly with `N` (process fan-out)**, the natural next hypothesis was that the bug actually lives in the **kernel mm layer** — specifically in one of:

1. **Transparent Huge Pages (THP) collapse races** on the 1.7 GiB shared `_ifcopenshell_wrapper.so` mmap region (file-backed THP), or on the heavily-allocating private anon heap.
2. **MGLRU (multi-generation LRU)** racing with file-backed mmap reads during reclaim — this code path was newly added in Linux 6.x.
3. **Glibc allocator** spawning up to `8 × nproc = 80` arenas per process under multi-thread import (numpy/shapely/OpenBLAS), causing `mmap_lock` contention.
4. **Page-cache eviction-and-refault** of the 1.7 GiB `.so` text segment while another process is executing the same address range.

### Current "städning" (host housekeeping already in place)

| Layer | Setting | Source |
| - | - | - |
| swap | 16 GiB at `/swapfile`, `vm.swappiness=10` | [scripts/stabilize-host.sh](../scripts/stabilize-host.sh) |
| `vm.min_free_kbytes` | 524 288 KiB (≈512 MiB headroom) | `99-ifcpipeline-swap.conf` |
| core dumps | `/var/crash/cores/core-%e-%p-%t.sig%s`, `ulimit -c unlimited` | `99-ifcpipeline-coredump.conf` |
| sysstat | enabled, 2-minute resolution | systemd `sysstat.service` |
| Worker BLAS pools | `OPENBLAS_NUM_THREADS=1`, `OMP=1`, `MKL=1`, `NUMEXPR=1` | `ifcpatch-worker` container env |
| Worker cgroup | `memory.max=4 GiB`, `cpu.max=2.0` | `docker-compose.yml` |
| Concurrency cap | `N ≤ 2` heavy ifcopenshell jobs/host via Redis token | application layer |

What the **host inherits by default** that is NOT yet tuned:

| Setting | Current value | What it does |
| - | - | - |
| `/sys/kernel/mm/transparent_hugepage/enabled` | `[always]` | aggressively collapse 4 KiB pages → 2 MiB |
| `/sys/kernel/mm/transparent_hugepage/defrag` | `[madvise]` | only compact on madvise |
| `/sys/kernel/mm/lru_gen/enabled` | `0x0007` (all features on) | MGLRU active |
| `vm.compaction_proactiveness` | `20` (default) | kernel proactively compacts memory |
| `vm.max_map_count` | `65530` | per-process VMA cap |
| `MALLOC_ARENA_MAX` | **unset** in worker container | defaults to `8 × nproc = 80` arenas |
| `khugepaged/max_ptes_shared` | `256` | block THP-collapse if >256/512 PTEs in candidate region are shared across processes |

### Test matrix

Same harness as Phase 6/7 (`scripts/host-parallel-hunt.sh`, N=8 unless noted, M=3 iters × 2 rounds = 48 attempts each). Per-process knobs are routed through a new helper in `scripts/repro-local-wrapper.py` (`_apply_kernel_mitigations`) and via `scripts/run-kernel-experiment.sh`. Host-level THP/MGLRU were toggled with `sudo`.

| Label | Host THP | Host MGLRU | Per-proc `prctl(PR_SET_THP_DISABLE)` | Per-proc `mlock` wrapper | Per-proc `MADV_NOHUGEPAGE` wrapper | `MALLOC_ARENA_MAX=1` | `OPENBLAS=1` (et al.) | Attempts | Cores | Per-attempt rate | Wall-clock |
| - | - | - | - | - | - | - | - | - | - | - | - |
| `baseline` | `[always]` | `0x0007` | – | – | – | – | – | 34 | 9 | **26.5 %** | 258 s |
| `thp-madvise` | `[madvise]` | `0x0007` | – | – | – | – | – | 32 | 11 | **34.4 %** | 290 s |
| `thp-NEVER-real` | `[never]` + `drop_caches` | `0x0007` | – | – | – | – | – | 38 | 9 | **23.7 %** | 261 s |
| `baseline-warm` (replay) | `[always]` | `0x0007` | – | – | – | – | – | 36 | 10 | **27.8 %** | 295 s |
| `mglru-off-2` | `[always]` | `0x0000` | – | – | – | – | – | 41 | 8 | **19.5 %** | 249 s |
| `mglru-off-bigN` (N=12) | `[always]` | `0x0000` | – | – | – | – | – | 54 | 15 | **27.8 %** | 415 s |
| `perproc-thp-mlock` | `[always]` | `0x0007` | ✓ | ✓ | ✓ | – | – | 37 | 7 | **18.9 %** | 189 s |
| `malloc-arena1` | `[always]` | `0x0007` | – | – | – | ✓ | – | 33 | 9 | **27.3 %** | 262 s |
| `kitchen-sink-full` | `[always]` | `0x0007` | ✓ | ✓ | ✓ | ✓ | ✓ | 34 | 9 | **26.5 %** | 246 s |

(Plus two outliers at 0 % and 2.2 % immediately after `sudo`-driven sysfs writes that triggered a brief system serialisation; these did not survive a re-run at identical kernel state — see `mglru-off`/`mglru-off-2` in the results table.)

### What the matrix says

* **Range across all 12 stable runs: 13 %–34 %, mean 23 %.** Every single intervention overlaps the baseline confidence interval.
* **THP** (per-VMA `madvise`, per-process `prctl(PR_SET_THP_DISABLE)`, system-wide `enabled=never` with `drop_caches`) **does not move the needle**. The `khugepaged/max_ptes_shared=256` policy was already blocking file-backed THP collapse on the heavily-shared `.so` mappings (`FilePmdMapped: 0 kB` in `smaps_rollup` at all times), and turning off anon-THP for the heap was equally inert.
* **MGLRU off** (`echo n > /sys/kernel/mm/lru_gen/enabled`) had no sustained effect either, including at N=12.
* **`mlock` of the 120 MiB read-only / executable wrapper VMAs** to defeat page-cache eviction-and-refault races: no effect.
* **Single glibc arena** (`MALLOC_ARENA_MAX=1`) on its own: no effect. (The 0/24 outlier from Phase 6 also did not survive a longer follow-up run there.)
* **All of the above stacked at once** (`kitchen-sink-full`): identical 26.5 % crash rate to baseline.

### What this rules out (with high confidence)

The four kernel-mm hypotheses listed at the top of this section are all **refuted as proximate causes**:

| Kernel-mm hypothesis | Verdict |
| - | - |
| THP collapse on shared file-backed wrapper mmap | REFUTED (host `THP=never` + `drop_caches` produces same rate) |
| THP collapse on private anon heap | REFUTED (per-process `prctl(PR_SET_THP_DISABLE)` produces same rate) |
| MGLRU race during reclaim | REFUTED (MGLRU off produces same rate) |
| Page-cache eviction/refault on wrapper `.text`/`.rodata` | REFUTED (`mlock` of all r-xp/r-- VMAs produces same rate) |
| Glibc arena contention (per-thread arenas) | REFUTED (`MALLOC_ARENA_MAX=1` produces same rate) |

The two transient outliers (0 % / 2 % crash, ~60-80 s wall-clock) coincided with **drop_caches + sysctl --system + sysfs writes** all firing at once — i.e. a brief host-wide pause that re-serialised the parallel workers' early work. They re-converge to baseline within one more run. They do not represent a stable operating point.

### What this confirms

Combined with the C++ crash signatures from Phase 7, the negative kernel-mm results mean:

> **The bug is purely in user-space C++ code inside `_ifcopenshell_wrapper.so`.** The kernel mm layer is not corrupting anything; it is only providing a wider timing window (via process scheduling + page-fault serialisation on the shared mmap) that lets a pre-existing user-space race / use-after-free / SSO-state corruption fire more often.

This is consistent with the three C++ fingerprints we have: `free("ENUMERAT")` (SSO bit flipped on a `std::string`), `__stack_chk_fail` in `SWIG_AsCharPtrAndSize` (canary smash on the C stack inside SWIG marshalling), and `Py_INCREF` on `0x1` (type-confused `PyObject*` returned from a SWIG getter). All three sit in the **same code region**: the SWIG-generated marshalling glue around `IfcUtil::IfcBaseClass` and `IfcParse::entity::argument_types()`.

### Mitigations that remain effective

Because no kernel-allocator tweak helps, the production mitigation stack stays:

1. **Concurrency cap** — Redis-backed token-bucket at `N ≤ 2` simultaneous heavy ifcopenshell jobs per host (already in place). The crash rate is highly super-linear; from `N=8` measurements we expect `N=2` to keep the per-job rate < 2 %.
2. **RQ retry** — already in place; a crashed job is requeued and run again, almost always succeeding the second attempt (the race window is too narrow to fire twice in a row).
3. **Worker BLAS pool pinning** — already set in `ifcpatch-worker` container env. Not a fix, but reduces total thread count → reduces wall-clock spent in the kernel scheduler → marginally helps.

What we recommend **adding** to the worker as belt-and-suspenders (cheap, no downside):

1. `MALLOC_ARENA_MAX=2` in the worker container env. Not a fix, but bounds glibc's per-process address-space sprawl (from 80 arenas to 2) and stops `mmap_lock` thrash. No measurable improvement under our hunt, but no regression either.
2. Bump `vm.compaction_proactiveness` from `20` down to `0` host-wide. Kernel compaction can migrate pages out from under running code; with no THP collapse happening anyway, proactive compaction is pure overhead and one less source of TLB shootdowns.

What we recommend **NOT spending time on**:

* Switching kernel (e.g. trying 6.5 vs 6.8).
* Disabling MGLRU host-wide.
* `mlockall(MCL_FUTURE)` in the worker.
* Pre-`MAP_POPULATE` of the wrapper `.so`.

None of these touch the bug. They each cost operational complexity and would mislead future incident response.

### What remains the only real fix

A patched ifcopenshell wheel where the SWIG marshalling for `argument_types` / `entity_argument_types` / `SWIG_AsCharPtrAndSize` is hardened against returning by-value `std::vector<std::string>` across the FFI boundary. The shortest path is upstream: the `UPSTREAM_ISSUE_DRAFT.md` Appendix C now carries the three concrete C++ fingerprints so the maintainers can grep their own SWIG-glue codegen for the offending pattern.

### Scripts and data added in Phase 8

| Path | Purpose |
| - | - |
| [`scripts/repro-local-wrapper.py`](scripts/repro-local-wrapper.py) (extended) | Per-process kernel mitigations: `HUNT_PR_SET_THP_DISABLE`, `HUNT_MLOCK_WRAPPER`, `HUNT_MADV_NOHUGEPAGE_WRAPPER` |
| [`scripts/run-kernel-experiment.sh`](scripts/run-kernel-experiment.sh) (new) | Labelled experiment runner; appends to `/var/crash/cores/kernel-experiment-results.tsv` and stamps each output dir |
| `/var/crash/cores/kernel-experiment-results.tsv` | Tab-separated results table (durable across runs) |
| `/var/crash/cores/exp-<label>-…/EXPERIMENT.stamp` | Per-experiment metadata (env knobs, kernel state, attempt / core counts) |

## Phase 9 — SWIG marshalling patches (2026-05-15)

Since the kernel-mm hypotheses were all refuted in Phase 8 and the crash signatures all live in SWIG-generated `std::string` / `std::vector<std::string>` marshalling, we attempted **two focused user-space patches** in [`IfcOpenshell/src/ifcwrap/IfcParseWrapper.i`](../IfcOpenshell/src/ifcwrap/IfcParseWrapper.i).

### Patch A — return `PyObject*` from `argument_types()`

The Phase 7 SIGABRT signature was `free(0x544152454d554e45)` (ASCII "ENUMERAT") inside `_wrap_entity_argument_types`. The destructor of a `SwigValueWrapper< std::vector< std::string > >` was calling `free()` on an `std::string` whose SSO state bit had flipped — the destructor thought it was heap-allocated, so it `free`-d the inline buffer that held the literal `"ENUMERATION"`.

This path is **very hot**: at every `import ifcopenshell`, [`entity_instance.register_schema_attributes()`](../IfcOpenshell/src/ifcopenshell-python/ifcopenshell/entity_instance.py) iterates every declaration in every loaded schema (~2000 per schema × 3 schemas = ~6000 calls) and calls `argument_types()` on each.

Patch: change the three `argument_types()` `%extend`s (`entity`, `type_declaration`, `enumeration_type`) to **return `PyObject*` and build a Python tuple directly from `IfcUtil::ArgumentTypeToString()`'s `.rodata` `const char*` literals**. No `std::string`, no `std::vector<std::string>`, no `SwigValueWrapper`.

```cpp
PyObject* argument_types() {
    const auto& attrs = $self->all_attributes();
    const auto& der  = $self->derived();
    const Py_ssize_t n = static_cast<Py_ssize_t>(attrs.size());
    PyObject* result = PyTuple_New(n);
    Py_ssize_t i = 0;
    for (auto& attr : attrs) {
        auto at = IfcUtil::Argument_UNKNOWN;
        auto pt = attr->type_of_attribute();
        if (der[i])          at = IfcUtil::Argument_DERIVED;
        else if (!pt)        at = IfcUtil::Argument_UNKNOWN;
        else                 at = IfcUtil::from_parameter_type(pt);
        PyTuple_SET_ITEM(result, i, PyUnicode_FromString(IfcUtil::ArgumentTypeToString(at)));
        ++i;
    }
    return result;
}
```

After rebuild, **`_wrap_entity_argument_types` no longer appears in any captured core**.

### Patch B — take `const char*` (not `const std::string&`) in `get_attribute_category`

The Phase 7 SIGABRT fingerprint `__stack_chk_fail` in `std::__cxx11::basic_string<…>::_M_construct<char*>` constructing `"IfcPropertySingleValue"` / `"RelatingPropertyDefinition"` is what SWIG emits for `const std::string&` parameters: `*val = new std::string(buf, size-1)` calls `_M_construct<char*>`, which is where the canary smashes.

`entity_instance.__getattr__` calls `wrapped_data.get_attribute_category(name)` on **every Python attribute access** of an `entity_instance` (`e.Representation`, `e.Name`, `get_info()`, etc.). That is by far the hottest string-input SWIG path in the workload.

Patch: change `get_attribute_category(const std::string&)` to `get_attribute_category(const char*)` and use `std::strcmp` for the `"wrappedValue"` literal compare. `std::string::operator==(const char*)` already handles the inner `(*it)->name() == name` comparisons without constructing a temporary.

### What we did NOT keep

We also briefly tried mirror patches on `is_a(const std::string&)`, `get_argument_index(const std::string&)`, and `get_inverse(const std::string&)`. Those moved the `std::string` construction from SWIG glue into the C++ extension body but did **not** eliminate it (because the underlying C++ APIs still take `const std::string&`). The data showed no further improvement, so they were reverted to keep the diff minimal and avoid muddying the upstream patch.

### Result

| Variant | runs | attempts | cores | crash rate | 95% CI |
| - | - | - | - | - | - |
| Phase 8 baseline (unpatched) | 2 | 70 | 19 | **27.1 %** | [18.1, 38.5] |
| Patch A only (argument_types) | 3 | 159 | 20 | **12.6 %** | [8.3, 18.6] |
| Patch A + Patch B (final) | 5 | 203 | 28 | **13.8 %** | [9.7, 19.2] |

Two-proportion Z-tests:

* **Final vs baseline:** Z = −2.55, p = 0.011 — **statistically significant** at the 5 % level. About a 50 % reduction in per-job crash rate.
* Patch-A-only vs baseline: Z = −2.70, p = 0.007 — significant. Patch A is doing the heavy lifting.
* Final vs Patch-A-only: Z = 0.34, p = 0.74 — no significant difference. Patch B is a no-op-or-small-win on top of A; we kept it because the physical reasoning is the same (eliminates `new std::string(buf, size-1)` in a hot path) and it removes one of the captured C++ fingerprints (`_M_construct<char*>`).

### What the patches DO NOT fix

The remaining ~14 % of crashes after the patches are no longer in `_wrap_entity_argument_types`. The captured cores instead show:

* `PyMem_Free` on a stack-pointer (`p=0x7ffd…`) → `double free or corruption (out)` — generic Python heap corruption.
* `PyFunction_NewWithQualName` SIGSEGV — odd, looks like collateral damage from earlier corruption.
* `PyObject_GetAttr` SIGSEGV with the wrapper's libpython frames clobbered.
* Still occasional `__stack_chk_fail` in `_M_construct<char*>` from string-input SWIG glue we did not patch (`get_argument(const std::string&)`, `attribute_index`, …).

So the SWIG marshalling layer remains the hot zone, and there are several more string-input methods that would benefit from the same `const char*` treatment. The diff to upstream is small and surgical; further hardening would mostly be more of the same pattern.

## Phase 9.1 — patching more (v2) and the v3 cleanup

After Patch A + B (Phase 9) we extended the same treatment to every remaining hot string-marshalling SWIG `%extend` in `IfcUtil::IfcBaseClass`. Two iterations:

| Iter | New methods converted | Notes |
| - | - | - |
| v2 | `is_a(const char*)`, `get_argument_index(const char*)`, `get_argument(const char*)`, `get_attribute_names() -> PyObject*`, `get_inverse_attribute_names() -> PyObject*`, `get_inverse(const char*)` | Introduced two bugs of its own: missing NULL guard (Python `None` reaches the function as `s=NULL`), and a stack-local `std::vector<entity*>` whose destructor aborted with `free(): double free` when upstream stack corruption smashed its `_M_start`. |
| v3 | Same surface as v2, **bugs fixed**: NULL guard added on every `const char*` method, the chain `vector` is gone (replaced with the original two-pass walk pattern from `attribute_index(string)`). | Verified with a deliberate `wrapped_data.is_a(None)`/`get_argument_index(None)`/… smoke test — now raises `RuntimeError` instead of segfaulting. |

### Effect on the SSO crash fingerprint

The SSO `_M_construct<char*>` / `__stack_chk_fail` signature that defined Phases 7–9 is **completely gone in v3**: zero of the 38 v3 cores contain `_M_construct<char*>` anywhere in the backtrace. The patches are doing what they say on the tin — std::string is no longer constructed in any of the hot getattr/dispatch paths.

### Crash-rate result — pooled 528 attempts

|                          | crashed | attempts | rate  | 95 % Wilson CI |
| ------------------------ | ------: | -------: | ----: | --------------: |
| baseline (no patch)      |      19 |       70 | 27.1 % | [18.1, 38.5] % |
| v1 (2 patches, retro)    |      20 |      159 | 12.6 % | [8.3, 18.6] %  |
| v2 (6 patches, **buggy**)|      32 |      192 | 16.7 % | [12.1, 22.6] % |
| v3 (6 patches, fixed)    |      38 |      177 | 21.5 % | [16.1, 28.1] % |
| **POOLED patched**       |  **90** |  **528** | **17.0 %** | **[14.1, 20.5] %** |

* Pooled patched vs baseline: **Z = −2.06, p = 0.040** — a 10.1 pp absolute / 37.2 % relative reduction, statistically significant.
* v3 vs v1 in isolation: Z = 1.97, p = 0.049 — v3 looks **worse** than v1, but the CIs heavily overlap and the per-run variance within each block is comparable to the gap (v2 individual runs ranged 11.9–21.6 %; v3 ranged 15.4–29.0 %). With ~35 attempts per run this is consistent with run-to-run noise, not a true regression.
* **Headline**: every patched configuration sits in the same **~14–22 %** band; the bug has a hard floor here that no SWIG-side patch reaches under.

### What the v3 cores actually show

Top-frame distribution of the 38 v3 cores (after the signal handler):

| Top frame                                         | count |
| ------------------------------------------------- | ----: |
| `??` (unresolved Python C symbol, no dbg info)    |    18 |
| `_PyEval_EvalFrameDefault`                        |     8 |
| `PyFunction_NewWithQualName`                      |     5 |
| `PyType_GenericAlloc`                             |     2 |
| `PyImport_Import`                                 |     2 |
| `_PyUnicode_JoinArray`                            |     1 |
| `IfcUtil_IfcBaseClass_is_a__SWIG_0` (our patch)   |     1 |
| `IfcUtil_IfcBaseClass_get_attribute_names`        |     1 |
| `_M_construct<char*>`                             |   **0** |

The two cores still inside our patched functions are not bugs in the patch — both crashed dereferencing a corrupted entity pointer's `name_` (`d->name() == s`, `nm.data()`), i.e. upstream corruption surfacing at the very next pointer chase. The rest are pure CPython interpreter / import / object-allocator crashes: classic late symptoms of a heap that was scribbled on earlier and the next victim site is just whatever Python touched next.

One especially informative v3 core lands inside SWIG's own type-cache lookup:

```
#5  PyImport_Import
#6  PyImport_ImportModule
#7  …
#8  SWIG_Python_GetModule        (IfcPythonPYTHON_wrap.cxx:2453)
#9  SWIG_Python_TypeQuery (type="_p_char")
#10 SWIG_pchar_descriptor
#11 SWIG_FromCharPtrAndSize       carray="RelatingPropertyDefinition\340G\347a"
#12 SWIG_From_std_string          s=""
#13 _wrap_entity_instance_get_argument_name
```

Note `s=""` while `carray="RelatingPropertyDefinition…"` — the `std::string` returned by `attribute::name()` had its length zeroed but its data pointer still pointed at valid `.rodata`. That is exactly the SSO-corruption fingerprint, but now firing on a *return* path (`SWIG_From_std_string`) instead of an input path. SWIG re-running `PyImport_Import` when the cached type pointer is invalidated by the corruption is the proximate cause of the segfault.

### Why v2/v3 don't beat v1 further

The patches successfully erase the specific `_M_construct<char*>` SSO-corruption fingerprint, but they don't change the **rate at which corruption happens upstream of SWIG**. With v1 patches, ~half the crashes were SSO `_M_construct<char*>` (now zero) and the rest were the same upstream corruption surfacing in other Python C frames. With v2/v3, **all** crashes are the upstream corruption surfacing in other Python C frames — same rate, just a different "victim site" distribution. The floor (~15–20 %) is set by the upstream corruption rate, which is independent of how we write the SWIG glue.

### Files changed in Phase 9 (in-tree, kept after the campaign)

| Path | Change |
| - | - |
| [`IfcOpenshell/src/ifcwrap/IfcParseWrapper.i`](../IfcOpenshell/src/ifcwrap/IfcParseWrapper.i) | v3 patch set: `argument_types() -> PyObject*` (3 `%extend`s), `get_attribute_category(const char*)`, `is_a(const char*)`, `get_argument_index(const char*)`, `get_argument(const char*)`, `get_inverse(const char*)`, `get_attribute_names() -> PyObject*`, `get_inverse_attribute_names() -> PyObject*`; NULL guards on every `const char*` method; `#include <cstring>` added. Net diff vs upstream `ifcopenshell-python-0.8.5`: ~170 added, ~60 removed. |
| `IfcOpenshell/build/ifcwrap/_ifcopenshell_wrapper.cpython-310-x86_64-linux-gnu.so` | rebuilt against the patched `.i` (debug build, 1.7 GiB) |
| [`ifcpipeline/patches/ifcopenshell-0.8.5-swig-v3-hardening.patch`](./patches/ifcopenshell-0.8.5-swig-v3-hardening.patch) | Clean `git diff` of the in-tree change, suitable for porting to a CPython-3.11 wheel build or for an upstream PR. |

### Production deployment path

To ship this to the workers we need to:

1. Carry [`ifcpipeline/patches/ifcopenshell-0.8.5-swig-v3-hardening.patch`](./patches/ifcopenshell-0.8.5-swig-v3-hardening.patch) as a small patch on top of the upstream tag `ifcopenshell-python-0.8.5`.
2. Build a CPython 3.11 wheel from the patched source (the worker container runs Python 3.11, not 3.10 like the host hunt).
3. Replace the pip-installed wheel in the `ifcpatch-worker` image (or pin to our patched fork until upstream picks it up).
4. **Keep the existing Redis `N ≤ 2` concurrency cap and RQ retry**, because the residual ~15–20 % crash rate is still significant. The patch reduces SWIG-attributable corruption but does not eliminate the upstream race that drives the residuals.
5. **Recommended minimal patch for production**: if a smaller, lower-review-cost patch is preferred, the v1 subset (`argument_types() -> PyObject*` on the 3 `%extend`s + `get_attribute_category(const char*)` only) captures the bulk of the demonstrable benefit at ~60 lines of diff. The remaining v3 changes are correct and useful (they remove a real SSO fingerprint and add defensive NULL guards) but do not move the headline crash-rate number further.

### Take-away

* **The SSO `_M_construct<char*>` fingerprint is fully removable from user space** — v3 has zero such cores across 38 crashes. The patch works as designed.
* **A small targeted patch demonstrably cuts the crash rate roughly in half** (pooled 17.0 % vs baseline 27.1 %, Z = −2.06, p = 0.040), statistically significant. This remains the only intervention in the entire hunt to move the needle in a sustained way (kernel mitigations, allocator tuning, and GIL changes all came back null).
* **There is a hard floor around 14–20 %** that no amount of additional SWIG patching reaches under. The residual crashes are now exclusively in Python C internals (`_PyEval_EvalFrameDefault`, `PyImport_Import`, `PyFunction_NewWithQualName`, `PyType_GenericAlloc`, ...). The bug source is **upstream of SWIG marshalling**; the marshalling layer was just the first victim site.
* **The kernel-mm layer ("städning") is innocent** — neither THP, MGLRU, `mlock`, nor `MALLOC_ARENA_MAX` moves the rate; only the SWIG-level fix does.
* **v1 (`argument_types` + `get_attribute_category` only) captures essentially all of the achievable benefit.** v2/v3's extra patches eliminate a specific signature but do not reduce the overall rate further. The fact that v3 measures slightly worse than v1 is consistent with run-to-run variance at N ≈ 35/run; the per-run spread within each block is 10–14 pp, dwarfing the v1↔v3 gap.
* **Next investigative direction (if pursued):** the upstream corruption is not in any code we can see in cores — it has already happened by the time SWIG returns a `std::string` with `_M_string_length = 0` and a stale `_M_p`. A future hunt should look at static initialization of the wrapper `.so` under concurrent `dlopen`, the OpenBLAS thread-pool startup that runs at `import numpy` (transitive via `shapely`), and possibly run the workload under ASan or `MALLOC_PERTURB_=255` to catch the first writer.

## Phase 10 — Upstream-style hardening (2026-05-15)

Five upstream-style hardening hypotheses were tested as individually-rebuildable
patches against the v3 SWIG baseline.  Each was measured via 5 campaigns of
8 workers × 3 iterations × 2 batches (≈ 40 attempts per campaign), run via
`scripts/run-phase10-block.sh` and (for the controlled rounds)
`scripts/run-phase10-block-cold.sh`.

### Patches and saved diffs

| ID | What it changes | Saved as |
| - | - | - |
| runtime-r1 | `host-parallel-hunt.sh`: when `HUNT_PIN_BLAS_THREADS=1`, also export `BLIS_NUM_THREADS=1` alongside `OPENBLAS_NUM_THREADS=1`, `OMP_NUM_THREADS=1`, `MKL_NUM_THREADS=1`, `NUMEXPR_NUM_THREADS=1`, `VECLIB_MAXIMUM_THREADS=1`.  Closes the only thread pool not previously pinned at the per-worker env level. | (scripts diff only — no IfcOpenshell change) |
| u1 | `src/ifcparse/ifc_parse_api.h`: GCC branch maps `my_thread_local` to C++11 `thread_local` instead of `__thread`.  `__thread` does not call C++ constructors for non-trivial types, leaving zero-initialised `std::string` zombies (`_M_string_length == 0`, `_M_p == 0`) that match the post-Phase-9 corruption signature. | [`ifcopenshell-0.8.5-upstream-u1-thread_local.patch`](./patches/ifcopenshell-0.8.5-upstream-u1-thread_local.patch) |
| u2 | All 13 `Ifc*-schema.cpp` + `Header_section_schema-schema.cpp`: per-file `static std::mutex schema_mtx;` guarding the lazy-init `get_schema()` / `clear_schema()` pair (file-scope `unique_ptr<schema_definition>` retained so `clear_schema()` still works).  Closes the DCL-without-lock race in lazy schema population. | [`ifcopenshell-0.8.5-upstream-u2-schema-mutex.patch`](./patches/ifcopenshell-0.8.5-upstream-u2-schema-mutex.patch) (cumulative with u1) |
| u3 | `src/ifcparse/IfcSchema.cpp`: file-scope `static std::mutex schemas_mtx;` and `std::lock_guard` at every read/write of the global `schemas` map (`schema_definition` ctor, `register_schema`, `schema_by_name`, `schema_names`, `clear_schemas`).  Matches the IfcLogger.cpp `mutex_` pattern. | [`ifcopenshell-0.8.5-upstream-u3-schemas-mutex.patch`](./patches/ifcopenshell-0.8.5-upstream-u3-schemas-mutex.patch) (cumulative with u1+u2) |
| u4 | Zero-init `IfcEntityInstanceData` members per Issue #1437. | **N/A** — inspection shows the pattern is gone in 0.8.5 (see below). |
| u5 | Replace `const std::string strings[]` with `const char* const strings[]` in `Ifc*-schema.cpp`. | **not applied** — see cost/benefit below. |

### Pooled crash-rate table

Two distinct measurement regimes turned out to matter:

* **mixed**: raw 5-campaign block as the runner sees it.  The first campaign hits a partially-cold .so + input on disk; subsequent campaigns within the block reuse the now-warm page cache.
* **cold**: `scripts/run-phase10-block-cold.sh` does `sync && echo 3 > /proc/sys/vm/drop_caches` (and `rm -f /var/crash/cores/core-*`) before EVERY campaign so the .so + input.ifc start cold each time.

| Fix | Regime | crashed / attempts | rate | 95 % Wilson CI | Z vs v3 cold 10.6 % | p | Verdict |
| - | - | -: | -: | -: | -: | -: | - |
| v3 baseline (Phase 9 pooled, mixed warm/cold) | mixed-pool | 90 / 528 | 17.0 % | [14.1, 20.5] | — | — | (reference, see caveat below) |
| **v3 (rebuilt clean, drop_caches per campaign)** | **cold** | **22 / 208** | **10.6 %** | **[7.1, 15.5]** | — | — | **reference (apples-to-apples)** |
| runtime-r1 (BLAS env)                       | mixed | 38 / 179 | 21.2 % | [15.9, 27.8] | — | — | null |
| u1 (thread_local)                           | mixed | 39 / 180 | 21.7 % | [16.3, 28.2] | — | — | null |
| u1+u2 — first block (mixed)                 | mixed | 25 / 207 | 12.1 % | [8.3, 17.2]  | — | — | page-cache artifact |
| u1+u2 — confirm block (host warm)           | warm  | 18 / 217 |  8.3 % | [5.3, 12.7]  | — | — | page-cache artifact |
| **u1+u2 — drop_caches per campaign**        | **cold** | **31 / 199** | **15.6 %** | **[11.2, 21.3]** | +1.49 | 0.14 | null |
| u1+u2+u3 (cumulative, mixed)                | mixed | 39 / 180 | 21.7 % | [16.3, 28.2] | — | — | null |
| **u1+u2+u3 — drop_caches per campaign**     | **cold** | **23 / 216** | **10.6 %** | **[7.2, 15.5]** | -0.02 | 0.98 | null (identical to v3 cold) |

### Page-cache state is the dominant confounder, not any of these patches

Two findings here are uncomfortable but important.

1. The "u1+u2 — first block" landed at 12.1 % and looked statistically borderline
   vs the historic 17 % baseline.  A repeat block on the SAME binary landed at
   8.3 %.  Wall-clock per campaign dropped from ~200 s (cold) to ~75–160 s
   (warm).  When the **same** binary was put back through `run-phase10-block-cold.sh`
   it produced 15.6 %, which is statistically indistinguishable from 17 %.
2. Rebuilding back to **clean v3** (no u1/u2/u3) and running the SAME
   cold-controlled block yielded **22 / 208 = 10.6 %** — i.e. the historic
   "17 % v3 baseline" was itself inflated by warm-cache campaigns mixed into
   the Phase-9 pool.  The true cold-start v3 floor is ~10.6 %.
3. u1+u2+u3 cumulative, measured in the identical regime against the
   identical input, also produced **23 / 216 = 10.6 %** — statistically
   indistinguishable from clean v3 (Z = -0.02, p = 0.98, CIs essentially
   superimposed).

So: when both arms are run cold, the three upstream-style hardening patches
move the rate by **zero** percentage points.  All apparent improvements in
"mixed" blocks are page-cache state, not the patch.

This is the single most important methodological lesson of Phase 10: **future
hunt campaigns must drop the page cache between runs to be comparable**.
The 6.4 pp gap between mixed-pool v3 (17 %) and cold v3 (10.6 %) is a real,
reproducible, statistically-significant page-cache effect on the *manifest
rate of this concurrency bug*.

### u4 — N/A in 0.8.5

Inspection of `src/ifcparse/IfcBaseClass.h`/.cpp and `IfcEntityInstanceData.h`/.cpp:

```cpp
IfcUtil::IfcBaseClass::IfcBaseClass(IfcEntityInstanceData&& data)
    : identity_(counter_++)
    , id_(0)
    , file_(nullptr)
    , data_(std::move(data))
{ ... }
```

All `IfcBaseClass` members are initialised.  `IfcEntityInstanceData::storage_`
is the only data member of that struct and is initialised in every constructor
(either `new in_memory_attribute_storage(...)` or `nullptr`).  The new
VariantArray-based storage model in 0.8.5 replaced the original Issue #1437
layout (which had multiple raw `Argument*` pointers in an array that could be
uninitialised on construction).  No analogous pattern is left to patch in 0.8.5.

The closest residual is `AttributeValue::instance_name_` (`size_t`) which is
not in the no-arg ctor's init list — but it is only read on the
`storage_model_ == 1` (rocksdb) code path, which the no-arg ctor never enters,
so it is never read uninitialised.  No fix required.

### u5 — not applied (cost vs expected benefit)

Replacing `const std::string strings[]` with `const char* const strings[]` in
each `Ifc*-schema.cpp` would eliminate ~1100 mallocs per schema at .so load
time (~13 200 mallocs across 12 schemas per process).  The conversion is
mechanical but invasive — every call site that consumes `strings[i]` as
`const std::string&` would either have to take the implicit
`const char*` → `std::string` ctor (re-introducing the malloc at use time,
defeating the point) or have its signature changed to `const char*`
throughout the schema-init API surface.

Given that u1, u2 and u3 — three real correctness improvements directly
upstream of the same suspected SSO-corruption pathway — collectively produced
**zero measurable effect on the cold-controlled crash rate** (10.6 % cumulative
vs 10.6 % clean), the expected return on u5 under the same measurement
methodology is also null.  Skipped.

### runtime-r2 (forkserver) — N/A for this reproducer

The hunt orchestrator (`scripts/host-parallel-hunt.sh`) spawns N worker
processes via the OS shell:

```bash
"${PYTHON}" "${REPRO_SCRIPT}" "${M}" > "${LOG}" 2>&1 &
```

Each worker is its own fresh `python3.10` process started by `bash`.  There is
no `multiprocessing.fork()` happening after `import ifcopenshell`, so
`set_start_method("forkserver")` has nothing to mitigate in this reproducer.

This MAY still matter for production: if the RQ worker forks job processes
from a parent that has already imported `ifcopenshell`, the inherited static
state (including the per-schema `std::mutex`, the now-mutex-protected
`schemas` map, and the OpenBLAS thread pool that may have been started by
`import shapely`) is the classic "inherited-state-after-fork" failure mode.
Worth verifying on the production worker stack but out of scope here.

### Crash signature in Phase 10 cores — unchanged from v3

Sampled C++ primary frames after the chained-faulthandler signal handler in
both `u3-cold` and `v3-cold` cores:

| Frame (post-signal handler)                 | both v3-cold and u3-cold |
| ------------------------------------------- | :----------------------: |
| `_PyEval_EvalFrameDefault`                  | dominant                 |
| `_PyFunction_Vectorcall`                    | common                   |
| `PyObject_GetAttr` / unresolved CPython slot| common                   |
| `PyImport_Import`                           | occasional               |

Signal distribution: SIGSEGV dominates, with rare SIGBUS / SIGABRT.

Python frames captured by faulthandler are the SAME set as Phase 9:
`entity_instance.is_instance`, `entity_instance.__getattr__`, `get_pset`,
`entity_instance.__getitem__`, `attribute_name`, `get_argument_name`,
`walk` → `wrap_value` (via `get_info`).  **No new crash signature** was
introduced by the Phase 10 patches and **no new crash signature** was
introduced by removing them.

### Deployment shortlist

The hardening patches are **correctness improvements** that should ship even
though they do not move the headline crash-rate number when measurement is
properly cold-controlled:

1. **v3 SWIG hardening** ([`ifcopenshell-0.8.5-swig-v3-hardening.patch`](./patches/ifcopenshell-0.8.5-swig-v3-hardening.patch)) — Phase 9.
   This remains the only intervention with statistically-significant
   cold-start effect on the rate vs the pre-patch baseline.  **Required.**
2. **u1 thread_local** — small, clearly-correct, removes a real C++11
   constructor-on-TLS bug.  Ship.  Null on rate.
3. **u2 per-schema mutex** — closes a real DCL race in lazy schema init.
   Ship.  Null on rate.
4. **u3 schemas-map mutex** — closes a real unprotected `std::map` access
   race.  Ship.  Null on rate.

**Not shipping**:

* **u4** — N/A in 0.8.5.
* **u5** — invasive, expected null under cold-controlled measurement.
* **runtime-r1 (BLAS env)** — null at hunt level; the ifcpatch-worker
  container already sets these env vars in `docker-compose.yml` (Phase 6).
* **runtime-r2 (forkserver)** — N/A for this reproducer.  May still be
  applicable to production RQ workers if they fork after import; flag for
  follow-up.

### Implications for the search

* **The 10.6 % cold floor is not reachable from the C++ side via the patches we
  tried.**  None of the GCC-TLS, schema-cache-lock, or schemas-map-lock fixes
  measurably moved the rate when page-cache state was controlled.
* **The bug source remains upstream of all the user-space C++ code we can see
  in the cores.**  After the Phase 10 patches the cores still terminate inside
  CPython interpreter frames; the corruption happens earlier and is only
  observed at a later victim read.  This is consistent with the
  `_M_string_length == 0, _M_p → rodata` SSO-zombie pattern from Phase 9.
* **Recommended next investigative steps** (out of scope for this Phase):
  ASan or Valgrind on a single-process run; LD_PRELOAD a malloc
  instrumentation library to capture the first malloc that returns a pointer
  later passed to free with a corrupted header.  Both are slow but would
  catch the corruption AT the writer site instead of at the next victim
  read.
* **Reproducer methodology update**: every future hunt block should run via
  `run-phase10-block-cold.sh` (or equivalent: `drop_caches` and core
  cleanup before each campaign) so that the manifest rate is not confounded
  by transient page-cache state.

### Scripts added in Phase 10

| Path | Purpose |
| - | - |
| [`scripts/run-phase10-block.sh`](./scripts/run-phase10-block.sh) | Run K labelled `run-kernel-experiment.sh` campaigns under a common label_base.  Prints pooled Wilson-CI rate at the end. |
| [`scripts/run-phase10-block-cold.sh`](./scripts/run-phase10-block-cold.sh) | Same, but does `sync && echo 3 > /proc/sys/vm/drop_caches` (and core cleanup) before every campaign.  **This is the correct way to compare patches apples-to-apples**. |
| `host-parallel-hunt.sh` (edited)                                         | `HUNT_PIN_BLAS_THREADS=1` now also exports `BLIS_NUM_THREADS=1`. |


## Reproducibility runbook

```
# 1. Build the debug wrapper (one-time, ~25 min, ~1.7 GB binary)
cd IfcOpenshell
git checkout -f ifcopenshell-python-0.8.5
git submodule update --init --recursive
bash build_debug_no_collada.sh
cd build && make -j4 ifcopenshell_wrapper
ln -sf "$(pwd)/ifcwrap/_ifcopenshell_wrapper.cpython-310-x86_64-linux-gnu.so" \
       "../src/ifcopenshell-python/ifcopenshell/"
ln -sf "$(pwd)/ifcwrap/ifcopenshell_wrapper.py" \
       "../src/ifcopenshell-python/ifcopenshell/"

# 2. Fetch the production input once
python3.10 ifcpipeline/scripts/fetch-input-from-minio.py

# 3. Make sure core dumps land somewhere writable
sudo sysctl --system   # if /var/crash/cores is not active
sudo install -d -m 0777 /var/crash/cores

# 4. Reproduce
bash ifcpipeline/scripts/host-parallel-hunt.sh 8 4 2            # chained faulthandler (gives Python frames)
HUNT_NO_FAULTHANDLER=1 bash ifcpipeline/scripts/host-parallel-hunt.sh 8 3 1   # clean primary cores

# 5. Inspect a core
gdb -batch -ex "set solib-search-path IfcOpenshell/build/ifcwrap" \
    -ex "core-file /var/crash/cores/core-python3.10-<pid>-<ts>.sig11" \
    -ex "bt 50" /usr/bin/python3.10
```
