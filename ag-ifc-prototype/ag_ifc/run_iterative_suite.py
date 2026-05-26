"""CLI: iterative clash resolution evaluation suite."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from ag_ifc.ag2_runner import ensure_vendor
from ag_ifc.iterative_clash import FixAction, IterativeResult, run_suite_case
from ag_ifc.ifc_models import load_manifest


def _root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_suite(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _result_to_dict(r: IterativeResult) -> dict:
    return {
        "suite_id": r.suite_id,
        "passed": r.passed,
        "initial_clash_count": r.initial_clash_count,
        "final_clash_count": r.final_clash_count,
        "iterations_used": r.iterations_used,
        "max_iterations": r.max_iterations,
        "skipped": r.skipped,
        "skip_reason": r.skip_reason,
        "work_dir": r.work_dir,
        "output_ifc": r.output_ifc,
        "elapsed_ms": round(r.elapsed_ms, 2),
        "fixes": [
            {
                "iteration": f.iteration,
                "clash_key": f.clash_key,
                "moved_guid": f.moved_guid,
                "moved_class": f.moved_class,
                "moved_file": f.moved_file,
                "translation": f.translation,
                "clash_count_before": f.clash_count_before,
                "clash_count_after": f.clash_count_after,
                "ag_proven": f.ag_proven,
                "ag_goal": f.ag_goal,
                "ag_error": f.ag_error,
            }
            for f in r.fixes
        ],
    }


def format_markdown(summary: dict, results: list[IterativeResult]) -> str:
    lines = [
        "# Iterative clash resolution report",
        "",
        f"- **Cases:** {summary['total']}",
        f"- **Passed:** {summary['passed']}",
        f"- **Failed:** {summary['failed']}",
        f"- **Skipped:** {summary['skipped']}",
        "",
        "## Results",
        "",
        "| Case | Initial | Final | Iters | AG proofs | Status |",
        "|------|--------:|------:|------:|----------:|--------|",
    ]
    for r in results:
        ag_ok = sum(1 for f in r.fixes if f.ag_proven)
        status = "SKIP" if r.skipped else ("PASS" if r.passed else "FAIL")
        lines.append(
            f"| {r.suite_id} | {r.initial_clash_count} | {r.final_clash_count} | "
            f"{r.iterations_used} | {ag_ok}/{len(r.fixes)} | {status} |"
        )
    lines.extend(["", "## Fix log (per case)", ""])
    for r in results:
        lines.append(f"### {r.suite_id}")
        if r.skipped:
            lines.append(f"Skipped: {r.skip_reason}")
            continue
        if not r.fixes:
            lines.append("No fixes needed (zero initial clashes).")
            continue
        for f in r.fixes:
            ag = "n/a" if f.ag_proven is None else ("yes" if f.ag_proven else "no")
            lines.append(
                f"- Iter {f.iteration}: moved `{f.moved_class}` ({f.moved_guid[:12]}…) "
                f"Δ={[round(x, 3) for x in f.translation]} "
                f"clashes {f.clash_count_before}→{f.clash_count_after} AG={ag}"
            )
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Iterative clash fix evaluation suite")
    parser.add_argument("--suite", default="scenarios/iterative_suite.json")
    parser.add_argument("--work-dir", default="reports/iterative_work")
    parser.add_argument("--report-dir", default="reports")
    parser.add_argument("--case", action="append", help="Run only these case ids")
    parser.add_argument("--no-ag", action="store_true", help="Skip AG verification")
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

    logger = logging.getLogger("iterative_clash")
    logger.setLevel(logging.WARNING)

    work_root = root / args.work_dir
    cases = suite["cases"]
    if args.case:
        cases = [c for c in cases if c["id"] in args.case]

    defaults = suite.get("defaults", {})
    results: list[IterativeResult] = []
    for case in cases:
        merged = {**defaults, **case}
        case = merged
        print(f"Running {case['id']}...", flush=True)
        result = run_suite_case(case, manifest, work_root, vendor, logger)
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

    for name in (f"iterative_suite_{stamp}.json", "iterative_suite_latest.json"):
        with (report_dir / name).open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

    md = format_markdown(summary, results)
    (report_dir / f"iterative_suite_{stamp}.md").write_text(md, encoding="utf-8")
    (report_dir / "iterative_suite_latest.md").write_text(md, encoding="utf-8")

    print(json.dumps(summary, indent=2))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
