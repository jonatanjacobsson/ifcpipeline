"""Unit tests for SpaceInteractions: sampling, face probing, classification, evidence.

Pure functions only -- plain lists of floats, no IFC file and no topologicpy kernel, so
these run anywhere. The geometry-in-the-loop behaviour is covered by the bench cases.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "topologicpy-worker"))

from ingest_scripts import _mep_ports, _space_index
from ingest_scripts.SpaceInteractions import (
    BOUNDS_CLASSES,
    SKIP_CLASSES,
    Ingester,
    axis_centreline_points,
    classify_probe,
    face_probe_points,
)

LOG = logging.getLogger("test")


def _box_verts(x0, y0, z0, x1, y1, z1):
    """The 8 corners of a box, flat — the coarsest tessellation a real exporter emits."""
    out = []
    for x in (x0, x1):
        for y in (y0, y1):
            for z in (z0, z1):
                out.extend([x, y, z])
    return out


# --- axis_centreline_points -------------------------------------------------

def test_a_long_run_is_sampled_along_its_whole_length():
    # A 16 m duct with only 8 vertices, all at the two ends. Bucketing existing
    # vertices would yield 2 points at the extremities; a tray crossing six rooms
    # would then be reported in the first and the last only.
    verts = _box_verts(0, 0, 0, 16, 0.2, 0.2)
    points, axis, length = axis_centreline_points(verts, spacing_m=0.25, max_samples=48)

    assert axis == 0
    assert length == 16
    assert len(points) == 48, "stations must not depend on tessellation density"
    xs = [p[0] for p in points]
    assert xs == sorted(xs), "samples must advance monotonically along the axis"
    assert min(xs) > 0 and max(xs) < 16, "stations are cell centres, inside the extent"
    # Every station sits on the duct's centreline, not on its surface.
    assert all(abs(p[1] - 0.1) < 1e-9 and abs(p[2] - 0.1) < 1e-9 for p in points)


def test_a_compact_element_is_not_over_sampled():
    points, _axis, length = axis_centreline_points(
        _box_verts(0, 0, 0, 0.2, 0.2, 0.2), spacing_m=0.25, max_samples=48
    )
    assert length == 0.2
    assert len(points) == 1


def test_max_samples_caps_a_very_long_run():
    points, _axis, _length = axis_centreline_points(
        _box_verts(0, 0, 0, 300, 0.1, 0.1), spacing_m=0.25, max_samples=48
    )
    assert len(points) == 48


def test_degenerate_geometry_is_survivable():
    assert axis_centreline_points([], spacing_m=0.25) == ([], 0, 0.0)
    points, _axis, length = axis_centreline_points([1.0, 2.0, 3.0], spacing_m=0.25)
    assert length == 0.0 and len(points) == 1


def test_minimum_separation_drops_coincident_stations():
    verts = _box_verts(0, 0, 0, 1.0, 0.2, 0.2)
    dense, _a, _l = axis_centreline_points(verts, spacing_m=0.01, max_samples=48, min_sep_m=0.0)
    sparse, _a, _l = axis_centreline_points(verts, spacing_m=0.01, max_samples=48, min_sep_m=0.5)
    assert len(sparse) < len(dense)


# --- face_probe_points ------------------------------------------------------

def _cube_mesh(size=2.0):
    x0 = y0 = z0 = 0.0
    x1 = y1 = z1 = size
    verts = [
        x0, y0, z0, x1, y0, z0, x1, y1, z0, x0, y1, z0,
        x0, y0, z1, x1, y0, z1, x1, y1, z1, x0, y1, z1,
    ]
    faces = [
        0, 1, 2, 0, 2, 3,   # bottom
        4, 6, 5, 4, 7, 6,   # top
        0, 4, 5, 0, 5, 1,   # -y
        3, 2, 6, 3, 6, 7,   # +y
        0, 3, 7, 0, 7, 4,   # -x
        1, 5, 6, 1, 6, 2,   # +x
    ]
    return verts, faces


def test_a_cube_yields_one_probe_direction_per_face():
    verts, faces = _cube_mesh(2.0)
    probes = face_probe_points(verts, faces, min_area_m2=0.05, max_faces=24, min_sep_m=0.5)
    normals = {tuple(round(c, 1) + 0.0 for c in normal) for _centroid, normal in probes}
    assert len(normals) == 6, f"expected the 6 cube faces, got {normals}"


def test_slivers_are_dropped():
    verts, faces = _cube_mesh(0.1)  # every face is 0.005 m2
    assert face_probe_points(verts, faces, min_area_m2=0.05) == []


def test_probe_budget_is_respected():
    verts, faces = _cube_mesh(4.0)
    assert len(face_probe_points(verts, faces, min_area_m2=0.05, max_faces=3, min_sep_m=0.0)) <= 3


def test_no_faces_means_no_probes():
    assert face_probe_points([0.0, 0.0, 0.0], [], min_area_m2=0.05) == []


# --- classification ---------------------------------------------------------

def test_only_a_strictly_interior_probe_decides():
    assert classify_probe(_space_index.STATUS_INSIDE) == "inside"
    # ON-boundary is never a verdict: whether a coplanar point returns 1 or 2 is decided
    # by sub-micron float differences and by the room solid's baked sewing tolerance.
    assert classify_probe(_space_index.STATUS_ON_BOUNDARY) is None
    assert classify_probe(_space_index.STATUS_OUTSIDE) is None
    assert classify_probe(_space_index.STATUS_ERROR) is None


def test_mep_can_never_bound_a_room_and_spaces_are_never_subjects():
    assert "IfcWall" in BOUNDS_CLASSES and "IfcSlab" in BOUNDS_CLASSES
    for mep in ("IfcDuctSegment", "IfcFlowSegment", "IfcPipeSegment", "IfcFlowTerminal"):
        assert mep not in BOUNDS_CLASSES
    for skipped in ("IfcSpace", "IfcOpeningElement", "IfcDistributionPort"):
        assert skipped in SKIP_CLASSES


# --- evidence contract ------------------------------------------------------

def _ingester(**kwargs):
    return Ingester(ifc_files=[], log=LOG, **kwargs)


def _room(kind="brep_cell"):
    return _space_index.RoomCell(
        global_id="ROOM1", name="Room_Nr. 030-614", long_name="Kontor", storey="Plan 3",
        cell=None, aabb=(0, 0, 0, 1, 1, 1), cell_index=0, cell_count=1, kind=kind,
    )


def test_evidence_reserves_extent_for_the_later_exact_pass():
    payload = _ingester()._evidence(
        method="interior_sample_probe", room=_room(), ifc_class="IfcFlowSegment",
        entity=None, source_file="E1.ifc", space_file="A1.ifc",
        samples={"total": 22, "hits": 6}, probe={"toleranceM": 0.0},
    )
    # Present-and-null, so a consumer can branch on it today and the exact pass can fill
    # it later without a migration or a re-run.
    assert "extent" in payload and payload["extent"] is None
    assert payload["precision"] == "sampled"
    assert payload["minDetectableChordM"] == 0.25
    assert payload["state"] == "assumed"
    assert payload["objectLongName"] == "Kontor"
    assert payload["cellKind"] == "brep_cell"


def test_a_degraded_bounding_box_room_says_so_on_the_edge():
    # The post-SIGSEGV retry probes boxes, not solids. A consumer must be able to tell.
    payload = _ingester()._evidence(
        method="aabb_sample_probe", room=_room(kind="aabb"), ifc_class="IfcFlowSegment",
        entity=None, source_file="E1.ifc", space_file="A1.ifc", samples=None,
        probe={"toleranceM": 0.0},
    )
    assert payload["cellKind"] == "aabb"
    assert payload["method"] == "aabb_sample_probe"


def test_the_probe_tolerance_recorded_is_zero_by_default():
    # A positive tolerance is a metric OUTWARD DILATION of the room solid (~70x cost),
    # so the default must stay 0 and must be visible on every edge.
    assert _ingester().tolerance == 0.0
    payload = _ingester()._evidence(
        method="interior_sample_probe", room=_room(), ifc_class="IfcWall", entity=None,
        source_file="A1.ifc", space_file="A1s.ifc", samples=None,
        probe={"toleranceM": 0.0, "offsetM": 0.1, "rayDistanceM": None},
    )
    assert payload["probe"]["toleranceM"] == 0.0
    assert "samples" not in payload


def test_min_detectable_chord_follows_the_sample_spacing():
    assert _ingester(sample_spacing_m=0.5)._evidence(
        method="m", room=_room(), ifc_class="IfcWall", entity=None, source_file="a",
        space_file="b", samples=None, probe={},
    )["minDetectableChordM"] == 0.5


# --- terminal detection -----------------------------------------------------

class _FakeType:
    def __init__(self, name):
        self._name = name

    def is_a(self):
        return self._name


class _FakeRel:
    def __init__(self, type_name):
        self.RelatingType = _FakeType(type_name)

    def is_a(self, name):
        return name == "IfcRelDefinesByType"


class _FakeEntity:
    def __init__(self, cls, type_name=None):
        self._cls = cls
        self.IsDefinedBy = [_FakeRel(type_name)] if type_name else []
        self.IsTypedBy = []

    def is_a(self):
        return self._cls


def test_an_ifc2x3_diffuser_is_recognised_through_its_type():
    # IFC2X3 has no IfcAirTerminal: the diffuser is an IfcFlowTerminal and only its
    # TYPE object says what it is.
    classes = {"IfcFlowTerminal", "IfcElectricDistributionPoint"}
    assert _mep_ports.is_terminal(_FakeEntity("IfcFlowTerminal", "IfcAirTerminalType"), classes)
    assert _mep_ports.is_terminal(_FakeEntity("IfcSanitaryTerminal", "IfcSanitaryTerminalType"), set())
    assert not _mep_ports.is_terminal(_FakeEntity("IfcFlowSegment"), classes)
    assert not _mep_ports.is_terminal(_FakeEntity("IfcWall", "IfcWallType"), classes)


def test_one_system_is_chosen_deterministically():
    systems = [
        {"globalId": "zzz", "name": "TA02", "ifcClass": "IfcSystem"},
        {"globalId": "aaa", "name": "TA01", "ifcClass": "IfcSystem"},
    ]
    assert _mep_ports.primary_system(systems)["globalId"] == "aaa"
    assert _mep_ports.primary_system([]) is None
    assert _mep_ports.primary_system(None) is None


# --- frame check ------------------------------------------------------------

def test_a_unit_mismatch_between_two_models_is_detectable():
    rooms = (774.0, 740.0, 0.0, 884.0, 815.0, 29.0)
    metres = (779.0, 743.0, 2.0, 878.0, 810.0, 25.0)
    millimetres = (779000.0, 743000.0, 2000.0, 878000.0, 810000.0, 25000.0)

    assert _space_index.aabb_overlap_fraction(rooms, metres) > 0.5
    # The dangerous case: a well-formed result with zero edges.
    assert _space_index.aabb_overlap_fraction(rooms, millimetres) == 0.0
    assert _space_index.aabb_overlap_fraction(rooms, None) == 0.0


def test_point_in_aabb_is_inclusive_on_both_faces():
    box = (0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
    assert _space_index.point_in_aabb((0.5, 0.5, 0.5), box)
    assert _space_index.point_in_aabb((0.0, 1.0, 0.0), box)
    assert not _space_index.point_in_aabb((1.0001, 0.5, 0.5), box)


# --- above_space / below_space (AABB rooms, no kernel) ----------------------

IDENTITY = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


class _Segment:
    IsDefinedBy = []
    IsTypedBy = []

    def is_a(self):
        return "IfcFlowSegment"


def _index_with_room(z0=0.0, z1=3.0):
    index = _space_index.RoomIndex(grid_m=3.0)
    index.cells.append(_space_index.RoomCell(
        global_id="ROOM1", name="R1", long_name="Office", storey="1",
        cell=None, aabb=(0.0, 0.0, z0, 10.0, 10.0, z1), cell_index=0, cell_count=1, kind="aabb",
    ))
    index.space_count = 1
    index._build_grid()
    return index


def _relate(index, z0, z1, **kw):
    ing = _ingester(include_bounds=False, include_serves_space=False, **kw)
    verts = _box_verts(1.0, 4.0, z0, 9.0, 4.2, z1)   # an 8 m tray along x
    box = _space_index.world_aabb(verts, IDENTITY)
    stats = {"terminals": 0}
    emitted = ing._relate_element(
        index=index, gid="TRAY", entity=_Segment(), verts=verts, faces=[], matrix=IDENTITY,
        box=box, source_file="E1.ifc", space_file="A1.ifc", terminal_classes=set(),
        ports={}, systems={}, unit_scale=1.0, stats=stats,
    )
    return ing.get_relationships(), emitted


def test_a_tray_in_the_ceiling_void_is_above_the_room():
    rels, _ = _relate(_index_with_room(0.0, 3.0), z0=3.5, z1=3.7)   # 0.5 m above the room top
    types = {r["relationship_type"] for r in rels}
    assert types == {"above_space"}, types
    edge = rels[0]
    assert edge["object_global_id"] == "ROOM1"
    assert edge["evidence"]["method"] == "ray_down"
    assert 0.25 <= edge["evidence"]["probe"]["rayDistanceM"] <= 0.75
    assert edge["evidence"]["samples"]["hits"] == edge["evidence"]["samples"]["total"] > 1


def test_a_tray_under_a_raised_floor_is_below_the_room():
    rels, _ = _relate(_index_with_room(4.0, 7.0), z0=3.5, z1=3.7)   # 0.3 m under the room floor
    assert {r["relationship_type"] for r in rels} == {"below_space"}
    assert rels[0]["evidence"]["method"] == "ray_up"


def test_the_storey_above_is_not_reached_through_the_slab():
    # Nobel: void runs sit 0.5-1.0 m from the room; the next storey starts at >= 2.0 m.
    rels, _ = _relate(_index_with_room(6.0, 9.0), z0=3.5, z1=3.7)   # room 2.3 m above
    assert rels == []


def test_a_room_the_element_enters_is_not_also_above_or_below_it():
    rels, _ = _relate(_index_with_room(0.0, 3.0), z0=1.0, z1=1.2)   # inside the room
    assert {r["relationship_type"] for r in rels} == {"passes_through"}


def test_vertical_relations_can_be_switched_off():
    rels, _ = _relate(_index_with_room(0.0, 3.0), z0=3.5, z1=3.7, include_vertical=False)
    assert rels == []


def test_a_ceiling_void_tray_is_not_related_to_the_storey_above_through_the_slab():
    # Room A: floor 0, ceiling 3. Tray at 3.5-3.7 (its ceiling void). Room B, the next
    # storey, starts 0.9 m above the tray -- inside a naive up-reach, but the tray has
    # already found A below it, so what is above is the slab and B is never asked.
    index = _index_with_room(0.0, 3.0)
    index.cells.append(_space_index.RoomCell(
        global_id="ROOM_B", name="B", long_name="Above", storey="2",
        cell=None, aabb=(0.0, 0.0, 4.6, 10.0, 10.0, 7.6), cell_index=0, cell_count=1, kind="aabb",
    ))
    index._build_grid()
    rels, _ = _relate(index, z0=3.5, z1=3.7, below_reach_m=1.25)
    assert [(r["relationship_type"], r["object_global_id"]) for r in rels] == [("above_space", "ROOM1")]


def test_a_raised_floor_beyond_the_short_up_reach_is_not_related():
    rels, _ = _relate(_index_with_room(4.6, 7.6), z0=3.5, z1=3.7)   # room floor 0.9 m above
    assert rels == [], "0.9 m is a slab gap, not a raised floor"


def test_the_vertical_gap_is_measured_not_stepped():
    rels, _ = _relate(_index_with_room(0.0, 3.0), z0=3.42, z1=3.62)  # exactly 0.42 m above
    assert rels[0]["evidence"]["probe"]["rayDistanceM"] == 0.42


# --- duplicates -------------------------------------------------------------

def test_the_same_edge_is_emitted_once():
    ing = _ingester()
    assert ing._emit(subject="A", room=_room(), rel_type="passes_through", confidence=0.9, evidence={})
    assert not ing._emit(subject="A", room=_room(), rel_type="passes_through", confidence=0.9, evidence={})
    assert len(ing.get_relationships()) == 1
    # A different type to the same room is a different edge.
    assert ing._emit(subject="A", room=_room(), rel_type="bounds", confidence=0.7, evidence={})


def test_a_room_with_two_cells_yields_one_passes_through_edge():
    index = _space_index.RoomIndex(grid_m=3.0)
    for ci in (0, 1):
        index.cells.append(_space_index.RoomCell(
            global_id="ROOM1", name="R1", long_name="Two cells", storey="1",
            cell=None, aabb=(0.0, 0.0, 0.0, 10.0, 10.0, 3.0), cell_index=ci, cell_count=2, kind="aabb",
        ))
    index.space_count = 1
    index._build_grid()
    rels, _ = _relate(index, z0=1.0, z1=1.2)
    assert [(r["relationship_type"], r["object_global_id"]) for r in rels] == [("passes_through", "ROOM1")]
