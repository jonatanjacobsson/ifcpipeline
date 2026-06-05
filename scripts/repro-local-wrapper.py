#!/usr/bin/env python3
"""
repro-local-wrapper.py - Standalone reproducer for the IfcOpenShell
concurrency SIGSEGV that targets a LOCALLY-built debug-symbol wrapper.

Unlike scripts/hunt-repro.py (which imports `tasks.load_custom_recipe`
from inside the ifcpatch-worker container), this script:

  * loads `ifcopenshell` from /home/bimbot-ubuntu/apps/IfcOpenshell
    (where we keep a `RelWithDebInfo` build with full debug symbols),
  * inlines the production RemoveElements workload directly so it has
    no dependency on the worker image or rq stack,
  * reads the production input file from /tmp/repro-ifcpatch/input.ifc
    (download once with the fetch_input.py helper below),
  * keeps the faulthandler + /proc snapshot instrumentation from
    hunt-repro.py so we get a Python frame at SIGSEGV in addition to
    the kernel core dump (now with usable C++ symbols).

Run a single iteration first to sanity-check the local wrapper:

    PYTHONPATH=/home/bimbot-ubuntu/apps/IfcOpenshell/src/ifcopenshell-python \\
        python3.10 ifcpipeline/scripts/repro-local-wrapper.py 1

Output markers in HUNT_DUMP_DIR/<pid>-<ts>.*:
  .start          one-liner: iteration / pid / recipe / ts
  .maps .status .smaps .io .stat .meminfo .loadavg  /proc snapshots
  .ok             one-liner if iteration finished without exception
  .faulthandler   Python frames at SIGSEGV/SIGABRT/SIGBUS (if any fired)

Env:
  HUNT_DUMP_DIR     default /var/crash/cores/host-hunt
  HUNT_ITERATIONS   default 5 (or sys.argv[1])
  INPUT_PATH        default /tmp/repro-ifcpatch/input.ifc
"""
from __future__ import annotations

import faulthandler
import logging
import os
import signal
import sys
import time
import traceback
from pathlib import Path
from typing import List, Set


HUNT_DUMP_DIR = Path(os.environ.get("HUNT_DUMP_DIR", "/var/crash/cores/host-hunt"))
INPUT_PATH = os.environ.get("INPUT_PATH", "/tmp/repro-ifcpatch/input.ifc")
ITERATIONS = int(
    os.environ.get("HUNT_ITERATIONS", sys.argv[1] if len(sys.argv) > 1 else "5")
)

# Production-equivalent selector — same string the ifcpatch-worker
# RemoveElements node was running when it crashed in production.
SELECTOR = (
    'IfcProduct, BIP.VK_Entreprenad != NULL, '
    'BIP.VK_Entreprenad != "Övrigt"'
)

# Hard policy from the live recipe: never remove these.
PROTECTED_TYPES = ("IfcSite", "IfcBuilding", "IfcBuildingStorey")

HUNT_DUMP_DIR.mkdir(parents=True, exist_ok=True)


def _ts_tag() -> str:
    return f"{os.getpid()}-{int(time.time() * 1000)}"


def _snapshot(prefix: str) -> None:
    """Write a few /proc snapshots — only when HUNT_PROC_SNAPSHOT=1.

    Each snapshot adds ~50 KB per iteration and across an N=8 * M=4 * R=3
    fan-out that adds up to hundreds of MB which is unhelpful when we are
    running on a host with little spare disk.  We default to OFF and let
    the operator opt in for a debugging run that needs them.
    """
    if os.environ.get("HUNT_PROC_SNAPSHOT") not in ("1", "true", "yes"):
        return
    sources = [
        ("/proc/self/maps", "maps"),
        ("/proc/self/status", "status"),
        ("/proc/self/smaps_rollup", "smaps"),
        ("/proc/self/io", "io"),
        ("/proc/self/stat", "stat"),
        ("/proc/meminfo", "meminfo"),
        ("/proc/loadavg", "loadavg"),
    ]
    for src_path, label in sources:
        try:
            with open(src_path, "rb") as src:
                data = src.read()
            with open(HUNT_DUMP_DIR / f"{prefix}.{label}", "wb") as dst:
                dst.write(data)
        except Exception:
            pass


def _apply_kernel_mitigations() -> dict:
    """Per-process kernel-allocator/mmap mitigations under env flags.

    These can be deployed without changing host sysctls, so they are
    the most realistic mitigations for the production worker.

    HUNT_PR_SET_THP_DISABLE=1
        prctl(PR_SET_THP_DISABLE, 1, 0, 0, 0).  Disables transparent
        huge page allocation/collapse for this process and its children.
        Defeats khugepaged THP-collapse races on private anon (heap) and
        on the shared file-backed wrapper .so.

    HUNT_MLOCK_WRAPPER=1
        After importing ifcopenshell, walk /proc/self/maps and call
        mlock(2) on every r-xp / r--p VMA backed by
        _ifcopenshell_wrapper*.so.  Pins the .text + .rodata into
        physical RAM so the kernel cannot evict-and-refault them while
        another process is executing the same address range.  Combined
        with HUNT_MAP_POPULATE_HINT below it also avoids majfault under
        memory pressure.

    HUNT_MADV_NOHUGEPAGE_WRAPPER=1
        After importing ifcopenshell, madvise(MADV_NOHUGEPAGE) every
        wrapper .so VMA.  Per-VMA opt-out of THP without changing host
        THP enabled=. Cheaper than HUNT_PR_SET_THP_DISABLE because it
        only opts out the wrapper and leaves anon heap eligible for THP.
    """
    import ctypes
    import ctypes.util

    applied = {}
    libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)

    if os.environ.get("HUNT_PR_SET_THP_DISABLE") in ("1", "true", "yes"):
        PR_SET_THP_DISABLE = 41
        rc = libc.prctl(PR_SET_THP_DISABLE, 1, 0, 0, 0)
        applied["prctl_thp_disable"] = (rc, ctypes.get_errno() if rc != 0 else 0)

    return applied, libc


def _mlock_wrapper(libc) -> dict:
    """mlock every wrapper .so VMA; opt-in via HUNT_MLOCK_WRAPPER=1.

    Must run AFTER `import ifcopenshell` so the .so is mmap'd."""
    if os.environ.get("HUNT_MLOCK_WRAPPER") not in ("1", "true", "yes"):
        return {}
    import ctypes
    pinned = 0
    pinned_bytes = 0
    failed = 0
    try:
        with open("/proc/self/maps", "r") as f:
            for line in f:
                if "_ifcopenshell_wrapper" not in line:
                    continue
                addr, perms, *_rest = line.strip().split(None, 5)
                # only pin r-x and r-- pages (text + rodata), skip rw
                if "x" not in perms and "r" not in perms:
                    continue
                if "w" in perms:
                    continue
                start_s, end_s = addr.split("-")
                start, end = int(start_s, 16), int(end_s, 16)
                rc = libc.mlock(ctypes.c_void_p(start), ctypes.c_size_t(end - start))
                if rc == 0:
                    pinned += 1
                    pinned_bytes += end - start
                else:
                    failed += 1
    except Exception as exc:
        return {"mlock_error": repr(exc)}
    return {"mlock_vmas": pinned, "mlock_bytes": pinned_bytes, "mlock_failed": failed}


def _madvise_nohugepage_wrapper(libc) -> dict:
    """madvise(MADV_NOHUGEPAGE) for every wrapper VMA; HUNT_MADV_NOHUGEPAGE_WRAPPER=1."""
    if os.environ.get("HUNT_MADV_NOHUGEPAGE_WRAPPER") not in ("1", "true", "yes"):
        return {}
    MADV_NOHUGEPAGE = 15
    import ctypes
    advised = 0
    failed = 0
    try:
        with open("/proc/self/maps", "r") as f:
            for line in f:
                if "_ifcopenshell_wrapper" not in line:
                    continue
                addr, perms, *_rest = line.strip().split(None, 5)
                start_s, end_s = addr.split("-")
                start, end = int(start_s, 16), int(end_s, 16)
                rc = libc.madvise(
                    ctypes.c_void_p(start),
                    ctypes.c_size_t(end - start),
                    MADV_NOHUGEPAGE,
                )
                if rc == 0:
                    advised += 1
                else:
                    failed += 1
    except Exception as exc:
        return {"madv_error": repr(exc)}
    return {"madv_nohuge_vmas": advised, "madv_nohuge_failed": failed}


def _faulthandler_setup(prefix: str) -> None:
    """
    Configure faulthandler so we get a Python frame in `<prefix>.faulthandler`.

    HUNT_NO_FAULTHANDLER=1   -> do NOT install any signal handler. The
                                kernel SIGSEGV is delivered directly to
                                SIG_DFL, which writes a core where %RIP
                                still points at the primary fault site.
                                We lose the Python frame but gain a clean
                                C++ unwind in gdb.

    HUNT_NO_CHAIN=1          -> install faulthandler.enable() / .register()
                                but with chain=False. We get the Python
                                frame written to `.faulthandler` AND the
                                process exits via faulthandler without
                                re-raising, so no kernel core is written.
                                Useful when we already trust the Python
                                site and want to keep disk usage low.

    default (no env)         -> chain=True. The Python frame is written to
                                `.faulthandler`, then SIGSEGV is re-raised
                                to SIG_DFL so a core is dumped, but the
                                core's RIP is the re-raise site, not the
                                primary fault — frames #5+ are libpython
                                stack at re-raise time.
    """
    if os.environ.get("HUNT_NO_FAULTHANDLER") in ("1", "true", "yes"):
        return
    chain = os.environ.get("HUNT_NO_CHAIN") not in ("1", "true", "yes")
    fh_path = HUNT_DUMP_DIR / f"{prefix}.faulthandler"
    fh_file = open(fh_path, "wb", buffering=0)
    faulthandler.enable(file=fh_file, all_threads=True)
    for sig in (
        signal.SIGSEGV,
        signal.SIGABRT,
        signal.SIGBUS,
        signal.SIGFPE,
        signal.SIGILL,
    ):
        try:
            faulthandler.register(sig, file=fh_file, all_threads=True, chain=chain)
        except (RuntimeError, ValueError):
            pass


def _select_products(f) -> list:
    import ifcopenshell.util.selector as sel
    selected = list(sel.filter_elements(f, SELECTOR) or [])
    kept = []
    for e in selected:
        try:
            if any(e.is_a(t) for t in PROTECTED_TYPES):
                continue
        except Exception:
            pass
        kept.append(e)
    return kept


def _bulk_detach_relationships(f, target_ids: Set[int]) -> int:
    import ifcopenshell
    detached = 0
    for rel in list(f.by_type("IfcRelationship")):
        try:
            info = rel.get_info(recursive=False)
        except Exception:
            continue
        dirty = False
        for attr_name, val in info.items():
            if attr_name in ("id", "type"):
                continue
            if isinstance(val, ifcopenshell.entity_instance) and val.id() in target_ids:
                try:
                    setattr(rel, attr_name, None)
                    dirty = True
                except Exception:
                    pass
            elif isinstance(val, (tuple, list)):
                filtered = [
                    v
                    for v in val
                    if not (
                        isinstance(v, ifcopenshell.entity_instance)
                        and v.id() in target_ids
                    )
                ]
                if len(filtered) != len(val):
                    try:
                        setattr(rel, attr_name, filtered)
                        dirty = True
                    except Exception:
                        pass
        if dirty:
            detached += 1
    return detached


def _collect_orphaned_geom(f, elements: list, remove_ids: Set[int]) -> tuple:
    orphaned_geom_ids: Set[int] = set()
    orphaned_placement_ids: Set[int] = set()
    for e in elements:
        repr_ = getattr(e, "Representation", None)
        if repr_:
            inv = f.get_inverse(repr_)
            if not ({x.id() for x in inv} - remove_ids):
                orphaned_geom_ids.add(repr_.id())
        place = getattr(e, "ObjectPlacement", None)
        if place:
            inv = f.get_inverse(place)
            if not ({x.id() for x in inv} - remove_ids):
                orphaned_placement_ids.add(place.id())
    return orphaned_geom_ids, orphaned_placement_ids


def _remove_geometry_tree(f, prod_repr_ids: Set[int]) -> int:
    removed = 0
    for rid in prod_repr_ids:
        try:
            prod_repr = f.by_id(rid)
        except Exception:
            continue
        sub_reps = list(getattr(prod_repr, "Representations", []) or [])
        try:
            f.remove(prod_repr)
            removed += 1
        except Exception:
            continue
        for sub in sub_reps:
            try:
                inv = f.get_inverse(sub)
                if not inv:
                    items = list(getattr(sub, "Items", []) or [])
                    f.remove(sub)
                    removed += 1
                    for item in items:
                        try:
                            if not f.get_inverse(item):
                                f.remove(item)
                                removed += 1
                        except Exception:
                            pass
            except Exception:
                pass
    return removed


def _remove_empty_relationships(f) -> int:
    removed = 0
    for rel in list(f.by_type("IfcRelationship")):
        try:
            info = rel.get_info(recursive=False)
            related = [k for k in info if k.startswith("Related")]
            if related and all(not info[k] or info[k] == () for k in related):
                f.remove(rel)
                removed += 1
        except Exception:
            pass
    return removed


def run_once(iteration: int, input_path: str) -> dict:
    import ifcopenshell
    tag = _ts_tag()
    Path(HUNT_DUMP_DIR / f"{tag}.start").write_text(
        f"iter={iteration} pid={os.getpid()} input={input_path} "
        f"selector={SELECTOR!r} ts={time.time()}\n"
    )
    _snapshot(tag)

    t0 = time.time()
    log = logging.getLogger(f"repro.{iteration}")

    f = ifcopenshell.open(input_path)
    n_root = len(f.by_type("IfcRoot"))

    elements = _select_products(f)
    remove_ids = {e.id() for e in elements}

    orphan_geom, orphan_place = _collect_orphaned_geom(f, elements, remove_ids)

    type_ids: Set[int] = set()
    try:
        import ifcopenshell.util.element as ielem
        for e in elements:
            et = ielem.get_type(e)
            if et:
                type_ids.add(et.id())
    except Exception:
        pass

    _bulk_detach_relationships(f, remove_ids)

    for e in elements:
        try:
            f.remove(e)
        except Exception:
            pass

    _remove_geometry_tree(f, orphan_geom)
    for pid_ in orphan_place:
        try:
            f.remove(f.by_id(pid_))
        except Exception:
            pass

    for tid in type_ids:
        try:
            type_entity = f.by_id(tid)
        except Exception:
            continue
        has_instances = False
        for rel in getattr(type_entity, "Types", []):
            if getattr(rel, "RelatedObjects", None):
                has_instances = True
                break
        if has_instances:
            continue
        try:
            for rel in list(getattr(type_entity, "Types", [])):
                f.remove(rel)
            f.remove(type_entity)
        except Exception:
            pass

    cleaned = _remove_empty_relationships(f)

    n_after = len(f.by_type("IfcRoot"))
    elapsed = time.time() - t0
    print(
        f"[repro {iteration}] OK  IfcRoot {n_root}->{n_after}  "
        f"removed={len(elements)}  empty-rels-cleaned={cleaned}  "
        f"{elapsed:.1f}s",
        flush=True,
    )
    Path(HUNT_DUMP_DIR / f"{tag}.ok").write_text(
        f"iter={iteration} elapsed={elapsed}\n"
    )
    return {"ok": True, "iteration": iteration, "elapsed_s": elapsed}


def main() -> int:
    print(
        f"# repro-local-wrapper  pid={os.getpid()}  iters={ITERATIONS}  "
        f"dump_dir={HUNT_DUMP_DIR}",
        flush=True,
    )
    print(f"# python={sys.version.split()[0]}", flush=True)

    # Apply per-process kernel-allocator mitigations BEFORE the wrapper
    # is loaded so PR_SET_THP_DISABLE takes effect for the very first
    # mmap of _ifcopenshell_wrapper.so.
    mitigations, libc = _apply_kernel_mitigations()
    if mitigations:
        print(f"# pre-import mitigations: {mitigations}", flush=True)

    import ifcopenshell  # noqa: F401  -- triggers wrapper load
    print(
        f"# ifcopenshell.__file__={ifcopenshell.__file__}",
        flush=True,
    )
    print(
        f"# wrapper.__file__={ifcopenshell.ifcopenshell_wrapper.__file__}",
        flush=True,
    )

    # Post-import mitigations (mlock + madvise) need the wrapper to be
    # mmap'd already.
    post_mit = {}
    post_mit.update(_madvise_nohugepage_wrapper(libc))
    post_mit.update(_mlock_wrapper(libc))
    if post_mit:
        print(f"# post-import mitigations: {post_mit}", flush=True)

    base_tag = _ts_tag()
    _faulthandler_setup(base_tag)
    print(
        f"# faulthandler installed -> {HUNT_DUMP_DIR}/{base_tag}.faulthandler",
        flush=True,
    )

    if not os.path.exists(INPUT_PATH):
        print(f"ERROR: input file missing: {INPUT_PATH}", file=sys.stderr)
        print(
            "  Download once with:\n"
            "  python3.10 ifcpipeline/scripts/fetch-input-from-minio.py",
            file=sys.stderr,
        )
        return 2

    failures = 0
    for i in range(1, ITERATIONS + 1):
        try:
            run_once(i, INPUT_PATH)
        except SystemExit:
            raise
        except BaseException as exc:
            failures += 1
            print(
                f"[repro {i}] EXCEPTION {type(exc).__name__}: {exc}",
                flush=True,
            )
            traceback.print_exc()

    print(
        f"# done failures={failures}/{ITERATIONS} pid={os.getpid()}",
        flush=True,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
