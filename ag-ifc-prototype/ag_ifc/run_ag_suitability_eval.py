"""CLI: evaluate AG suitability on all IfcClash clashes + emit prefilter rules."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from ag_ifc.ag2_runner import ensure_vendor
from ag_ifc.ag_suitability_eval import run_full_evaluation
from ag_ifc.ifc_models import load_manifest
from ag_ifc.ifc_scenarios import load_ifc_scenarios


def _root() -> Path:
    return Path(__file__).resolve().parent.parent


def format_markdown(report: dict) -> str:
    s = report["summary"]
    lines = [
        "# AG clash suitability evaluation",
        "",
        f"- **Scenarios:** {s['scenarios']}",
        f"- **Clashes evaluated:** {s['total_clashes']}",
        f"- **Solve tier (auto-fix candidates):** {s['solve_tier']} ({s['solve_rate_pct']}%)",
        f"- **Review tier:** {s['review_tier']}",
        f"- **Exclude tier:** {s['exclude_tier']}",
        f"- **AG relational proof on clash:** {s['ag_proven_clashes']}",
        "",
        "## Per scenario",
        "",
        "| Scenario | Clashes | Solve | Review | Exclude | AG proven |",
        "|----------|--------:|------:|-------:|--------:|----------:|",
    ]
    for row in report["scenario_summaries"]:
        if row["skipped"]:
            lines.append(f"| {row['scenario_id']} | — | SKIP | — | — | — |")
            continue
        lines.append(
            f"| {row['scenario_id']} | {row['clash_count']} | {row['solve']} | "
            f"{row['review']} | {row['exclude']} | {row['ag_proven']} |"
        )

    rules = report.get("prefilter_rules", {})
    lines.extend(["", "## Recommended auto-solve class pairs", ""])
    for pair in rules.get("recommended_auto_solve_pairs", [])[:15]:
        stats = rules.get("by_class_pair", {}).get(pair, {})
        lines.append(f"- `{pair}` — solve rate {stats.get('solve_rate_pct', '?')}%")

    lines.extend(["", "## AG role (from synthetic + IFC eval)", ""])
    ag = rules.get("ag_guidance", {})
    for item in ag.get("use_for_certification", []):
        lines.append(f"- **Use:** {item}")
    for item in ag.get("do_not_use_for", []):
        lines.append(f"- **Not:** {item}")

    lines.extend([
        "",
        "## Pre-filter usage",
        "",
        "```bash",
        "PYTHONPATH=. python3 -m ag_ifc.run_prefilter clash_export.json -o clash_solve_candidates.json",
        "```",
        "",
        "Keeps only `solve` tier clashes for iterative / workflow3d retest loops.",
    ])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate AG suitability on IfcClash clashes")
    parser.add_argument("--scenarios", default="scenarios/ifc_clash_scenarios.json")
    parser.add_argument("--report-dir", default="reports")
    parser.add_argument("--work-dir", default="reports/ag_suitability_work")
    parser.add_argument("--scenario", action="append", help="Limit to scenario ids")
    parser.add_argument("--no-ag", action="store_true", help="Heuristics only, skip DDAR proofs")
    args = parser.parse_args(argv)

    root = _root()
    try:
        import ifcopenshell  # noqa: F401
    except ImportError:
        print("Install: pip install -r requirements-ifc.txt", file=sys.stderr)
        return 1

    data, scenarios = load_ifc_scenarios(root / args.scenarios)
    manifest = load_manifest()
    if args.scenario:
        scenarios = [s for s in scenarios if s["id"] in args.scenario]

    vendor = None
    if not args.no_ag:
        vendor = ensure_vendor(root)

    logger = logging.getLogger("ag_suitability")
    logger.setLevel(logging.WARNING)

    report = run_full_evaluation(
        scenarios,
        manifest,
        work_dir=root / args.work_dir,
        verify_ag=not args.no_ag,
        vendor=vendor,
        logger=logger,
    )
    report["generated_at"] = datetime.now(timezone.utc).isoformat()

    report_dir = root / args.report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    for name in (f"ag_suitability_{stamp}.json", "ag_suitability_latest.json"):
        (report_dir / name).write_text(json.dumps(report, indent=2), encoding="utf-8")

    md = format_markdown(report)
    # fix accidental double escape
    (report_dir / "ag_suitability_latest.md").write_text(format_markdown(report), encoding="utf-8")

    print(json.dumps(report["summary"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())