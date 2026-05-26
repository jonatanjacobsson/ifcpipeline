"""AEC clash triage: severity, discipline, movability, spatial clustering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from ag_ifc.ifc_geometry import (
    DISCIPLINE_PRIORITY,
    MEP_CLASSES,
    clash_midpoint,
    clash_separation_vector,
    discipline_from_class,
)


@dataclass
class ScoredClash:
    clash_key: str
    clash: dict[str, Any]
    score: float
    severity: str
    cluster_id: str
    movable_side: str
    movable_guid: str
    movable_class: str
    rationale: list[str]


def _cluster_id(mid: np.ndarray, cell_m: float = 2.0) -> str:
    gx = int(np.floor(mid[0] / cell_m))
    gy = int(np.floor(mid[1] / cell_m))
    gz = int(np.floor(mid[2] / cell_m))
    return f"c_{gx}_{gy}_{gz}"


def _choose_movable(clash: dict[str, Any], move_side: str) -> tuple[str, str, str]:
    if move_side == "a":
        return clash["a_global_id"], clash.get("a_ifc_class", ""), "a"
    if move_side == "b":
        return clash["b_global_id"], clash.get("b_ifc_class", ""), "b"
    a_class = clash.get("a_ifc_class", "")
    b_class = clash.get("b_ifc_class", "")
    if a_class in MEP_CLASSES:
        return clash["a_global_id"], a_class, "a"
    if b_class in MEP_CLASSES:
        return clash["b_global_id"], b_class, "b"
    return clash["a_global_id"], a_class, "a"


def _severity(penetration_m: float, mode: str) -> tuple[str, float]:
    if mode == "clearance":
        if penetration_m > 0.15:
            return "high", 100.0
        if penetration_m > 0.05:
            return "medium", 60.0
        return "low", 30.0
    if penetration_m > 0.2:
        return "critical", 120.0
    if penetration_m > 0.08:
        return "high", 90.0
    if penetration_m > 0.03:
        return "medium", 55.0
    return "low", 25.0


def score_clash(
    clash_key: str,
    clash: dict[str, Any],
    *,
    move_side: str = "auto",
    clash_mode: str = "intersection",
    cluster_cell_m: float = 2.0,
) -> ScoredClash:
    item = dict(clash)
    item["clash_key"] = clash_key
    mid = clash_midpoint(item)
    sep = clash_separation_vector(item)
    penetration = float(np.linalg.norm(sep))
    severity, sev_score = _severity(penetration, clash_mode)

    guid, ifc_class, side = _choose_movable(item, move_side)
    movable_disc = discipline_from_class(ifc_class)
    other_class = item.get("b_ifc_class") if side == "a" else item.get("a_ifc_class")
    static_disc = discipline_from_class(other_class or "")

    disc_bonus = (DISCIPLINE_PRIORITY.get(movable_disc, 5) - DISCIPLINE_PRIORITY.get(static_disc, 5)) * 5.0
    mep_bonus = 15.0 if ifc_class in MEP_CLASSES else 0.0
    score = sev_score + disc_bonus + mep_bonus + penetration * 40.0

    rationale = [
        f"severity={severity} penetration~{penetration:.3f}m",
        f"movable={ifc_class} ({movable_disc}) vs static ({static_disc})",
        f"cluster={_cluster_id(mid, cluster_cell_m)}",
    ]
    return ScoredClash(
        clash_key=clash_key,
        clash=item,
        score=score,
        severity=severity,
        cluster_id=_cluster_id(mid, cluster_cell_m),
        movable_side=side,
        movable_guid=guid,
        movable_class=ifc_class,
        rationale=rationale,
    )


def sort_clashes(
    clashes: dict[str, Any],
    *,
    move_side: str = "auto",
    clash_mode: str = "intersection",
    cluster_cell_m: float = 2.0,
) -> list[ScoredClash]:
    scored = [
        score_clash(key, data, move_side=move_side, clash_mode=clash_mode, cluster_cell_m=cluster_cell_m)
        for key, data in clashes.items()
    ]
    scored.sort(key=lambda s: (-s.score, s.cluster_id, s.clash_key))
    return scored
