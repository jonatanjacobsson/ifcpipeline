"""MEP reasoning: parallel preferred, bends costly."""

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ag_ifc.ifc_geometry import Aabb
from ag_ifc.mep_reasoning import FixStrategy, analyze_route_waypoints, reason_mep_fix


def test_parallel_route_zero_bends():
    wps = [np.array([0, 0, 0]), np.array([1, 0, 0])]
    m = analyze_route_waypoints(wps)
    assert m.bend_count == 0
    assert m.parallel_to_run_axis


def test_two_segments_one_bend():
    wps = [np.array([0, 0, 0]), np.array([1, 0, 0]), np.array([1, 0, 1])]
    m = analyze_route_waypoints(wps)
    assert m.bend_count == 1


def test_reason_prefers_parallel_when_clear():
    clash = {"p1": [0, 0, 0], "p2": [0.1, 0, 0], "a_global_id": "a", "b_global_id": "b"}
    start = np.array([0.0, 0.0, 0.0])
    goal = np.array([0.0, 0.5, 0.0])
    r = reason_mep_fix(
        clash,
        start=start,
        goal=goal,
        obstacles=[],
        movable_geom=None,
        movable_class="IfcDuctSegment",
        static_class="IfcBeam",
    )
    assert r.strategy in (FixStrategy.PARALLEL_TRANSLATE, FixStrategy.REROUTE_MINIMAL)
    assert r.cost_score < 20
