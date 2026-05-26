"""Per-clash multi-attempt resolution with cumulative moves."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ag_ifc.clash_regression import clash_stable_id
from ag_ifc.ifc_geometry import element_geom, obstacle_aabbs_for_clash
from ag_ifc.iterative_clash import _apply_translation
from ag_ifc.mep_reasoning import MepReasoningResult, reason_mep_fix


@dataclass
class ClashAttempt:
    attempt: int
    clash_key: str
    stable_id: str
    translation: list[float]
    cumulative_translation: list[float]
    route_waypoints: list[list[float]]
    route_reached_goal: bool
    clash_count_before: int
    clash_count_after: int
    still_present: bool
    mep_reasoning: dict[str, Any] | None = None
    bbox_regression: dict[str, Any] | None = None
    ag_proven: bool | None = None


@dataclass
class ClashResolution:
    stable_id: str
    clash_key: str
    resolved: bool
    attempts: list[ClashAttempt] = field(default_factory=list)
    moved_guid: str = ""
    moved_class: str = ""
    total_translation: list[float] = field(default_factory=list)
    final_mep_strategy: str | None = None


def resolve_clash_with_retries(
    clash_key: str,
    clash: dict[str, Any],
    *,
    guid: str,
    movable_class: str,
    target_path: Path,
    work_ifc_paths: list[str],
    run_clash: Callable[[], dict],
    clash_count_fn: Callable[[dict], int],
    max_attempts_per_clash: int,
    clearance_m: float,
    step_m: float,
    grid_step_m: float,
    step_growth: float = 1.35,
    bend_penalty: float = 4.0,
    apply_fix: Callable[[np.ndarray], None] | None = None,
    on_attempt: Callable[[ClashAttempt], None] | None = None,
    bbox_check: Callable[[np.ndarray], dict[str, Any] | None] | None = None,
    skip_if_strategy_reject: bool = True,
) -> ClashResolution:
    stable = clash_stable_id(clash)
    cumulative = np.zeros(3, dtype=float)
    local_step = step_m
    attempts: list[ClashAttempt] = []
    resolved = False
    final_strategy: str | None = None
    static_class = clash.get("b_ifc_class", "") if clash.get("a_global_id") == guid else clash.get("a_ifc_class", "")

    def apply_translation(delta: np.ndarray) -> None:
        nonlocal cumulative
        cumulative = cumulative + delta
        if apply_fix:
            apply_fix(delta)
        else:
            _apply_translation(target_path, guid, delta)

    for attempt in range(1, max_attempts_per_clash + 1):
        before_data = run_clash()
        before_count = clash_count_fn(before_data)
        clashes = before_data.get("clashes", {})
        current = None
        for k, v in clashes.items():
            if clash_stable_id(v) == stable or k == clash_key:
                current = dict(v)
                current["clash_key"] = k
                break
        if current is None:
            resolved = True
            break

        geom = element_geom(str(target_path), guid)
        start_pt = geom.placement_origin if geom else np.array(current.get("p1") or [0, 0, 0], dtype=float)
        from ag_ifc.routing3d import goal_point_from_clash

        goal_pt = goal_point_from_clash(current, start_pt, clearance_m=clearance_m, step_m=local_step)
        obstacles = obstacle_aabbs_for_clash(
            work_ifc_paths,
            current,
            exclude_guid=guid,
            inflate_m=clearance_m,
        )

        mep: MepReasoningResult = reason_mep_fix(
            current,
            start=start_pt,
            goal=goal_pt,
            obstacles=obstacles,
            movable_geom=geom,
            movable_class=movable_class,
            static_class=static_class or "",
            clearance_m=clearance_m,
            grid_step_m=grid_step_m,
            bend_penalty=bend_penalty,
        )
        final_strategy = mep.strategy.value

        if skip_if_strategy_reject and mep.strategy.value in (
            "reject_wrong_target",
            "review_manual",
        ):
            rec = ClashAttempt(
                attempt=attempt,
                clash_key=clash_key,
                stable_id=stable,
                translation=[0.0, 0.0, 0.0],
                cumulative_translation=cumulative.tolist(),
                route_waypoints=mep.preferred_route,
                route_reached_goal=False,
                clash_count_before=before_count,
                clash_count_after=before_count,
                still_present=True,
                mep_reasoning=mep.to_dict(),
            )
            attempts.append(rec)
            if on_attempt:
                on_attempt(rec)
            break

        wps = [np.array(p, dtype=float) for p in mep.preferred_route]
        if len(wps) >= 2:
            delta = wps[-1] - wps[0]
        else:
            delta = goal_pt - start_pt

        bbox_report = None
        if bbox_check is not None:
            bbox_report = bbox_check(cumulative + delta)

        apply_translation(delta)

        after_data = run_clash()
        after_count = clash_count_fn(after_data)
        still = any(clash_stable_id(v) == stable for v in after_data.get("clashes", {}).values())

        rec = ClashAttempt(
            attempt=attempt,
            clash_key=clash_key,
            stable_id=stable,
            translation=delta.tolist(),
            cumulative_translation=cumulative.tolist(),
            route_waypoints=mep.preferred_route,
            route_reached_goal=len(wps) >= 2,
            clash_count_before=before_count,
            clash_count_after=after_count,
            still_present=still,
            mep_reasoning=mep.to_dict(),
            bbox_regression=bbox_report,
        )
        attempts.append(rec)
        if on_attempt:
            on_attempt(rec)

        if bbox_report and not bbox_report.get("passed", True):
            break

        if not still:
            resolved = True
            break
        local_step *= step_growth

    return ClashResolution(
        stable_id=stable,
        clash_key=clash_key,
        resolved=resolved,
        attempts=attempts,
        moved_guid=guid,
        moved_class=movable_class,
        total_translation=cumulative.tolist(),
        final_mep_strategy=final_strategy,
    )
