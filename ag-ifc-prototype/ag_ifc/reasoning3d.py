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

from ag_ifc.workflow_types import AgProofRecord, Workflow3DResult, WorkflowFix
from ag_ifc.clash_runner import clash_count, run_clash_set
from ag_ifc.clash_prefilter import assess_clash_suitability
from ag_ifc.clash_sorter import ScoredClash, sort_clashes
from ag_ifc.compiler import clash_to_ag2_multiplane, route_segments_to_ag2_problems
from ag_ifc.ifc_geometry import element_geom, obstacle_aabbs_for_clash
from ag_ifc.ifc_models import load_manifest, resolve_model_path
from ag_ifc.iterative_clash import _apply_translation, _prepare_work_copies
from ag_ifc.resolve_pipeline import PipelineOptions, run_resolve_pipeline
from ag_ifc.routing3d import Route3D, goal_point_from_clash, route_orthogonal









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
    prefilter_solve_only: bool = True,
    max_attempts_per_clash: int = 5,
    global_regression: bool = True,
    export_bcf: bool = True,
    stop_on_regression_failure: bool = True,
    vendor: Path | None = None,
    logger: logging.Logger | None = None,
) -> Workflow3DResult:
    opts = PipelineOptions(
        max_clash_rounds=max_iterations,
        max_attempts_per_clash=max_attempts_per_clash,
        clearance_m=clearance_m,
        step_m=step_m,
        grid_step_m=grid_step_m,
        move_side=move_side,
        verify_ag=verify_ag,
        prefilter_solve_only=prefilter_solve_only,
        global_regression=global_regression,
        export_bcf=export_bcf,
        stop_on_regression_failure=stop_on_regression_failure,
    )
    return run_resolve_pipeline(
        case_id,
        path_a,
        path_b,
        clash_set_options=clash_set_options,
        work_root=work_root,
        options=opts,
        vendor=vendor,
        logger=logger,
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
        prefilter_solve_only=case.get("prefilter_solve_only", True),
        max_attempts_per_clash=case.get("max_attempts_per_clash", 5),
        global_regression=case.get("global_regression", True),
        export_bcf=case.get("export_bcf", True),
        stop_on_regression_failure=case.get("stop_on_regression_failure", True),
        vendor=vendor,
        logger=logger,
    )
