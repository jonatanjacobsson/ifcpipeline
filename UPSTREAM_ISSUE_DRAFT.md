# Upstream issue draft — IfcOpenShell parallel-load SIGSEGV

> **DO NOT POST AS-IS.** This is a local draft for review. Once approved,
> file at <https://github.com/IfcOpenShell/IfcOpenShell/issues/new>.
> Strip references to internal paths/customer names before posting.
>
> For the internal consolidated reference on this bug (mitigations we
> shipped, debugging runbook, all evidence) see
> [IFCOPENSHELL_CONCURRENCY_RESEARCH.md](IFCOPENSHELL_CONCURRENCY_RESEARCH.md).
> Do **not** include any of those internal mitigation details in the
> upstream issue — they are not relevant to the maintainers and may leak
> internal architecture.

---

## Suggested title

> Multi-process SIGSEGV in `_ifcopenshell_wrapper` SWIG bindings under
> concurrent ifcopenshell load — ~22 % per-execute fault rate at N=12
> processes, distributed across 15+ wrapper entry points (`is_a`,
> `get_argument`, `wrap_value`, `__del__`, `attribute_name`, `open`, …)

## Suggested labels

`crash`, `python`, `linux`, `concurrency`, `needs-debug-build`

---

## Summary

Running N separate Python processes that each open the same ~50 MiB
IFC2X3 file and run an IfcPatch-style `RemoveElements` workload produces
a **non-deterministic SIGSEGV** in the ifcopenshell C++ wrapper. The
crash rate scales **strongly super-linearly** with N:

| total simultaneous ifcopenshell processes on the host | per-execute crash rate |
| ----: | ----: |
|  1 | 0 % (0 / 30) |
|  2 | ~3 % (2 / 80) |
| **12** | **22 %** (42 / 194) |

The single-process case is 100 % stable. Each process runs in its own
Linux namespace / cgroup with its own page tables and its own copy of
the loaded `.so` — yet the bug rate increases far faster than would be
explained by independent per-process probability. This implies the
contention is at the OS resource level (allocator, kernel data
structure, NUMA, mmap, or similar) and is *not* a "ran out of memory"
or "OOM-killed" failure — at the moment of crash the host has 22+ GiB
of `MemAvailable`, the crashing process is using ~150 MB of its 4 GiB
cgroup limit, and swap is unused.

Affects both `ifcopenshell==0.8.4.post1` and `ifcopenshell==0.8.5`
manylinux wheels at indistinguishable rates — this is a long-standing
issue, not a recent regression.

## Environment

| field             | value                                                          |
|-------------------|----------------------------------------------------------------|
| OS                | Linux 6.8.0-1052-azure (Debian-based container, manylinux wheel) |
| arch              | x86\_64                                                        |
| Python            | CPython 3.11.15                                                |
| ifcopenshell      | tested 0.8.4.post1 (PyPI) and 0.8.5 (PyPI)                     |
| ifcpatch          | tested 0.8.4 and 0.8.5                                         |
| `.so` (0.8.4.post1)| `_ifcopenshell_wrapper.cpython-311-x86_64-linux-gnu.so` md5 `6c3e3432c86572579d9ffb8f4d5f80ea`, 151 696 120 B |
| `.so` (0.8.5)     | `_ifcopenshell_wrapper.cpython-311-x86_64-linux-gnu.so` md5 `5c7568eac11c40e8ce7f8473f4938dda`, 154 982 176 B |
| host RAM / swap   | 33 GiB / 16 GiB swap (problem also reproduces with no swap)    |
| container limits  | 4 GiB / 2 CPU per worker (also reproduces at 6 GiB)            |

## Reproduction

Single-process baseline (passes 5/5 on both versions):

```bash
python repro_remove_elements.py path/to/file.ifc 5
```

Parallel harness (3–5 copies of the same script in parallel):

```bash
# uses python's subprocess to fan out N copies, captures rc + core dumps
bash parallel_load.sh 5 4   # 5 parallel × 4 iterations × 2 versions
```

The recipe shape that crashes (`RemoveElements`, distilled from
[ifcpipeline](https://...) — full file attached):

```python
import ifcopenshell, ifcopenshell.util.selector

f = ifcopenshell.open(input_path)

# 1. select a subset of IfcProduct via selector
targets = list(ifcopenshell.util.selector.filter_elements(
    f, 'IfcProduct, BIP.VK_Entreprenad != NULL, BIP.VK_Entreprenad != "Övrigt"',
))
target_ids = {e.id() for e in targets}

# 2. detach those targets from every IfcRelationship via setattr(rel, name, ...)
for rel in list(f.by_type("IfcRelationship")):
    info = rel.get_info(recursive=False)
    for name, val in info.items():
        if name in ("id", "type"): continue
        if isinstance(val, ifcopenshell.entity_instance) and val.id() in target_ids:
            setattr(rel, name, None)
        elif isinstance(val, (tuple, list)):
            kept = [v for v in val if not (isinstance(v, ifcopenshell.entity_instance) and v.id() in target_ids)]
            if len(kept) != len(val):
                setattr(rel, name, kept)

# 3. remove the products
for e in targets:
    f.remove(e)

# 4. remove orphaned representations + items + placements + types
#    (~7000 + ~3300 + ~750 more f.remove() calls)

# 5. final cleanup pass — production crashes here, but only sometimes,
#    and only when ≥3 processes are doing the same on the same file in parallel
for rel in list(f.by_type("IfcRelationship")):
    info = rel.get_info(recursive=False)
    related_attrs = [k for k in info if k.startswith("Related")]
    if related_attrs and all(not info[k] or info[k] == () for k in related_attrs):
        f.remove(rel)   # ← may SIGSEGV
```

Per-execution rates observed at three concurrency levels (this is the
key data — the bug is concurrency-sensitive, not version-sensitive):

| concurrency (total ifcopenshell processes on host) | version    | crashes | runs | rate     |
| -------------------------------------------------: | ---------- | ------: | ---: | -------: |
|  1                                                 | 0.8.5      |       0 |   30 |    0.0 % |
|  2 (cross-container)                               | 0.8.5      |       2 |   80 |    2.5 % |
|  3 (same container)                                | 0.8.4.post1|       5 |  156 |    3.2 % |
|  3 (same container)                                | 0.8.5      |       1 |  156 |    0.6 % |
| **12** (4 containers × 3 procs)                    | 0.8.5      |  **42** | **194** | **21.6 %** |

Two-version comparison at concurrency 3: Fisher's exact two-tailed
*p* ≈ 0.21 — statistically indistinguishable. The single-vs-twelve
comparison (0/30 vs 42/194) is *p* < 10⁻⁵ — the **concurrency** axis is
the only one that matters.

## Symptom

For the first time we used `faulthandler.enable()` +
`faulthandler.register(signal.SIGSEGV, file=…, chain=True)` to catch the
**primary** Python frame at the moment of SIGSEGV. Across 37 captured
faulthandler stacks at host concurrency 12, the crash distributes
across **15 distinct entry points into the wrapper**, every one of
which is either inside `ifcopenshell_wrapper.py` (calling the SWIG
`.so`) or inside `entity_instance.py` / `util/*` methods that
themselves call into the wrapper:

| count | crash site (Python frame at SIGSEGV)                            |
| ----: | --------------------------------------------------------------- |
|     8 | `ifcopenshell_wrapper.py:6120 in is_a`                          |
|     5 | `ifcopenshell_wrapper.py:6127 in get_argument`                  |
|     4 | `entity_instance.py:309 in wrap_value`                          |
|     3 | `entity_instance.py:306 in wrap_value`                          |
|     3 | `entity_instance.py:189 in __del__`                             |
|     2 | `util/element.py:259 in get_property_definition`                |
|     2 | `entity_instance.py:337 in attribute_name`                      |
|     2 | `entity_instance.py:174 in __init__`                            |
|     1 | `ifcopenshell_wrapper.py:6863 in open` (crashed loading file)  |
|     1 | `ifcopenshell_wrapper.py:6105 in __len__`                       |
|     1 | `entity_instance.py:626 in _`                                   |
|     1 | `entity_instance.py:297 in walk`                                |
|     1 | `file.py:789 in schema`                                         |
|     1 | `util/element.py:109 in get_pset`                               |
|     1 | `util/selector.py:1012 in filter_function`                      |
|     1 | (user-recipe) calling `entity_instance.id()`                    |

A single bug in a single function would not produce this distribution.
The pattern is consistent with **shared global state in the C++ side
getting corrupted under concurrent process pressure**, after which any
subsequent SWIG call across the boundary may fault on a freed/null
pointer dereference.

The kernel-visible secondary fault we get in the core dumps is the
expected NULL-vtable jump in CPython's eval loop after the wrapper
returned a corrupted Python object:

```text
Program terminated with signal SIGSEGV, Segmentation fault.
#0  0x0000000000000000 in ?? ()                       ← NULL jump
#3  0x...    in _PyEval_EvalFrameDefault () from libpython3.11.so.1.0
… (standard CPython interpreter stack)
```

The wrapper `.so` shipped on PyPI is stripped (`gdb` reports
`(*): Shared library is missing debugging information.` for
`/usr/local/lib/python3.11/site-packages/ifcopenshell/_ifcopenshell_wrapper.cpython-311-x86_64-linux-gnu.so`),
which is why we are blocked on a debug-symbol build before we can
report the actual offending C++ frame. The 39 cores we captured
(see Attachments) are 600 MB–1.1 GB each and ready for re-analysis
with debug symbols when available.

## Memory pressure was *not* the trigger

A common first reaction is "your container is OOM'ing". To rule that
out, on every iteration we snapshotted `/proc/self/{maps,status,
smaps_rollup,io,stat}` + `/proc/meminfo` + `/proc/loadavg` to disk
**before** the `ifcpatch.execute` call, then matched them by pid +
timestamp to the faulthandler outputs. Across 37 paired crashes:

| metric (across 37 crashes)            | min       | median    | max       |
| ------------------------------------- | --------- | --------- | --------- |
| host `MemAvailable` at crash time     | > 5 GiB on every sample | – | – |
| crashing process `VmRSS` at crash time | 149 MB    | 151 MB    | 1 151 MB  |
| crashing process `Threads`            | 1         | 1         | 1         |
| host swap used at crash time          | 0 KB      | 0 KB      | 0 KB      |

Example snapshot taken 6 ms before the `entity_instance.py:297 in walk`
crash:

```
MemAvailable:   23,543,672 kB    (≈ 22.5 GiB free)
SwapCached:              0 kB
VmRSS:             150,628 kB    (process held ~150 MB out of 4 GiB cap)
loadavg:        2.59 0.95 0.66  10/1905 1066
```

So the bug fires with abundant memory, no swap pressure, single-threaded
crashing processes that aren't anywhere near their cgroup ceiling.

## Where we think the bug lives

The crash distribution across 15 different SWIG entry points (with
**`is_a`** and **`get_argument`** at the top — both very low-level
methods that read from the schema/entity index, not the removal path
specifically) suggests the corrupted state is a **shared structure that
backs entity lookup**, not the removal cleanup we originally suspected.

Crashes that appear specifically *during* the file-open phase
(`wrapper.open`) and during fresh entity-instance construction
(`entity_instance.__init__`) are particularly telling — those processes
hadn't yet mutated anything when they died. They imply the corruption
is happening in a structure that's populated/read at file-open time and
shared across the wrapper's lifetime.

Plausible mechanisms (we don't have C++ symbols yet to pick between
them):

1. **A static/process-wide structure populated during `dlopen()` or
   first `ifcopenshell.open()`** (e.g. the schema registry, the
   by-type index, an `IfcFile` factory cache) that's modified in
   place by entity-construction calls without the locking we'd
   normally rely on the GIL for, and gets stomped when many processes
   trigger the same construction concurrently. Even though each
   process has its own copy in its own address space, page-cache /
   COW interactions could turn a logically-stable read into a torn
   read across processes.
2. **A subtle allocator interaction** — e.g. a use-after-free that's
   benign at low concurrency because the freed memory hasn't been
   reused, but at N=12 the host allocator returns it to a sibling
   process before the original owner reads it.
3. **A race on a shared file/socket/tmpfile** the wrapper uses
   internally on file-open (we have not yet straced this).

The Python side (`file.py`, `entity_instance.py`) is byte-identical
between 0.8.4.post1 and 0.8.5 modulo `isort`. The `.so` differs by
+3.13 MiB so the actual offending C++ has changed substantially across
the 951 commits between the tags, but the bug rate did not change —
suggesting the root cause is older than 0.8.4.

## What we need from upstream

* A **debug build** of `_ifcopenshell_wrapper.cpython-311-x86_64-linux-gnu.so`
  for at least one of the affected versions (0.8.4.post1 / 0.8.5),
  with symbols left in. Either a pre-built wheel under e.g.
  `ifcopenshell==0.8.5.dev+debug`, or build instructions for the
  manylinux container that produces the wheel. With that we will
  re-run the harness, capture a primary fault, and update this issue
  with a real C++ stack frame.
* Failing that, any pointer to which compilation unit owns the
  by-type entity index and the `IfcFile::remove()` cleanup path
  would let us add diagnostic prints and bisect.

## What we tried locally

* Single-process baseline with the same recipe + same input file:
  passes 5/5 on both 0.8.4.post1 and 0.8.5. Rules out a deterministic
  data-corruption bug in the recipe code.
* Different IFC files: only one customer file in our corpus reliably
  triggers it (50 MiB, 88 946 IfcRoot, 8 821 IfcProduct, 43 080
  IfcRelationship — Revit-exported via ODA SDAI 24.12). Other files
  don't, but our test corpus is small.
* Pinning to 0.8.4.post1 (which we did defensively in production
  before the parallel test was built): if anything *more* crashes
  than 0.8.5 (5 vs 1 in 156 runs each).
* Adding 16 GiB host swap: did not eliminate the crash, only the
  OOM-aliasing risk.
* Reducing parallelism from 3 to 2 in production: zero recurrence in
  ~3 hours of normal load (1 sched-trigger storm) — but that's far
  too short an observation window to claim it as a fix.

## Attachments

* `hunt-repro.py` — heavily-instrumented stand-alone reproducer
  (faulthandler + per-iteration `/proc` snapshots; no IfcPatch
  dependency; only `ifcopenshell` + `boto3` for the file fetch which
  can be replaced with a local path).
* `RemoveElements.py` — the production custom recipe (full).
* `hunt-harness.sh` — the harness that fans out N processes per
  container × M containers, gdb-analyzes every new core, and counts
  rounds.
* `faulthandler/*.faulthandler` — **37 captured Python stacks at the
  exact moment of SIGSEGV**, the most useful piece of evidence in
  this report. Take these as the ground truth for crash sites.
* `proc-snapshots/*.{maps,status,smaps,io,stat,meminfo,loadavg}` —
  paired by pid + timestamp with the faulthandler files; show the
  state of the process and host immediately before each crash. Use
  these to refute "you ran out of memory" hypotheses.
* `gdb-bts/*.bt` — 39 gdb-batch backtraces extracted from the
  raw cores. All show the secondary CPython collapse because the
  shipped wrapper `.so` is stripped — these are mostly useful as
  proof that the dump exists, not as a primary lead.
* `sample-core.sig11` (687 MB) — one preserved raw core dump for
  re-analysis with debug symbols when available. The other 38 cores
  totalled ~28 GiB and were deleted after metadata was preserved;
  we can re-trigger and re-capture trivially with the same harness.
* `HUNT_REPORT_2026-05-14.md` — the internal hunt report this issue
  is based on.
* `HUNT_REPORT_2026-05-15-debugbuild.md` — **debug-symbol host hunt** with
  full `RelWithDebInfo` build of `ifcopenshell-python-0.8.5`; primary C++
  frames are usable here. See appendix C below for the highlights.
* Input file: 50 MiB, sha256 `ed013a91083ce85d4942c88f8ed9b90c61ee4988a1a09a0347e74eba98dbd05c`,
  contains customer geometry — happy to share privately.

---

## Appendix C — Debug-symbol C++ frames (2026-05-15)

We built `ifcopenshell-python-0.8.5` from source with
`-DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_CXX_FLAGS="-g3 -fno-omit-frame-pointer"`
(see [`IfcOpenshell/build_debug_no_collada.sh`](../IfcOpenshell/build_debug_no_collada.sh))
and re-ran the harness on the host. The same workload reproduces the SIGSEGV
at 9–33 % per-execute under N=8 (10-vCPU host, no containers). With
`HUNT_NO_FAULTHANDLER=1` to keep the kernel core's primary RIP intact,
gdb resolves usable C++ frames.

**Signature 1 — `__libc_free` called with a value that is ASCII text:**

```
#0  __GI___libc_free (mem=0x544152454d554e45)
                          ^^^^^^^^^^^^^^^^^^
                          ASCII "ENUMERAT" (little-endian)
#1  std::__cxx11::basic_string<char>::_M_destroy
#2  std::__cxx11::basic_string<char>::_M_dispose
#3  std::__cxx11::basic_string<char>::~basic_string
#…
#10 std::vector<std::__cxx11::basic_string<char>>::~vector
#11 _wrap_entity_argument_types (args=<optimized out>)
    at IfcPythonPYTHON_wrap.cxx:90968
```

The address being passed to `free()` is **the byte content of the string
`"ENUMERAT"` interpreted as a pointer**. This is the classic fingerprint
of `std::string` SSO state corruption: a 11-character string
(`"ENUMERATION"`, which fits the 15-byte SSO buffer) is being treated as
heap-allocated. The destructor reads the data-pointer field of the
`basic_string`, which has been overwritten with the SSO buffer's own
character data, and calls `free()` on it.

The source line is the SWIG-extended `IfcParse::entity::argument_types()`
in [`src/ifcwrap/IfcParseWrapper.i`](../IfcOpenshell/src/ifcwrap/IfcParseWrapper.i)
(lines marked `IfcParse_entity_argument_types` in the generated wrapper),
which builds a `std::vector<std::string>` from the
`static const char* const argument_type_string[]` array of literals in
`src/ifcparse/IfcUtil.cpp:144-168`. The literals themselves are
immutable `.rodata`; the corruption is in the vector's per-element
state.

**Signature 2 — stack-canary smash inside SWIG string marshalling:**

```
#7  __stack_chk_fail
#8  SWIG_AsCharPtrAndSize (obj=<optimized out>,
                           cptr=<caller's stack>,
                           psize=<caller's heap>,
                           alloc=<optimized out>)
    at IfcPythonPYTHON_wrap.cxx:5794
```

`SWIG_AsCharPtrAndSize` is the standard SWIG glue that converts a
Python `str` into a C `char*`+`size_t`. The canary check at the
function's `}` fires, meaning something between prologue and epilogue
wrote past the function's stack frame (or clobbered the per-thread
canary master at `%fs:0x28`).

**Signature 3 — `Py_INCREF` on a near-NULL `PyObject*`:**

```
=> 0x6188c2a5ed6c:  addq   $0x1,(%rdi)       ← Py_INCREF
   0x6188c2a5ed70:  lea    0x434d69(%rip),%r8 →  _PyRuntime
```

with `%rdi = 0x1`. CPython's `_PyEval_EvalFrameDefault` is bumping the
refcount of a `PyObject*` whose value is `0x1`. This is what we expect
if a SWIG-marshalled return value (e.g. `entity_instance.is_a()` or
`get_argument_name()`) handed the interpreter a bogus pointer.

**Common thread**: every reproducible C++ frame is in `std::string` /
`std::vector<std::string>` glue inside the SWIG-generated wrapper, *not*
inside `libIfcParse` itself. The internal copies of strings and string
vectors that SWIG builds to marshal between Python and C++ are getting
corrupted under multi-process load.

### Things we ruled out in this round

| Hypothesis | How tested | Result |
| - | - | - |
| OpenBLAS / OMP background threads | `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1` (+ MKL/NUMEXPR) | no change in crash rate |
| Glibc malloc arena contention | `MALLOC_ARENA_MAX=1` | one 24-attempt run had 0 crashes, but a 54-attempt run hit 9 % — within noise of baseline |
| `Py_BEGIN_ALLOW_THREADS` in `open()` / `read()` letting non-Python threads race with schema lazy-init | Rebuilt wrapper without the GIL release, re-ran N=8 M=4 R=2 | 9 cores / 43 attempts = 21 %, vs baseline 8 / 49 = 16 % — no improvement |
| Single-process | Confirmed (5/5 OK with the same input on the same wrapper) | bug is multi-process only |

This narrows the suspect set to **kernel-level interactions** between
the 8 simultaneous `mmap`s of the 1.7 GB wrapper `.so`, the page cache,
and/or the glibc allocator's `mmap` arena rotation under heavy small-
allocation pressure. We have no further isolated fix at this time.

---

## Internal notes (not for posting)

* Strip `BIP.VK_Entreprenad`, `Övrigt`, customer file name, and any
  Forsmark/LA Orexo references before posting. Replace the selector
  with a generic `'IfcProduct'` selector that produces a similar
  removal volume.
* Consider posting *after* we've also verified single-process is
  stable on a freshly-published 0.8.6 (when it ships) so we can
  preempt the obvious "did you try latest?" reply.
* Repository: <https://github.com/IfcOpenShell/IfcOpenShell>
* Issue tracker: <https://github.com/IfcOpenShell/IfcOpenShell/issues>
* Tag template: `ifcopenshell-python-X.Y.Z`
* Author of the most relevant recent removal-graph commit
  (`bcfad8d9`): co-authored with Claude Opus 4.6 — likely the right
  person to mention in the issue body if maintainer routing matters.
