"""Clash resolve pipeline: multi-attempt per clash, global regression, BCF export."""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np

from ag_ifc.ag2_runner import prove_problem
from ag_ifc.bcf_export import ValidatedFix, export_manifest, export_validated_fixes_bcf
from ag_ifc.clash_prefilter import assess_clash_suitability
from ag_ifc.clash_regression import (
    ClashSnapshot,
    compare_regression,
    clash_stable_id,
    save_snapshot,
)
from ag_ifc.clash_resolve import ClashResolution, resolve_clash_with_retries
from ag_ifc.clash_runner import clash_count, run_clash_set
from ag_ifc.clash_sorter import ScoredClash, sort_clashes
from ag_ifc.compiler import clash_to_ag2_multiplane, route_segments_to_ag2_problems
from ag_ifc.ifc_geometry import element_geom
from ag_ifc.iterative_clash import _prepare_work_copies
from ag_ifc.workflow_types import AgProofRecord, Workflow3DResult, WorkflowFix, prove_stubs


@dataclass
class PipelineOptions:
    max_clash_rounds: int = 10
    max_attempts_per_clash: int = 5
    clearance_m: float = 0.05
    step_m: float = 0.15
    grid_step_m: float = 0.1
    move_side: Literal["a", "b", "auto"] = "auto"
    verify_ag: bool = True
    prefilter_solve_only: bool = True
    global_regression: bool = True
    export_bcf: bool = True
    stop_on_regression_failure: bool = True


def run_resolve_pipeline(
    case_id: str,
    path_a: Path,
    path_b: Path,
    *,
    clash_set_options: dict[str, Any],
    work_root: Path,
    options: PipelineOptions | None = None,
    vendor: Path | None = None,
    logger: logging.Logger | None = None,
) -> Workflow3DResult:
    opts = options or PipelineOptions()
    log = logger or logging.getLogger("resolve_pipeline")
    start = time.perf_counter()

    work_dir = work_root / case_id
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)

    work_a, work_b = _prepare_work_copies(path_a, path_b, work_dir)
    ifc_paths = [str(work_a), str(work_b)]
    guid_to_file: dict[str, Path] = {}

    def refresh_guid_map():
        import ifcopenshell

        guid_to_file.clear()
        for path in (work_a, work_b):
            ifc = ifcopenshell.open(path)
            for prod in ifc.by_type("IfcProduct"):
                if prod.GlobalId:
                    guid_to_file[prod.GlobalId] = path

    refresh_guid_map()
    output_json = str(work_dir / "clash_out.json")
    clash_mode = clash_set_options.get("mode", "intersection")

    def make_clash_set() -> dict[str, Any]:
        cs = {
            "name": case_id,
            "a": [{"file": str(work_a)}],
            "b": [{"file": str(work_b)}],
            "mode": clash_mode,
            "tolerance": clash_set_options.get("tolerance", 0.01),
            "check_all": clash_set_options.get("check_all", False),
            "allow_touching": clash_set_options.get("allow_touching", False),
            "clearance": clash_set_options.get("clearance", 0),
            "_output_path": output_json,
        }
        if clash_set_options.get("a_selector"):
            cs["a"][0]["selector"] = clash_set_options["a_selector"]
        if clash_set_options.get("b_selector"):
            cs["b"][0]["selector"] = clash_set_options["b_selector"]
        return cs

    def run_clash() -> dict[str, Any]:
        return run_clash_set(make_clash_set(), log)

    baseline = ClashSnapshot.from_clash_result(run_clash())
    save_snapshot(work_dir / "baseline_snapshot.json", baseline)
    initial = baseline.count

    fixes: list[WorkflowFix] = []
    validated: list[ValidatedFix] = []
    regression_reports: list[dict[str, Any]] = []
    triage_snapshot: list[dict[str, Any]] = []
    clash_round = 0
    regression_failed = False

    if initial == 0:
        return Workflow3DResult(
            case_id=case_id,
            passed=True,
            initial_clash_count=0,
            final_clash_count=0,
            iterations_used=0,
            max_iterations=opts.max_clash_rounds,
            work_dir=str(work_dir),
            elapsed_ms=(time.perf_counter() - start) * 1000,
        )

    ranked_initial = sort_clashes(
        baseline.clashes,
        move_side=opts.move_side,
        clash_mode=clash_mode,
    )
    triage_snapshot = [
        {
            "clash_key": s.clash_key,
            "score": round(s.score, 2),
            "severity": s.severity,
            "cluster_id": s.cluster_id,
            "movable_class": s.movable_class,
            "rationale": s.rationale,
        }
        for s in ranked_initial
    ]

    while clash_round < opts.max_clash_rounds:
        result_data = run_clash()
        count = clash_count(result_data)
        if count == 0:
            break

        clashes = result_data.get("clashes", {})
        ranked = sort_clashes(clashes, move_side=opts.move_side, clash_mode=clash_mode)
        if opts.prefilter_solve_only:
            ranked = [
                s
                for s in ranked
                if assess_clash_suitability(
                    s.clash_key,
                    s.clash,
                    clash_mode=clash_mode,
                    move_side=opts.move_side,
                    clearance_m=opts.clearance_m,
                    verify_ag=False,
                ).tier
                == "solve"
            ]
            if not ranked:
                ranked = sort_clashes(clashes, move_side=opts.move_side, clash_mode=clash_mode)[:1]

        scored: ScoredClash = ranked[0]
        clash = scored.clash
        guid = scored.movable_guid
        if guid not in guid_to_file:
            refresh_guid_map()
        target_path = guid_to_file.get(guid)
        if target_path is None:
            return Workflow3DResult(
                case_id=case_id,
                passed=False,
                initial_clash_count=initial,
                final_clash_count=count,
                iterations_used=clash_round,
                max_iterations=opts.max_clash_rounds,
                fixes=fixes,
                triage_order=triage_snapshot,
                work_dir=str(work_dir),
                skipped=True,
                skip_reason=f"GUID {guid} not found",
                elapsed_ms=(time.perf_counter() - start) * 1000,
            )

        stable = clash_stable_id(clash)
        ag_records: list[AgProofRecord] = []

        def after_attempt(attempt_rec) -> None:
            if opts.verify_ag and vendor is not None:
                pass  # AG on final attempt below

        resolution: ClashResolution = resolve_clash_with_retries(
            scored.clash_key,
            clash,
            guid=guid,
            target_path=target_path,
            work_ifc_paths=ifc_paths,
            make_clash_set=make_clash_set,
            run_clash=run_clash,
            clash_count_fn=clash_count,
            max_attempts_per_clash=opts.max_attempts_per_clash,
            clearance_m=opts.clearance_m,
            step_m=opts.step_m,
            grid_step_m=opts.grid_step_m,
        )
        refresh_guid_map()

        if opts.verify_ag and vendor is not None and resolution.attempts:
            clash_record = {
                "clash_id": f"{case_id}_r{clash_round}",
                **clash,
                "clearance_required_m": opts.clearance_m,
            }
            multi = clash_to_ag2_multiplane(clash_record, clearance_m=opts.clearance_m)
            ag_records.extend(prove_stubs(multi, vendor, f"{case_id}_mp"))
            last_wp = resolution.attempts[-1].route_waypoints
            if last_wp:
                seg_stubs = route_segments_to_ag2_problems(
                    last_wp,
                    clearance_m=opts.clearance_m,
                    clash_id=f"{case_id}_r{clash_round}",
                )
                ag_records.extend(prove_stubs(seg_stubs, vendor, f"{case_id}_rt"))

        ag_ok = any(r.proven for r in ag_records) if ag_records else False

        current_snap = ClashSnapshot.from_clash_result(run_clash())
        reg = compare_regression(
            baseline,
            current_snap,
            target_resolved={stable} if resolution.resolved else None,
        )
        regression_reports.append(reg.to_dict())
        save_snapshot(work_dir / f"regression_round_{clash_round}.json", current_snap)

        if opts.global_regression and not reg.passed:
            regression_failed = True
            log.warning("Global regression failed: %s", reg.message)

        last = resolution.attempts[-1] if resolution.attempts else None
        fixes.append(
            WorkflowFix(
                iteration=clash_round + 1,
                clash_key=scored.clash_key,
                severity=scored.severity,
                cluster_id=scored.cluster_id,
                moved_guid=guid,
                moved_class=scored.movable_class,
                route_waypoints=last.route_waypoints if last else [],
                route_reached_goal=last.route_reached_goal if last else False,
                translation=resolution.total_translation,
                clash_count_before=last.clash_count_before if last else count,
                clash_count_after=last.clash_count_after if last else count,
                ag_proofs=ag_records,
                triage_rationale=scored.rationale
                + [f"attempts={len(resolution.attempts)}", f"resolved={resolution.resolved}"],
            )
        )

        if resolution.resolved and reg.passed:
            validated.append(
                ValidatedFix.from_resolution(
                    case_id,
                    clash,
                    resolution,
                    regression_passed=reg.passed,
                    ag_proven=ag_ok,
                    moved_class=scored.movable_class,
                )
            )

        clash_round += 1
        if regression_failed and opts.stop_on_regression_failure:
            break

    final_snap = ClashSnapshot.from_clash_result(run_clash())
    final = final_snap.count
    final_reg = compare_regression(baseline, final_snap)
    regression_reports.append({"final": final_reg.to_dict()})

    bcf_path: str | None = None
    if opts.export_bcf and validated:
        bcf_out = work_dir / f"{case_id}_validated_fixes.bcf"
        export_validated_fixes_bcf(validated, bcf_out, project_name=case_id)
        export_manifest(validated, work_dir / f"{case_id}_validated_fixes.json")
        bcf_path = str(bcf_out)

    passed = final == 0 and not regression_failed
    result = Workflow3DResult(
        case_id=case_id,
        passed=passed,
        initial_clash_count=initial,
        final_clash_count=final,
        iterations_used=clash_round,
        max_iterations=opts.max_clash_rounds,
        fixes=fixes,
        triage_order=triage_snapshot,
        work_dir=str(work_dir),
        elapsed_ms=(time.perf_counter() - start) * 1000,
    )
    # attach extras via dynamic attrs for runners
    result.regression_reports = regression_reports  # type: ignore[attr-defined]
    result.validated_fixes = [v.__dict__ for v in validated]  # type: ignore[attr-defined]
    result.bcf_export = bcf_path  # type: ignore[attr-defined]
    result.regression_passed = final_reg.passed  # type: ignore[attr-defined]
    return result
