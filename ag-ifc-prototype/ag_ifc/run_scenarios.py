"""CLI: run full AEC scenario matrix and write reports."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from ag_ifc.ag2_runner import ensure_vendor
from ag_ifc.scenario_report import build_summary, format_markdown
from ag_ifc.scenario_runner import run_catalog
from ag_ifc.scenarios import merge_catalogs


def _root() -> Path:
    return Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run AEC scenario matrix against AlphaGeometry2"
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Regenerate catalog_generated.json before running",
    )
    parser.add_argument(
        "--base-only",
        action="store_true",
        help="Skip generated catalog (hand-authored only)",
    )
    parser.add_argument(
        "--report-dir",
        default="reports",
        help="Output directory for JSON, MD, CSV",
    )
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Exit 1 if any scenario with expected.* fails match",
    )
    args = parser.parse_args(argv)

    root = _root()
    if args.generate:
        import subprocess

        subprocess.run(
            [sys.executable, str(root / "scripts" / "generate_scenarios.py")],
            check=True,
            cwd=str(root),
        )

    paths = [root / "scenarios" / "catalog_base.json"]
    generated = root / "scenarios" / "catalog_generated.json"
    if not args.base_only:
        if not generated.exists():
            import subprocess

            subprocess.run(
                [sys.executable, str(root / "scripts" / "generate_scenarios.py")],
                check=True,
                cwd=str(root),
            )
        paths.append(generated)

    scenarios = merge_catalogs(*paths)
    vendor = ensure_vendor(root)
    outcomes = run_catalog(scenarios, vendor)
    summary = build_summary(outcomes)

    report_dir = root / args.report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    json_path = report_dir / f"scenario_matrix_{stamp}.json"
    md_path = report_dir / f"scenario_matrix_{stamp}.md"
    csv_path = report_dir / f"scenario_matrix_{stamp}.csv"
    latest_json = report_dir / "scenario_matrix_latest.json"
    latest_md = report_dir / "scenario_matrix_latest.md"

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scenario_count": len(scenarios),
        "catalogs": [str(p.relative_to(root)) for p in paths],
        "summary": summary,
        "results": [o.to_dict() for o in outcomes],
    }
    for target in (json_path, latest_json):
        with target.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

    md_text = format_markdown(summary, outcomes)
    for target in (md_path, latest_md):
        target.write_text(md_text, encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "scenario_id",
                "category",
                "subcategory",
                "aec_use_case",
                "aec_utility_hypothesis",
                "setup_ok",
                "proven",
                "expected_match",
                "error",
            ],
        )
        writer.writeheader()
        for o in outcomes:
            writer.writerow(
                {
                    "scenario_id": o.scenario_id,
                    "category": o.category,
                    "subcategory": o.subcategory,
                    "aec_use_case": o.aec_use_case,
                    "aec_utility_hypothesis": o.aec_utility_hypothesis,
                    "setup_ok": o.setup_ok,
                    "proven": o.proven,
                    "expected_match": o.expected_match,
                    "error": o.error or "",
                }
            )

    print(json.dumps(summary, indent=2))
    print(f"\nWrote:\n  {json_path}\n  {md_path}\n  {csv_path}")

    regressions = [o for o in outcomes if o.expected_match is False]
    if regressions:
        print(f"\nRegression mismatches: {len(regressions)}")
        for o in regressions[:10]:
            print(f"  - {o.scenario_id}")

    if args.fail_on_regression and regressions:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
