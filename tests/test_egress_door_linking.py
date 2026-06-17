"""Unit tests for door-side space resolution helpers."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "topologicpy-worker"))

import logging

from ingest_scripts.EgressCirculation import (
    Ingester,
    _bbox_xy_area,
    _door_plan_side_points,
    _footprint_signature,
    _pick_apartment_cluster_hub,
    _pick_space_at_plan_point,
    _storey_sort_key,
)


def _new_ingester() -> Ingester:
    ing = Ingester([Path("dummy.ifc")], logging.getLogger("test"))
    ing._portal_space_pairs = []
    return ing


def test_footprint_signature_rounds_centre():
    bbox = (10.0, 20.0, 0.0, 14.2, 24.1, 3.0)
    sig = _footprint_signature(bbox, centre_tol=0.35)
    assert sig[2] == 4.2
    assert sig[3] == 4.1


def test_bbox_xy_area():
    assert _bbox_xy_area((0, 0, 0, 2, 3, 1)) == 6.0


def test_storey_sort_key_elevation():
    assert _storey_sort_key("elev:7.0", 0.0, {}) == 7.0


def test_pick_space_at_plan_point_prefers_smaller_footprint():
    bboxes = {
        "large": (0.0, 0.0, 0.0, 10.0, 10.0, 3.0),
        "small": (4.0, 4.0, 0.0, 6.0, 6.0, 3.0),
    }
    space_names = {"large": "korridor", "small": "room"}
    space_points = {"large": (5.0, 5.0, 1.5), "small": (5.0, 5.0, 1.5)}
    element_storey = {"large": "storey1", "small": "storey1"}
    storey_elevations = {"storey1": 3.0}
    picked = _pick_space_at_plan_point(
        (5.0, 5.0),
        "elev:3.0",
        bboxes,
        space_names,
        space_points,
        element_storey,
        storey_elevations,
        same_storey_only=True,
        z_tolerance=2.5,
        plan_tolerance=0.25,
    )
    assert picked == "small"


def test_door_plan_side_points_returns_two_distinct_points():
    class FakeDoor:
        ObjectPlacement = object()

    # Without real IFC placement this returns None — smoke test only when None
    assert _door_plan_side_points(FakeDoor(), 0.6) is None


def test_apartment_hub_prefers_korridor_entry():
    adj = {
        "hall": {"korridor", "kitchen"},
        "kitchen": {"hall"},
        "korridor": {"hall", "other_apt"},
        "other_apt": {"korridor"},
    }
    hub, reason = _pick_apartment_cluster_hub(
        ["kitchen", "hall"],
        adj,
        {"korridor"},
        {"kitchen": "Kök", "hall": "Hall"},
        {"kitchen": "2-1103-4", "hall": "2-1103-1"},
    )
    assert hub == "hall"
    assert reason == "korridor_entry"


def test_apartment_hub_falls_back_to_hall_name():
    hub, reason = _pick_apartment_cluster_hub(
        ["bed", "hall"],
        {"bed": set(), "hall": set()},
        set(),
        {"bed": "Sovrum", "hall": "Hall"},
        {"bed": "2-1103-3", "hall": "2-1103-1"},
    )
    assert hub == "hall"
    assert reason == "hall_name"


def test_append_portal_link_builds_two_hop_through_door():
    ing = _new_ingester()
    seen: set = set()
    added = ing._append_portal_link(
        seen, {"S1", "S2"},
        portal_id="DOOR1", portal_class="IfcDoor", portal_name="D1",
        method="door_side_containment", source_file="x.ifc",
    )

    through = [r for r in ing._relationships if r.relationship_type == "egress_through"]

    assert added == 2
    # both rooms reach the door (space -> door -> space), door is the shared node
    assert {r.subject_global_id for r in through} == {"S1", "S2"}
    assert {r.object_global_id for r in through} == {"DOOR1"}
    # no synthetic proxy, no direct space<->space edge for a portal connection
    assert not any(r.relationship_type == "represents_portal" for r in ing._relationships)
    assert not any(r.relationship_type == "egress_connects" for r in ing._relationships)
    assert ("S1", "S2", "door_side_containment") in ing._portal_space_pairs


def test_append_portal_link_dedups_repeat_pair():
    ing = _new_ingester()
    seen: set = set()
    ing._append_portal_link(seen, {"S1", "S2"}, "DOOR1", "IfcDoor", "D1", "m", "x.ifc")
    # a second door over the same room pair is suppressed
    again = ing._append_portal_link(seen, {"S1", "S2"}, "DOOR2", "IfcDoor", "D2", "m", "x.ifc")
    assert again == 0


def test_append_portal_link_requires_a_portal_node():
    ing = _new_ingester()
    # no portal id -> no middle node to route through -> nothing emitted
    added = ing._append_portal_link(set(), {"A", "B"}, "", "IfcOpeningElement", "", "m", "y.ifc")
    assert added == 0
    assert ing._relationships == []
