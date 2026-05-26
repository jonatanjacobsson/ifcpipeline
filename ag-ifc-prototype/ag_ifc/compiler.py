"""Minimal clash JSON → AG2 problem stub (formalization preview)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class FormalizationStub:
    clash_id: str
    ag2: str
    mapping: dict[str, str]
    assumptions: list[str]


def load_clash(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def clash_to_ag2_stub(clash: dict[str, Any]) -> FormalizationStub:
    """
    Build a plan-view AG2 problem from a clash record.

    This is intentionally minimal: it places symbolic points from clash
  midpoint and encodes a parallel-run goal. Real IFC extraction replaces this.
    """
    clash_id = str(clash.get("clash_id", "unknown"))
    pos = clash.get("position") or clash.get("p1") or [0.0, 0.0, 0.0]
    x, y = float(pos[0]), float(pos[1])
    clearance = float(clash.get("clearance_required_m", 0.05))

    # Symbolic layout (metres): beam a-b along +X through clash; duct offset +Y.
    ag2 = (
        f"a@{x:.4f}_{y:.4f} = ; "
        f"b@{x + 4:.4f}_{y:.4f} = ; "
        f"c@{x:.4f}_{y + clearance:.4f} = ; "
        f"d@{x + 4:.4f}_{y + clearance:.4f} = "
        f"para a b c d ? para c d a b"
    )
    mapping = {
        "a": "beam_start",
        "b": "beam_end",
        "c": "duct_start",
        "d": "duct_end",
        "a_global_id": str(clash.get("a_global_id", "")),
        "b_global_id": str(clash.get("b_global_id", "")),
    }
    assumptions = [
        "Plan-view abstraction (Z ignored).",
        "Beam axis approximated as segment through clash midpoint.",
        f"Target clearance encoded as Y-offset {clearance} m.",
    ]
    return FormalizationStub(
        clash_id=clash_id,
        ag2=ag2,
        mapping=mapping,
        assumptions=assumptions,
    )

def segment_to_ag2(
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    plane: str = "xy",
    offset_m: float = 0.05,
    segment_id: str = "s0",
) -> FormalizationStub:
    """Build AG2 problem for one orthogonal route segment in a 2D plane section."""
    if plane == "xy":
        x0, y0 = start
        x1, y1 = end
        off = offset_m
        ag2 = (
            f"a@{x0:.4f}_{y0:.4f} = ; "
            f"b@{x1:.4f}_{y1:.4f} = ; "
            f"c@{x0:.4f}_{y0 + off:.4f} = ; "
            f"d@{x1:.4f}_{y1 + off:.4f} = "
            f"para a b c d ? para c d a b"
        )
        assumptions = [f"Plane XY segment {segment_id}", f"Clearance offset {off} m in +Y"]
    elif plane == "xz":
        x0, z0 = start
        x1, z1 = end
        off = offset_m
        ag2 = (
            f"a@{x0:.4f}_{z0:.4f} = ; "
            f"b@{x1:.4f}_{z1:.4f} = ; "
            f"c@{x0:.4f}_{z0 + off:.4f} = ; "
            f"d@{x1:.4f}_{z1 + off:.4f} = "
            f"para a b c d ? para c d a b"
        )
        assumptions = [f"Plane XZ segment {segment_id}", f"Clearance offset {off} m in +Z section"]
    else:
        y0, z0 = start
        y1, z1 = end
        off = offset_m
        ag2 = (
            f"a@{y0:.4f}_{z0:.4f} = ; "
            f"b@{y1:.4f}_{z1:.4f} = ; "
            f"c@{y0 + off:.4f}_{z0:.4f} = ; "
            f"d@{y1 + off:.4f}_{z1:.4f} = "
            f"para a b c d ? para c d a b"
        )
        assumptions = [f"Plane YZ segment {segment_id}", f"Clearance offset {off} m in +Y section"]

    return FormalizationStub(
        clash_id=segment_id,
        ag2=ag2,
        mapping={"plane": plane, "segment": segment_id},
        assumptions=assumptions,
    )


def route_segments_to_ag2_problems(
    waypoints: list,
    *,
    clearance_m: float = 0.05,
    clash_id: str = "route",
) -> list[FormalizationStub]:
    """Emit one AG2 stub per orthogonal segment, projected to dominant plane."""
    import numpy as np

    stubs: list[FormalizationStub] = []
    pts = [np.array(p, dtype=float) for p in waypoints]
    for i in range(len(pts) - 1):
        seg = pts[i + 1] - pts[i]
        if np.linalg.norm(seg) < 1e-9:
            continue
        ax = int(np.argmax(np.abs(seg)))
        seg_id = f"{clash_id}_seg{i}_{'xyz'[ax]}"
        if ax == 0:
            stub = segment_to_ag2(
                (pts[i][0], pts[i][1]),
                (pts[i + 1][0], pts[i + 1][1]),
                plane="xy",
                offset_m=clearance_m,
                segment_id=seg_id,
            )
        elif ax == 1:
            stub = segment_to_ag2(
                (pts[i][0], pts[i][2]),
                (pts[i + 1][0], pts[i + 1][2]),
                plane="xz",
                offset_m=clearance_m,
                segment_id=seg_id,
            )
        else:
            stub = segment_to_ag2(
                (pts[i][1], pts[i][2]),
                (pts[i + 1][1], pts[i + 1][2]),
                plane="yz",
                offset_m=clearance_m,
                segment_id=seg_id,
            )
        stubs.append(stub)
    return stubs


def clash_to_ag2_multiplane(
    clash: dict,
    *,
    clearance_m: float = 0.05,
) -> list[FormalizationStub]:
    """XY plan stub plus section stubs from clash penetration axis."""
    base = clash_to_ag2_stub({**clash, "clearance_required_m": clearance_m})
    stubs = [base]
    p1 = clash.get("p1") or [0.0, 0.0, 0.0]
    p2 = clash.get("p2") or p1
    mid = [(p1[i] + p2[i]) / 2 for i in range(3)]
    dx = abs(p2[0] - p1[0])
    dy = abs(p2[1] - p1[1])
    dz = abs(p2[2] - p1[2])
    cid = str(clash.get("clash_id", "clash"))
    if dz >= max(dx, dy) * 0.25:
        stubs.append(
            segment_to_ag2(
                (mid[0], mid[2]),
                (mid[0], mid[2] + clearance_m),
                plane="xz",
                offset_m=clearance_m,
                segment_id=f"{cid}_xz_escape",
            )
        )
    if dy >= max(dx, dz) * 0.25:
        stubs.append(
            segment_to_ag2(
                (mid[0], mid[1]),
                (mid[0], mid[1] + clearance_m),
                plane="xy",
                offset_m=clearance_m,
                segment_id=f"{cid}_xy_escape",
            )
        )
    return stubs
