"""CLI: pre-filter an IfcClash JSON export to solve-tier candidates only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ag_ifc.ag2_runner import ensure_vendor
from ag_ifc.clash_prefilter import prefilter_ifcclash_file


def _root() -> Path:
    return Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pre-filter IfcClash results for AG auto-fix")
    parser.add_argument("input", help="IfcClash JSON export (object or array)")
    parser.add_argument("-o", "--output", help="Write filtered JSON here")
    parser.add_argument(
        "--tiers",
        default="solve",
        help="Comma-separated tiers to keep: solve,review,exclude",
    )
    parser.add_argument("--clash-mode", default="intersection")
    parser.add_argument("--verify-ag", action="store_true", help="Run DDAR per clash (slower)")
    parser.add_argument("--no-meta", action="store_true")
    args = parser.parse_args(argv)

    tiers = tuple(t.strip() for t in args.tiers.split(",") if t.strip())
    vendor = None
    if args.verify_ag:
        vendor = ensure_vendor(_root())

    result = prefilter_ifcclash_file(
        args.input,
        output_path=args.output,
        tiers=tiers,  # type: ignore[arg-type]
        include_meta=not args.no_meta,
        clash_mode=args.clash_mode,
        verify_ag=args.verify_ag,
        vendor=vendor,
    )

    if not args.output:
        print(json.dumps(result, indent=2))

    meta = result.get("_prefilter") if isinstance(result, dict) else None
    if isinstance(result, list) and result:
        meta = result[0].get("_prefilter")
    if meta:
        print(
            f"Filtered {meta['filtered_count']}/{meta['original_count']} clashes "
            f"(tiers={meta['tiers_kept']})",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
