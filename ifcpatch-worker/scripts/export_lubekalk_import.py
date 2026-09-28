#!/usr/bin/env python3
"""
Export a Lubekalk quantity import from an IFC patched by ``AssignLubekalkPset``.

Reads each element's ``Lubekalk`` property set, expands it into Lubekalk rows
(the primary row plus any insulation, hanger and detail-insulation rows), sums
identical rows the way the import expects, and writes:

* ``Lubekalk import``   — columns Kod, Rev, Handling, P-del, Beteckning, Dim 1,
  Dim 2, Montageläge, Kanalmaterial, Antal, Längd, Höjd. Paste or import as is.
* ``Isolering att koda`` — insulation on the model that has no Lubekalk code in
  the mapping yet, with its length per type and dimension, so it is priced
  deliberately instead of dropped.
* ``Granska``            — every element that is not ``Klar``/``Ingår``/``Utanför``,
  and every row whose code is an assumption (``Säkerhet`` medel/låg).
* ``Förkortade beteckningar`` — Beteckning values shortened to what Lubekalk
  holds (15 characters; a longer value fails with "The field is too small"),
  next to the full name on the element.
* ``Utelämnade koder``   — rows left out with ``--omit-codes`` (codes the
  receiving Lubekalk register does not know), with their lengths and counts, so
  they are added by hand instead of silently lost.
* ``Status``             — counts by status, class and code.

Writes .xlsx when openpyxl is installed, otherwise one semicolon CSV per sheet
with decimal commas (Swedish Excel opens both). ``--lubekalk-csv`` also writes
the import sheet as the file Lubekalk's import reads directly: semicolons,
decimal commas, Windows-1252.

    python scripts/export_lubekalk_import.py patched.ifc [more.ifc …] --out lubekalk_import.xlsx \
        [--omit-codes CU1,RU1] [--lubekalk-csv lubekalk_import.csv]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import ifcopenshell

WORKER_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKER_ROOT / "custom_recipes"))

from _lubekalk_export import BETECKNING_MAX, collect, write, write_lubekalk_csv  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ifc", type=Path, nargs="+", help="IFC(s) patched by AssignLubekalkPset")
    parser.add_argument("--out", type=Path, default=Path("lubekalk_import.xlsx"))
    parser.add_argument("--summary", type=Path, help="Also write the status summary as JSON")
    parser.add_argument("--omit-codes", default="",
                        help="Comma-separated codes the receiving Lubekalk register lacks "
                             "(it answers 'Okänd kod'); listed on 'Utelämnade koder' instead")
    parser.add_argument("--lubekalk-csv", type=Path,
                        help="Also write the import sheet as Lubekalk's CSV (semicolons, decimal commas)")
    parser.add_argument("--csv-encoding", default="cp1252", help="Encoding of --lubekalk-csv (default cp1252)")
    parser.add_argument("--keep-beteckning-only", metavar="CODES",
                        help="Blank Beteckning on every row except these codes (e.g. SAK), so details "
                             "attach to their duct row in Lubekalk")
    parser.add_argument("--beteckning-max", type=int, default=BETECKNING_MAX,
                        help=f"Longest Beteckning Lubekalk accepts (default {BETECKNING_MAX})")
    args = parser.parse_args()
    result = collect(*(ifcopenshell.open(str(path)) for path in args.ifc),
                     omit_codes=args.omit_codes.split(","), beteckning_max=args.beteckning_max,
                     keep_beteckning=args.keep_beteckning_only.split(",") if args.keep_beteckning_only else None)
    for path in write(result, args.out):
        print(f"wrote {path}")
    if args.lubekalk_csv:
        replaced = write_lubekalk_csv(result["import"], args.lubekalk_csv, args.csv_encoding)
        print(f"wrote {args.lubekalk_csv} ({len(result['import'])} rows, {args.csv_encoding}"
              + (f", {replaced} characters not representable and replaced" if replaced else "") + ")")
    if result["shortened"]:
        print(f"shortened {len(result['shortened'])} Beteckning values to {args.beteckning_max} characters")
    for code, totals in result["summary"]["omitted_codes"].items():
        print(f"omitted {code}: {totals['rader']} rows, {totals['Längd']} m, {totals['Antal']} st")
    if args.summary:
        args.summary.write_text(json.dumps(result["summary"], ensure_ascii=False, indent=2), encoding="utf-8")
    summary = result["summary"]
    print(f"elements {summary['elements']}: {summary['by_status']}")
    print(f"import-ready share {summary['import_ready_share']}; "
          f"{len(result['import'])} import rows from {summary['rows_before_aggregation']} element rows; "
          f"{summary['review_rows']} to review; {summary['insulation_to_code_groups']} insulation groups to code")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
