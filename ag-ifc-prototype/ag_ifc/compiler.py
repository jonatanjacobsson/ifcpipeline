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
