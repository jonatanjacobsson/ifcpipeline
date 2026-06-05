#!/usr/bin/env python3
"""Compare PyPI ifcfast vs ifcopenshell vs ifcpipeline export paths.

Usage:
  PYTHONPATH=shared python3 n8n-tests/profile-ifcfast-diagnosis.py [model.ifc]
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "shared"))

DEFAULT_IFC = os.path.join(
    ROOT,
    "ifc-coord/reports/nobel_mep_eval/ifc_coord_elec_vs_plumb_rerun/work/nobel_elec_vs_plumb/P1_2b_BIM_XXX_5000_00.ifc",
)
QUERY = os.environ.get("IFC_BENCH_QUERY", "IfcProduct")
ATTRS = ["Name", "Description"]


def ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_IFC
    if not os.path.isfile(path):
        print(f"Missing IFC: {path}", file=sys.stderr)
        return 1

    size_mb = os.path.getsize(path) / (1024 * 1024)
    print(f"IFC: {path}\nSize: {size_mb:.1f} MiB  query={QUERY}\n")

    import ifcopenshell

    t0 = time.perf_counter()
    m = ifcopenshell.open(path)
    open_ms = ms(t0)
    t0 = time.perf_counter()
    n = sum(1 for _ in m)
    iter_ms = ms(t0)
    print(f"[IOS] open:              {open_ms:>8.1f} ms")
    print(f"[IOS] iterate all:       {iter_ms:>8.1f} ms  ({n:,} entities)")
    print(f"[IOS] open+iter:         {open_ms + iter_ms:>8.1f} ms\n")

    import ifcfast

    t0 = time.perf_counter()
    fm = ifcfast.open(path)
    fast_open_ms = ms(t0)
    t0 = time.perf_counter()
    pdf = fm.products_df
    df_ms = ms(t0)
    print(f"[PyPI ifcfast] open:           {fast_open_ms:>8.1f} ms")
    print(f"[PyPI ifcfast] products_df:    {df_ms:>8.1f} ms  ({len(pdf):,} rows)")
    print(f"[PyPI ifcfast] total:          {fast_open_ms + df_ms:>8.1f} ms")
    ratio = (open_ms + iter_ms) / max(fast_open_ms + df_ms, 0.1)
    print(f"      vs IOS open+iter: ~{ratio:.0f}x faster\n")

    from ifcfast_export import export_products_csv

    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "native.csv")
        t0 = time.perf_counter()
        rows = export_products_csv(path, out, query=QUERY, attributes=ATTRS)
        native_ms = ms(t0)
        print(f"[Worker] native export_products_csv: {native_ms:>8.1f} ms  ({rows:,} rows)")

        import ifccsv
        import ifcopenshell.util.selector

        t0 = time.perf_counter()
        m2 = ifcopenshell.open(path)
        t_open = ms(t0)
        t0 = time.perf_counter()
        elements = list(ifcopenshell.util.selector.filter_elements(m2, "IfcElement"))
        t_filt = ms(t0)
        out2 = os.path.join(tmp, "ifccsv.csv")
        t0 = time.perf_counter()
        conv = ifccsv.IfcCsv()
        conv.export(m2, elements, ATTRS, output=out2, format="csv", include_global_id=True)
        t_exp = ms(t0)
        print(f"\n[ifccsv] open {t_open} ms + filter IfcElement {t_filt} ms ({len(elements):,}) + export {t_exp} ms")
        print(f"         total {t_open + t_filt + t_exp:.1f} ms  ({len(conv.results):,} rows)")
    print("\nNote: row counts differ — products_df (tier-1) vs IfcElement selector (full graph).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
