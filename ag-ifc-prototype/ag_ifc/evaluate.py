"""Run AG-IFC prototype evaluation suite and write JSON report."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from ag_ifc.ag2_runner import ensure_vendor, prove_problem, run_upstream_smoke, vendor_path
from ag_ifc.clash_tools import run_ifcclash_smoke
from ag_ifc.compiler import clash_to_ag2_stub, load_clash


def _root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_fixture_problems(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    return data["problems"]


def run_evaluation(with_ifc: bool = False) -> dict:
    root = _root()
    vendor = ensure_vendor(root)
    manifest_path = root / "fixtures" / "evaluation_manifest.json"
    with manifest_path.open(encoding="utf-8") as handle:
        manifest = json.load(handle)

    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "prototype_version": "0.1.0",
        "ag2_vendor": str(vendor),
        "sections": {},
    }

    report["sections"]["ag2_reference"] = run_upstream_smoke(vendor)

    fixture_results = []
    for item in _load_fixture_problems(root / "fixtures" / "problems.json"):
        result = prove_problem(item["id"], item["ag2"], vendor)
        fixture_results.append(
            {
                "id": item["id"],
                "description": item.get("description"),
                "tags": item.get("tags", []),
                "proven": result.proven,
                "goal": result.goal,
                "elapsed_ms": round(result.elapsed_ms, 2),
                "error": result.error,
            }
        )
    report["sections"]["ag2_fixtures"] = {
        "total": len(fixture_results),
        "proven": sum(1 for r in fixture_results if r["proven"]),
        "results": fixture_results,
    }

    clash_path = root / "fixtures" / "clash_sample.json"
    clash = load_clash(clash_path)
    stub = clash_to_ag2_stub(clash)
    compiled_proof = prove_problem(f"clash_{stub.clash_id}", stub.ag2, vendor)
    report["sections"]["clash_compiler"] = {
        "clash_id": stub.clash_id,
        "assumptions": stub.assumptions,
        "mapping": stub.mapping,
        "ag2": stub.ag2,
        "proven": compiled_proof.proven,
        "goal": compiled_proof.goal,
        "error": compiled_proof.error,
    }

    if with_ifc:
        examples = (root / "fixtures" / "evaluation_manifest.json").parent.parent
        examples = root.parent / "shared" / "examples"
        report["sections"]["ifcclash_smoke"] = run_ifcclash_smoke(
            examples,
            manifest.get("ifc_clash_sets", []),
        )
    else:
        report["sections"]["ifcclash_smoke"] = {
            "skipped": True,
            "reason": "pass --with-ifc to enable",
        }

    report["summary"] = {
        "ag2_reference_ok": report["sections"]["ag2_reference"].get("ok", False),
        "fixtures_proven": report["sections"]["ag2_fixtures"]["proven"],
        "fixtures_total": report["sections"]["ag2_fixtures"]["total"],
        "clash_compiler_proven": report["sections"]["clash_compiler"]["proven"],
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AG-IFC prototype evaluation")
    parser.add_argument(
        "--report",
        default="reports/eval_report.json",
        help="Output JSON path (relative to ag-ifc-prototype root)",
    )
    parser.add_argument(
        "--with-ifc",
        action="store_true",
        help="Run IfcClash smoke test (requires requirements-ifc.txt)",
    )
    args = parser.parse_args(argv)

    root = _root()
    report = run_evaluation(with_ifc=args.with_ifc)

    out = root / args.report
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)

    summary = report["summary"]
    print(json.dumps(summary, indent=2))
    ok = (
        summary["ag2_reference_ok"]
        and summary["fixtures_proven"] == summary["fixtures_total"]
        and summary["clash_compiler_proven"]
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
