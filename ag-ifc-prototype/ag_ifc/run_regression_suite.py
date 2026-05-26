"""CLI: full global regression suite with BCF export of validated fixes."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from ag_ifc.ag2_runner import ensure_vendor
from ag_ifc.ifc_models import load_manifest
from ag_ifc.reasoning3d import run_workflow_case
from ag_ifc.workflow_types import Workflow3DResult


def _root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_suite(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _result_dict(r: Workflow3DResult) -> dict:
    return {
        "case_id": r.case_id,
        "passed": r.passed,
        "initial_clash_count": r.initial_clash_count,
        "final_clash_count": r.final_clash_count,
        "iterations_used": r.iterations_used,
        "regression_passed": r.regression_passed,
        "bcf_export": r.bcf_export,
        "validated_fix_count": len(r.validated_fixes),
        "work_dir": r.work_dir,
        "elapsed_ms": round(r.elapsed_ms, 2),
        "regression_reports": r.regression_reports,
        "validated_fixes": r.validated_fixes,
    }


def format_markdown(summary: dict, results: list[Workflow3DResult]) -> str:
    lines = [
        "# Global regression + BCF export report",
        "",
        f"- Cases: {summary['total']}",
        f"- Passed (zero clashes + regression): {summary['passed']}",
        f"- Regression failures: {summary['regression_failed']}",
        f"- BCF exports: {summary['bcf_exports']}",
        "",
        "| Case | Initial | Final | Regression | Validated | BCF |",
        "|------|--------:|------:|:----------:|----------:|:---:|",
    ]
    for r in results:
        bcf = "yes" if r.bcf_export else "—"
        reg = "pass" if r.regression_passed else "FAIL"
        lines.append(
            f"| {r.case_id} | {r.initial_clash_count} | {r.final_clash_count} | "
            f"{reg} | {len(r.validated_fixes)} | {bcf} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Regression suite with BCF validated fixes")
    parser.add_argument("--suite", default="scenarios/regression_suite.json")
    parser.add_argument("--work-dir", default="reports/regression_work")
    parser.add_argument("--report-dir", default="reports")
    parser.add_argument("--case", action="append")
    parser.add_argument("--no-ag", action="store_true")
    parser.add_argument("--no-bcf", action="store_true")
    parser.add_argument("--allow-regression", action="store_true", help="Do not stop on new global clashes")
    args = parser.parse_args(argv)

    root = _root()
    try:
        import ifcopenshell  # noqa: F401
    except ImportError:
        print("Install requirements-ifc.txt", file=sys.stderr)
        return 1

    suite = _load_suite(root / args.suite)
    manifest = load_manifest()
    manifest["ifc_clash_defaults"] = suite.get("ifc_clash_defaults", {})

    vendor = None if args.no_ag else ensure_vendor(root)
    logger = logging.getLogger("regression")
    logger.setLevel(logging.WARNING)

    cases = suite["cases"]
    if args.case:
        cases = [c for c in cases if c["id"] in args.case]
    defaults = suite.get("defaults", {})

    results: list[Workflow3DResult] = []
    for case in cases:
        merged = {**defaults, **case}
        if args.no_bcf:
            merged["export_bcf"] = False
        if args.allow_regression:
            merged["stop_on_regression_failure"] = False
        print(f"Running {merged['id']}...", flush=True)
        r = run_workflow_case(merged, manifest, root / args.work_dir, vendor, logger)
        results.append(r)
        print(
            f"  {'PASS' if r.passed else 'FAIL'} clashes {r.initial_clash_count}->{r.final_clash_count} "
            f"regression={'ok' if r.regression_passed else 'FAIL'} bcf={bool(r.bcf_export)}",
            flush=True,
        )

    passed = sum(1 for r in results if r.passed and not r.skipped)
    reg_fail = sum(1 for r in results if not r.regression_passed)
    bcf_n = sum(1 for r in results if r.bcf_export)

    summary = {
        "total": len(results),
        "passed": passed,
        "regression_failed": reg_fail,
        "bcf_exports": bcf_n,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    report_dir = root / args.report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = {"summary": summary, "results": [_result_dict(r) for r in results]}
    (report_dir / "regression_suite_latest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (report_dir / "regression_suite_latest.md").write_text(
        format_markdown(summary, results).replace("\\n", "\n"),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
