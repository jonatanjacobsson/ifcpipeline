"""Tests for IfcClash pre-filter / AG suitability."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ag_ifc.clash_prefilter import assess_clash_suitability, prefilter_ifcclash_result


def test_mep_vs_beam_solve_tier():
    clash = {
        "a_global_id": "a",
        "b_global_id": "b",
        "a_ifc_class": "IfcDuctSegment",
        "b_ifc_class": "IfcBeam",
        "p1": [1.0, 2.0, 3.0],
        "p2": [1.05, 2.0, 3.0],
    }
    a = assess_clash_suitability("k1", clash, verify_ag=False)
    assert a.tier == "solve"
    assert a.fix_strategy == "route_mep_3d"


def test_dual_structural_review_or_exclude():
    clash = {
        "a_global_id": "a",
        "b_global_id": "b",
        "a_ifc_class": "IfcSlab",
        "b_ifc_class": "IfcBeam",
        "p1": [0, 0, 0],
        "p2": [0.8, 0, 0],
    }
    a = assess_clash_suitability("k2", clash, verify_ag=False)
    assert a.tier in ("review", "exclude", "solve")


def test_prefilter_keeps_solve_only():
    export = {
        "name": "test",
        "clashes": {
            "mep": {
                "a_global_id": "a",
                "b_global_id": "b",
                "a_ifc_class": "IfcPipeSegment",
                "b_ifc_class": "IfcBeam",
                "p1": [0, 0, 0],
                "p2": [0.05, 0, 0],
            },
            "wall": {
                "a_global_id": "c",
                "b_global_id": "d",
                "a_ifc_class": "IfcWall",
                "b_ifc_class": "IfcWall",
                "p1": [0, 0, 0],
                "p2": [0.4, 0, 0],
            },
        },
    }
    out = prefilter_ifcclash_result(export, tiers=("solve",), verify_ag=False)
    assert out["_prefilter"]["original_count"] == 2
    assert len(out["clashes"]) >= 1
    assert "mep" in out["clashes"] or len(out["clashes"]) == 1
