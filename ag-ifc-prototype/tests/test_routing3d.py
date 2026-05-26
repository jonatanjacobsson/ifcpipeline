"""Unit tests for 3D routing and clash sorting."""

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ag_ifc.clash_sorter import sort_clashes
from ag_ifc.compiler import clash_to_ag2_multiplane, route_segments_to_ag2_problems
from ag_ifc.ifc_geometry import Aabb
from ag_ifc.routing3d import route_orthogonal


def test_route_avoids_obstacle_box():
    start = np.array([0.0, 0.0, 0.0])
    goal = np.array([1.0, 0.0, 0.0])
    obs = Aabb(np.array([0.3, -0.5, -0.5]), np.array([0.7, 0.5, 0.5]))
    route = route_orthogonal(start, goal, [obs], clearance_m=0.05, grid_step_m=0.1)
    assert len(route.waypoints) >= 2
    for wp in route.waypoints:
        assert not obs.contains_point(wp)


def test_sort_clashes_prefers_mep():
    clashes = {
        "c1": {
            "p1": [0, 0, 0],
            "p2": [0.01, 0, 0],
            "a_global_id": "g1",
            "b_global_id": "g2",
            "a_ifc_class": "IfcWall",
            "b_ifc_class": "IfcPipeSegment",
        },
        "c2": {
            "p1": [5, 5, 5],
            "p2": [5.01, 5, 5],
            "a_global_id": "g3",
            "b_global_id": "g4",
            "a_ifc_class": "IfcSlab",
            "b_ifc_class": "IfcBeam",
        },
    }
    ranked = sort_clashes(clashes)
    assert ranked[0].movable_class == "IfcPipeSegment"


def test_multiplane_ag_stubs():
    clash = {
        "clash_id": "t1",
        "p1": [1.0, 2.0, 3.0],
        "p2": [1.0, 2.0, 3.5],
        "clearance_required_m": 0.05,
    }
    stubs = clash_to_ag2_multiplane(clash, clearance_m=0.05)
    assert len(stubs) >= 2
    segs = route_segments_to_ag2_problems(
        [np.array([0, 0, 0]), np.array([1, 0, 0]), np.array([1, 0, 1])],
        clearance_m=0.05,
        clash_id="r1",
    )
    assert len(segs) == 2
