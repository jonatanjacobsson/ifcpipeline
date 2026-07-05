"""Ingest-script benchmark harness — times every ingest_scripts Ingester on real IFC files.

Runs INSIDE the topologicpy-worker container (2 CPU / 6 GB cgroup — representative of prod).
Each case executes in a fresh subprocess so native-lib state, module caches, and crashes
(SIGSEGV) can't bleed between cases. Results include an output fingerprint (stable hashes of
the relationship/element sets) so optimized code can be diffed against the baseline for
correctness, not just speed.

Usage (driven by run_bench.sh from the host):
    python bench.py --matrix matrix.json --out results.json [--only A,B] [--timeout 900]
                    [--profile] [--repeat 1]
    python bench.py --single-case '<case json>' [--profile-out /path/prof]   # internal
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

TIME_KEYS = {"elapsed_seconds", "duration_ms", "elapsed", "seconds", "duration_seconds"}


def _fingerprint(ingester) -> dict:
    """Stable content hashes over the extracted graph (order-independent)."""
    rels = ingester.get_relationships()
    elems = ingester.get_elements()
    rel_lines = sorted(
        "|".join([
            str(r.get("subject_global_id")), str(r.get("object_global_id")),
            str(r.get("relationship_family")), str(r.get("relationship_type")),
            f"{float(r.get('confidence') or 0):.4f}",
        ])
        for r in rels
    )
    elem_lines = sorted(
        f"{e.get('global_id')}|{e.get('ifc_class')}" for e in elems
    )
    summary = {k: v for k, v in ingester.get_summary().items() if k not in TIME_KEYS}
    return {
        "relationship_count": len(rels),
        "element_count": len(elems),
        "rel_sha256": hashlib.sha256("\n".join(rel_lines).encode()).hexdigest(),
        "elem_sha256": hashlib.sha256("\n".join(elem_lines).encode()).hexdigest(),
        "summary": summary,
    }


def run_single(case: dict, profile_out: str | None = None) -> dict:
    import logging

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    log = logging.getLogger(f"bench.{case['script']}")

    from ingest_scripts import load_script

    files = [Path(f) for f in case["files"]]
    for f in files:
        if not f.exists():
            return {"status": "missing_input", "missing": str(f)}

    t_import0 = time.perf_counter()
    cls = load_script(case["script"])
    t_import = time.perf_counter() - t_import0

    ingester = cls(ifc_files=files, log=log, **case.get("kwargs", {}))

    prof = None
    if profile_out:
        import cProfile
        prof = cProfile.Profile()

    t0 = time.perf_counter()
    try:
        if prof:
            prof.enable()
        ingester.extract()
    finally:
        if prof:
            prof.disable()
    wall_extract = time.perf_counter() - t0

    t1 = time.perf_counter()
    fp = _fingerprint(ingester)
    wall_fingerprint = time.perf_counter() - t1

    if prof and profile_out:
        import io
        import pstats
        prof.dump_stats(profile_out + ".pstats")
        s = io.StringIO()
        ps = pstats.Stats(prof, stream=s).sort_stats("cumulative")
        ps.print_stats(60)
        with open(profile_out + ".txt", "w") as fh:
            fh.write(s.getvalue())

    peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    return {
        "status": "ok",
        "wall_extract_s": round(wall_extract, 3),
        "wall_fingerprint_s": round(wall_fingerprint, 3),
        "import_s": round(t_import, 3),
        "peak_rss_mb": round(peak_rss_mb, 1),
        "fingerprint": fp,
    }


def run_matrix(args) -> None:
    matrix = json.loads(Path(args.matrix).read_text())
    only = set(args.only.split(",")) if args.only else None
    results = []
    out_dir = Path(args.out).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    prof_dir = out_dir / "profiles"
    if args.profile:
        prof_dir.mkdir(exist_ok=True)

    for case in matrix:
        cid = case["case_id"]
        if only and cid not in only and case["script"] not in only:
            continue
        runs = []
        for i in range(args.repeat):
            cmd = [sys.executable, os.path.abspath(__file__), "--single-case", json.dumps(case)]
            if args.profile:
                cmd += ["--profile-out", str(prof_dir / f"{cid}_r{i}")]
            t0 = time.perf_counter()
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=args.timeout,
                    env=os.environ.copy(),
                )
                wall_total = time.perf_counter() - t0
                last_json = None
                for line in reversed(proc.stdout.splitlines()):
                    line = line.strip()
                    if line.startswith("{"):
                        try:
                            last_json = json.loads(line)
                            break
                        except json.JSONDecodeError:
                            continue
                if proc.returncode != 0 or last_json is None:
                    runs.append({
                        "status": "crashed", "returncode": proc.returncode,
                        "wall_total_s": round(wall_total, 3),
                        "stderr_tail": proc.stderr[-2000:],
                        "stdout_tail": proc.stdout[-1000:],
                    })
                else:
                    last_json["wall_total_s"] = round(wall_total, 3)
                    runs.append(last_json)
            except subprocess.TimeoutExpired:
                runs.append({"status": "timeout", "timeout_s": args.timeout})
        entry = {"case_id": cid, "script": case["script"], "files": case["files"],
                 "kwargs": case.get("kwargs", {}), "runs": runs}
        results.append(entry)
        best = min((r.get("wall_extract_s", float("inf")) for r in runs), default=None)
        status = runs[-1]["status"] if runs else "skipped"
        print(f"[bench] {cid:45s} {status:8s} extract={best}s", flush=True)
        Path(args.out).write_text(json.dumps(results, indent=1))

    print(f"[bench] wrote {args.out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix")
    ap.add_argument("--out", default="results.json")
    ap.add_argument("--only", default="")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--profile", action="store_true")
    ap.add_argument("--single-case")
    ap.add_argument("--profile-out", default=None)
    args = ap.parse_args()

    if args.single_case:
        res = run_single(json.loads(args.single_case), profile_out=args.profile_out)
        print(json.dumps(res, default=str))
        return
    if not args.matrix:
        ap.error("--matrix required (or --single-case)")
    run_matrix(args)


if __name__ == "__main__":
    main()
