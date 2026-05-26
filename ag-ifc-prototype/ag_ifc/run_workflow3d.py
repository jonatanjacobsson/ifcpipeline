"""CLI: 3D clash routing + AEC reasoning workflow evaluation suite."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from ag_ifc.ag2_runner import ensure_vendor
from ag_ifc.ifc_models import load_manifest
from ag_ifc.reasoning3d import Workflow3DResult, run_workflow_case


def _root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_suite(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _result_to_dict(r: Workflow3DResult) -> dict:
    return {
        "case_id": r.case_id,
        "passed": r.passed,
        "initial_clash_count": r.initial_clash_count,
        "final_clash_count": r.final_clash_count,
        "iterations_used": r.iterations_used,
        "max_iterations": r.max_iterations,
        "skipped": r.skipped,
        "skip_reason": r.skip_reason,
        "work_dir": r.work_dir,
        "elapsed_ms": round(r.elapsed_ms, 2),
        "regression_passed": getattr(r, "regression_passed", True),
        "bcf_export": getattr(r, "bcf_export", None),
        "validated_fix_count": len(getattr(r, "validated_fixes", [])),
        "triage_order": r.triage_order,
        "fixes": [
            {
                "iteration": f.iteration,
                "clash_key": f.clash_key,
                "severity": f.severity,
                "cluster_id": f.cluster_id,
                "moved_guid": f.moved_guid,
                "moved_class": f.moved_class,
                "route_reached_goal": f.route_reached_goal,
                "route_waypoints": f.route_waypoints,
                "translation": f.translation,
                "clash_count_before": f.clash_count_before,
                "clash_count_after": f.clash_count_after,
                "triage_rationale": f.triage_rationale,
                "ag_proofs": [
                    {
                        "problem_id": p.problem_id,
                        "proven": p.proven,
                        "goal": p.goal,
                        "plane": p.plane,
                        "error": p.error,
                    }
                    for p in f.ag_proofs
                ],
            }
            for f in r.fixes
        ],
    }


def format_markdown(summary: dict, results: list[Workflow3DResult]) -> str:
    lines = [
        "# 3D clash routing + AEC reasoning report",
        "",
        f"- **Cases:** {summary['total']}",
        f"- **Passed:** {summary['passed']}",
        f"- **Failed:** {summary['failed']}",
        f"- **Skipped:** {summary['skipped']}",
        "",
        "## Results",
        "",
        "| Case | Initial | Final | Iters | Route OK | AG proofs | Status |",
        "|------|--------:|------:|------:|---------:|----------:|--------|",
    ]
    for r in results:
        route_ok = sum(1 for f in r.fixes if f.route_reached_goal)
        ag_ok = sum(
            sum(1 for p in f.ag_proofs if p.proven)
            for f in r.fixes
        )
        ag_total = sum(len(f.ag_proofs) for f in r.fixes)
        status = "SKIP" if r.skipped else ("PASS" if r.passed else "FAIL")
        lines.append(
            f"| {r.case_id} | {r.initial_clash_count} | {r.final_clash_count} | "
            f"{r.iterations_used} | {route_ok}/{len(r.fixes)} | {ag_ok}/{ag_total} | {status} |"
        )
    lines.extend(["", "## Triage order (first iteration)", ""])
    for r in results:
        if not r.triage_order:
            continue
        lines.append(f"### {r.case_id}")
        for item in r.triage_order[:8]:
            lines.append(
                f"- `{item['clash_key']}` score={item['score']} "
                f"{item['severity']} cluster={item['cluster_id']}"
            )
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="3D clash routing workflow suite")
    parser.add_argument("--suite", default="scenarios/workflow3d_suite.json")
    parser.add_argument("--work-dir", default="reports/workflow3d_work")
    parser.add_argument("--report-dir", default="reports")
    parser.add_argument("--case", action="append")
    parser.add_argument("--no-ag", action="store_true")
    args = parser.parse_args(argv)

    root = _root()
    try:
        import ifcopenshell  # noqa: F401
        from ifcclash.ifcclash import Clasher  # noqa: F401
    except ImportError:
        print("Install: pip install -r requirements-ifc.txt", file=sys.stderr)
        return 1

    suite = _load_suite(root / args.suite)
    manifest = load_manifest()
    manifest["ifc_clash_defaults"] = suite.get("ifc_clash_defaults", {})

    vendor = None
    if not args.no_ag:
        vendor = ensure_vendor(root)

    logger = logging.getLogger("reasoning3d")
    logger.setLevel(logging.WARNING)

    work_root = root / args.work_dir
    cases = suite["cases"]
    if args.case:
        cases = [c for c in cases if c["id"] in args.case]

    defaults = suite.get("defaults", {})
    results: list[Workflow3DResult] = []
    for case in cases:
        merged = {**defaults, **case}
        print(f"Running {merged['id']}...", flush=True)
        result = run_workflow_case(merged, manifest, work_root, vendor, logger)
        results.append(result)
        status = "PASS" if result.passed else ("SKIP" if result.skipped else "FAIL")
        print(
            f"  {status} initial={result.initial_clash_count} "
            f"final={result.final_clash_count} iters={result.iterations_used}",
            flush=True,
        )

    passed = sum(1 for r in results if r.passed and not r.skipped)
    failed = sum(1 for r in results if not r.passed and not r.skipped)
    skipped = sum(1 for r in results if r.skipped)

    summary = {
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    report_dir = root / args.report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {"summary": summary, "results": [_result_to_dict(r) for r in results]}

    for name in (f"workflow3d_suite_{stamp}.json", "workflow3d_suite_latest.json"):
        with (report_dir / name).open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

    md = format_markdown(summary, results)
    (report_dir / f"workflow3d_suite_{stamp}.md").write_text(md, encoding="utf-8")
    (report_dir / "workflow3d_suite_latest.md").write_text(md, encoding="utf-8")

    print(json.dumps(summary, indent=2))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
