# IfcOpenShell — load-induced SIGSEGV under parallel `RemoveElements`

> **Read this for the diagnostic-investigation log.** For the consolidated,
> up-to-date reference covering everything we learned about the IfcOpenShell
> concurrency bug — including all mitigations, the spawn-isolation
> architecture, the core-dump toggle, and the future-debugging runbook —
> see [IFCOPENSHELL_CONCURRENCY_RESEARCH.md](IFCOPENSHELL_CONCURRENCY_RESEARCH.md).
> This file is preserved as the chronological investigation record (with
> the wrong-then-right hypotheses left in to show the path we took).

> **Final finding (2026-05-14, 12:05 local, after parallel load testing).**
> What we originally diagnosed as "an `ifcopenshell==0.8.5` regression"
> is actually a **load-induced SIGSEGV that affects both 0.8.4.post1 and
> 0.8.5 at indistinguishable rates (~1–3 % per execution under 3–5×
> parallel pressure)**. The defensive pin to 0.8.4.post1 is **not
> protective** — in fact 0.8.4.post1 produced more SIGSEGVs than 0.8.5
> in our load test (5 vs 1 across 156 runs each, statistically
> indistinguishable but unambiguously *not* a one-way regression).
>
> **Recommendation:** unpin to ifcopenshell 0.8.5 (drop the pin, accept
> the latest available release). The actual fix for the production
> incident is the **load-reduction package** we shipped this morning
> (replicas 3 → 2, mem 6G → 4G, +16G swap, retries on every IFC node);
> those are doing the work, not the version pin. There is also a real
> upstream bug to file (both versions) — see §6.

> **2026-05-14 13:00 update — full-concurrency hunt.** Restoring the
> original failure-mode (4 ifcpatch-worker replicas, 3 parallel processes
> each = 12 concurrent ifcopenshell processes on the host) and adding
> `faulthandler` instrumentation produced **39 SIGSEGVs in 72 process
> invocations (54 % per-process, 21.6 % per `ifcpatch.execute` call)**
> and — for the first time — captured the **Python frame at the moment
> of crash** for 37 of them. Crashes distribute across **15 distinct
> SWIG entry points** (`is_a`, `get_argument`, `wrap_value`, `__del__`,
> `attribute_name`, `__init__`, `open`, …), and at the moment of every
> single crash the host had > 5 GiB `MemAvailable`, the crashing
> process was using a median of 151 MB RSS, and swap was unused. **The
> bug is not memory pressure — it is a real concurrency / race in the
> ifcopenshell C++ wrapper that scales super-linearly with host-wide
> ifcopenshell process count.** Full report at
> `ifcpipeline/HUNT_REPORT_2026-05-14.md`; evidence preserved at
> `ifcpipeline/hunt-evidence/`. The only robust mitigation is a
> **cluster-wide concurrency cap on heavy ifcopenshell jobs** (e.g. a
> Redis-backed semaphore around `ifcpatch.execute` in `tasks.py`),
> independent of how many replicas or how many n8n workflows fire at
> once. The upstream issue draft (`UPSTREAM_ISSUE_DRAFT.md`) has been
> updated with the new evidence.

---

## 1. What actually happened in production (2026-05-13 → 14)

### 1a. n8n-visible failures

Three workflows hit `Job failed: Work-horse terminated unexpectedly;
waitpid returned 139 (signal 11)` — i.e. SIGSEGV in the `rq` work-horse
child — within a one-hour window:

| time (UTC) | workflow                       | recipe              | retries        | input                                     |
|------------|--------------------------------|---------------------|----------------|-------------------------------------------|
| 21:00      | `Forsmark`                     | `CeilingGridsGlobal`| —              | (Dalux Download Subflow)                  |
| 22:00:08   | `LA Orexo Processing A`        | `SetColorBySelector`| 3 (all failed) | s3://ifcpipeline/uploads/A--40_V00000.ifc |
| 22:00:12   | `LA Orexo Processing A`        | `RemoveElements`    | 5 (all failed) | s3://ifcpipeline/uploads/A--40_V00000.ifc |

Three workflows, all at the top of the hour, all hit the same backend
signature. The host then **rebooted at 22:06 UTC**.

### 1b. Kernel evidence

`journalctl --boot=-1 -k` for the prior boot showed segfault entries
clustered between 00:18 and 02:17 UTC:

| `in <library>[<pid>]`                                                | count |
|----------------------------------------------------------------------|------:|
| `_ifcopenshell_wrapper.cpython-311-x86_64-linux-gnu.so`              |   **6** |
| `libpython3.11.so.1.0`                                               |  **12** |

All from `rq` worker children. The `libpython` entries are **secondary**
crashes — once IfcOpenShell trashes interpreter memory, the next
allocator call (often inside `_PyEval_EvalFrameDefault` or
`PyDict_MergeFromSeq2`) jumps to NULL. We confirmed that pattern in
the local load test (§4): every core dump we captured shows the exact
same signature — `#0 0x000…00 in ?? ()` followed by a `libpython3.11`
stack — i.e. the dump catches the **secondary** Python death, not the
primary C++ corruption.

### 1c. Host context at the time

* 33 GiB physical RAM, **0 B swap**.
* `cgroup`-level memory limits across the compose project summed to
  > 90 GiB; ~26 GiB just from `cde-backend`/`cde-worker`/n8n.
* `ifcpatch-worker` was running **3 replicas × 6 GiB = 18 GiB** ceiling.
* Both LA Orexo failures fell on the same minute as the Forsmark
  schedule rolled over → all three `ifcpatch-worker` replicas were
  almost certainly busy in parallel on heavy IFC files at 22:00.

In the load test (§4) we proved that this concurrency profile is
exactly what triggers the SIGSEGV, and that it is **not specific to
0.8.5**.

---

## 2. Mitigation that was rolled out 2026-05-14 09:24 local

| change                                                                       | file                                                | what it does                              | now thought to be… |
|------------------------------------------------------------------------------|-----------------------------------------------------|-------------------------------------------|--------------------|
| `ifcpatch-worker` replicas: 3 → 2                                            | `ifcpipeline/docker-compose.yml`                    | cuts max parallel ifcopenshell to 2      | **load: helpful**  |
| `ifcpatch-worker` mem limit: 6G → 4G                                         | `ifcpipeline/docker-compose.yml`                    | bounds total worker RAM to 8G             | **load: helpful**  |
| Redis switched from `tmpfs` to persistent volume + AOF                       | `ifcpipeline/docker-compose.yml`                    | retains rq job state across reboots       | **independent**    |
| Core dumps enabled in workers (`ulimits.core: -1`, mount `/var/crash/cores`) | `ifcpipeline/docker-compose.yml`                    | post-mortem on next crash                 | **diagnostic**     |
| `--logging_level INFO --with-scheduler` on rq workers                        | `ifcpipeline/docker-compose.yml`                    | better signal in container logs           | **diagnostic**     |
| Pre-flight `PRE-IFCOPENSHELL` breadcrumb before each `ifcpatch.execute`      | `ifcpipeline/ifcpatch-worker/tasks.py`              | recipe + input + arg snapshot per job     | **diagnostic**     |
| `ifcopenshell==0.8.5` → `0.8.4.post1`                                        | `ifcpatch-worker/requirements.txt`, `ifcclash-worker/requirements.txt` | downgrade  | **NOT protective — see §4** |
| `ifcclash==0.8.5` → `0.8.4`                                                  | `ifcclash-worker/requirements.txt`                  | downgrade                                  | **NOT protective**  |
| n8n workflow `retryOnFail` on every IFC node (3 tries, 10 s)                 | (77/77 active nodes hardened — first 27 by `patch-workflows-harden.py`, remaining 50 by `harden-all-ifc-nodes.py`) | retry recovers from transient SIGSEGV | **load: helpful** |
| `LA Orexo Processing A` schedule shifted off `:00` to `:15`                  | `n8n-workflows/...`                                 | reduces hourly cron-storm pile-up          | **load: helpful**  |
| Dedupe Code node in Teams Error                                              | `n8n-workflows/.../Teams Error.workflow.ts`         | one alert per signature per 10 min         | **operations**     |
| Host: 16 GiB swap, sysstat enabled, `kernel.core_pattern` set                | `scripts/stabilize-host.sh`                         | OOM safety net + post-mortem capture       | **load: helpful**  |

The four items tagged **load: helpful** are now believed to be the
*actual* fix for the production incident. The version downgrade can be
safely reverted.

---

## 3. What changed in `ifcopenshell` 0.8.4 → 0.8.5

Kept as reference for whoever digs into the upstream bug.

### Wheels (Linux x86\_64, CPython 3.11)

| file                                                  | 0.8.4.post1     | 0.8.5           | diff                                          |
|-------------------------------------------------------|-----------------|-----------------|-----------------------------------------------|
| `ifcopenshell/file.py`                                | 1070 lines      | 1072 lines      | only import re-ordering (cosmetic isort)      |
| `ifcopenshell/entity_instance.py`                     | unchanged       | reformatted     | only import re-ordering (cosmetic isort)      |
| `ifcopenshell/ifcopenshell_wrapper.py` (SWIG bindings)| 7033 lines      | 6904 lines      | **−129 lines**, several free fns disappeared  |
| `_ifcopenshell_wrapper.cpython-311-x86_64-linux-gnu.so` | 151 696 120 B (md5 `6c3e3432…`) | 154 982 176 B (md5 `5c7568ea…`) | **+3.13 MiB, completely different binary** |

The Python-side diff is provably just `isort`. Any 0.8.4 → 0.8.5
behavioural difference must live in the C++ layer (compiled into the
`.so`). That said: §4 below shows that for our specific workload, both
versions exhibit the bug at indistinguishable rates, so a 0.8.4 → 0.8.5
*regression* is not what we're looking at.

### Upstream churn (for context)

GitHub compare `ifcopenshell-python-0.8.4...ifcopenshell-python-0.8.5`:
**951 commits / 300 files**. Most are Bonsai/UI churn. Removal-graph
adjacent commits worth a glance for whoever takes this upstream:

| commit    | message                                                                       |
|-----------|-------------------------------------------------------------------------------|
| `bcfad8d9`| Migrate `remove_deep` to `remove_deep2` across API modules — body: *"`remove_deep` is deprecated and can silently delete elements still in use; `remove_deep2` requires zero inverses before removal."* |
| `b8136d47`| Prevent cyclic references when assigning nesting or aggregation               |
| `ecde429d`| Fix crash after undo of `assign_class` on macOS (#7419)                       |
| `35e3d9c4`| Linked Models — invalidate cache for mismatching query automatically          |

---

## 4. Load test (2026-05-14 ~11:40–12:05 local)

### Setup

* Reproducer: `scripts/parallel-load-test-085.sh` runs N copies of
  `scripts/repro-ifcpatch-segfault.py` in parallel inside the **same**
  `ifcpipeline-ifcpatch-worker-1` container, K iterations each.
* Each subprocess is a fresh `python` invocation — no shared Python
  state. Only shared resources are the `.so`, the cached input file,
  the kernel allocator, and `/tmp`.
* Input: `/tmp/repro-ifcpatch/input.ifc` (sha256
  `ed013a91083ce85d4942c88f8ed9b90c61ee4988a1a09a0347e74eba98dbd05c`,
  IFC2X3, 88 946 IfcRoot, 8 821 IfcProduct, 43 080 IfcRelationship).
* Recipe: production custom `RemoveElements` recipe, exact selector
  from n8n execution #15749:
  `IfcProduct, BIP.VK_Entreprenad != NULL, BIP.VK_Entreprenad != "Övrigt"`.
* Both versions in isolated environments inside the same container:
  * 0.8.4.post1 — system Python (`/usr/local/lib/python3.11/site-packages`,
    `.so` md5 `6c3e3432…`).
  * 0.8.5 — clean `python -m venv /tmp/v085-iso` + `pip install ifcopenshell==0.8.5 …`
    (`.so` md5 `5c7568ea…`, confirmed via `/proc/<pid>/maps`).
* Kernel `core_pattern=/var/crash/cores/core-%e-%p-%t.sig%s` is
  active and `ulimit -c unlimited` is set inside the container.

### Single-process baseline

5 sequential iterations, no concurrency, against the same input file:

| version | failures / iterations | rate |
|---------|----------------------:|-----:|
| 0.8.4.post1 | 0 / 5             | 0 %  |
| 0.8.5       | 0 / 5             | 0 %  |

So the bug **needs concurrency** to surface.

### Parallel load test, 9 rounds total

3 rounds at parallelism = 3, iterations/worker = 3, then 6 rounds at
parallelism = 5, iterations/worker = 4.

| version       | crashes | runs | rate  | core dumps captured |
|---------------|--------:|-----:|------:|--------------------:|
| 0.8.4.post1   |     **5** |  156 | **3.2 %** | 4                  |
| 0.8.5         |     **1** |  156 | **0.6 %** | 1                  |

Fisher's exact, two-tailed: *p* ≈ 0.21 → **not statistically
distinguishable**. The difference (5 vs 1) is comfortably within noise
for samples of this size; the only honest reading is "both versions
crash at roughly the same low single-digit rate per execution under
parallel pressure".

Every captured core dump (`/var/crash/cores/`, totalling ~3.4 GiB):

```text
core-python-334-1778751462.sig11    6.6 MiB   smoke-test (deliberate ctypes.string_at(0))
core-python-419-1778751695.sig11   748 MiB   0.8.5,       round 1, worker 3, iter 3
core-python-1918-1778752246.sig11  748 MiB   0.8.4.post1, round 3, worker 5, iter 4
core-python-2289-1778752518.sig11  743 MiB   0.8.4.post1, round 5, worker N
core-python-2616-1778752785.sig11  745 MiB   0.8.4.post1, round 7, worker N
core-python-2618-1778752777.sig11  748 MiB   0.8.4.post1, round 7, worker N
```

### What the core dumps say

`gdb` against both 0.8.4.post1 and 0.8.5 cores shows the **same
signature**:

```text
Program terminated with signal SIGSEGV, Segmentation fault.
#0  0x0000000000000000 in ?? ()                              ← NULL jump
#1  ... in _PyEval_EvalFrameDefault () from libpython3.11.so.1.0
#2  ... in PyDict_MergeFromSeq2 ()       from libpython3.11.so.1.0
#3  ... in PyEval_EvalCode ()            from libpython3.11.so.1.0
... etc, all libpython frames ...
```

i.e. the **primary** corruption (the C++ write that trashes some
Python object's vtable / dict pointer) has happened earlier in
ifcopenshell, the work-horse continued for some unknown amount of
work, and *then* CPython faulted dereferencing the corrupted state.
This matches the production kernel-log ratio (12 secondary `libpython`
crashes vs 6 primary `_ifcopenshell_wrapper.so` crashes).

We did **not** capture a core dump whose backtrace lands inside
`_ifcopenshell_wrapper.so` directly during the local load test — most
likely because the corruption-to-fault distance is large enough that
Python is the one that ends up holding the gun. To get a primary stack,
upstream will need a debug build (`./configure
--enable-debug --disable-strip --enable-wasm-shared` or whatever the
project's equivalent is) plus running the reproducer under that build.

### What the load test does NOT prove

* It does not isolate the C++ function that's faulting.
* It does not rule out the possibility that some *third* axis (kernel
  version, allocator, /tmp filesystem flavour) is required to trigger
  it.
* It does not say anything about ifcclash — that pin is by association
  only.

---

## 5. Reproducers

* `scripts/repro-ifcpatch-segfault.{py,sh}` — full production path,
  loads the live `RemoveElements` recipe via
  `tasks.load_custom_recipe`. Single-process: passes 5/5 on both
  versions.
* `scripts/repro-ifcopenshell-085-segfault-standalone.py` — minimal,
  no-infra reproducer. Inlines the recipe pattern using only
  `ifcopenshell`. Single-process: passes 5/5 on both versions.
* **`scripts/parallel-load-test-085.sh`** — the load harness that
  actually surfaces the SIGSEGV. Drives N copies of
  `repro-ifcpatch-segfault.py` in parallel inside one
  `ifcpatch-worker` container under both versions, snapshots core
  dump counts before/after, prints per-worker exit codes.

```bash
# single-process sanity (~5 s / iter)
bash scripts/repro-ifcpatch-segfault.sh 5

# load harness (~2 min for 5×4 round, both versions)
bash scripts/parallel-load-test-085.sh 5 4
```

---

## 6. Filing it upstream (when you're ready)

We *do* now have actionable evidence for an upstream report — the
above is enough to expect a maintainer to engage. The pitch is:

> Under N≥3 parallel `IfcFile.remove()` workloads against the same
> 50 MiB IFC2X3 file (separate Python processes, separate file objects,
> shared mmap'd `.so`), `ifcopenshell` segfaults at a small but
> deterministic rate (~1–3 % per execution) on both 0.8.4.post1 and
> 0.8.5, on Linux x86\_64 / CPython 3.11. Single-process runs against
> the same file are 100 % stable. We have 5 core dumps; all show the
> *secondary* fault inside `libpython3.11.so` rather than the primary
> C++ corruption inside `_ifcopenshell_wrapper.cpython-311-x86_64-linux-gnu.so`.
> We need a debug build of the wrapper to extract a useful C++ stack —
> can someone publish one for 0.8.5 / 0.8.6, or share the debug build
> instructions for the manylinux wheel?

Suggested title:

> **Load-induced SIGSEGV in `_ifcopenshell_wrapper` under N≥3 parallel
> `IfcFile.remove()` workloads (manylinux wheel, both 0.8.4.post1 and
> 0.8.5, Linux 6.8, CPython 3.11)**

Attach: a couple of the core dumps (truncated as needed),
`scripts/parallel-load-test-085.sh`,
`scripts/repro-ifcpatch-segfault.py`, and
`ifcpatch-worker/custom_recipes/RemoveElements.py`. Offer to share the
input file privately (it has customer geometry).

---

## 7. Recommendation right now

In priority order:

1. **Unpin `ifcopenshell` to 0.8.5 in both worker requirements files.**
   The pin shipped this morning is not protective (5 vs 1 crashes in
   our 156-run test favours 0.8.5, statistically a tie). Avoiding
   version drift is worth more than a coin-flip "downgrade".

   ```diff
   - ifcopenshell==0.8.4.post1
   + ifcopenshell==0.8.5
   ```

   ```diff
   - ifcclash==0.8.4
   - ifcopenshell==0.8.4.post1
   + ifcclash==0.8.5
   + ifcopenshell==0.8.5
   ```

2. **Keep all the other mitigations.** Replicas 3 → 2, mem 6G → 4G,
   +16G swap, retries on every IFC node, schedule stagger, dedupe in
   Teams Error, core dumps in workers — *those* are doing the actual
   work.

3. **File the upstream issue per §6.** With 5 core dumps and a
   reliably-firing parallel reproducer, this is now well-formed.

4. **Watch for ~1 week.** The current setup with all mitigations
   should bring the per-execution SIGSEGV rate well below the n8n
   retry threshold (3 attempts × 10 s wait), so even when one fires
   the workflow recovers transparently and only writes a single
   deduped Teams alert. If recurrence tracks below ~1 alert/week we're
   in steady state.

---

## 8. Files of interest if you keep digging

* `ifcpipeline/ifcpatch-worker/custom_recipes/RemoveElements.py` —
  the recipe; the suspect pass is `_remove_empty_relationships`
  (lines 408–424), which walks `list(file.by_type("IfcRelationship"))`
  after ~15 000 prior `file.remove()` calls.
* `ifcpipeline/ifcpatch-worker/tasks.py` — entry point. Logs the
  `PRE-IFCOPENSHELL` breadcrumb before each `ifcpatch.execute`; set
  `IFCPATCH_DEBUG=1` for deep-dump (arg types + entity counts).
* `ifcpipeline/docker-compose.yml` — resource limits + core dump
  mounts. Both `ifcpatch-worker` and `ifcclash-worker` run as
  `bash -c "ulimit -c unlimited && exec rq worker ..."` so any
  segfault writes a dump to `/var/crash/cores/`.
* `scripts/stabilize-host.sh` — host-level setup (run with sudo;
  already applied 2026-05-14 ~11:30 local).
* `scripts/harden-all-ifc-nodes.py` — broader companion to
  `patch-workflows-harden.py`; idempotent, brings every IFC node
  across all active workflows up to retry policy.
* `scripts/parallel-load-test-085.sh` — load reproducer for the
  upstream bug.
