"""Per-clash multi-attempt resolution with cumulative moves."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

import numpy as np

from ag_ifc.clash_regression import ClashSnapshot, clash_stable_id
from ag_ifc.ifc_geometry import element_geom, obstacle_aabbs_for_clash
from ag_ifc.iterative_clash import _apply_translation
from ag_ifc.routing3d import Route3D, goal_point_from_clash, route_orthogonal


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


def resolve_clash_with_retries(
    clash_key: str,
    clash: dict[str, Any],
    *,
    guid: str,
    target_path: Path,
    work_ifc_paths: list[str],
    make_clash_set: Callable[[], dict[str, Any]],
    run_clash: Callable[[dict], dict],
    clash_count_fn: Callable[[dict], int],
    max_attempts_per_clash: int,
    clearance_m: float,
    step_m: float,
    grid_step_m: float,
    step_growth: float = 1.35,
    apply_fix: Callable[[np.ndarray], None] | None = None,
    on_attempt: Callable[[ClashAttempt], None] | None = None,
) -> ClashResolution:
    """
    Apply multiple fix attempts on the same clash until it clears or attempts exhaust.

    Translations are cumulative along the movable element placement.
    """
    stable = clash_stable_id(clash)
    cumulative = np.zeros(3, dtype=float)
    local_step = step_m
    attempts: list[ClashAttempt] = []
    resolved = False

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
        goal_pt = goal_point_from_clash(current, start_pt, clearance_m=clearance_m, step_m=local_step)
        obstacles = obstacle_aabbs_for_clash(
            work_ifc_paths,
            current,
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
        delta = route.net_translation
        if np.linalg.norm(delta) < 1e-9:
            delta = goal_pt - start_pt

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
            route_waypoints=[w.tolist() for w in route.waypoints],
            route_reached_goal=route.reached_goal,
            clash_count_before=before_count,
            clash_count_after=after_count,
            still_present=still,
        )
        attempts.append(rec)
        if on_attempt:
            on_attempt(rec)

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
        total_translation=cumulative.tolist(),
    )
