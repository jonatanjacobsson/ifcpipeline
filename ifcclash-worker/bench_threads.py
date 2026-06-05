"""Benchmark ifcclash iterator-thread count.

Measures the geometry-build phase only (`add_collision_objects`-equivalent
loop) for a given pair of IFC files at several thread counts, using the
same kernel and tuned via the env var the worker reads.

Run inside the ifcclash-worker container:

    docker compose exec ifcclash-worker python /app/bench_threads.py \
        --a /uploads/<a>.ifc --b /uploads/<b>.ifc \
        --threads 1,4,8

The bench bypasses S3, the rq queue, and validation, so the timings
reflect pure ifcopenshell.geom + ifcopenshell.geom.tree work.
"""

from __future__ import annotations

import argparse
import gc
import os
import resource
import sys
import time
from typing import Optional

import ifcopenshell
import ifcopenshell.geom


def _peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _build_tree_for(
    ifc_file: ifcopenshell.file,
    threads: int,
    geometry_library: str,
    geom_settings: ifcopenshell.geom.settings,
) -> tuple[float, float, int]:
    """Mirror CustomClasher.add_collision_objects geometry phase.

    Returns (iterator_init_seconds, tree_build_seconds, n_shapes).
    """
    elements = set(ifc_file.by_type("IfcElement"))
    elements -= set(ifc_file.by_type("IfcFeatureElement"))

    tree = ifcopenshell.geom.tree()

    t0 = time.perf_counter()
    iterator = ifcopenshell.geom.iterator(
        geom_settings,
        ifc_file,
        threads,
        include=list(elements),
        geometry_library=geometry_library,
    )
    if not iterator.initialize():
        return time.perf_counter() - t0, 0.0, 0
    iter_init = time.perf_counter() - t0

    t1 = time.perf_counter()
    n = 0
    while True:
        tree.add_element(iterator.get())
        n += 1
        if not iterator.next():
            break
    tree_build = time.perf_counter() - t1
    return iter_init, tree_build, n


def _bench_one(
    path_a: str,
    path_b: str,
    threads: int,
    geometry_library: str,
) -> dict:
    gc.collect()
    rss_before = _peak_rss_mb()

    geom_settings = ifcopenshell.geom.settings()

    t0 = time.perf_counter()
    ifc_a = ifcopenshell.open(path_a)
    ifc_b = ifcopenshell.open(path_b)
    open_s = time.perf_counter() - t0

    a_iter, a_tree, a_n = _build_tree_for(ifc_a, threads, geometry_library, geom_settings)
    b_iter, b_tree, b_n = _build_tree_for(ifc_b, threads, geometry_library, geom_settings)

    rss_after = _peak_rss_mb()

    total_geom = a_iter + a_tree + b_iter + b_tree
    return {
        "threads": threads,
        "geometry_library": geometry_library,
        "open_s": open_s,
        "a_iter_init_s": a_iter,
        "a_tree_build_s": a_tree,
        "a_shapes": a_n,
        "b_iter_init_s": b_iter,
        "b_tree_build_s": b_tree,
        "b_shapes": b_n,
        "total_geom_s": total_geom,
        "peak_rss_delta_mb": rss_after - rss_before,
        "peak_rss_mb": rss_after,
    }


def _fmt(row: dict) -> str:
    return (
        f"threads={row['threads']:>2}  "
        f"open={row['open_s']:.2f}s  "
        f"a({row['a_shapes']})={row['a_iter_init_s']:.2f}+{row['a_tree_build_s']:.2f}s  "
        f"b({row['b_shapes']})={row['b_iter_init_s']:.2f}+{row['b_tree_build_s']:.2f}s  "
        f"total_geom={row['total_geom_s']:.2f}s  "
        f"peak_rss={row['peak_rss_mb']:.0f}MB  "
        f"(Δ{row['peak_rss_delta_mb']:+.0f}MB)"
    )


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--a", required=True, help="Path to first IFC file")
    p.add_argument("--b", required=True, help="Path to second IFC file")
    p.add_argument(
        "--threads", default="1,4,8",
        help="Comma-separated thread counts to benchmark (default 1,4,8)",
    )
    p.add_argument(
        "--kernel", default=os.environ.get("IFCCLASH_GEOMETRY_LIBRARY", "opencascade"),
        help="Geometry library kernel: opencascade|cgal|cgal-simple|hybrid-cgal-simple-opencascade",
    )
    args = p.parse_args(argv)

    if not os.path.exists(args.a):
        print(f"ERROR: --a not found: {args.a}", file=sys.stderr)
        return 2
    if not os.path.exists(args.b):
        print(f"ERROR: --b not found: {args.b}", file=sys.stderr)
        return 2

    thread_list = [int(t) for t in args.threads.split(",") if t.strip()]
    print(
        f"Bench: {os.path.basename(args.a)} vs {os.path.basename(args.b)} "
        f"kernel={args.kernel} threads={thread_list}"
    )

    results = []
    for n in thread_list:
        os.environ["IFCCLASH_ITERATOR_THREADS"] = str(n)
        row = _bench_one(args.a, args.b, n, args.kernel)
        print(_fmt(row))
        results.append(row)

    if len(results) >= 2:
        baseline = results[0]["total_geom_s"]
        print()
        print("Speedup vs threads=%d:" % results[0]["threads"])
        for r in results:
            print(
                f"  threads={r['threads']:>2}  "
                f"speedup={baseline / r['total_geom_s']:.2f}x  "
                f"({r['total_geom_s']:.2f}s)"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
