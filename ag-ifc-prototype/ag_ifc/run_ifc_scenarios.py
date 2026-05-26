"""CLI: run IFC clash scenario matrix (+ optional AG formalization)."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from ag_ifc.ag2_runner import ensure_vendor
from ag_ifc.ifc_models import list_available_sets
from ag_ifc.ifc_scenarios import load_ifc_scenarios, run_ifc_scenario


def _root() -> Path:
    return Path(__file__).resolve().parent.parent


def build_summary(results: list) -> dict:
    by_tag: dict[str, dict] = defaultdict(lambda: {"total": 0, "with_clashes": 0})
    for r in results:
        for tag in r.tags:
            by_tag[tag]["total"] += 1
            if r.clash_count > 0:
                by_tag[tag]["with_clashes"] += 1

    return {
        "total": len(results),
        "skipped": sum(1 for r in results if r.skipped),
        "with_clashes": sum(1 for r in results if r.clash_count > 0),
        "total_clash_pairs": sum(r.clash_count for r in results),
        "ag_formalized": sum(1 for r in results if r.ag_formalization),
        "ag_proven": sum(
            1 for r in results if r.ag_formalization and r.ag_formalization.get("proven")
        ),
        "by_tag": dict(by_tag),
    }


def format_markdown(summary: dict, results: list, model_status: list) -> str:
    lines = [
        "# IFC clash scenario report",
        "",
        f"- Scenarios run: **{summary['total']}**",
        f"- With clashes: **{summary['with_clashes']}** ({summary['total_clash_pairs']} pairs total)",
        f"- Skipped: **{summary['skipped']}**",
    ]
    if summary.get("ag_formalized"):
        lines.append(
            f"- AG formalization proven: **{summary['ag_proven']}** / {summary['ag_formalized']}"
        )
    lines.extend(["", "## Model availability", ""])
    for ms in model_status:
        status = "ready" if ms["ready"] else ("optional/LFS" if ms.get("lfs_required") else "missing")
        lines.append(f"- **{ms['id']}** ({ms['name']}): {status}")

    lines.extend(
        [
            "",
            "## Scenarios with clashes",
            "",
            "| ID | Clashes | A class | B class |",
            "|----|--------:|---------|---------|",
        ]
    )
    for r in sorted(results, key=lambda x: -x.clash_count):
        if r.clash_count <= 0 or r.skipped:
            continue
        sc = r.sample_clash or {}
        lines.append(
            f"| {r.scenario_id} | {r.clash_count} | {sc.get('a_ifc_class', '')} | {sc.get('b_ifc_class', '')} |"
        )

    lines.extend(["", "## All scenarios", "", "| ID | Clashes | Skipped | ms |", "|----|--------:|---------|-----:|"])
    for r in results:
        lines.append(
            f"| {r.scenario_id} | {r.clash_count} | {r.skipped} | {r.elapsed_ms:.0f} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run IFC clash scenario matrix")
    parser.add_argument("--report-dir", default="reports")
    parser.add_argument("--formalize-ag", action="store_true", help="Run AG2 on sample clash")
    parser.add_argument("--list-models", action="store_true")
    parser.add_argument("--fetch-models", action="store_true", help="Try downloading missing IFCs")
    args = parser.parse_args(argv)

    if args.list_models or args.fetch_models:
        status = list_available_sets(fetch=args.fetch_models)
        print(json.dumps(status, indent=2))
        if args.list_models and not args.fetch_models:
            return 0

    try:
        from ifcclash.ifcclash import Clasher  # noqa: F401
    except ImportError:
        print(
            "ifcclash not installed. Run: pip install -r requirements-ifc.txt",
            file=sys.stderr,
        )
        return 1

    manifest = __import__("ag_ifc.ifc_models", fromlist=["load_manifest"]).load_manifest()
    _, scenarios = load_ifc_scenarios()

    logger = logging.getLogger("ifcclash")
    logger.setLevel(logging.WARNING)

    vendor = None
    if args.formalize_ag:
        vendor = ensure_vendor(_root())

    results = [
        run_ifc_scenario(
            s,
            manifest,
            logger=logger,
            formalize_ag=args.formalize_ag,
            vendor=vendor,
        )
        for s in scenarios
    ]

    model_status = list_available_sets(fetch=False)
    summary = build_summary(results)

    report_dir = _root() / args.report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "model_sets": model_status,
        "results": [
            {
                "scenario_id": r.scenario_id,
                "name": r.name,
                "aec_use_case": r.aec_use_case,
                "tags": r.tags,
                "clash_count": r.clash_count,
                "skipped": r.skipped,
                "skip_reason": r.skip_reason,
                "elapsed_ms": round(r.elapsed_ms, 2),
                "sample_clash": r.sample_clash,
                "ag_formalization": r.ag_formalization,
            }
            for r in results
        ],
    }

    json_path = report_dir / f"ifc_scenarios_{stamp}.json"
    latest = report_dir / "ifc_scenarios_latest.json"
    for target in (json_path, latest):
        with target.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

    md = format_markdown(summary, results, model_status)
    (report_dir / f"ifc_scenarios_{stamp}.md").write_text(md, encoding="utf-8")
    (report_dir / "ifc_scenarios_latest.md").write_text(md, encoding="utf-8")

    csv_path = report_dir / f"ifc_scenarios_{stamp}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "scenario_id",
                "clash_count",
                "skipped",
                "a_ifc_class",
                "b_ifc_class",
                "ag_proven",
                "elapsed_ms",
            ],
        )
        writer.writeheader()
        for r in results:
            sc = r.sample_clash or {}
            ag = r.ag_formalization or {}
            writer.writerow(
                {
                    "scenario_id": r.scenario_id,
                    "clash_count": r.clash_count,
                    "skipped": r.skipped,
                    "a_ifc_class": sc.get("a_ifc_class", ""),
                    "b_ifc_class": sc.get("b_ifc_class", ""),
                    "ag_proven": ag.get("proven", ""),
                    "elapsed_ms": round(r.elapsed_ms, 2),
                }
            )

    print(json.dumps(summary, indent=2))
    print(f"\nWrote {latest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
