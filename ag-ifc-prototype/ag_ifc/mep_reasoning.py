"""MEP coordination reasoning: prefer parallel translation; bends allowed but costly."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np

from ag_ifc.ifc_geometry import MEP_CLASSES, ElementGeom
from ag_ifc.routing3d import Route3D, route_orthogonal


class FixStrategy(str, Enum):
    PARALLEL_TRANSLATE = "parallel_translate"
    REROUTE_MINIMAL = "reroute_minimal"
    REROUTE_WITH_BENDS = "reroute_with_bends"
    REVIEW_MANUAL = "review_manual"
    REJECT_WRONG_TARGET = "reject_wrong_target"


@dataclass
class RouteMetrics:
    segment_count: int
    bend_count: int
    total_length_m: float
    max_single_axis_fraction: float
    parallel_to_run_axis: bool


@dataclass
class MepReasoningResult:
    strategy: FixStrategy
    preferred_route: list[list[float]]
    metrics: RouteMetrics
    cost_score: float
    rationale: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    optimize_for: str = "coordination_cost"
    ag_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy.value,
            "cost_score": round(self.cost_score, 3),
            "metrics": {
                "segment_count": self.metrics.segment_count,
                "bend_count": self.metrics.bend_count,
                "total_length_m": round(self.metrics.total_length_m, 4),
                "parallel_to_run_axis": self.metrics.parallel_to_run_axis,
            },
            "rationale": self.rationale,
            "warnings": self.warnings,
            "optimize_for": self.optimize_for,
            "ag_notes": self.ag_notes,
        }


# Relative costs — bends are expensive; do not treat all 3D routes as equal
COST_PER_METRE = 1.0
COST_PER_BEND = 4.0
COST_NON_PARALLEL = 2.5
COST_STRUCTURAL_MOVE = 8.0


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    if n < 1e-9:
        return v
    return v / n


def analyze_route_waypoints(
    waypoints: list[np.ndarray],
    run_axis: np.ndarray | None = None,
) -> RouteMetrics:
    if len(waypoints) < 2:
        return RouteMetrics(0, 0, 0.0, 1.0, True)

    segs = [waypoints[i + 1] - waypoints[i] for i in range(len(waypoints) - 1)]
    lengths = [float(np.linalg.norm(s)) for s in segs]
    total = sum(lengths)
    bends = 0
    for i in range(1, len(segs)):
        u0, u1 = _unit(segs[i - 1]), _unit(segs[i])
        if np.linalg.norm(np.cross(u0, u1)) > 0.08:
            bends += 1

    dominant_frac = 0.0
    if total > 1e-9 and segs:
        ax = int(np.argmax(np.abs(segs[np.argmax(lengths)])))
        dominant_frac = lengths[np.argmax(lengths)] / total

    parallel = bends == 0 and len(segs) == 1
    if run_axis is not None and segs and np.linalg.norm(run_axis) > 1e-9:
        ra = _unit(run_axis)
        parallel = parallel or np.linalg.norm(np.cross(_unit(segs[0]), ra)) < 0.1

    return RouteMetrics(
        segment_count=len(segs),
        bend_count=bends,
        total_length_m=total,
        max_single_axis_fraction=dominant_frac,
        parallel_to_run_axis=parallel,
    )


def _route_cost(metrics: RouteMetrics, *, movable_is_mep: bool) -> float:
    cost = metrics.total_length_m * COST_PER_METRE
    cost += metrics.bend_count * COST_PER_BEND
    if not metrics.parallel_to_run_axis and movable_is_mep:
        cost += COST_NON_PARALLEL
    return cost


def propose_parallel_translation(
    start: np.ndarray,
    goal: np.ndarray,
    run_axis: np.ndarray | None,
    obstacles: list,
    *,
    clearance_m: float,
) -> Route3D | None:
    """Single-segment move along run axis or toward goal when unobstructed."""
    delta = goal - start
    if run_axis is not None and np.linalg.norm(run_axis) > 1e-9:
        ra = _unit(run_axis)
        proj = ra * float(np.dot(delta, ra))
        candidate_end = start + proj
    else:
        candidate_end = goal

    seg_route = Route3D(
        waypoints=[start.copy(), candidate_end],
        grid_step_m=0.1,
        clearance_m=clearance_m,
        reached_goal=True,
    )
    for obs in obstacles:
        for wp in seg_route.waypoints:
            if obs.contains_point(wp):
                return None
    return seg_route


def reason_mep_fix(
    clash: dict[str, Any],
    *,
    start: np.ndarray,
    goal: np.ndarray,
    obstacles: list,
    movable_geom: ElementGeom | None,
    movable_class: str,
    static_class: str,
    clearance_m: float = 0.05,
    grid_step_m: float = 0.1,
    bend_penalty: float = 4.0,
    max_bends_without_warning: int = 2,
) -> MepReasoningResult:
    """
    Choose coordination strategy without over-optimizing local clash clearance alone.

    Objective: minimize fabrication/coordination cost (bends >> parallel offset),
    while still resolving the clash when reasonable.
    """
    rationale: list[str] = []
    warnings: list[str] = []
    movable_is_mep = movable_class in MEP_CLASSES
    run_axis = movable_geom.dominant_axis if movable_geom else None

    if movable_is_mep is False and static_class in MEP_CLASSES:
        return MepReasoningResult(
            strategy=FixStrategy.REJECT_WRONG_TARGET,
            preferred_route=[start.tolist(), goal.tolist()],
            metrics=RouteMetrics(0, 0, 0.0, 0.0, False),
            cost_score=COST_STRUCTURAL_MOVE,
            rationale=["Static element is MEP; moving non-MEP side is usually wrong"],
            warnings=["Avoid optimizing local clash by moving structure/architecture"],
            optimize_for="discipline_correctness",
            ag_notes=["Do not certify moves that displace load-bearing elements for MEP"],
        )

    parallel = propose_parallel_translation(
        start, goal, run_axis, obstacles, clearance_m=clearance_m
    )
    ortho = route_orthogonal(
        start,
        goal,
        obstacles,
        clearance_m=clearance_m,
        grid_step_m=grid_step_m,
        bend_penalty=bend_penalty,
    )

    candidates: list[tuple[FixStrategy, Route3D]] = []
    if parallel is not None:
        candidates.append((FixStrategy.PARALLEL_TRANSLATE, parallel))
    if ortho.reached_goal or len(ortho.waypoints) >= 2:
        m = analyze_route_waypoints(ortho.waypoints, run_axis)
        if m.bend_count == 0:
            candidates.append((FixStrategy.REROUTE_MINIMAL, ortho))
        else:
            candidates.append((FixStrategy.REROUTE_WITH_BENDS, ortho))

    if not candidates:
        return MepReasoningResult(
            strategy=FixStrategy.REVIEW_MANUAL,
            preferred_route=[start.tolist(), goal.tolist()],
            metrics=RouteMetrics(1, 0, float(np.linalg.norm(goal - start)), 0.0, False),
            cost_score=999.0,
            rationale=["No feasible auto route; manual coordination required"],
            warnings=["Do not force solver to zero local clashes at any cost"],
            optimize_for="human_review",
        )

    scored: list[tuple[float, FixStrategy, Route3D, RouteMetrics]] = []
    for strategy, route in candidates:
        metrics = analyze_route_waypoints(route.waypoints, run_axis)
        cost = _route_cost(metrics, movable_is_mep=movable_is_mep)
        scored.append((cost, strategy, route, metrics))

    scored.sort(key=lambda x: x[0])
    cost, strategy, route, metrics = scored[0]

    if strategy == FixStrategy.PARALLEL_TRANSLATE:
        rationale.append("Parallel offset along run axis preferred (no bends)")
        ag_notes = ["Certify para offset vs structure; single translation segment"]
    elif strategy == FixStrategy.REROUTE_MINIMAL:
        rationale.append("Orthogonal path without bends")
        ag_notes = ["Certify para segments only"]
    else:
        rationale.append(f"Reroute with {metrics.bend_count} bend(s) — higher coordination cost")
        ag_notes = [f"Segment bends={metrics.bend_count}; use perp at corners if certifying"]

    if metrics.bend_count > max_bends_without_warning:
        warnings.append(
            f"{metrics.bend_count} bends exceeds soft limit {max_bends_without_warning}; "
            "verify fabrication / pressure drop / pull path"
        )

    if cost > 15 and metrics.bend_count > 0:
        warnings.append("High coordination cost — confirm local clash fix is worth global route change")

    return MepReasoningResult(
        strategy=strategy,
        preferred_route=[w.tolist() for w in route.waypoints],
        metrics=metrics,
        cost_score=cost,
        rationale=rationale,
        warnings=warnings,
        optimize_for="coordination_cost_not_clash_count_only",
        ag_notes=ag_notes,
    )
