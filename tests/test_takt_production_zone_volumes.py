"""TaktProduction: takt zones that are volumes of their own (a zone model run with the discipline models).

An IfcSpatialZone authored in a separate file cannot reach its members by IFC relationship.
Its own body decides instead: an element whose reference point (the centre of its world
bounding box, raised a little for thin elements) lies inside the zone's body is a member.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "topologicpy-worker"))

import ingest_scripts.TaktProduction as tp  # noqa: E402


def _box(x0, y0, z0, x1, y1, z1):
    """A closed box as 12 outward triangles."""
    v = np.array([
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
    ], dtype=float)
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
             (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    return v[np.array(faces)]


def test_points_in_a_box():
    box = _box(0, 0, 0, 10, 10, 3)
    pts = [(5, 5, 1.5), (0.01, 9.99, 0.01), (5, 5, 3.5), (5, 5, -0.5), (11, 5, 1), (5, 5, 2.99)]
    assert tp.points_in_mesh(pts, box).tolist() == [True, True, False, False, False, True]


def test_points_in_a_stepped_zone_like_a_double_height_room():
    # A storey prism, and on top of part of it the room's volume rising through the void above.
    mesh = np.concatenate([_box(0, 0, 0, 10, 10, 3), _box(0, 0, 3, 4, 4, 6)])
    pts = [(2, 2, 4.5), (8, 8, 4.5), (2, 2, 1.0), (2, 2, 6.5)]
    assert tp.points_in_mesh(pts, mesh).tolist() == [True, False, True, False]


def test_many_points_are_chunked_without_changing_the_answer(monkeypatch):
    box = _box(0, 0, 0, 10, 10, 3)
    pts = np.column_stack([np.linspace(-5, 15, 50), np.full(50, 5.0), np.full(50, 1.0)])
    expected = (pts[:, 0] > 0) & (pts[:, 0] < 10)
    monkeypatch.setattr(tp, "_POINT_CHUNK", 7)
    assert (tp.points_in_mesh(pts, box) == expected).all()


def test_sample_points_span_the_box_at_the_reference_height():
    pts = tp.sample_points((0, 0, 0, 6, 3, 3))
    assert len(pts) == 8 and (3.0, 1.5, 1.5) not in pts
    assert {p[2] for p in pts} == {1.5} and min(p[0] for p in pts) == pytest.approx(1.0)


def test_a_thin_element_counts_a_little_higher():
    # A floor finish lying just under its storey line belongs to that storey.
    assert tp.reference_point((0, 0, -0.03, 2, 2, 0.0))[2] == pytest.approx(-0.015 + tp.Z_BIAS_M)
    # A wall is judged at its middle.
    assert tp.reference_point((0, 0, 0, 0.2, 4, 3.0)) == pytest.approx((0.1, 2.0, 1.5))


def _model(path: Path, build) -> Path:
    import ifcopenshell
    import ifcopenshell.api

    f = ifcopenshell.api.run("project.create_file", version="IFC4")
    project = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcProject", name="P")
    unit = ifcopenshell.api.run("unit.add_si_unit", f, unit_type="LENGTHUNIT")
    ifcopenshell.api.run("unit.assign_unit", f, units=[unit])
    context = ifcopenshell.api.run("context.add_context", f, context_type="Model")
    body = ifcopenshell.api.run("context.add_context", f, context_type="Model", context_identifier="Body",
                                target_view="MODEL_VIEW", parent=context)
    site = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcSite", name="S")
    building = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcBuilding", name="B")
    storey = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcBuildingStorey", name="010")
    ifcopenshell.api.run("aggregate.assign_object", f, products=[site], relating_object=project)
    ifcopenshell.api.run("aggregate.assign_object", f, products=[building], relating_object=site)
    ifcopenshell.api.run("aggregate.assign_object", f, products=[storey], relating_object=building)
    build(f, body, storey)
    f.write(str(path))
    return path


def _prism(f, body, product, x0, y0, z0, x1, y1, z1):
    import ifcopenshell.api
    import ifcopenshell.util.shape_builder as sb

    x0, y0, z0, x1, y1, z1 = (float(v) for v in (x0, y0, z0, x1, y1, z1))
    builder = sb.ShapeBuilder(f)
    outline = builder.polyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], closed=True)
    solid = builder.extrude(builder.profile(outline), magnitude=z1 - z0, position=(0.0, 0.0, z0))
    rep = builder.get_representation(body, [solid])
    ifcopenshell.api.run("geometry.edit_object_placement", f, product=product)
    ifcopenshell.api.run("geometry.assign_representation", f, product=product, representation=rep)


def test_a_zone_model_takes_the_elements_inside_its_volumes(tmp_path):
    import ifcopenshell.api

    def zones(f, body, storey):
        for name, x0 in (("Z1", 0.0), ("Z2", 10.0)):
            z = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcSpatialZone", name=name,
                                     predefined_type="CONSTRUCTION")
            z.ObjectType = "Taktzon"
            _prism(f, body, z, x0, 0, 0, x0 + 10, 10, 3)
            ifcopenshell.api.run("spatial.reference_structure", f, products=[z], relating_structure=storey)
            qto = ifcopenshell.api.run("pset.add_qto", f, product=z, name="BaseQuantities")
            ifcopenshell.api.run("pset.edit_qto", f, qto=qto, properties={"NetFloorArea": 100.0, "GrossVolume": 300.0})

    def walls(f, body, storey):
        for name, box in (("W-in-Z1", (2, 2, 0, 2.2, 8, 3)), ("W-in-Z2", (12, 2, 0, 12.2, 8, 3)),
                          ("W-outside", (50, 50, 0, 50.2, 56, 3)), ("finish-Z1", (1, 1, -0.03, 4, 4, 0.0)),
                          # Its centre (x 20.5) is past Z2's end at x 20; a third of it is inside.
                          ("W-across-edge", (19.0, 2, 0, 22.0, 2.2, 3))):
            cls = "IfcCovering" if name.startswith("finish") else "IfcWall"
            w = ifcopenshell.api.run("root.create_entity", f, ifc_class=cls, name=name)
            _prism(f, body, w, *box)
            ifcopenshell.api.run("spatial.assign_container", f, products=[w], relating_structure=storey)

    zone_file = _model(tmp_path / "zones.ifc", zones)
    wall_file = _model(tmp_path / "walls.ifc", walls)
    ingester = tp.Ingester([zone_file, wall_file], logging.getLogger("t"), adjacency=False, num_threads=1)
    ingester.extract()

    import ifcopenshell
    names = {e.GlobalId: e.Name for path in (zone_file, wall_file) for e in ifcopenshell.open(str(path)).by_type("IfcRoot")}
    members = sorted((names[r.subject_global_id], names[r.object_global_id], r.evidence["method"],
                      r.evidence.get("elementFile"))
                     for r in ingester._relationships if r.relationship_type == tp.CONTAINS_ELEMENT_TYPE)
    assert members == [
        ("Z1", "W-in-Z1", "point_in_zone_volume", "walls.ifc"),
        ("Z1", "finish-Z1", "point_in_zone_volume", "walls.ifc"),
        ("Z2", "W-across-edge", "points_in_zone_volume", "walls.ifc"),
        ("Z2", "W-in-Z2", "point_in_zone_volume", "walls.ifc"),
    ]
    assert ingester._summary["rung"] == "spatial_zone"
    assert ingester._summary["unassigned_elements"] == 1
    # A zone model's zone has no rooms: its own Qto stands for its area.
    zones_out = {e.name: e.extra for e in ingester._elements}
    assert zones_out["Z1"]["area_m2"] == 100.0 and zones_out["Z1"]["element_count"] == 2


def test_a_zone_model_with_tbs_levels_places_an_element_once_per_level(tmp_path):
    """Frame sectors, façade sides and interior zones overlap in space; each level keeps its members."""
    import ifcopenshell
    import ifcopenshell.api

    def zones(f, body, storey):
        for name, level, tbs, box in (
            ("040:V1", "Taktzon", "NC/ÖVB/040/V/V1", (0, 0, 0, 10, 10, 3)),
            ("040:V2", "Taktzon", "NC/ÖVB/040/V/V2", (10, 0, 0, 20, 10, 3)),
            # The frame sector closes with the slab above: it reaches 0.15 m past the storey line.
            ("ST-040:V", "Stomzon", "NC/ÖVB/040/V", (0, 0, 0.15, 20, 10, 3.15)),
            ("FA-040:V", "Fasadzon", "NC/ÖVB/040/Fasad/V", (-1, 0, 0, 1, 10, 3)),
            ("untagged", None, None, (100, 100, 0, 110, 110, 3)),
        ):
            z = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcSpatialZone", name=name,
                                     predefined_type="CONSTRUCTION")
            z.ObjectType = "Taktzon"
            _prism(f, body, z, *box)
            ifcopenshell.api.run("spatial.reference_structure", f, products=[z], relating_structure=storey)
            if level:
                pset = ifcopenshell.api.run("pset.add_pset", f, product=z, name="Taktplanering")
                ifcopenshell.api.run("pset.edit_pset", f, pset=pset, properties={"TaktLevel": level, "TBS": tbs})

    def elements(f, body, storey):
        for name, cls, box in (
            ("wall-V1", "IfcWall", (2, 2, 0, 2.2, 8, 3)),
            ("wall-V2", "IfcWall", (12, 2, 0, 12.2, 8, 3)),
            ("window-west", "IfcWindow", (0, 4, 1, 0.3, 6, 2.2)),
            ("slab-above", "IfcSlab", (0, 0, 2.8, 20, 10, 3.0)),
            ("in-untagged", "IfcWall", (102, 102, 0, 102.2, 108, 3)),
        ):
            e = ifcopenshell.api.run("root.create_entity", f, ifc_class=cls, name=name)
            _prism(f, body, e, *box)
            ifcopenshell.api.run("spatial.assign_container", f, products=[e], relating_structure=storey)

    zone_file = _model(tmp_path / "zones.ifc", zones)
    element_file = _model(tmp_path / "elements.ifc", elements)
    ingester = tp.Ingester([zone_file, element_file], logging.getLogger("t"), adjacency=False, num_threads=1)
    ingester.extract()

    names = {e.GlobalId: e.Name for path in (zone_file, element_file)
             for e in ifcopenshell.open(str(path)).by_type("IfcRoot")}
    members: dict = {}
    for r in ingester._relationships:
        if r.relationship_type == tp.CONTAINS_ELEMENT_TYPE:
            members.setdefault(names[r.object_global_id], set()).add(names[r.subject_global_id])
    assert members == {
        "wall-V1": {"040:V1", "ST-040:V"},
        "wall-V2": {"040:V2", "ST-040:V"},
        "window-west": {"040:V1", "ST-040:V", "FA-040:V"},
        # The slab above lies above the interior zones' storey line: the frame's alone.
        "slab-above": {"ST-040:V"},
        "in-untagged": {"untagged"},
    }
    out = {e.name: e.extra for e in ingester._elements}
    assert (out["ST-040:V"]["level"], out["ST-040:V"]["tbs"]) == ("Stomzon", "NC/ÖVB/040/V")
    assert out["040:V1"]["tbs"] == "NC/ÖVB/040/V/V1" and "level" not in out["untagged"]
    assert {z["name"]: z["level"] for z in ingester._summary["zones"]}["FA-040:V"] == "Fasadzon"
