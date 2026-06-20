"""CLI driver for the TGraph vs Graph vs NetworkX evaluation.

Examples
--------
    python -m tgraph_eval.run_eval --probe          # verify adapters on smoke model
    python -m tgraph_eval.run_eval --smoke          # one small model, all ops
    python -m tgraph_eval.run_eval --full           # curated non-heavy matrix
    python -m tgraph_eval.run_eval --heavy          # add the 125/142 MB models
    python -m tgraph_eval.run_eval --models E1,M1 --ops betweenness,closeness --repeats 3

Writes into the directory given by --out (default /results):
    <out>/<key>.json          per-model structured report
    <out>/summary.csv         flat one-row-per-(model,op) table
    <out>/summary.md          human-readable Markdown report
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

from tgraph_eval import bench_core, models


def _print(*a):
    print(*a, flush=True)


# --------------------------------------------------------------------------- #
# probe: confirm the real TGraph return shapes before a full run
# --------------------------------------------------------------------------- #

def probe(path: str) -> int:
    from topologicpy.Graph import Graph
    from topologicpy.TGraph import TGraph
    from tgraph_eval.bench_core import LegacyAdapter, TGraphAdapter, GID_KEY, HAS_NX

    _print(f"topologicpy version: {bench_core._tpy_version()}")
    _print(f"networkx available:  {HAS_NX}")
    _print(f"probe model:         {path}")

    _print("\n-- building legacy Graph --")
    g = LegacyAdapter.build(path)
    _print("  type:", type(g).__name__, "| order:", LegacyAdapter.order(g), "| size:", LegacyAdapter.size(g))

    _print("\n-- building TGraph --")
    tg = TGraphAdapter.build(path)
    _print("  type:", type(tg).__name__, "| order:", TGraph.Order(tg), "| size:", TGraph.Size(tg))

    verts = TGraph.Vertices(tg)
    _print("\n-- TGraph.Vertices --")
    _print("  count:", len(verts), "| record type:", type(verts[0]).__name__ if verts else None)
    if verts:
        r = verts[0]
        _print("  record keys:", list(r.keys()) if isinstance(r, dict) else "n/a")
        _print("  dictionary keys:", list((r.get('dictionary') or {}).keys())[:12] if isinstance(r, dict) else "n/a")
        _print("  sample GID:", (r.get('dictionary') or {}).get(GID_KEY) if isinstance(r, dict) else "n/a")

    edges = TGraph.Edges(tg)
    _print("\n-- TGraph.Edges --")
    _print("  count:", len(edges), "| record type:", type(edges[0]).__name__ if edges else None)
    if edges and isinstance(edges[0], dict):
        _print("  edge keys:", list(edges[0].keys()))
    _print("  edge_records() path used:", "public" if (edges and isinstance(edges[0], dict) and 'src' in edges[0]) else "private _edges fallback")

    _print("\n-- centrality return shape --")
    bc = TGraph.BetweennessCentrality(tg, key="betweenness_centrality", silent=True)
    _print("  TGraph.BetweennessCentrality ->", type(bc).__name__, "len:", len(bc) if hasattr(bc, '__len__') else "n/a")

    _print("\n-- fidelity quick check --")
    lv, tv = LegacyAdapter.vertex_set(g), TGraphAdapter.vertex_set(tg)
    le, te = LegacyAdapter.edge_set(g), TGraphAdapter.edge_set(tg)
    _print(f"  gid vertices: legacy={len(lv)} tgraph={len(tv)} jaccard={bench_core.jaccard(lv, tv)}")
    _print(f"  gid edges:    legacy={len(le)} tgraph={len(te)} jaccard={bench_core.jaccard(le, te)}")
    _print("\nprobe OK")
    return 0


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #

def _fmt(x: Any) -> str:
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:.4g}"
    return str(x)


def write_reports(reports: List[Dict[str, Any]], out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)

    for rep in reports:
        key = rep["model"]["key"]
        with open(os.path.join(out_dir, f"{key}.json"), "w") as fh:
            json.dump(rep, fh, indent=2, default=str)

    # ---- flat CSV ----
    csv_path = os.path.join(out_dir, "summary.csv")
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["model", "discipline", "size_mb", "op",
                    "legacy_s", "tgraph_s", "networkx_s", "speedup",
                    "legacy_status", "tgraph_status",
                    "accuracy_summary"])
        for rep in reports:
            m = rep["model"]
            con = rep.get("construct", {})
            w.writerow([m["key"], m["discipline"], m["size_mb"], "construct",
                        _g(con, "legacy", "median_s"), _g(con, "tgraph", "median_s"),
                        "", con.get("speedup"),
                        _g(con, "legacy", "status"), _g(con, "tgraph", "status"), ""])
            for op, e in rep.get("ops", {}).items():
                w.writerow([m["key"], m["discipline"], m["size_mb"], op,
                            _g(e, "legacy", "median_s"), _g(e, "tgraph", "median_s"),
                            _g(e, "networkx", "median_s"), e.get("speedup"),
                            _g(e, "legacy", "status"), _g(e, "tgraph", "status"),
                            _accuracy_summary(op, e)])

    # ---- Markdown ----
    md_path = os.path.join(out_dir, "summary.md")
    with open(md_path, "w") as fh:
        fh.write(_render_md(reports))

    _print(f"\nwrote: {csv_path}")
    _print(f"wrote: {md_path}")
    _print(f"wrote: {len(reports)} per-model JSON file(s) in {out_dir}")


def _g(d: Dict[str, Any], *path) -> Any:
    cur = d
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _accuracy_summary(op: str, e: Dict[str, Any]) -> str:
    acc = e.get("accuracy")
    if not acc:
        return ""
    if op in ("betweenness", "closeness"):
        gv = acc.get("graph_vs_tgraph", {})
        nv = acc.get("tgraph_vs_networkx", {})
        parts = []
        if gv:
            parts.append(f"G~TG r={_fmt(gv.get('pearson'))} maxΔ={_fmt(gv.get('max_abs_diff'))}")
        if nv:
            parts.append(f"TG~NX r={_fmt(nv.get('pearson'))}")
        return "; ".join(parts)
    if op == "degree":
        return f"maxΔ={_fmt(acc.get('max_abs_diff'))} r={_fmt(acc.get('pearson'))}"
    if op in ("bridges", "cut_vertices"):
        return f"L={acc.get('legacy_count')} TG={acc.get('tgraph_count')} J={_fmt(acc.get('jaccard'))}"
    if op == "shortest_path":
        return (f"hops nx={acc.get('networkx_hops')} L={acc.get('legacy_hops')} "
                f"TG={acc.get('tgraph_hops')}")
    if op == "community":
        return f"L={acc.get('legacy_communities')} TG={acc.get('tgraph_communities')}"
    return ""


def _render_md(reports: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ver = reports[0].get("topologicpy_version", "?") if reports else "?"
    lines.append(f"# TGraph evaluation report\n")
    lines.append(f"- generated: `{ts}`")
    lines.append(f"- topologicpy: `{ver}`")
    lines.append(f"- models: {', '.join(r['model']['key'] for r in reports)}\n")

    # Construction + fidelity overview
    lines.append("## Construction & fidelity\n")
    lines.append("| Model | Disc. | MB | Graph build (s) | TGraph build (s) | Build speedup | |V| L/TG | |E| L/TG | vtx Jaccard | edge Jaccard | RSS MB |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in reports:
        m, con, fid = r["model"], r.get("construct", {}), r.get("fidelity", {})
        lines.append("| {k} | {d} | {mb} | {lb} | {tb} | {sp} | {lo}/{to} | {le}/{te} | {vj} | {ej} | {rss} |".format(
            k=m["key"], d=m["discipline"], mb=m["size_mb"],
            lb=_fmt(_g(con, "legacy", "median_s")), tb=_fmt(_g(con, "tgraph", "median_s")),
            sp=_fmt(con.get("speedup")),
            lo=_fmt(fid.get("legacy_order")), to=_fmt(fid.get("tgraph_order")),
            le=_fmt(fid.get("legacy_size")), te=_fmt(fid.get("tgraph_size")),
            vj=_fmt(fid.get("vertex_jaccard")), ej=_fmt(fid.get("edge_jaccard")),
            rss=_fmt(r.get("rss_mb")),
        ))

    # Per-model op tables
    for r in reports:
        m = r["model"]
        lines.append(f"\n## {m['key']} — {m['discipline']} (~{m['size_mb']} MB)\n")
        if r.get("errors"):
            lines.append("**errors:**")
            for err in r["errors"]:
                lines.append(f"- `{err}`")
            lines.append("")
        lines.append("| Op | Graph (s) | TGraph (s) | NetworkX (s) | Speedup TG/G | Accuracy / fidelity |")
        lines.append("|---|---|---|---|---|---|")
        for op, e in r.get("ops", {}).items():
            lines.append("| {op} | {l} | {t} | {n} | {sp} | {acc} |".format(
                op=op,
                l=_status_or_time(e, "legacy"), t=_status_or_time(e, "tgraph"),
                n=_status_or_time(e, "networkx"), sp=_fmt(e.get("speedup")),
                acc=_accuracy_summary(op, e) or "-",
            ))
    lines.append("\n---\n")
    lines.append("_Speedup = legacy Graph time / TGraph time (higher = TGraph faster). "
                 "Jaccard = set overlap on IFC GlobalId (1.0 = identical graph). "
                 "r = Pearson correlation of per-vertex values; NetworkX is the independent oracle._\n")
    return "\n".join(lines)


def _status_or_time(e: Dict[str, Any], engine: str) -> str:
    sub = e.get(engine)
    if not isinstance(sub, dict):
        return "-"
    if sub.get("status") == "ok":
        return _fmt(sub.get("median_s"))
    return sub.get("status", "-")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="TGraph vs Graph vs NetworkX evaluation")
    p.add_argument("--smoke", action="store_true", help="one small model (E1), all ops")
    p.add_argument("--full", action="store_true", help="curated non-heavy matrix")
    p.add_argument("--heavy", action="store_true", help="include 125/142 MB models")
    p.add_argument("--probe", action="store_true", help="verify adapters on the smoke model and exit")
    p.add_argument("--models", default="", help="comma list of model keys (overrides smoke/full/heavy)")
    p.add_argument("--ops", default="", help="comma list of ops (default: all)")
    p.add_argument("--repeats", type=int, default=1, help="repeats per op (median reported)")
    p.add_argument("--timeout", type=float, default=600.0, help="per-op timeout seconds (0 = none)")
    p.add_argument("--out", default="/results", help="output directory")
    args = p.parse_args(argv)

    if args.probe:
        return probe(models.by_key(models.SMOKE_KEY).path)

    keys = [k.strip() for k in args.models.split(",") if k.strip()] or None
    ops = [o.strip() for o in args.ops.split(",") if o.strip()] or list(bench_core.DEFAULT_OPS)

    if not (args.smoke or args.full or args.heavy or keys):
        args.smoke = True  # safe default

    selected = models.select(smoke=args.smoke, heavy=args.heavy, keys=keys)
    _print(f"topologicpy {bench_core._tpy_version()} | networkx={bench_core.HAS_NX}")
    _print(f"models: {[m.key for m in selected]} | ops: {ops} | repeats={args.repeats} | timeout={args.timeout}s\n")

    reports = []
    for m in selected:
        _print(f"===== {m.label} =====")
        rep = bench_core.run_model(m, ops, repeats=args.repeats, timeout=args.timeout, log=_print)
        reports.append(rep)
        _print(f"  done: {len(rep.get('errors', []))} error(s), peak RSS {rep.get('rss_mb')} MB\n")

    write_reports(reports, args.out)
    print("\n" + _render_md(reports))
    return 0


if __name__ == "__main__":
    sys.exit(main())
