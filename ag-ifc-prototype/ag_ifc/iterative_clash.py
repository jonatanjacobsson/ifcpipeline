"""Iterative clash detect → fix → verify loop with optional AG certification."""

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
from ag_ifc.clash_runner import clash_count, clashes_list, run_clash_set
from ag_ifc.compiler import clash_to_ag2_stub
from ag_ifc.ifc_models import load_manifest, resolve_model_path

MEP_CLASSES = {
    "IfcFlowSegment",
    "IfcFlowFitting",
    "IfcFlowTerminal",
    "IfcFlowController",
    "IfcFlowMovingDevice",
    "IfcFlowStorageDevice",
    "IfcFlowTreatmentDevice",
    "IfcDuctSegment",
    "IfcDuctFitting",
    "IfcPipeSegment",
    "IfcPipeFitting",
    "IfcCableCarrierSegment",
    "IfcCableCarrierFitting",
    "IfcAirTerminal",
    "IfcBuildingElementProxy",
}


@dataclass
class FixAction:
    iteration: int
    clash_key: str
    moved_guid: str
    moved_class: str
    moved_file: str
    translation: list[float]
    clash_count_before: int
    clash_count_after: int
    ag_proven: bool | None = None
    ag_goal: str | None = None
    ag_error: str | None = None


@dataclass
class IterativeResult:
    suite_id: str
    passed: bool
    initial_clash_count: int
    final_clash_count: int
    iterations_used: int
    max_iterations: int
    fixes: list[FixAction] = field(default_factory=list)
    work_dir: str = ""
    output_ifc: str | None = None
    skipped: bool = False
    skip_reason: str | None = None
    elapsed_ms: float = 0


def _choose_movable(clash: dict[str, Any], move_side: str) -> tuple[str, str, str]:
    """Return (guid, ifc_class, side 'a'|'b')."""
    if move_side == "a":
        return clash["a_global_id"], clash.get("a_ifc_class", ""), "a"
    if move_side == "b":
        return clash["b_global_id"], clash.get("b_ifc_class", ""), "b"
    # auto: prefer MEP on either side
    a_class = clash.get("a_ifc_class", "")
    b_class = clash.get("b_ifc_class", "")
    if a_class in MEP_CLASSES:
        return clash["a_global_id"], a_class, "a"
    if b_class in MEP_CLASSES:
        return clash["b_global_id"], b_class, "b"
    # default: move A (often the "incoming" discipline file)
    return clash["a_global_id"], a_class, "a"


def _translation_for_clash(clash: dict[str, Any], clearance_m: float, step_m: float) -> np.ndarray:
    p1 = np.array(clash["p1"], dtype=float)
    p2 = np.array(clash["p2"], dtype=float)
    delta = p2 - p1
    norm = np.linalg.norm(delta)
    if norm < 1e-9:
        delta = np.array([0.0, 1.0, 0.0])
        norm = 1.0
    direction = delta / norm
    distance = max(clearance_m, 0.0) + step_m
    return direction * distance


def _apply_translation(ifc_path: Path, guid: str, translation: np.ndarray) -> None:
    import ifcopenshell
    import ifcopenshell.api
    import ifcopenshell.util.placement

    ifc = ifcopenshell.open(ifc_path)
    elem = ifc.by_guid(guid)
    old = ifcopenshell.util.placement.get_local_placement(elem.ObjectPlacement)
    trans = np.eye(4)
    trans[0:3, 3] = translation[0:3]
    new_m = trans @ old
    ifcopenshell.api.run(
        "geometry.edit_object_placement",
        ifc,
        product=elem,
        matrix=new_m,
        is_si=True,
    )
    ifc.write(str(ifc_path))


def _prepare_work_copies(
    path_a: Path,
    path_b: Path,
    work_dir: Path,
) -> tuple[Path, Path]:
    work_dir.mkdir(parents=True, exist_ok=True)
    work_a = work_dir / path_a.name
    work_b = work_dir / path_b.name
    if not work_a.exists():
        shutil.copy(path_a, work_a)
    if not work_b.exists():
        shutil.copy(path_b, work_b)
    return work_a, work_b


def run_iterative_resolution(
    suite_id: str,
    path_a: Path,
    path_b: Path,
  *,
    clash_set_options: dict[str, Any],
    work_root: Path,
    max_iterations: int = 10,
    clearance_m: float = 0.05,
    step_m: float = 0.15,
    move_side: Literal["a", "b", "auto"] = "auto",
    verify_ag: bool = True,
    vendor: Path | None = None,
    logger: logging.Logger | None = None,
) -> IterativeResult:
    log = logger or logging.getLogger("iterative_clash")
    start = time.perf_counter()
    work_dir = work_root / suite_id
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

    def make_clash_set() -> dict[str, Any]:
        cs = {
            "name": suite_id,
            "a": [{"file": str(work_a)}],
            "b": [{"file": str(work_b)}],
            "mode": clash_set_options.get("mode", "intersection"),
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
    fixes: list[FixAction] = []

    if initial == 0:
        return IterativeResult(
            suite_id=suite_id,
            passed=True,
            initial_clash_count=0,
            final_clash_count=0,
            iterations_used=0,
            max_iterations=max_iterations,
            fixes=[],
            work_dir=str(work_dir),
            output_ifc=str(work_a),
            elapsed_ms=(time.perf_counter() - start) * 1000,
        )

    iteration = 0
    while iteration < max_iterations:
        result_data = run_clash_set(make_clash_set(), log)
        count = clash_count(result_data)
        if count == 0:
            break

        clash = clashes_list(result_data)[0]
        guid, ifc_class, side = _choose_movable(clash, move_side)
        if guid not in guid_to_file:
            refresh_guid_map()
        target_path = guid_to_file.get(guid)
        if target_path is None:
            return IterativeResult(
                suite_id=suite_id,
                passed=False,
                initial_clash_count=initial,
                final_clash_count=count,
                iterations_used=iteration,
                max_iterations=max_iterations,
                fixes=fixes,
                work_dir=str(work_dir),
                skipped=True,
                skip_reason=f"GUID {guid} not found in work copies",
                elapsed_ms=(time.perf_counter() - start) * 1000,
            )

        translation = _translation_for_clash(clash, clearance_m, step_m)
        ag_proven: bool | None = None
        ag_goal: str | None = None
        ag_error: str | None = None

        if verify_ag and vendor is not None:
            clash_record = {
                "clash_id": f"{suite_id}_iter{iteration}",
                **clash,
                "clearance_required_m": clearance_m,
            }
            stub = clash_to_ag2_stub(clash_record)
            proof: ProveResult = prove_problem(
                f"{suite_id}_iter{iteration}", stub.ag2, vendor
            )
            ag_proven = proof.proven
            ag_goal = proof.goal
            ag_error = proof.error

        _apply_translation(target_path, guid, translation)
        refresh_guid_map()

        after_data = run_clash_set(make_clash_set(), log)
        after_count = clash_count(after_data)

        fixes.append(
            FixAction(
                iteration=iteration + 1,
                clash_key=clash.get("clash_key", ""),
                moved_guid=guid,
                moved_class=ifc_class,
                moved_file=target_path.name,
                translation=translation.tolist(),
                clash_count_before=count,
                clash_count_after=after_count,
                ag_proven=ag_proven,
                ag_goal=ag_goal,
                ag_error=ag_error,
            )
        )
        iteration += 1

        if after_count >= count:
            # No improvement — increase step and retry same iter count
            step_m *= 1.5
            log.warning(
                "Clash count did not decrease (%s -> %s); increasing step to %s",
                count,
                after_count,
                step_m,
            )
            if iteration >= max_iterations - 1:
                break

    final_data = run_clash_set(make_clash_set(), log)
    final = clash_count(final_data)

    moved_file = work_a if move_side != "b" else work_b
    return IterativeResult(
        suite_id=suite_id,
        passed=final == 0,
        initial_clash_count=initial,
        final_clash_count=final,
        iterations_used=iteration,
        max_iterations=max_iterations,
        fixes=fixes,
        work_dir=str(work_dir),
        output_ifc=str(moved_file),
        elapsed_ms=(time.perf_counter() - start) * 1000,
    )


def run_suite_case(
    case: dict[str, Any],
    manifest: dict[str, Any],
    work_root: Path,
    vendor: Path | None,
    logger: logging.Logger,
) -> IterativeResult:
    model_sets = {ms["id"]: ms for ms in manifest["model_sets"]}
    ms_a = model_sets[case["model_set"]]
    path_a = resolve_model_path(ms_a, case["a_file"], fetch=True)
    set_b = case.get("b_model_set", case["model_set"])
    path_b = resolve_model_path(model_sets[set_b], case["b_file"], fetch=True)
    if path_a is None or path_b is None:
        return IterativeResult(
            suite_id=case["id"],
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

    return run_iterative_resolution(
        case["id"],
        path_a,
        path_b,
        clash_set_options=clash_opts,
        work_root=work_root,
        max_iterations=case.get("max_iterations", 10),
        clearance_m=case.get("clearance_m", 0.05),
        step_m=case.get("step_m", 0.15),
        move_side=case.get("move_side", "auto"),
        verify_ag=case.get("verify_ag", True),
        vendor=vendor,
        logger=logger,
    )
