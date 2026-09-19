"""The narrow phases behind EgressCirculation's space_adjacency broad phase.

Bounding boxes are a broad phase only. An axis-aligned box around an L- or U-shaped
room covers ground the room does not, and on the Nobel model that linked rooms up to
26 m apart across open floor plate: of 724 box-adjacent pairs only 21% had footprints
that actually touched. Two islands of rooms hung off the graph by a single 24 m and a
single 25 m fabricated link.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "topologicpy-worker"))

from ingest_scripts.EgressCirculation import _bbox2d_face_adjacent, _footprint_gap


# --- the broad phase still behaves as before --------------------------------

def test_boxes_that_share_a_face_still_pass_the_broad_phase():
    a = (0.0, 0.0, 10.0, 5.0)
    b = (10.1, 0.0, 20.0, 5.0)          # 0.1 m gap, 5 m of overlap
    assert _bbox2d_face_adjacent(a, b, tol=0.15, min_shared=0.30)


def test_an_l_shaped_room_boxes_overlap_a_room_it_never_touches():
    """The failure this change is about: the boxes overlap, the rooms are 20 m apart."""
    l_shape = (0.0, 0.0, 40.0, 40.0)     # box of a room wrapping the plate
    far_room = (18.0, 18.0, 22.0, 22.0)  # sits in the middle of that box
    assert _bbox2d_face_adjacent(l_shape, far_room, tol=0.15, min_shared=0.30)


# --- the narrow phase ---------------------------------------------------------

def test_rooms_sharing_a_wall_pass_the_footprint_check():
    left = [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0)]
    right = [(10.2, 0.0), (20.0, 0.0), (20.0, 5.0), (10.2, 5.0)]   # 0.2 m wall
    gap = _footprint_gap(left, right, tol=1.5)
    assert gap is not None and abs(gap - 0.2) < 1e-6


def test_rooms_across_open_floor_are_rejected_however_their_boxes_overlap():
    # The U-shaped room's own footprint, and a room sitting inside the U.
    u_shape = [(0.0, 0.0), (40.0, 0.0), (40.0, 40.0), (36.0, 40.0),
               (36.0, 4.0), (4.0, 4.0), (4.0, 40.0), (0.0, 40.0)]
    inside = [(18.0, 18.0), (22.0, 18.0), (22.0, 22.0), (18.0, 22.0)]
    assert _bbox2d_face_adjacent((0, 0, 40, 40), (18, 18, 22, 22), 0.15, 0.30), "broad phase admits it"
    assert _footprint_gap(u_shape, inside, tol=1.5) is None, "narrow phase must reject it"


def test_the_tolerance_is_the_boundary():
    a = [(0.0, 0.0)]
    b = [(1.4, 0.0)]
    assert _footprint_gap(a, b, tol=1.5) is not None
    assert _footprint_gap(a, [(1.6, 0.0)], tol=1.5) is None


def test_a_missing_footprint_falls_back_to_the_box_verdict():
    # Geometry can fail for a single space; that must not silently drop its edges.
    assert _footprint_gap(None, [(0.0, 0.0)], tol=1.5) == 0.0
    assert _footprint_gap([], [(0.0, 0.0)], tol=1.5) == 0.0


def test_the_default_tolerance_is_the_calibrated_one():
    """0.6 m keeps only 65% of pairs a real IfcDoor proves adjacent; 1.5 m keeps 89%."""
    import inspect
    from ingest_scripts.EgressCirculation import Ingester
    sig = inspect.signature(Ingester.__init__)
    assert sig.parameters["footprint_tolerance"].default == 1.5
    assert sig.parameters["max_connector_offset"].default == 6.0
    # Appended last: resolve_positional_arguments maps the n8n Arguments list by
    # declaration order, so inserting these earlier would re-map existing callers.
    names = list(sig.parameters)
    assert names[-2:] == ["footprint_tolerance", "max_connector_offset"]
