"""3D clash routing + AEC reasoning workflow with multi-plane AlphaGeometry certification."""

from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np

from ag_ifc.ag2_runner import ProveResult, prove_problem
from ag_ifc.clash_runner import clash_count, run_clash_set
from ag_ifc.clash_sorter import ScoredClash, sort_clashes
from ag_ifc.compiler import clash_to_ag2_multiplane, route_segments_to_ag2_problems
from ag_ifc.ifc_geometry import element_geom, obstacle_aabbs_for_clash
from ag_ifc.ifc_models import load_manifest, resolve_model_path
from ag_ifc.iterative_clash import _apply_translation, _prepare_work_copies
from ag_ifc.routing3d import Route3D, goal_point_from_clash, route_orthogonal


@dataclass
class AgProofRecord:
    problem_id: str
    proven: bool
    goal: str | None
    plane: str
    error: str | None = None


@dataclass
class WorkflowFix:
    iteration: int
    clash_key: str
    severity: str
    cluster_id: str
    moved_guid: str
    moved_class: str
    route_waypoints: list[list[float]]
    route_reached_goal: bool
    translation: list[float]
    clash_count_before: int
    clash_count_after: int
    ag_proofs: list[AgProofRecord] = field(default_factory=list)
    triage_rationale: list[str] = field(default_factory=list)


@dataclass
class Workflow3DResult:
    case_id: str
    passed: bool
    initial_clash_count: int
    final_clash_count: int
    iterations_used: int
    max_iterations: int
    fixes: list[WorkflowFix] = field(default_factory=list)
    triage_order: list[dict[str, Any]] = field(default_factory=list)
    work_dir: str = ""
    skipped: bool = False
    skip_reason: str | None = None
    elapsed_ms: float = 0


def _prove_stubs(
    stubs: list,
    vendor: Path,
    prefix: str,
) -> list[AgProofRecord]:
    records: list[AgProofRecord] = []
    for stub in stubs:
        pid = f"{prefix}_{stub.clash_id}"
        result: ProveResult = prove_problem(pid, stub.ag2, vendor)
        plane = stub.mapping.get("plane", "xy") if isinstance(stub.mapping, dict) else "xy"
        records.append(
            AgProofRecord(
                problem_id=pid,
                proven=result.proven,
                goal=result.goal,
                plane=str(plane),
                error=result.error,
            )
        )
    return records


def _clashes_dict(result_data: dict[str, Any]) -> dict[str, Any]:
    return result_data.get("clashes", {})


def run_workflow3d_resolution(
    case_id: str,
    path_a: Path,
    path_b: Path,
    *,
    clash_set_options: dict[str, Any],
    work_root: Path,
    max_iterations: int = 10,
    clearance_m: float = 0.05,
    step_m: float = 0.15,
    grid_step_m: float = 0.1,
    move_side: Literal["a", "b", "auto"] = "auto",
    verify_ag: bool = True,
    vendor: Path | None = None,
    logger: logging.Logger | None = None,
) -> Workflow3DResult:
    log = logger or logging.getLogger("reasoning3d")
    start = time.perf_counter()
    work_dir = work_root / case_id
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)

    work_a, work_b = _prepare_work_copies(path_a, path_b, work_dir)
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

    result_data = run_clash_set(make_clash_set(), log)
    initial = clash_count(result_data)
    fixes: list[WorkflowFix] = []
    triage_snapshot: list[dict[str, Any]] = []

    if initial == 0:
        return Workflow3DResult(
            case_id=case_id,
            passed=True,
            initial_clash_count=0,
            final_clash_count=0,
            iterations_used=0,
            max_iterations=max_iterations,
            work_dir=str(work_dir),
            elapsed_ms=(time.perf_counter() - start) * 1000,
        )

    iteration = 0
    local_step = step_m
    while iteration < max_iterations:
        result_data = run_clash_set(make_clash_set(), log)
        count = clash_count(result_data)
        if count == 0:
            break

        clashes = _clashes_dict(result_data)
        ranked = sort_clashes(
            clashes,
            move_side=move_side,
            clash_mode=clash_mode,
        )
        if iteration == 0:
            triage_snapshot = [
                {
                    "clash_key": s.clash_key,
                    "score": round(s.score, 2),
                    "severity": s.severity,
                    "cluster_id": s.cluster_id,
                    "movable_class": s.movable_class,
                    "rationale": s.rationale,
                }
                for s in ranked
            ]

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
                iterations_used=iteration,
                max_iterations=max_iterations,
                fixes=fixes,
                triage_order=triage_snapshot,
                work_dir=str(work_dir),
                skipped=True,
                skip_reason=f"GUID {guid} not found in work copies",
                elapsed_ms=(time.perf_counter() - start) * 1000,
            )

        geom = element_geom(str(target_path), guid)
        start_pt = geom.placement_origin if geom else np.array(clash.get("p1") or [0, 0, 0], dtype=float)
        goal_pt = goal_point_from_clash(clash, start_pt, clearance_m=clearance_m, step_m=local_step)
        obstacles = obstacle_aabbs_for_clash(
            [str(work_a), str(work_b)],
            clash,
            exclude_guid=guid,
            inflate_m=clearance_m,
        )
        route: Route3D = route_orthogonal(
            start_pt,
            goal_pt,
            obstacles,
            clearance_m=clearance_m,
            grid_step_m=grid_step_m,
        )
        translation = route.net_translation
        if np.linalg.norm(translation) < 1e-9:
            translation = goal_pt - start_pt

        ag_records: list[AgProofRecord] = []
        if verify_ag and vendor is not None:
            clash_record = {
                "clash_id": f"{case_id}_iter{iteration}",
                **clash,
                "clearance_required_m": clearance_m,
            }
            multi = clash_to_ag2_multiplane(clash_record, clearance_m=clearance_m)
            ag_records.extend(_prove_stubs(multi, vendor, f"{case_id}_mp"))
            seg_stubs = route_segments_to_ag2_problems(
                route.waypoints,
                clearance_m=clearance_m,
                clash_id=f"{case_id}_iter{iteration}",
            )
            ag_records.extend(_prove_stubs(seg_stubs, vendor, f"{case_id}_rt"))

        _apply_translation(target_path, guid, translation)
        refresh_guid_map()

        after_data = run_clash_set(make_clash_set(), log)
        after_count = clash_count(after_data)

        fixes.append(
            WorkflowFix(
                iteration=iteration + 1,
                clash_key=scored.clash_key,
                severity=scored.severity,
                cluster_id=scored.cluster_id,
                moved_guid=guid,
                moved_class=scored.movable_class,
                route_waypoints=[wp.tolist() for wp in route.waypoints],
                route_reached_goal=route.reached_goal,
                translation=translation.tolist(),
                clash_count_before=count,
                clash_count_after=after_count,
                ag_proofs=ag_records,
                triage_rationale=scored.rationale,
            )
        )
        iteration += 1
        if after_count >= count:
            local_step *= 1.5
            log.warning("No clash reduction; step -> %s", local_step)

    final_data = run_clash_set(make_clash_set(), log)
    final = clash_count(final_data)

    return Workflow3DResult(
        case_id=case_id,
        passed=final == 0,
        initial_clash_count=initial,
        final_clash_count=final,
        iterations_used=iteration,
        max_iterations=max_iterations,
        fixes=fixes,
        triage_order=triage_snapshot,
        work_dir=str(work_dir),
        elapsed_ms=(time.perf_counter() - start) * 1000,
    )


def run_workflow_case(
    case: dict[str, Any],
    manifest: dict[str, Any],
    work_root: Path,
    vendor: Path | None,
    logger: logging.Logger,
) -> Workflow3DResult:
    model_sets = {ms["id"]: ms for ms in manifest["model_sets"]}
    ms_a = model_sets[case["model_set"]]
    path_a = resolve_model_path(ms_a, case["a_file"], fetch=True)
    set_b = case.get("b_model_set", case["model_set"])
    path_b = resolve_model_path(model_sets[set_b], case["b_file"], fetch=True)
    if path_a is None or path_b is None:
        return Workflow3DResult(
            case_id=case["id"],
            passed=False,
            initial_clash_count=-1,
            final_clash_count=-1,
            iterations_used=0,
            max_iterations=case.get("max_iterations", 10),
            skipped=True,
            skip_reason="IFC model files not available",
        )

    defaults = manifest.get("ifc_clash_defaults", {})
    clash_opts = {**defaults, **case.get("clash_options", {})}

    return run_workflow3d_resolution(
        case["id"],
        path_a,
        path_b,
        clash_set_options=clash_opts,
        work_root=work_root,
        max_iterations=case.get("max_iterations", 10),
        clearance_m=case.get("clearance_m", 0.05),
        step_m=case.get("step_m", 0.15),
        grid_step_m=case.get("grid_step_m", 0.1),
        move_side=case.get("move_side", "auto"),
        verify_ag=case.get("verify_ag", True),
        vendor=vendor,
        logger=logger,
    )
