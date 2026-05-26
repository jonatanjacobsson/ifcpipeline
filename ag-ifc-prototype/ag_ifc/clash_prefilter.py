"""IfcClash result pre-filter: which clashes AG can certify and auto-fix may resolve."""

from __future__ import annotations

import json
from pathlib import Path

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

from ag_ifc.clash_sorter import _choose_movable
from ag_ifc.compiler import clash_to_ag2_multiplane
from ag_ifc.ifc_geometry import (
    MEP_CLASSES,
    STRUCTURAL_CLASSES,
    clash_midpoint,
    clash_separation_vector,
    discipline_from_class,
    element_geom,
)

Tier = Literal["solve", "review", "exclude"]

# From synthetic AG matrix (see reports/scenario_matrix_latest.json)
AG_STRONG_CATEGORIES = {"mep_coordination", "clash_resolution", "structural_grid", "vertical_section"}
AG_WEAK_GOALS = {"cong", "dist", "distseq"}

# IFC class pairs that iterative/workflow suites resolved on PCERT models
HIGH_CONFIDENCE_PAIRS = {
    ("IfcPipeSegment", "IfcRoad"),
    ("IfcPipeSegment", "IfcPavement"),
    ("IfcFlowSegment", "IfcRoad"),
    ("IfcDuctSegment", "IfcBeam"),
    ("IfcFlowSegment", "IfcBeam"),
    ("IfcPipeSegment", "IfcBeam"),
    ("IfcGeographicElement", "IfcWall"),
    ("IfcGeographicElement", "IfcSlab"),
    ("IfcBuildingElementProxy", "IfcWall"),
}

LOW_CONFIDENCE_PAIRS = {
    ("IfcSlab", "IfcBeam"),
    ("IfcSlab", "IfcSlab"),
    ("IfcWall", "IfcWall"),
    ("IfcColumn", "IfcColumn"),
}


@dataclass
class ClashSuitability:
    clash_key: str
    tier: Tier
    confidence: float
    fix_strategy: str
    class_pair: str
    movable_class: str
    movable_discipline: str
    static_class: str
    penetration_m: float
    ag_viable: bool | None = None
    ag_proven_count: int = 0
    ag_stub_count: int = 0
    ag_plan_proven: bool | None = None
    reasons: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "clash_key": self.clash_key,
            "tier": self.tier,
            "confidence": round(self.confidence, 3),
            "fix_strategy": self.fix_strategy,
            "class_pair": self.class_pair,
            "movable_class": self.movable_class,
            "movable_discipline": self.movable_discipline,
            "static_class": self.static_class,
            "penetration_m": round(self.penetration_m, 4),
            "ag_viable": self.ag_viable,
            "ag_proven_count": self.ag_proven_count,
            "ag_stub_count": self.ag_stub_count,
            "ag_plan_proven": self.ag_plan_proven,
            "reasons": self.reasons,
            "blockers": self.blockers,
        }


def _class_pair_key(a_class: str, b_class: str, movable_side: str) -> tuple[str, str]:
    if movable_side == "b":
        return b_class, a_class
    return a_class, b_class


def _pair_in_set(pair: tuple[str, str], options: set[tuple[str, str]]) -> bool:
    return pair in options or (pair[1], pair[0]) in options


def _fix_strategy(
    movable_class: str,
    static_class: str,
    movable_disc: str,
    penetration_m: float,
    clash_mode: str,
) -> str:
    if movable_class in MEP_CLASSES:
        return "route_mep_3d"
    if movable_disc == "landscape":
        return "translate_landscape"
    if movable_disc == "mep":
        return "route_mep_3d"
    if clash_mode == "clearance" and penetration_m < 0.12:
        return "translate_clearance"
    if movable_disc == "architecture" and static_class in STRUCTURAL_CLASSES:
        return "translate_arch"
    return "translate_generic"



def _load_rules_file() -> dict:
    path = Path(__file__).resolve().parent.parent / "scenarios" / "ag_prefilter_rules.json"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _pairs_from_rules(key: str) -> set[tuple[str, str]]:
    data = _load_rules_file()
    raw = data.get(key, [])
    out: set[tuple[str, str]] = set()
    for item in raw:
        if "|" in item:
            a, b = item.split("|", 1)
            out.add((a, b))
    return out


def high_confidence_pairs() -> set[tuple[str, str]]:
    file_pairs = _pairs_from_rules("high_confidence_class_pairs")
    return HIGH_CONFIDENCE_PAIRS | file_pairs


def low_confidence_pairs() -> set[tuple[str, str]]:
    file_pairs = _pairs_from_rules("low_confidence_class_pairs")
    return LOW_CONFIDENCE_PAIRS | file_pairs

def _score_heuristics(
    clash: dict[str, Any],
    *,
    clash_mode: str,
    move_side: str,
    max_penetration_solve_m: float,
    min_penetration_m: float,
) -> ClashSuitability:
    clash_key = str(clash.get("clash_key", ""))
    guid, movable_class, side = _choose_movable(clash, move_side)
    static_class = clash.get("b_ifc_class", "") if side == "a" else clash.get("a_ifc_class", "")
    movable_disc = discipline_from_class(movable_class)
    static_disc = discipline_from_class(static_class)
    pair = _class_pair_key(
        clash.get("a_ifc_class", ""),
        clash.get("b_ifc_class", ""),
        side,
    )
    class_pair = f"{pair[0]}|{pair[1]}"
    penetration = float(np.linalg.norm(clash_separation_vector(clash)))

    reasons: list[str] = []
    blockers: list[str] = []
    confidence = 0.35
    tier: Tier = "review"

    if movable_class in MEP_CLASSES:
        confidence += 0.35
        reasons.append("movable_is_mep")
    if static_class in STRUCTURAL_CLASSES and movable_disc == "mep":
        confidence += 0.15
        reasons.append("mep_vs_structure")
    if _pair_in_set(pair, high_confidence_pairs()):
        confidence += 0.2
        reasons.append("known_resolved_class_pair")
    if _pair_in_set(pair, low_confidence_pairs()):
        confidence -= 0.25
        blockers.append("dual_structural_or_low_success_pair")
    if movable_disc == static_disc == "structural":
        confidence -= 0.2
        blockers.append("both_structural_discipline")
    if penetration < min_penetration_m:
        confidence -= 0.15
        blockers.append("penetration_below_noise_floor")
    if penetration > max_penetration_solve_m:
        confidence -= 0.3
        blockers.append("penetration_exceeds_auto_solve_threshold")
    if clash_mode == "clearance" and penetration > 0.2:
        blockers.append("clearance_mode_large_gap")

    # Plan-dominant clashes suit AG para proofs (Z small vs XY)
    mid = clash_midpoint(clash)
    p1 = np.array(clash.get("p1") or mid, dtype=float)
    p2 = np.array(clash.get("p2") or mid, dtype=float)
    dx, dy, dz = abs(p2[0] - p1[0]), abs(p2[1] - p1[1]), abs(p2[2] - p1[2])
    planar = dz <= max(dx, dy, 1e-6) * 0.35
    if planar:
        confidence += 0.1
        reasons.append("plan_dominant_penetration")
    else:
        confidence += 0.05
        reasons.append("vertical_component_present")

    confidence = max(0.0, min(1.0, confidence))
    strategy = _fix_strategy(movable_class, static_class, movable_disc, penetration, clash_mode)

    if confidence >= 0.65 and not blockers:
        tier = "solve"
    elif confidence < 0.35 or len(blockers) >= 2:
        tier = "exclude"
    else:
        tier = "review"

    return ClashSuitability(
        clash_key=clash_key,
        tier=tier,
        confidence=confidence,
        fix_strategy=strategy,
        class_pair=class_pair,
        movable_class=movable_class,
        movable_discipline=movable_disc,
        static_class=static_class,
        penetration_m=penetration,
        reasons=reasons,
        blockers=blockers,
    )


def evaluate_ag_viability(
    clash: dict[str, Any],
    vendor: Any,
    *,
    clearance_m: float = 0.05,
    clash_id: str = "clash",
) -> tuple[bool, int, int, bool]:
    from ag_ifc.ag2_runner import prove_problem

    record = {**clash, "clash_id": clash_id, "clearance_required_m": clearance_m}
    stubs = clash_to_ag2_multiplane(record, clearance_m=clearance_m)
    proven = 0
    plan_proven = False
    for stub in stubs:
        result = prove_problem(f"{clash_id}_{stub.clash_id}", stub.ag2, vendor)
        if result.proven:
            proven += 1
            if stub.clash_id == clash_id or "iter" not in stub.clash_id:
                plan_proven = plan_proven or True
        if stub.clash_id.endswith("_xy_escape") or stub.mapping.get("plane") == "xy":
            if result.proven:
                plan_proven = True
    viable = proven > 0
    return viable, proven, len(stubs), plan_proven


def assess_clash_suitability(
    clash_key: str,
    clash: dict[str, Any],
    *,
    clash_mode: str = "intersection",
    move_side: str = "auto",
    clearance_m: float = 0.05,
    verify_ag: bool = False,
    vendor: Any = None,
    ifc_paths: list[str] | None = None,
    require_geometry: bool = False,
    max_penetration_solve_m: float = 0.75,
    min_penetration_m: float = 0.002,
) -> ClashSuitability:
    item = dict(clash)
    item["clash_key"] = clash_key
    result = _score_heuristics(
        item,
        clash_mode=clash_mode,
        move_side=move_side,
        max_penetration_solve_m=max_penetration_solve_m,
        min_penetration_m=min_penetration_m,
    )

    if require_geometry and ifc_paths:
        guid, _, _ = _choose_movable(item, move_side)
        found = any(element_geom(path, guid) is not None for path in ifc_paths)
        if not found:
            result.confidence -= 0.15
            result.blockers.append("geometry_aabb_unavailable")
            if result.tier == "solve":
                result.tier = "review"

    if verify_ag and vendor is not None:
        viable, proven, total, plan_proven = evaluate_ag_viability(
            item, vendor, clearance_m=clearance_m, clash_id=clash_key[:32]
        )
        result.ag_viable = viable
        result.ag_proven_count = proven
        result.ag_stub_count = total
        result.ag_plan_proven = plan_proven
        if viable:
            result.confidence = min(1.0, result.confidence + 0.15)
            result.reasons.append("ag_multiplane_proven")
        else:
            result.confidence = max(0.0, result.confidence - 0.25)
            result.blockers.append("ag_no_relational_proof")
            if result.tier == "solve":
                result.tier = "review"
        if not plan_proven and result.tier == "solve":
            result.tier = "review"
            result.blockers.append("ag_plan_stub_not_proven")

    # Re-tier after AG adjustment
    if result.confidence >= 0.65 and len(result.blockers) <= 1:
        if "ag_no_relational_proof" not in result.blockers:
            result.tier = "solve"
    if result.confidence < 0.35 or sum(
        1 for b in result.blockers if b.startswith("dual_") or b.startswith("penetration_exceeds")
    ) >= 1 and result.confidence < 0.5:
        result.tier = "exclude"

    return result


def prefilter_clash_dict(
    clashes: dict[str, Any],
    *,
    tiers: tuple[Tier, ...] = ("solve",),
    assessments: list[ClashSuitability] | None = None,
    **assess_kwargs: Any,
) -> tuple[dict[str, Any], list[ClashSuitability]]:
    """Return filtered clashes and full suitability assessments."""
    scored: list[ClashSuitability] = []
    filtered: dict[str, Any] = {}
    for key, data in clashes.items():
        assessment = assess_clash_suitability(key, data, **assess_kwargs)
        scored.append(assessment)
        if assessment.tier in tiers:
            filtered[key] = data
    if assessments is not None:
        assessments.clear()
        assessments.extend(scored)
    return filtered, scored


def prefilter_ifcclash_result(
    clash_set_result: dict[str, Any],
    *,
    tiers: tuple[Tier, ...] = ("solve",),
    include_meta: bool = True,
    **assess_kwargs: Any,
) -> dict[str, Any]:
    """
    Pre-filter one IfcClash export object (single clash set).

    Adds `_prefilter` metadata with per-clash suitability and summary counts.
    """
    clashes = clash_set_result.get("clashes", {})
    filtered, assessments = prefilter_clash_dict(clashes, tiers=tiers, **assess_kwargs)
    out = dict(clash_set_result)
    out["clashes"] = filtered
    if include_meta:
        by_tier: dict[str, int] = {"solve": 0, "review": 0, "exclude": 0}
        for a in assessments:
            by_tier[a.tier] = by_tier.get(a.tier, 0) + 1
        out["_prefilter"] = {
            "original_count": len(clashes),
            "filtered_count": len(filtered),
            "tiers_kept": list(tiers),
            "by_tier": by_tier,
            "assessments": [a.to_dict() for a in assessments],
        }
    return out


def prefilter_ifcclash_file(
    path: str | Path,
    output_path: str | Path | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    import json
    from pathlib import Path as P

    p = P(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, list):
        filtered_sets = [prefilter_ifcclash_result(item, **kwargs) for item in data]
        result = filtered_sets
    else:
        result = prefilter_ifcclash_result(data, **kwargs)
    if output_path:
        P(output_path).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result if not isinstance(result, list) else {"clash_sets": result}
