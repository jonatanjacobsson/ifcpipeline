"""Unit tests for FederatedRelationships geometric classification (cross-discipline edges)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "topologicpy-worker"))

from ingest_scripts.FederatedRelationships import (
    aabb_relation,
    classify_pair,
    discipline,
)


def _el(gid, cls, aabb):
    return {"gid": gid, "ifc_class": cls, "discipline": discipline(cls),
            "aabb": aabb, "centroid": ((aabb[0]+aabb[3])/2, (aabb[1]+aabb[4])/2, (aabb[2]+aabb[5])/2)}


# --- discipline tagging -----------------------------------------------------
def test_discipline_tags():
    assert discipline("IfcDuctSegment") == "mep"
    assert discipline("IfcWall") == "architectural"
    assert discipline("IfcColumn") == "structural"
    assert discipline("IfcSpace") == "spatial"


# --- aabb math --------------------------------------------------------------
def test_aabb_overlap_and_gap():
    a = (0, 0, 0, 1, 1, 1)
    assert aabb_relation(a, (0.5, 0.5, 0.5, 2, 2, 2))["overlaps"] is True
    r = aabb_relation(a, (2, 0, 0, 3, 1, 1))  # 1m gap on x
    assert r["overlaps"] is False and abs(r["gap"] - 1.0) < 1e-9


# --- classification ---------------------------------------------------------
def test_penetrates_duct_through_wall():
    duct = _el("D", "IfcDuctSegment", (0, 0, 2, 5, 0.3, 2.3))   # long run along x
    wall = _el("W", "IfcWall", (2, -0.1, 0, 2.2, 3, 3))         # thin wall the duct crosses
    res = classify_pair(duct, wall)
    assert res["type"] == "penetrates"
    assert "penetration_m" in res["evidence"]


def test_sits_in_space():
    box = _el("B", "IfcDuctSegment", (1, 1, 1, 1.2, 1.2, 1.2))  # mep element
    space = _el("S", "IfcSpace", (0, 0, 0, 4, 4, 3))            # centroid inside
    res = classify_pair(box, space)
    assert res["type"] == "sits_in"


def test_mounted_on_top_of_slab():
    equip = _el("E", "IfcFlowTerminal", (1, 1, 3.0, 1.5, 1.5, 3.5))  # sits on slab top z=3
    slab = _el("SL", "IfcSlab", (0, 0, 2.8, 5, 5, 3.0))
    res = classify_pair(equip, slab)
    assert res["type"] == "mounted_on"
    assert res["evidence"]["contact"] == "on_top"


def test_intersects_generic_cross_discipline():
    pipe = _el("P", "IfcFlowTerminal", (0, 0, 0, 1, 1, 1))
    col = _el("C", "IfcColumn", (0.5, 0.5, 0.5, 1.5, 1.5, 1.5))
    res = classify_pair(pipe, col)
    assert res["type"] in {"intersects", "mounted_on"}  # overlap → a cross-discipline relation


def test_within_clearance():
    duct = _el("D", "IfcDuctSegment", (0, 0, 0, 1, 1, 1))
    wall = _el("W", "IfcWall", (1.02, 0, 0, 2, 1, 1))  # 20mm gap
    res = classify_pair(duct, wall, clearance=0.05)
    assert res["type"] == "within_clearance"


def test_same_discipline_skipped():
    a = _el("A", "IfcWall", (0, 0, 0, 1, 1, 1))
    b = _el("B", "IfcSlab", (0.5, 0.5, 0.5, 2, 2, 2))  # both architectural
    assert classify_pair(a, b) is None
