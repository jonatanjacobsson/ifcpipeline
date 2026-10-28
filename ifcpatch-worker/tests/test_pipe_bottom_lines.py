"""PipeBottomLinesDwg: pipe-bottom lines from IFC ports, joined into 3D polylines, written as DWG."""

import json
import logging
import math
import os
import sys
from pathlib import Path

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.api.context
import ifcopenshell.guid
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.api.system
import ifcopenshell.api.unit
import pytest

WORKER_ROOT = Path(__file__).resolve().parent.parent
CUSTOM = WORKER_ROOT / "custom_recipes"
if str(CUSTOM) not in sys.path:
    sys.path.insert(0, str(CUSTOM))

from pipe_bottom_lines import export  # noqa: E402
from pipe_bottom_lines.ifc_geometry import circle_through, parse_color_rules  # noqa: E402

KRAFT = [{"name": "Markrör KRAFT", "color": "FFFF00", "selector": "IfcCableCarrierSegment, Name=/.*Gul.*/"}]
LAYER = u"VG - 611 - Markrör KRAFT - 50mm"


class Model(object):
    """A small IFC4 in millimetres with conduits, fittings and connected ports.

    Everything sits on a basement storey whose floor is at +2000, so conduits
    at +1000 lie under it, in the ground. The entrance storey, at the
    project's ±0, has its floor at +5000.
    """

    def __init__(self, floor=2000.0):
        self.f = ifcopenshell.file(schema="IFC4")
        ifcopenshell.api.root.create_entity(self.f, ifc_class="IfcProject", name="Test")
        mm = ifcopenshell.api.unit.add_si_unit(self.f, unit_type="LENGTHUNIT", prefix="MILLI")
        ifcopenshell.api.unit.assign_unit(self.f, units=[mm])
        model = ifcopenshell.api.context.add_context(self.f, context_type="Model")
        self.body = ifcopenshell.api.context.add_context(
            self.f, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=model)
        self.storey = ifcopenshell.api.root.create_entity(self.f, ifc_class="IfcBuildingStorey", name="PL09")
        self.storey.ObjectPlacement = self.placement((0, 0, floor))
        self.storey.Elevation = floor - 5000.0
        entrance = ifcopenshell.api.root.create_entity(self.f, ifc_class="IfcBuildingStorey", name="PL10")
        entrance.ObjectPlacement = self.placement((0, 0, 5000.0))
        entrance.Elevation = 0.0
        self.contained = self.f.createIfcRelContainedInSpatialStructure(
            ifcopenshell.guid.new(), None, None, None, [], self.storey)

    def contain(self, element):
        self.contained.RelatedElements = list(self.contained.RelatedElements) + [element]

    def placement(self, origin, z=(0.0, 0.0, 1.0), x=(1.0, 0.0, 0.0)):
        axis = self.f.createIfcAxis2Placement3D(
            self.f.createIfcCartesianPoint([float(v) for v in origin]),
            self.f.createIfcDirection([float(v) for v in z]),
            self.f.createIfcDirection([float(v) for v in x]))
        return self.f.createIfcLocalPlacement(None, axis)

    def port(self, element, origin, axis):
        port = ifcopenshell.api.system.add_port(self.f, element=element)
        port.PredefinedType = "CABLECARRIER"
        x = (0.0, 0.0, 1.0) if abs(axis[2]) < 0.9 else (1.0, 0.0, 0.0)
        x = _cross(axis, x)
        port.ObjectPlacement = self.placement(origin, axis, x)
        return port

    def conduit(self, name, start, end, diameter=50.0, predefined="CONDUITSEGMENT", system="611",
                colour=(234, 234, 0)):
        el = ifcopenshell.api.root.create_entity(
            self.f, ifc_class="IfcCableCarrierSegment", name=name, predefined_type=predefined)
        el.ObjectPlacement = self.placement((0, 0, 0))
        profile = self.f.createIfcCircleProfileDef(
            "AREA", None, self.f.createIfcAxis2Placement2D(self.f.createIfcCartesianPoint([0.0, 0.0])),
            diameter / 2.0)
        direction = _unit(_sub(end, start))
        solid = self.f.createIfcExtrudedAreaSolid(
            profile, self.f.createIfcAxis2Placement3D(
                self.f.createIfcCartesianPoint([float(v) for v in start]),
                self.f.createIfcDirection(list(direction)),
                self.f.createIfcDirection(list(_cross(direction, (0.0, 0.0, 1.0))
                                               if abs(direction[2]) < 0.9 else (1.0, 0.0, 0.0)))),
            self.f.createIfcDirection([0.0, 0.0, 1.0]), _length(_sub(end, start)))
        rep = self.f.createIfcShapeRepresentation(self.body, "Body", "SweptSolid", [solid])
        el.Representation = self.f.createIfcProductDefinitionShape(None, None, [rep])
        # "Plastic - Yellow" by default, the way Revit writes the conduit material colour.
        shading = self.f.createIfcSurfaceStyleShading(
            self.f.createIfcColourRgb(None, *(c / 255.0 for c in colour)), 0.0)
        self.f.createIfcStyledItem(solid, [self.f.createIfcSurfaceStyle("Material", "BOTH", [shading])], None)
        if system:
            pset = ifcopenshell.api.pset.add_pset(self.f, product=el, name="Projekt")
            ifcopenshell.api.pset.edit_pset(self.f, pset=pset, properties={"Systembeteckning": system, "Etapp": "C"})
            pset = ifcopenshell.api.pset.add_pset(self.f, product=el, name="Klassning")
            ifcopenshell.api.pset.edit_pset(self.f, pset=pset, properties={
                "Våning": "PL09", "BSAB96": "SBQ", "Längd": "10000", "Guid": el.GlobalId})
        a = self.port(el, start, _unit(_sub(start, end)))
        b = self.port(el, end, _unit(_sub(end, start)))
        self.contain(el)
        return el, a, b

    def fitting(self, name, location, ports, rings=None, path=None):
        """rings: a socket radius (mm) per port, for a body of 20 mm sockets;
        path: (centreline points, radius) in mm, for a body swept along it."""
        el = ifcopenshell.api.root.create_entity(self.f, ifc_class="IfcCableCarrierFitting", name=name)
        el.ObjectPlacement = self.placement(location)
        made = [self.port(el, origin, axis) for origin, axis in ports]
        if path:
            points, radius = path
            directrix = self.f.createIfcPolyline([self.f.createIfcCartesianPoint([float(v) for v in _sub(q, location)])
                                                  for q in points])
            solid = self.f.createIfcSweptDiskSolid(directrix, float(radius), None, None, None)
            rep = self.f.createIfcShapeRepresentation(self.body, "Body", "AdvancedSweptSolid", [solid])
            el.Representation = self.f.createIfcProductDefinitionShape(None, None, [rep])
        if rings:
            solids = []
            for (origin, axis), radius in zip(ports, rings):
                inward = tuple(-v for v in axis)
                ref = _cross(inward, (0.0, 0.0, 1.0)) if abs(inward[2]) < 0.9 else (1.0, 0.0, 0.0)
                profile = self.f.createIfcCircleProfileDef(
                    "AREA", None, self.f.createIfcAxis2Placement2D(self.f.createIfcCartesianPoint([0.0, 0.0])),
                    float(radius))
                solids.append(self.f.createIfcExtrudedAreaSolid(
                    profile, self.f.createIfcAxis2Placement3D(
                        self.f.createIfcCartesianPoint([float(v) for v in _sub(origin, location)]),
                        self.f.createIfcDirection(list(inward)), self.f.createIfcDirection(list(_unit(ref)))),
                    self.f.createIfcDirection([0.0, 0.0, 1.0]), 20.0))
            rep = self.f.createIfcShapeRepresentation(self.body, "Body", "SweptSolid", solids)
            el.Representation = self.f.createIfcProductDefinitionShape(None, None, [rep])
        self.contain(el)
        return el, made

    def connect(self, a, b):
        ifcopenshell.api.system.connect_port(self.f, port1=a, port2=b)


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _length(a):
    return math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)


def _unit(a):
    size = _length(a)
    return (a[0] / size, a[1] / size, a[2] / size)


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _mul(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def elbow_run(bend_radius_mm, gap_mm=0.0):
    """Conduit along X, a 90-degree elbow, conduit along Y. Returns the model."""
    m = Model()
    r = bend_radius_mm
    a, _a0, a1 = m.conduit("Conduit with Fittings:SRN 50 - Gul:1", (0, 0, 1000), (10000, 0, 1000))
    corner = (10000 + r, 0, 1000)
    elbow, (e0, e1) = m.fitting(
        "SRN Böjar:SRN 50 Gul:2", corner,
        [((10000, 0, 1000 + gap_mm), (-1.0, 0.0, 0.0)), ((10000 + r, r, 1000), (0.0, 1.0, 0.0))])
    b, b0, _b1 = m.conduit("Conduit with Fittings:SRN 50 - Gul:3", (10000 + r, r, 1000), (10000 + r, 10000, 1000))
    m.connect(a1, e0)
    m.connect(e1, b0)
    return m


def legged_bend(turn_deg, radius_mm, leg_mm, diameter=50.0, body=True):
    """Conduit along X, an SRN-style bend turning left by turn_deg: a straight
    end of leg_mm, an arc of radius_mm, a straight end; conduit on. Returns
    (model, bend, its centreline in metres)."""
    m = Model()
    t = math.radians(turn_deg)
    z = 1000.0
    start = (10000.0, 0.0, z)
    arc_centre = (10000.0 + leg_mm, radius_mm, z)
    path = [start]
    for k in range(int(round(turn_deg)) + 1):
        a = -math.pi / 2 + t * k / round(turn_deg)
        path.append((arc_centre[0] + radius_mm * math.cos(a), arc_centre[1] + radius_mm * math.sin(a), z))
    d = (math.cos(t), math.sin(t), 0.0)
    end = _add(path[-1], _mul(d, leg_mm))
    path.append(end)
    corner = (10000.0 + leg_mm + radius_mm * math.tan(t / 2), 0.0, z)
    a, _a0, a1 = m.conduit("Conduit with Fittings:SRN 50 - Gul:1", (0, 0, z), start, diameter=diameter)
    bend, (e0, e1) = m.fitting("SRN Böjar:SRN 50 Gul:2", corner, [(start, (-1.0, 0.0, 0.0)), (end, d)],
                               path=(path, diameter / 2) if body else None)
    b, b0, _b1 = m.conduit("Conduit with Fittings:SRN 50 - Gul:3", end, _add(end, _mul(d, 5000.0)), diameter=diameter)
    m.connect(a1, e0)
    m.connect(e1, b0)
    return m, bend, [tuple(v / 1000.0 for v in q) for q in path]


def off_path(point, path):
    """Distance (metres) in plan from point to the polyline path."""
    best = float("inf")
    for p, q in zip(path, path[1:]):
        dx, dy = q[0] - p[0], q[1] - p[1]
        t = max(0.0, min(1.0, ((point[0] - p[0]) * dx + (point[1] - p[1]) * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(point[0] - p[0] - t * dx, point[1] - p[1] - t * dy))
    return best


def build(m, **options):
    options.setdefault("group_properties", ["Projekt.Systembeteckning"])
    options.setdefault("color_rules", parse_color_rules(KRAFT))
    return export.build(m.f, **options)


def test_circle_through_quarter_arc():
    center, radius, x_axis, y_axis, sweep = circle_through((1, 0, 0), (0, 1, 0), (math.sqrt(0.5), math.sqrt(0.5), 0))
    assert center == pytest.approx((0, 0, 0), abs=1e-12)
    assert radius == pytest.approx(1.0)
    assert sweep == pytest.approx(math.pi / 2)


def test_long_bend_is_cut_into_about_200mm_segments():
    report = build(elbow_run(500.0))
    assert report.long_bends == 1 and report.short_bends == 0
    assert len(report.polylines) == 1
    points, layer, rgb, closed = report.polylines[0]
    assert layer == LAYER and rgb is None and not closed
    assert report.layer_colors[LAYER] == (255, 255, 0)
    # Conduit, a 785 mm bend in round(785 / 200) = 4 segments, conduit.
    assert len(points) == 2 + 3 + 2
    bend = points[1:6]
    lengths = [math.dist(p, q) for p, q in zip(bend, bend[1:])]
    assert all(0.15 < s < 0.25 for s in lengths)
    # The pipe-bottom line is the outside bottom: 25 mm below the 1000 mm centreline.
    assert all(p[2] == pytest.approx(0.975) for p in points)
    assert points[0] == pytest.approx((0.0, 0.0, 0.975))
    assert points[-1] == pytest.approx((10.5, 10.0, 0.975))


def test_a_bend_with_straight_ends_follows_its_body():
    # 60 mm straight ends and a 1000 mm arc: one arc from port to port would
    # run 25 mm inside the bend at its middle.
    m, bend, path = legged_bend(90.0, 1000.0, 60.0)
    report = build(m, long_bend_min=0.0, bend_segment=0.05)
    points = [p for piece in report.vg_pieces if piece.guid == bend.GlobalId for p in piece.points]
    assert report.long_bends == 1 and len(points) > 30
    assert all(p[2] == pytest.approx(0.975) for p in points)
    assert max(off_path(p, path) for p in points) < 0.0015
    # The straight ends are there: the line leaves each port along its axis
    # (the piece may run either way).
    if points[0][1] > 0.5:
        points = points[::-1]
    assert points[0][:2] == pytest.approx((10.0, 0.0), abs=1e-6)
    assert points[1][:2] == pytest.approx((10.06, 0.0), abs=0.002)


def test_a_bend_of_a_few_degrees_follows_its_body_too():
    # 15 degrees: the corner of the two port axes lies inside the conduit.
    m, bend, path = legged_bend(15.0, 1000.0, 60.0)
    report = build(m, long_bend_min=0.0, bend_segment=0.05)
    points = [p for piece in report.vg_pieces if piece.guid == bend.GlobalId for p in piece.points]
    assert max(off_path(p, path) for p in points) < 0.0015


def test_a_bend_without_a_body_is_one_arc_from_port_to_port():
    m, bend, path = legged_bend(90.0, 1000.0, 60.0, body=False)
    report = build(m, long_bend_min=0.0, bend_segment=0.05)
    points = [p for piece in report.vg_pieces if piece.guid == bend.GlobalId for p in piece.points]
    assert max(off_path(p, path) for p in points) == pytest.approx(0.0249, abs=0.002)


def test_short_bend_is_one_straight_segment():
    report = build(elbow_run(100.0))
    assert report.short_bends == 1 and report.long_bends == 0
    points = report.polylines[0][0]
    assert len(points) == 4
    assert points[1] == pytest.approx((10.0, 0.0, 0.975))
    assert points[2] == pytest.approx((10.1, 0.1, 0.975))


def test_a_millimetre_port_gap_still_joins():
    report = build(elbow_run(500.0, gap_mm=1.2))
    assert report.snapped_ports == 1
    assert len(report.polylines) == 1


def test_without_rules_the_layer_is_group_and_size():
    report = build(elbow_run(500.0), color_rules=[])
    assert [layer for _p, layer, _c, _closed in report.polylines] == [u"VG - 611 - 50mm"]


def layers(report):
    return sorted(set(layer for _p, layer, _c, _closed in report.polylines))


def test_a_group_property_one_element_of_a_line_has_names_the_whole_line():
    m = Model()
    a, _a0, a1 = m.conduit("Conduit with Fittings:SRN 50 - Gul:1", (0, 0, 1000), (10000, 0, 1000), system=None)
    b, b0, _b1 = m.conduit("Conduit with Fittings:SRN 50 - Gul:2", (10000, 0, 1000), (20000, 0, 1000))
    m.connect(a1, b0)
    assert layers(build(m, color_rules=[])) == [u"VG - 611 - 50mm"]


def test_auto_groups_take_the_lines_ifc_system():
    import ifcopenshell.api.system as api_system

    m = elbow_run(500.0)
    system = ifcopenshell.api.system.add_system(m.f, ifc_class="IfcDistributionSystem")
    system.Name = "S1 29"
    system.ObjectType = "S1"
    first = [e for e in m.f.by_type("IfcCableCarrierSegment")][0]
    api_system.assign_system(m.f, products=[first], system=system)
    # Only one conduit is in the system; its elbow and the other conduit are on its line.
    assert layers(build(m, color_rules=[], group_properties=[])) == [u"VG - S1 - 50mm"]
    # A group property wins where the line has it.
    assert layers(build(m, color_rules=[])) == [u"VG - 611 - 50mm"]


def test_lines_nothing_names_are_numbered_west_to_east():
    m = Model()
    # Black: no colour of their own in the model, so each line gets a default colour.
    m.conduit("Conduit with Fittings:SRN 50 - Gul:1", (30000, 0, 1000), (40000, 0, 1000), system=None, colour=(0, 0, 0))
    m.conduit("Conduit with Fittings:SRN 50 - Gul:2", (0, 5000, 1000), (10000, 5000, 1000), system=None, colour=(0, 0, 0))
    report = build(m, color_rules=[], group_properties=[])
    assert layers(report) == [u"VG - Ledning 1 - 50mm", u"VG - Ledning 2 - 50mm"]
    west = [layer for points, layer, _c, _closed in report.polylines if points[0][0] < 20.0]
    assert west == [u"VG - Ledning 1 - 50mm"]
    # Separate lines get separate colours.
    assert len(set(report.layer_colors.values())) == 2


def test_the_recipes_group_properties_default_to_auto():
    from PipeBottomLinesDwg import Patcher as DwgPatcher
    from PipeBottomLinesIfc import Patcher

    m = elbow_run(500.0)
    assert Patcher(m.f, logging.getLogger("test")).group_properties == []
    assert DwgPatcher(m.f, logging.getLogger("test")).group_properties == []
    assert Patcher(m.f, logging.getLogger("test"), "", "", "", "Auto").group_properties == []
    assert Patcher(m.f, logging.getLogger("test"), "", "", "", "A.B, C").group_properties == ["A.B", "C"]


def test_a_conduit_selector_brings_its_fittings_along():
    report = build(elbow_run(500.0), selector="IfcCableCarrierSegment, Name=/.*SRN.*/")
    assert report.connected_fittings == 1
    assert report.elements == 3
    assert report.long_bends == 1
    assert len(report.polylines) == 1


def test_connected_fittings_can_be_left_out():
    report = build(elbow_run(500.0), selector="IfcCableCarrierSegment, Name=/.*SRN.*/",
                   include_connected_fittings=False)
    assert report.connected_fittings == 0
    assert report.elements == 2
    assert report.long_bends == 0
    assert len(report.polylines) == 2


def test_cable_trays_and_their_fittings_are_left_out():
    m = elbow_run(500.0)
    tray, _t0, t1 = m.conduit("Cable Tray:Kabelstege 600:9", (0, 5000, 3000), (5000, 5000, 3000),
                              predefined="CABLETRAYSEGMENT")
    union, (u0,) = m.fitting("Kabelstege union:10", (5000, 5000, 3000), [((5000, 5000, 3000), (-1.0, 0.0, 0.0))])
    m.connect(t1, u0)
    report = build(m, in_ground=False)
    assert report.elements == 3
    assert len(report.polylines) == 1


def test_only_what_is_in_the_ground_is_drawn():
    m = elbow_run(500.0)
    # A conduit above the +2000 floor, in the building.
    m.conduit("Conduit with Fittings:Kabelrör 50:7", (0, 20000, 3000), (5000, 20000, 3000))
    report = build(m)
    assert report.considered == 4 and report.elements == 3
    assert report.ground_level == pytest.approx(2.0)
    assert build(m, in_ground=False).elements == 4


def test_a_ground_word_puts_an_element_in_the_ground():
    m = elbow_run(500.0)
    # Above the basement floor, but its type says it is a ground sewer pipe.
    m.conduit("Pipe Types:P2, PP-rör släta markavloppsrör:7", (0, 20000, 3000), (5000, 20000, 3000))
    assert build(m).elements == 4


def test_a_kulvert_pipe_rising_above_the_entrance_floor_is_left_out():
    m = elbow_run(500.0)
    # Buried outside the basement, under the entrance floor at +5000: in.
    m.conduit("Pipe Types:X8, Kulvertrör single:7", (0, 20000, 4000), (5000, 20000, 4000))
    # The same pipe type rising into the building, midpoint +6000: out.
    m.conduit("Pipe Types:X8, Kulvertrör single:8", (5000, 21000, 4000), (5000, 21000, 8000))
    assert build(m).elements == 4


def test_without_rules_a_layer_takes_its_conduits_colour():
    report = build(elbow_run(500.0), color_rules=[])
    assert report.layer_colors == {u"VG - 611 - 50mm": (234, 234, 0)}


def test_systems_without_a_model_colour_get_distinct_defaults():
    m = Model()
    # Black, the way the VS exports write every pipe material.
    m.conduit("Conduit:A:1", (0, 0, 1000), (5000, 0, 1000), system="S11", colour=(0, 0, 0))
    m.conduit("Conduit:B:2", (0, 5000, 1000), (5000, 5000, 1000), system="S11L", colour=(0, 0, 0))
    colours = build(m, color_rules=[]).layer_colors
    assert set(colours) == {u"VG - S11 - 50mm", u"VG - S11L - 50mm"}
    assert colours[u"VG - S11 - 50mm"] != colours[u"VG - S11L - 50mm"]
    assert (0, 0, 0) not in colours.values()


def tube_points(path, radius, sides=16, tangents=None):
    """Rings of sides corners around each point of path (mm), square to it
    (or to tangents, one per point)."""
    rings = []
    for index, point in enumerate(path):
        ahead = path[min(index + 1, len(path) - 1)]
        behind = path[max(index - 1, 0)]
        tangent = _unit(tangents[index]) if tangents else _unit(_sub(ahead, behind))
        side = _unit(_cross(tangent, (0.0, 0.0, 1.0)) if abs(tangent[2]) < 0.9 else _cross(tangent, (1.0, 0.0, 0.0)))
        up = _cross(side, tangent)
        rings.append([_add(point, _add(_mul(side, radius * math.cos(2 * math.pi * k / sides)),
                                         _mul(up, radius * math.sin(2 * math.pi * k / sides))))
                      for k in range(sides)])
    return rings


def tube_element(m, ifc_class, path, radius, caps=False, sides=16, tangents=None):
    """A port-less element whose body is a tube of triangles along path (mm):
    open at its ends like MagiCAD's, or closed with caps like a Civil 3D pipe."""
    import ifcopenshell.api.root as root

    el = root.create_entity(m.f, ifc_class=ifc_class)
    el.ObjectPlacement = m.placement((0, 0, 0))
    rings = tube_points(path, radius, sides, tangents)
    coords = [list(map(float, q)) for ring in rings for q in ring]
    faces = []
    for r in range(len(rings) - 1):
        for k in range(sides):
            a, b = r * sides + k + 1, r * sides + (k + 1) % sides + 1
            faces += [[a, b, b + sides], [a, b + sides, a + sides]]
    if caps:
        last = (len(rings) - 1) * sides
        faces += [[1, k + 2, k + 1] for k in range(1, sides - 1)]
        faces += [[last + 1, last + k + 1, last + k + 2] for k in range(1, sides - 1)]
    body = m.f.createIfcTriangulatedFaceSet(m.f.createIfcCartesianPointList3D(coords), None, caps, faces, None)
    rep = m.f.createIfcShapeRepresentation(m.body, "Body", "Tessellation", [body])
    el.Representation = m.f.createIfcProductDefinitionShape(None, None, [rep])
    m.contain(el)
    return el


def test_mesh_rims_are_an_open_tubes_ends():
    import numpy
    from pipe_bottom_lines.ifc_geometry import mesh_rims

    rings = tube_points([(0.0, 0.0, 0.0), (1.0, 0.0, 0.1), (3.0, 0.0, 0.3)], 0.055)
    verts = numpy.array([q for ring in rings for q in ring])
    faces = []
    for r in range(len(rings) - 1):
        for k in range(16):
            a, b = r * 16 + k, r * 16 + (k + 1) % 16
            faces += [[a, b, b + 16], [a, b + 16, a + 16]]
    rims = sorted(mesh_rims(verts, numpy.array(faces)), key=lambda rim: rim[0][0])
    assert len(rims) == 2
    (c0, a0, r0), (c1, a1, r1) = rims
    assert numpy.allclose(c0, (0.0, 0.0, 0.0), atol=1e-6) and numpy.allclose(c1, (3.0, 0.0, 0.3), atol=1e-6)
    assert abs(r0 - 0.055) < 1e-6 and abs(r1 - 0.055) < 1e-6
    # Out of the body at both ends.
    assert a0[0] < -0.99 and a1[0] > 0.99


def test_prism_ends_are_a_closed_pipes_end_rings_even_cut_at_a_slant():
    import numpy
    from pipe_bottom_lines.ifc_geometry import prism_ends

    start, end = numpy.array((10.0, 5.0, 2.0)), numpy.array((12.0, 5.5, 2.3))
    rings = numpy.array(tube_points([tuple(start), tuple(end)], 0.1, sides=24))
    # A Civil 3D pipe ends on a vertical cut: slide each corner along the axis to a plane.
    axis = (end - start) / numpy.linalg.norm(end - start)
    rings[0] += numpy.outer(rings[0][:, 2] - start[2], axis) * 0.15
    ends = sorted(prism_ends(rings.reshape(-1, 3)), key=lambda e: e[0][0])
    assert numpy.allclose(ends[0][0], start, atol=2e-3) and numpy.allclose(ends[1][0], end, atol=1e-6)
    assert all(abs(radius - 0.1) < 1e-3 for _c, _a, radius in ends)
    assert ends[0][1] @ axis < -0.99 and ends[1][1] @ axis > 0.99


def test_pipes_without_ports_take_their_ends_from_their_body():
    with_ports = build(elbow_run(500.0))
    m = elbow_run(500.0)
    for entity in list(m.f.by_type("IfcRelConnectsPorts")) + list(m.f.by_type("IfcRelNests")) + \
            list(m.f.by_type("IfcDistributionPort")):
        m.f.remove(entity)
    without = build(m)
    assert without.mesh_port_elements == 2
    lines = lambda report: sorted(tuple(sorted(tuple(round(v, 4) for v in q) for q in piece.points))
                                  for piece in report.vg_pieces if piece.kind == "line" and len(piece.points) == 2
                                  and _length(_sub(piece.points[0], piece.points[1])) > 5.0)
    assert lines(without) == lines(with_ports)


def test_open_tube_pipes_and_fittings_join_by_their_rims():
    m = Model()
    r = 300.0
    angles = [t * math.pi / 16 for t in range(9)]
    bend = [(5000.0 - r + r * math.sin(a), r - r * math.cos(a), 1000.0) for a in angles]
    tube_element(m, "IfcPipeSegment", [(0.0, 0.0, 1000.0), bend[0]], 55.0)
    tube_element(m, "IfcPipeFitting", bend, 55.0, tangents=[(math.cos(a), math.sin(a), 0.0) for a in angles])
    tube_element(m, "IfcPipeSegment", [bend[-1], (5000.0, 5000.0, 1000.0)], 55.0)
    report = build(m, color_rules=[], group_properties=["X.Y"])
    assert report.mesh_port_elements == 3 and not report.skipped
    assert {piece.kind for piece in report.vg_pieces} == {"line", "arc"}
    assert {piece.size_mm for piece in report.vg_pieces} == {110.0}
    assert len(report.runs) == 1
    points = report.runs[0].points
    # The run starts and ends on its pipes' outside bottom and follows the bend's arc.
    assert {tuple(round(v, 4) for v in points[0]), tuple(round(v, 4) for v in points[-1])} == {
        (0.0, 0.0, 0.945), (5.0, 5.0, 0.945)}
    middle = (5.0 - r / 1000 + r / 1000 * math.sin(math.pi / 4), r / 1000 - r / 1000 * math.cos(math.pi / 4))
    assert min(math.hypot(q[0] - middle[0], q[1] - middle[1]) for q in points) < 0.01


def test_a_selected_proxy_that_is_a_long_round_tube_is_a_pipe_but_a_well_is_not():
    m = Model()
    tube_element(m, "IfcBuildingElementProxy", [(0.0, 0.0, 1000.0), (20000.0, 0.0, 900.0)], 104.0, caps=True,
                 sides=12)
    # A well: a round upright cylinder, as wide as it is high.
    tube_element(m, "IfcBuildingElementProxy", [(20000.0, 0.0, 400.0), (20000.0, 0.0, 1300.0)], 355.0, caps=True,
                 sides=24)
    options = dict(color_rules=[], group_properties=["X.Y"])
    # Proxies only count when the selector names them.
    assert not build(m, **options).vg_pieces
    report = build(m, selector="IfcFlowSegment, IfcFlowFitting, IfcBuildingElementProxy", **options)
    assert report.mesh_port_elements == 1 and len(report.vg_pieces) == 1
    piece = report.vg_pieces[0]
    assert piece.size_mm == 208.0
    assert {tuple(round(v, 4) for v in q) for q in piece.points} == {(0.0, 0.0, 0.896), (20.0, 0.0, 0.796)}


def write_ifc(tmp_path, name="vg.ifc", m=None, ifc_options=None, **options):
    m = m or elbow_run(500.0)
    report = build(m, **options)
    path = tmp_path / name
    counts = export.write_ifc(report, m.f, str(path), **(ifc_options or {}))
    return counts, ifcopenshell.open(str(path))


def test_ifc_lines_and_markers_can_be_two_files(tmp_path):
    m = elbow_run(500.0)
    full_counts, full = write_ifc(tmp_path, "all.ifc", m=m)
    counts, lines = write_ifc(tmp_path, "lines.ifc", m=m, ifc_options={"markers": False})
    assert counts == (1, 0)
    assert {a.ObjectType for a in lines.by_type("IfcAnnotation")} == {"VG Linjer"}
    assert [t.Name for t in lines.by_type("IfcTypeProduct")] == ["VG Linjer"]
    counts, markers = write_ifc(tmp_path, "markers.ifc", m=m, ifc_options={"lines": False})
    assert counts == (0, full_counts[1])
    assert {a.ObjectType for a in markers.by_type("IfcAnnotation")} == {u"VG Markör"}
    # The same objects as in the full file: same GlobalIds, names and descriptions.
    whole = {a.GlobalId: (a.Name, a.Description) for a in full.by_type("IfcAnnotation")}
    split = {a.GlobalId: (a.Name, a.Description) for f in (lines, markers) for a in f.by_type("IfcAnnotation")}
    assert split == whole
    # Apart from the project and its site, the two files share no object.
    shared = {e.GlobalId for e in lines.by_type("IfcRoot")} & {e.GlobalId for e in markers.by_type("IfcRoot")}
    assert sorted(lines.by_guid(g).is_a() for g in shared) == ["IfcProject", "IfcRelAggregates", "IfcSite"]
    # The full file keeps the containment it always had.
    assert full.by_type("IfcRelContainedInSpatialStructure")[0].GlobalId not in {
        e.GlobalId for f in (lines, markers) for e in f.by_type("IfcRelContainedInSpatialStructure")}
    with pytest.raises(ValueError):
        write_ifc(tmp_path, "none.ifc", ifc_options={"lines": False, "markers": False})


def test_the_ifc_recipe_writes_only_markers_or_only_lines():
    from PipeBottomLinesIfc import Patcher

    for content, wanted in (("markers", {u"VG Markör"}), ("lines", {"VG Linjer"}), ("", {"VG Linjer", u"VG Markör"})):
        patcher = Patcher(elbow_run(500.0).f, logging.getLogger("test"), "", content, json.dumps(KRAFT),
                          "Projekt.Systembeteckning")
        patcher.patch()
        assert {a.ObjectType for a in patcher.get_output().by_type("IfcAnnotation")} == wanted
    with pytest.raises(ValueError):
        Patcher(elbow_run(500.0).f, logging.getLogger("test"), "", "both")


def representations(annotation):
    return [(r.RepresentationIdentifier, r.RepresentationType) for r in annotation.Representation.Representations]


def test_ifc_has_a_line_per_run_and_markers_on_its_joints(tmp_path):
    counts, out = write_ifc(tmp_path)
    # Conduit, bend, conduit join into one run, like the DWG polyline; markers
    # on its start, the two bend ends (0.785 m apart) and its end.
    assert counts == (1, 4)
    lines = [a for a in out.by_type("IfcAnnotation") if a.ObjectType == "VG Linjer"]
    points = [a for a in out.by_type("IfcAnnotation") if a.ObjectType == "VG Markör"]
    assert len(lines) == 1 and len(points) == 4
    # A tube, no polyline: StreamBIM draws a Curve3D straight past the bends.
    assert representations(lines[0]) == [("Body", "Tessellation")]
    assert not out.by_type("IfcPolyline")
    assert lines[0].Name == LAYER
    assert [layer.Name for layer in out.by_type("IfcPresentationLayerAssignment")] == [LAYER]


def test_a_run_can_also_carry_its_polyline(tmp_path):
    _counts, out = write_ifc(tmp_path, ifc_options={"polyline": True})
    line = [a for a in out.by_type("IfcAnnotation") if a.ObjectType == "VG Linjer"][0]
    assert representations(line) == [("Annotation", "Curve3D"), ("Body", "Tessellation")]
    assert len(line.Representation.Representations[0].Items[0].Points) == 7
    _counts, out = write_ifc(tmp_path, "bare.ifc", ifc_options={"polyline": True, "tube_radius": 0.0})
    line = [a for a in out.by_type("IfcAnnotation") if a.ObjectType == "VG Linjer"][0]
    assert representations(line) == [("Annotation", "Curve3D")]


def test_a_run_needs_a_tube_or_its_polyline(tmp_path):
    with pytest.raises(ValueError, match="tube or its polyline"):
        write_ifc(tmp_path, ifc_options={"tube_radius": 0.0})


def test_the_tube_runs_along_the_line(tmp_path):
    _counts, out = write_ifc(tmp_path, ifc_options={"polyline": True})
    line = [a for a in out.by_type("IfcAnnotation") if a.ObjectType == "VG Linjer"][0]
    curve, tube = line.Representation.Representations
    vertices = [p.Coordinates for p in curve.Items[0].Points]
    assert tube.Items[0].Closed

    def distance(p):
        best = float("inf")
        for a, b in zip(vertices, vertices[1:]):
            ab, ap = _sub(b, a), _sub(p, a)
            t = max(0.0, min(1.0, sum(x * y for x, y in zip(ap, ab)) / sum(x * x for x in ab)))
            best = min(best, _length(_sub(p, tuple(a[i] + t * ab[i] for i in range(3)))))
        return best

    rings = tube.Items[0].Coordinates.CoordList[:-2]
    # 20 mm across: every ring corner 10 mm from the line, give or take the mitre at a bend.
    assert len(rings) == 7 * 8
    assert all(9.5 < distance(p) < 10.5 for p in rings)


def test_runs_and_markers_are_placed_in_world_coordinates(tmp_path):
    import ifcopenshell.util.placement

    m = elbow_run(500.0)
    site = ifcopenshell.api.root.create_entity(m.f, ifc_class="IfcSite", name="Site")
    turn = math.radians(-10.0)
    site.ObjectPlacement = m.placement((1000.0, 2000.0, 0.0), x=(math.cos(turn), math.sin(turn), 0.0))
    _counts, out = write_ifc(tmp_path, m=m, ifc_options={"polyline": True})
    # The site keeps its placement; the elements' world coordinates do not depend on it.
    out_site = ifcopenshell.util.placement.get_local_placement(out.by_type("IfcSite")[0].ObjectPlacement)
    assert out_site[:3, 3] == pytest.approx([1000.0, 2000.0, 0.0])
    assert out_site[:3, 0] == pytest.approx([math.cos(turn), math.sin(turn), 0.0])
    line = [a for a in out.by_type("IfcAnnotation") if a.ObjectType == "VG Linjer"][0]
    assert line.ObjectPlacement.PlacementRelTo is None
    first = line.Representation.Representations[0].Items[0].Points[0].Coordinates
    assert first == pytest.approx((0.0, 0.0, 975.0))
    placed = {tuple(int(round(v)) for v in ifcopenshell.util.placement.get_local_placement(a.ObjectPlacement)[:3, 3])
              for a in out.by_type("IfcAnnotation") if a.ObjectType == "VG Markör"}
    assert placed == {(75, 0, 975), (9925, 0, 975), (10500, 575, 975), (10500, 9925, 975)}


def run_of(lengths, kinds=None, rises=None, size=50.0):
    """One run along x: pieces of lengths, of kinds ("line" = pipe), each rising by rises."""
    from pipe_bottom_lines import chain
    from pipe_bottom_lines.ifc_geometry import Group, Piece

    group = Group(["611"], (255, 255, 0), None)
    pieces, x, z = [], 0.0, 10.0
    for number, length in enumerate(lengths):
        rise = rises[number] if rises else 0.0
        pieces.append(Piece([(x, 0.0, z), (x + length, 0.0, z + rise)], group, size, "piece%d" % number,
                            kinds[number] if kinds else "line"))
        x, z = x + length, z + rise
    runs = chain.chain_pieces([(p.points, (p.layer, p.color)) for p in pieces], tolerance=1e-9, collinear_tolerance=0.0)
    assert len(runs) == 1
    return pieces, runs[0]


def test_long_pipes_show_both_ends_on_themselves():
    from pipe_bottom_lines.ifc_output import run_markers

    # A 0.2 m pipe, a bend, a 0.834 m pipe, a bend, a 9 m pipe: the middle
    # pipe shows both its ends, each marker half a marker (75 mm) in from its joint.
    pieces, run = run_of([0.2, 0.086, 0.834, 0.086, 9.0], ["line", "arc", "line", "arc", "line"])
    xs = [spot.point[0] for spot in run_markers(run, pieces)]
    assert xs == pytest.approx([0.075, 0.361, 1.045, 1.281, 10.131])


def test_a_cluster_of_short_fittings_gets_no_markers():
    from pipe_bottom_lines.ifc_output import run_markers

    # Two 2 m pipes with five 50 mm fittings climbing 20 mm each between them.
    pieces, run = run_of([2.0] + [0.05] * 5 + [2.0], ["line"] + ["arc"] * 5 + ["line"],
                         [0.0] + [0.02] * 5 + [0.0])
    xs = [spot.point[0] for spot in run_markers(run, pieces)]
    assert xs == pytest.approx([0.075, 1.925, 2.325, 4.175])


def test_short_pieces_get_markers_no_closer_than_the_spacing():
    from pipe_bottom_lines.ifc_output import run_markers

    # A long conduit bend cut into 0.2 m segments, 4 m in all.
    pieces, run = run_of([0.2] * 20, ["bend"] * 20)
    xs = [spot.point[0] for spot in run_markers(run, pieces)]
    assert xs[0] == pytest.approx(0.075) and xs[-1] == pytest.approx(3.925)
    assert all(b - a >= 0.5 - 1e-9 for a, b in zip(xs, xs[1:]))
    assert 6 <= len(xs) <= 8


def test_spacing_zero_marks_every_joint():
    from pipe_bottom_lines.ifc_output import run_markers

    pieces, run = run_of([0.2, 0.05, 0.2], ["line", "arc", "line"])
    xs = [spot.point[0] for spot in run_markers(run, pieces, spacing=0)]
    assert xs == pytest.approx([0.0, 0.2, 0.25, 0.45])


def test_markers_are_bigger_than_their_pipe():
    from pipe_bottom_lines.ifc_geometry import Group, Piece
    from pipe_bottom_lines.ifc_output import marker_width

    group = Group(["611"], (255, 255, 0), None)
    assert marker_width(Piece([], group, 160.0, "a", "line")) == pytest.approx(0.21)
    assert marker_width(Piece([], group, 50.0, "a", "line")) == pytest.approx(0.15)
    assert marker_width(Piece([], group, 50.0, "a", "line"), 0.2) == pytest.approx(0.2)


def _closed_and_outward(points, triangles):
    """Every edge used once each way (watertight, consistently wound) and a
    positive volume (wound outwards)."""
    import numpy
    from collections import Counter
    edges = Counter((p, q) for a, b, c in triangles for p, q in ((a, b), (b, c), (c, a)))
    watertight = all(k == 1 and edges.get((q, p)) == 1 for (p, q), k in edges.items())
    pts = numpy.array(points, dtype=float)
    volume = sum(numpy.dot(pts[a], numpy.cross(pts[b], pts[c])) for a, b, c in triangles) / 6.0
    return watertight, volume


def test_every_glyph_is_one_watertight_solid():
    from pipe_bottom_lines import font
    holes = {"0": 1, "4": 1, "6": 1, "8": 2, "9": 1}
    for char in "0123456789+-.":
        outline, advance = font.glyph_outline(char)
        assert outline.is_valid and len(outline.interiors) == holes.get(char, 0), char
        minx, miny, maxx, maxy = outline.bounds
        assert minx >= 0.0 and maxx <= advance + 1e-9 and miny >= 0.0 and maxy <= 1.0 + 1e-9, char
        points, triangles, _advance = font.glyph_prism(char)
        watertight, volume = _closed_and_outward(points, triangles)
        # A solid of unit relief: its volume is its outline's area.
        assert watertight and volume == pytest.approx(outline.area), char
    # Digits stand on the baseline and reach the cap line.
    for char in "0123456789":
        assert font.glyph_outline(char)[0].bounds[1] == pytest.approx(0.0, abs=0.01), char
        assert font.glyph_outline(char)[0].bounds[3] == pytest.approx(1.0, abs=0.01), char
    assert font.glyph_prism("x") is None


def test_markers_point_out_of_the_pipe_with_the_level_set_into_their_plate():
    import numpy
    from pipe_bottom_lines import font
    from pipe_bottom_lines.ifc_output import TEXT_RELIEF, TEXT_SINK, MarkerSpot, marker_shapes, reading_direction

    direction = numpy.array([-1.0, 0.0, -0.1]) / numpy.linalg.norm([-1.0, 0.0, -0.1])
    out = numpy.array([-0.1, 0.0, 1.0]) / numpy.linalg.norm([-0.1, 0.0, 1.0])
    spot = MarkerSpot((5.0, 2.0, 12.746), direction, 0.15, out)
    pyramid, text = marker_shapes(spot)
    corners = [numpy.array(c) for c in pyramid[0]]
    # The tip on the point; the plate 150 mm straight out of the sloping pipe, 150 mm square.
    assert numpy.allclose(corners[0], 0.0)
    for corner in corners[1:]:
        assert corner.dot(out) == pytest.approx(0.15)
    assert numpy.linalg.norm(corners[1] - corners[2]) == pytest.approx(0.15)
    # "+12.75", one closed solid per glyph, its base just inside the plate and
    # its face 1 mm out of it, within its square, reading along the pipe.
    along = reading_direction(direction)
    assert along[0] > 0
    assert len(text) == 6
    side = numpy.cross(out, along)
    every = [numpy.array(c) for glyph, _t in text for c in glyph]
    height = max(c.dot(side) for c in every) - min(c.dot(side) for c in every)
    assert height == pytest.approx(0.86 * 0.15 / font.text_prisms("+12.75")[1], rel=0.01)
    assert TEXT_RELIEF == pytest.approx(0.001)
    starts = []
    for glyph, triangles in text:
        watertight, volume = _closed_and_outward(glyph, triangles)
        assert watertight and volume > 0
        levels = sorted({round(numpy.array(c).dot(out), 9) for c in glyph})
        assert levels == pytest.approx([0.15 - TEXT_SINK, 0.15 + TEXT_RELIEF])
        for corner in map(numpy.array, glyph):
            assert abs(corner.dot(along)) < 0.075 and abs(corner.dot(side)) < 0.075
        starts.append(min(numpy.array(c).dot(along) for c in glyph))
    assert starts == sorted(starts)


def test_a_riser_marker_points_away_from_the_run():
    import numpy
    from pipe_bottom_lines import chain
    from pipe_bottom_lines.ifc_geometry import Group, Piece
    from pipe_bottom_lines.ifc_output import run_markers

    group = Group(["611"], (255, 255, 0), None)
    pieces = [Piece([(0.0, 0.0, 10.0), (2.0, 0.0, 10.0)], group, 75.0, "a", "line"),
              Piece([(2.0, 0.0, 10.0), (2.0, 0.0, 11.0)], group, 75.0, "b", "line")]
    run = chain.chain_pieces([(p.points, (p.layer, p.color)) for p in pieces], tolerance=1e-9)[0]
    spots = run_markers(run, pieces)
    riser = [spot for spot in spots if spot.point[2] > 10.0]
    assert riser and all(numpy.allclose(spot.out, (1.0, 0.0, 0.0)) for spot in riser)


def riser_run(pieces_points, size=75.0):
    from pipe_bottom_lines import chain
    from pipe_bottom_lines.ifc_geometry import Group, Piece

    group = Group(["611"], (255, 255, 0), None)
    pieces = [Piece(points, group, size, "p%d" % number, "line") for number, points in enumerate(pieces_points)]
    return pieces, chain.chain_pieces([(p.points, (p.layer, p.color)) for p in pieces], tolerance=1e-9)[0]


def test_a_riser_line_runs_up_the_wall_the_run_turns_onto():
    import numpy
    from pipe_bottom_lines.ifc_output import run_markers, wall_run

    # Level along +x, then up a riser: its VG points are its axis (the
    # centreline dropped down its own length). The IFC line takes the riser's
    # +x wall instead, at the height of the pipe there, and so do its markers.
    pieces, run = riser_run([[(0.0, 0.0, 10.0), (2.0, 0.0, 10.0)], [(2.0, 0.0, 10.0), (2.0, 0.0, 11.0)]])
    points, shown = wall_run(run, pieces)
    assert points[0] == pytest.approx((0.0, 0.0, 10.0))
    assert points[-1] == pytest.approx((2.0375, 0.0, 11.0375))
    assert shown[1].points[-1] == pytest.approx((2.0375, 0.0, 11.0375))
    assert pieces[1].points[-1] == (2.0, 0.0, 11.0)
    riser = [spot for spot in run_markers(run, shown) if spot.point[2] > 10.1]
    # (The bare corner leans the riser's foot by its 45° wall; a real elbow does not.)
    assert riser and all(spot.point[0] == pytest.approx(2.0375, abs=0.001) for spot in riser)
    assert all(numpy.allclose(spot.out, (1.0, 0.0, 0.0)) for spot in riser)


def test_a_run_down_a_riser_takes_the_wall_away_from_where_it_goes():
    from pipe_bottom_lines.ifc_output import wall_run

    pieces, run = riser_run([[(0.0, 0.0, 11.0), (0.0, 0.0, 10.0)], [(0.0, 0.0, 10.0), (2.0, 0.0, 10.0)]])
    points, _shown = wall_run(run, pieces)
    ends = sorted([points[0], points[-1]], key=lambda p: p[2])
    assert ends[0] == pytest.approx((2.0, 0.0, 10.0))
    assert ends[1] == pytest.approx((-0.0375, 0.0, 11.0375))


def test_level_and_gently_sloping_lines_stay_where_the_dwg_has_them():
    from pipe_bottom_lines.ifc_output import wall_run

    # 1:20 and level: the IFC line is the DWG's, point for point.
    pieces, run = riser_run([[(0.0, 0.0, 10.0), (2.0, 0.0, 10.1)], [(2.0, 0.0, 10.1), (2.0, 3.0, 10.1)]])
    points, shown = wall_run(run, pieces)
    assert points == [tuple(p) for p in run.points]
    assert [p.points for p in shown] == [p.points for p in pieces]


def three_runs_at_one_level(offsets):
    from pipe_bottom_lines import chain
    from pipe_bottom_lines.ifc_geometry import Group, Piece

    group = Group(["611"], (255, 255, 0), None)
    pieces = [Piece(points, group, 50.0, "piece%d" % number, "line") for number, points in enumerate(offsets)]
    runs = chain.chain_pieces([(p.points, (p.layer, p.color)) for p in pieces], tolerance=1e-9)
    return pieces, runs


def test_runs_side_by_side_keep_their_own_markers():
    from pipe_bottom_lines import ifc_output

    # Two conduits 0.2 m apart at one level: each run shows its own ends.
    pieces, runs = three_runs_at_one_level([[(0.0, 0.0, 10.0), (3.0, 0.0, 10.0)], [(0.0, 0.2, 10.0), (3.0, 0.2, 10.0)]])
    assert len(runs) == 2
    _out, _lines, markers = ifc_output.build(elbow_run(500.0).f, pieces, runs)
    assert markers == 4


def test_runs_meeting_at_one_point_show_it_once():
    from pipe_bottom_lines import ifc_output

    # A branch, a marker on every joint: the straight run and the branch off
    # its middle both have one on that point, shown once.
    pieces, runs = three_runs_at_one_level([[(0.0, 0.0, 10.0), (1.0, 0.0, 10.0)], [(1.0, 0.0, 10.0), (2.0, 0.0, 10.0)],
                                            [(1.0, 0.0, 10.0), (1.0, 1.0, 10.0)]])
    assert len(runs) == 2
    _out, _lines, markers = ifc_output.build(elbow_run(500.0).f, pieces, runs, marker_spacing=0)
    assert markers == 4


def test_ifc_markers_carry_the_vg_level_and_point_down_onto_it(tmp_path):
    import ifcopenshell.util.element
    import ifcopenshell.util.placement

    _counts, out = write_ifc(tmp_path)
    # Half a marker (75 mm) in from each conduit end, at the VG level.
    expected = {(75, 0, 975), (9925, 0, 975), (10500, 575, 975), (10500, 9925, 975)}
    placed = set()
    for marker in [a for a in out.by_type("IfcAnnotation") if a.ObjectType == "VG Markör"]:
        assert marker.Name == "VG +0.975"
        psets = ifcopenshell.util.element.get_psets(marker)
        assert {name: {k: v for k, v in props.items() if k != "id"} for name, props in psets.items()} == {
            u"MÅTT": {"+VG": "+0.98", u"Höjd UK Plushöjd": pytest.approx(975.0)}}
        origin = ifcopenshell.util.placement.get_local_placement(marker.ObjectPlacement)[:3, 3]
        placed.add(tuple(int(round(v)) for v in origin))
        pyramid, text = marker.Representation.Representations[0].Items
        colours = [tuple(round(c * 255) for c in (s.Styles[0].Styles[0].SurfaceColour.Red,
                                                     s.Styles[0].Styles[0].SurfaceColour.Green,
                                                     s.Styles[0].Styles[0].SurfaceColour.Blue))
                   for s in (pyramid.StyledByItem[0], text.StyledByItem[0])]
        assert colours == [(255, 255, 0), (0, 0, 0)]
        # Tip on the point, a 150 mm square plate 150 mm straight up (the conduit is level).
        corners = [tuple(round(v, 3) for v in c) for c in pyramid.Coordinates.CoordList]
        assert corners[0] == (0.0, 0.0, 0.0)
        assert {c[2] for c in corners[1:]} == {150.0}
        assert max(c[0] for c in corners) - min(c[0] for c in corners) == pytest.approx(150.0)
        assert max(c[1] for c in corners) - min(c[1] for c in corners) == pytest.approx(150.0)
    assert placed == expected


def test_a_run_and_its_markers_share_a_description(tmp_path):
    _counts, out = write_ifc(tmp_path)
    annotations = out.by_type("IfcAnnotation")
    assert {a.Description for a in annotations} == {u"611 - Markrör KRAFT - 50mm #1 (+0.98 → +0.98)"}
    assert {a.ObjectType for a in annotations} == {"VG Linjer", "VG Markör"}


def test_ifc_lines_carry_their_heights_diameter_and_the_model_colour(tmp_path):
    import ifcopenshell.util.element

    # No colour rule: the colour comes from the model.
    _counts, out = write_ifc(tmp_path, color_rules=[])
    for line in [a for a in out.by_type("IfcAnnotation") if a.ObjectType == "VG Linjer"]:
        psets = ifcopenshell.util.element.get_psets(line)
        assert {name: {k: v for k, v in props.items() if k != "id"} for name, props in psets.items()} == {
            "VG": {"VG level start": "+0.975", "VG level end": "+0.975", "Diameter": "50 mm"}}
        # The conduit's "Plastic - Yellow", also on the elbow, which has no colour of its own.
        colour = line.Representation.Representations[0].Items[0].StyledByItem[0].Styles[0].Styles[0].SurfaceColour
        assert (round(colour.Red * 255), round(colour.Green * 255), round(colour.Blue * 255)) == (234, 234, 0)


def test_ifc_run_properties_can_go_in_another_set_or_none(tmp_path):
    import ifcopenshell.util.element

    cases = (("Ledningar", {"Ledningar": {"VG level start": "+0.975", "VG level end": "+0.975", "Diameter": "50 mm"}}),
             (u"Entreprenör:CC_", {u"Entreprenör": {"CC_VG level start": "+0.975", "CC_VG level end": "+0.975",
                                                   "CC_diameter": "50 mm"}}),
             (None, {}))
    for number, (run_pset, wanted) in enumerate(cases):
        path = "pset-%d.ifc" % number
        _counts, out = write_ifc(tmp_path, path, color_rules=[], ifc_options={"run_pset": run_pset})
        for line in [a for a in out.by_type("IfcAnnotation") if a.ObjectType == "VG Linjer"]:
            psets = ifcopenshell.util.element.get_psets(line)
            assert {name: {k: v for k, v in props.items() if k != "id"} for name, props in psets.items()} == wanted
        text = (tmp_path / path).read_text(encoding="utf-8", errors="replace")
        assert ("CC_" in text) == (number == 1)


def test_ifc_recipe_run_pset_argument():
    from PipeBottomLinesIfc import Patcher

    m = elbow_run(500.0)
    base = ["", "lines", "", ""]
    for value, wanted in (("", "VG"), ("VG", "VG"), ("Ledningar", "Ledningar"), ("A:CC_", "A:CC_"), ("none", None),
                          ("None", None)):
        assert Patcher(m.f, logging.getLogger("test"), *(base + [value])).run_pset == wanted
    assert Patcher(m.f, logging.getLogger("test"), *base).run_pset == "VG"


def test_ifc_recipe_dash_means_an_empty_argument():
    from PipeBottomLinesIfc import Patcher

    m = elbow_run(500.0)
    dashed = Patcher(m.f, logging.getLogger("test"), *(["-"] * 9))
    empty = Patcher(m.f, logging.getLogger("test"), *([""] * 9))
    for name in ("selector", "content", "color_rules", "group_properties", "run_pset", "offset", "rotation",
                 "in_ground"):
        assert repr(getattr(dashed, name)) == repr(getattr(empty, name)), name
    assert dashed.content == "all" and dashed.run_pset == "VG" and dashed.long_bend_min == 0
    assert dashed.selector is None and dashed.in_ground
    assert dashed.offset == (0.0, 0.0, 0.0) and dashed.rotation == 0.0


def test_a_colour_rule_wins_over_the_model_colour(tmp_path):
    _counts, out = write_ifc(tmp_path)
    colours = {tuple(round(c * 255) for c in (s.Styles[0].SurfaceColour.Red, s.Styles[0].SurfaceColour.Green,
                                              s.Styles[0].SurfaceColour.Blue))
               for s in out.by_type("IfcSurfaceStyle") if s.Name.startswith("VG-linje")}
    assert colours == {(255, 255, 0)}


def test_markers_on_half_millimetre_points_get_unique_guids():
    from pipe_bottom_lines import chain, ifc_output
    from pipe_bottom_lines.ifc_geometry import Group, Piece

    # A coordinate where x / 0.001 rounds down and x * 1000 up, and a second
    # end point just above it: two markers in two merge cells that a GlobalId
    # taken from x * 1000 would give the same id.
    x = next(x for x in ((n + 0.5) / 1000.0 for n in range(100000)) if round(x / 0.001) != round(x * 1000.0))
    group = Group(["611"], (255, 255, 0), None)
    pieces = [Piece([(px, 5.0, 12.0), (px, 6.0, 12.0)], group, 50.0, "piece%d" % number, "line")
              for number, px in enumerate((x, x + 1e-7))]
    runs = chain.chain_pieces([(p.points, (p.layer, p.color)) for p in pieces], tolerance=1e-9)
    out, _lines, markers = ifc_output.build(elbow_run(500.0).f, pieces, runs)
    assert markers == 4
    guids = [entity.GlobalId for entity in out.by_type("IfcRoot")]
    assert len(guids) == len(set(guids))


def test_ifc_is_valid_and_its_guids_are_stable(tmp_path):
    import ifcopenshell.validate

    model = elbow_run(500.0)
    _counts, first = write_ifc(tmp_path, "a.ifc", model)
    _counts, second = write_ifc(tmp_path, "b.ifc", model)
    logger = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(first, logger)
    assert logger.statements == []
    assert sorted(e.GlobalId for e in first.by_type("IfcRoot")) == sorted(e.GlobalId for e in second.by_type("IfcRoot"))


def test_transform_turns_counter_clockwise_then_offsets():
    moved = export.transform([(10.0, 0.0, 1.0), (0.0, 0.0, 0.0)], 90.0, (1000.0, 2000.0, 10.0))
    assert moved[0] == pytest.approx((1000.0, 2010.0, 11.0))
    assert moved[1] == pytest.approx((1000.0, 2000.0, 10.0))


def needs_dwg():
    pytest.importorskip("pythonnet")
    if not os.path.isfile(os.environ.get("ACADSHARP_DLL", "/opt/acadsharp/ACadSharp.dll")):
        pytest.skip("ACadSharp.dll not available")


def test_recipe_moves_the_dwg(tmp_path):
    needs_dwg()
    from PipeBottomLinesDwg import Patcher
    from pipe_bottom_lines import dwg

    patcher = Patcher(elbow_run(500.0).f, logging.getLogger("test"), "", json.dumps(KRAFT),
                      "Projekt.Systembeteckning", "1000", "2000", "10", "90")
    patcher.patch()
    target = tmp_path / "vg.dwg"
    os.replace(patcher.get_output(), target)
    assert patcher.get_summary()["rotation_deg"] == 90.0

    from ACadSharp.Entities import Polyline3D
    from ACadSharp.IO import DwgReader

    dwg.load()
    document = DwgReader.Read(str(target))
    polyline = [e for e in document.Entities if isinstance(e, Polyline3D)][0]
    ends = {tuple(round(c, 6) for c in (v.Location.X, v.Location.Y, v.Location.Z))
            for v in list(polyline.Vertices)[::6]}
    # (0, 0, 0.975) and (10.5, 10, 0.975), turned 90 degrees and moved.
    assert ends == {(1000.0, 2000.0, 10.975), (990.0, 2010.5, 10.975)}


def test_recipe_writes_a_dwg(tmp_path):
    needs_dwg()
    from PipeBottomLinesDwg import Patcher
    from pipe_bottom_lines import dwg

    patcher = Patcher(elbow_run(500.0).f, logging.getLogger("test"), "", json.dumps(KRAFT), "Projekt.Systembeteckning")
    patcher.patch()
    target = tmp_path / "vg.dwg"
    os.replace(patcher.get_output(), target)
    assert patcher.get_summary()["polylines"] == 1

    from ACadSharp.Entities import Polyline3D
    from ACadSharp.IO import DwgReader

    dwg.load()
    document = DwgReader.Read(str(target))
    polylines = [e for e in document.Entities if isinstance(e, Polyline3D)]
    assert [p.Layer.Name for p in polylines] == [LAYER]
    assert len(list(polylines[0].Vertices)) == 7


def test_the_dwg_opens_on_its_lines(tmp_path):
    needs_dwg()
    from PipeBottomLinesDwg import Patcher
    from pipe_bottom_lines import dwg

    # Moved far from the origin, the way a SWEREF DWG is.
    patcher = Patcher(elbow_run(500.0).f, logging.getLogger("test"), "", json.dumps(KRAFT),
                      "Projekt.Systembeteckning", "100000", "6100000", "0")
    patcher.patch()
    target = tmp_path / "vg.dwg"
    os.replace(patcher.get_output(), target)

    from ACadSharp.Entities import Polyline3D
    from ACadSharp.IO import DwgReader

    dwg.load()
    document = DwgReader.Read(str(target))
    vertices = [(v.Location.X, v.Location.Y, v.Location.Z)
                for e in document.Entities if isinstance(e, Polyline3D) for v in e.Vertices]
    low = [min(v[axis] for v in vertices) for axis in range(3)]
    high = [max(v[axis] for v in vertices) for axis in range(3)]
    header = document.Header
    ext_min, ext_max = header.ModelSpaceExtMin, header.ModelSpaceExtMax
    assert (ext_min.X, ext_min.Y, ext_min.Z) == pytest.approx(low)
    assert (ext_max.X, ext_max.Y, ext_max.Z) == pytest.approx(high)
    assert low[0] > 99000 and low[1] > 6099000
    view = [v for v in document.VPorts if v.Name == "*Active"][0]
    assert (view.Center.X, view.Center.Y) == pytest.approx(((low[0] + high[0]) / 2, (low[1] + high[1]) / 2))
    # The whole run in view: 10.5 m by 10.5 m with a margin.
    assert view.ViewHeight == pytest.approx(max(high[1] - low[1], high[0] - low[0]) * 1.05)


def test_ifc_recipe_returns_the_annotations_as_its_output(tmp_path):
    import ifcopenshell.validate
    from PipeBottomLinesIfc import Patcher

    patcher = Patcher(elbow_run(500.0).f, logging.getLogger("test"), "", "", json.dumps(KRAFT),
                      "Projekt.Systembeteckning")
    patcher.patch()
    out = patcher.get_output()
    assert isinstance(out, ifcopenshell.file)
    lines = [a for a in out.by_type("IfcAnnotation") if a.ObjectType == "VG Linjer"]
    assert [line.Name for line in lines] == [LAYER]
    assert representations(lines[0]) == [("Body", "Tessellation")]
    summary = patcher.get_summary()
    assert (summary["ifc_lines"], summary["ifc_markers"]) == (1, 4)
    assert summary["rotation_deg"] == 0.0 and summary["offset_m"] == [0.0, 0.0, 0.0]
    logger = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(out, logger)
    assert logger.statements == []


def test_ifc_recipe_options_in_n8n_order():
    from PipeBottomLinesIfc import Patcher

    patcher = Patcher(elbow_run(500.0).f, logging.getLogger("test"), "IfcCableCarrierSegment", "lines",
                      json.dumps(KRAFT), "Projekt.Systembeteckning", "VG", "1", "2", "3", "90")
    assert patcher.selector == "IfcCableCarrierSegment" and not patcher.in_ground
    assert patcher.content == "lines" and patcher.run_pset == "VG"
    assert [rule.name for rule in patcher.color_rules] == [u"Markrör KRAFT"]
    assert patcher.group_properties == ["Projekt.Systembeteckning"]
    assert patcher.offset == (1.0, 2.0, 3.0) and patcher.rotation == 90.0


def test_ifc_recipe_fixed_options():
    from PipeBottomLinesIfc import Patcher

    patcher = Patcher(elbow_run(500.0).f, logging.getLogger("test"))
    assert patcher.marker_size == pytest.approx(0.15)
    assert patcher.marker_spacing == pytest.approx(0.5)
    assert patcher.tube_radius == pytest.approx(0.01)
    assert not patcher.polyline
    assert (patcher.long_bend_min, patcher.bend_segment) == (0.0, pytest.approx(0.05))
    assert patcher.include_connected_fittings
    with pytest.raises(TypeError):
        Patcher(elbow_run(500.0).f, logging.getLogger("test"), *([""] * 10))


def test_a_selector_draws_what_it_picks_in_the_ground_or_not():
    from PipeBottomLinesIfc import Patcher

    # The floor is at +500, so the conduit at +1000 is not in the ground.
    m = Model(floor=500.0)
    m.conduit("Conduit with Fittings:SRN 50 - Gul:1", (0, 0, 1000), (10000, 0, 1000))
    unselected = Patcher(m.f, logging.getLogger("test"))
    assert unselected.in_ground
    with pytest.raises(ValueError, match="No pipe-bottom lines"):
        unselected.patch()
    selected = Patcher(m.f, logging.getLogger("test"), "IfcCableCarrierSegment")
    assert not selected.in_ground
    selected.patch()
    assert selected.get_summary()["ifc_lines"] == 1


def test_ifc_recipe_moves_runs_and_markers(tmp_path):
    import ifcopenshell.util.element
    import ifcopenshell.util.placement
    from PipeBottomLinesIfc import Patcher

    def run(*move):
        m = elbow_run(500.0)
        context = [c for c in m.f.by_type("IfcGeometricRepresentationContext") if c.is_a() == "IfcGeometricRepresentationContext"][0]
        context.TrueNorth = m.f.create_entity("IfcDirection", DirectionRatios=[0.0, 1.0])
        m.f.create_entity("IfcMapConversion", SourceCRS=context,
                          TargetCRS=m.f.create_entity("IfcProjectedCRS", Name="EPSG:3006"),
                          Eastings=100.0, Northings=200.0, OrthogonalHeight=0.0)
        patcher = Patcher(m.f, logging.getLogger("test"), "", "", json.dumps(KRAFT), "Projekt.Systembeteckning",
                          "VG", *move)
        patcher.patch()
        return patcher.get_output()

    def markers(out):
        return dict((a.GlobalId, (a.Name, tuple(int(round(v)) for v in
                     ifcopenshell.util.placement.get_local_placement(a.ObjectPlacement)[:3, 3])))
                    for a in out.by_type("IfcAnnotation") if a.ObjectType == u"VG Markör")

    still, moved = run(), run("1000", "2000", "10", "90")
    before, after = markers(still), markers(moved)
    # Same markers (GlobalIds), half a marker (75 mm) in from the run's ends.
    assert set(before) == set(after)
    assert set(before.values()) >= {(u"VG +0.975", (75, 0, 975)), (u"VG +0.975", (10500, 9925, 975))}
    # Turned 90 degrees, then moved 1000, 2000, 10 m; the level moves with them.
    assert set(after.values()) >= {(u"VG +10.975", (1000000, 2000075, 10975)),
                                   (u"VG +10.975", (990075, 2010500, 10975))}
    line = [a for a in moved.by_type("IfcAnnotation") if a.ObjectType == "VG Linjer"][0]
    assert ifcopenshell.util.element.get_psets(line)["VG"]["VG level start"] == "+10.975"
    # The source's map conversion only where the runs stay in its coordinates; north turns with them.
    assert len(still.by_type("IfcMapConversion")) == 1 and not moved.by_type("IfcMapConversion")
    north = [c for c in moved.by_type("IfcGeometricRepresentationContext") if c.TrueNorth][0].TrueNorth
    assert list(north.DirectionRatios) == pytest.approx([-1.0, 0.0])


def test_lines_and_markers_are_typed_apart(tmp_path):
    _counts, out = write_ifc(tmp_path)
    types = {t.Name: sorted(a.ObjectType for rel in t.Types for a in rel.RelatedObjects)
             for t in out.by_type("IfcTypeProduct")}
    assert types == {u"VG Linjer": [u"VG Linjer"], u"VG Markör": [u"VG Markör"] * 4}
    assert {t.ApplicableOccurrence for t in out.by_type("IfcTypeProduct")} == {"IfcAnnotation"}


def _distance_to_polyline(p, points):
    import numpy
    p = numpy.array(p)
    best = float("inf")
    for a, b in zip(points, points[1:]):
        a, b = numpy.array(a), numpy.array(b)
        d = b - a
        t = 0.0 if not d.dot(d) else max(0.0, min(1.0, (p - a).dot(d) / d.dot(d)))
        best = min(best, numpy.linalg.norm(p - (a + t * d)))
    return best


def test_a_riser_without_level_pipes_stays_on_one_wall():
    import numpy
    from pipe_bottom_lines.ifc_output import run_markers, wall_run

    # A bend and a pipe going straight up, leaning a few hundredths of a
    # millimetre the way exported risers do, and nothing level in the run:
    # every point keeps one wall, and the markers sit on the line.
    pieces, run = riser_run([[(0.0, 0.0, 10.0), (1e-5, 0.0, 10.087)],
                             [(1e-5, 0.0, 10.087), (2e-5, 1e-5, 11.0)]], size=110.0)
    points, shown = wall_run(run, pieces)
    offsets = [numpy.subtract(moved, original)[:2] for moved, original in zip(points, run.points)]
    assert all(numpy.linalg.norm(o) == pytest.approx(0.055, abs=1e-4) for o in offsets)
    assert all(o.dot(offsets[0]) > 0.99 * 0.055 ** 2 for o in offsets)
    for spot in run_markers(run, shown):
        assert _distance_to_polyline(spot.point, points) < 1e-4
        assert numpy.isfinite(spot.out).all()


def test_markers_show_the_level_of_the_joint_they_mark():
    from pipe_bottom_lines import chain
    from pipe_bottom_lines.ifc_geometry import Group, Piece
    from pipe_bottom_lines.ifc_output import run_markers

    group = Group(["611"], (255, 255, 0), None)
    # 2 m at 20 per mille from +12.744: half a marker in (75 mm) is 1.5 mm higher,
    # which would round to +12.75; the marker shows its joint, +12.744.
    pieces = [Piece([(0.0, 0.0, 12.744), (2.0, 0.0, 12.784)], group, 75.0, "a", "line")]
    run = chain.chain_pieces([(p.points, (p.layer, p.color)) for p in pieces], tolerance=1e-9)[0]
    first, last = run_markers(run, pieces)
    assert first.point[0] == pytest.approx(0.075, abs=1e-4)
    assert first.point[2] == pytest.approx(12.7455, abs=1e-5)
    assert first.level == pytest.approx(12.744)
    assert last.level == pytest.approx(12.784)


def test_on_a_steep_pipe_the_marker_sits_on_its_joint():
    from pipe_bottom_lines import chain
    from pipe_bottom_lines.ifc_geometry import Group, Piece
    from pipe_bottom_lines.ifc_output import run_markers

    group = Group(["611"], (255, 255, 0), None)
    # 10 % slope: half a marker in would be 7.5 mm higher than the joint.
    pieces = [Piece([(0.0, 0.0, 12.0), (2.0, 0.0, 12.2)], group, 75.0, "a", "line")]
    run = chain.chain_pieces([(p.points, (p.layer, p.color)) for p in pieces], tolerance=1e-9)[0]
    first, last = run_markers(run, pieces)
    assert first.point == pytest.approx((0.0, 0.0, 12.0))
    assert last.point == pytest.approx((2.0, 0.0, 12.2))
    assert (first.level, last.level) == pytest.approx((12.0, 12.2))


def test_ifc_markers_are_labelled_with_their_joint(tmp_path):
    import ifcopenshell.util.element
    from pipe_bottom_lines import chain, ifc_output
    from pipe_bottom_lines.ifc_geometry import Group, Piece

    group = Group(["611"], (255, 255, 0), None)
    pieces = [Piece([(0.0, 0.0, 12.744), (2.0, 0.0, 12.784)], group, 75.0, "a", "line")]
    runs = chain.chain_pieces([(p.points, (p.layer, p.color)) for p in pieces], tolerance=1e-9)
    out, _lines, markers = ifc_output.build(elbow_run(500.0).f, pieces, runs)
    labels = sorted((m.Name, ifcopenshell.util.element.get_psets(m)[u"MÅTT"]["+VG"])
                    for m in out.by_type("IfcAnnotation") if m.ObjectType == u"VG Markör")
    assert labels == [("VG +12.744", "+12.74"), ("VG +12.784", "+12.78")]


def test_the_ifc_recipe_follows_short_conduit_bends():
    from PipeBottomLinesIfc import Patcher

    # A 190 mm bend (298 mm along the arc) is one straight chord in the DWG,
    # 30 mm off the conduit at its middle; the IFC follows the arc.
    for args in ((), ("Projekt.Systembeteckning", "VG")):
        patcher = Patcher(elbow_run(190.0).f, logging.getLogger("test"), "", "", json.dumps(KRAFT), *args)
        assert (patcher.long_bend_min, patcher.bend_segment) == (0.0, pytest.approx(0.05))
        patcher.patch()
        assert patcher.report.short_bends == 0 and patcher.report.long_bends == 1


def y_branch(branch_mm=75.0, reducer=False, rings=True):
    """Conduit along X (110), a 45-degree Y at x 10100, the main on past it,
    and a branch conduit; with reducer, a 110/75 reducer between the Y and the
    main beyond (75)."""
    m = Model()
    s = math.sqrt(0.5)
    centre = (10100.0, 0.0, 1000.0)
    tip = (10100.0 + 150.0 * s, 150.0 * s, 1000.0)
    a, _a0, a1 = m.conduit("SRN 110 Gul:1", (0, 0, 1000), (10000, 0, 1000), diameter=110.0)
    y, (y0, y1, y2) = m.fitting(
        "SRN Grenrör Gul:2", centre,
        [((10000, 0, 1000), (-1.0, 0.0, 0.0)), ((10200, 0, 1000), (1.0, 0.0, 0.0)), (tip, (s, s, 0.0))],
        rings=(60.5, 60.5, 60.5 if branch_mm == 110.0 else 41.25) if rings else None)
    branch, b0, _b1 = m.conduit("SRN Gul:3", tip, (tip[0] + 5000 * s, tip[1] + 5000 * s, 1000), diameter=branch_mm)
    m.connect(a1, y0)
    m.connect(y2, b0)
    if reducer:
        r, (r0, r1) = m.fitting("SRN Reduktion Gul:4", (10250, 0, 1000),
                                [((10200, 0, 1000), (-1.0, 0.0, 0.0)), ((10300, 0, 1000), (1.0, 0.0, 0.0))],
                                rings=(60.0, 42.5) if rings else None)
        c, c0, _c1 = m.conduit("SRN 75 Gul:5", (10300, 0, 1000), (20000, 0, 1000), diameter=75.0)
        m.connect(y1, r0)
        m.connect(r1, c0)
    else:
        c, c0, _c1 = m.conduit("SRN 110 Gul:5", (10200, 0, 1000), (20000, 0, 1000), diameter=110.0)
        m.connect(y1, c0)
    return m, y


def legs(report, fitting):
    return [p for p in report.vg_pieces if p.guid == fitting.GlobalId]


def test_a_y_branch_line_ends_on_the_outside_of_the_main():
    m, y = y_branch()
    (branch,) = [p for p in legs(build(m), y) if p.size_mm == 75.0]
    end = branch.points[-1]
    # Its line, 37.5 mm under the branch's centreline, reaches the 55 mm main
    # 40.2 mm from the main's axis, not at the Y's centre.
    assert math.hypot(end[1], end[2] - 1.0) == pytest.approx(0.055, abs=1e-6)
    assert end == pytest.approx((10.1 + 0.040232, 0.040232, 0.9625), abs=1e-5)


def test_a_y_branch_as_big_as_the_main_reaches_the_centre():
    m, y = y_branch(branch_mm=110.0)
    report = build(m)
    branch = [p for p in legs(report, y) if _length(_sub(p.points[0], (10.1, 0.0, 0.945))) > 0.01
              and p.points[0][1] > 0.01]
    assert len(branch) == 1 and branch[0].points[-1] == pytest.approx((10.1, 0.0, 0.945))


def test_a_fitting_on_a_reducer_takes_its_own_size_not_the_pipe_past_it():
    m, y = y_branch(reducer=True)
    report = build(m)
    sizes = sorted((p.size_mm, round(p.points[0][0], 2), round(p.points[-1][0], 2)) for p in legs(report, y))
    # Both main legs of the Y are 110: the leg on the reducer has the ring of
    # the leg on the 110 conduit, not the 75 conduit past the reducer.
    assert [s for s in sizes if s[0] == 110.0] == [(110.0, 10.0, 10.1), (110.0, 10.2, 10.1)]
    reducer = [p for p in report.vg_pieces if p.guid != y.GlobalId and p.kind == "line"
               and 10.2 <= p.points[0][0] <= 10.3 and 10.2 <= p.points[-1][0] <= 10.3]
    # The reducer's end on the Y has the Y's ring there: 110.
    ends = sorted((p.size_mm, round(q[0], 2), round(q[2], 4)) for p in reducer for q in (p.points[0], p.points[-1])
                  if round(q[0], 2) in (10.2, 10.3))
    assert ends == [(75.0, 10.3, 0.9625), (110.0, 10.2, 0.945)]


def test_without_a_body_a_fitting_on_a_fitting_takes_the_pipe_past_it():
    m, y = y_branch(reducer=True, rings=False)
    sizes = sorted(p.size_mm for p in legs(build(m), y))
    assert sizes == [75.0, 75.0, 110.0]


def _no_geometry(self):
    raise AssertionError("the recipe read the geometry before checking its requirements")


def test_dwg_recipe_without_acadsharp_stops_before_the_geometry(monkeypatch, tmp_path):
    import PipeBottomLinesDwg
    from pipe_bottom_lines import dwg

    monkeypatch.setattr(dwg, "_LOADED", [])
    monkeypatch.setenv("ACADSHARP_DLL", str(tmp_path / "ACadSharp.dll"))
    monkeypatch.setattr(PipeBottomLinesDwg.Patcher, "build_report", _no_geometry)
    patcher = PipeBottomLinesDwg.Patcher(elbow_run(500.0).f, logging.getLogger("test"), "", json.dumps(KRAFT))
    with pytest.raises(dwg.DwgUnavailable) as raised:
        patcher.patch()
    message = str(raised.value)
    assert str(tmp_path / "ACadSharp.dll") in message
    assert ".NET 8" in message and "PipeBottomLinesIfc" in message


def test_dwg_loader_names_a_missing_pythonnet(monkeypatch, tmp_path):
    from pipe_bottom_lines import dwg

    dll = tmp_path / "ACadSharp.dll"
    dll.write_bytes(b"")
    monkeypatch.setattr(dwg, "_LOADED", [])
    monkeypatch.setenv("ACADSHARP_DLL", str(dll))
    monkeypatch.setitem(sys.modules, "pythonnet", None)
    with pytest.raises(dwg.DwgUnavailable, match="pythonnet is not installed"):
        dwg.load()


def test_ifc_recipe_without_shapely_stops_before_the_geometry(monkeypatch):
    import pipe_bottom_lines
    from PipeBottomLinesIfc import Patcher

    monkeypatch.setitem(sys.modules, "shapely", None)
    for name in ("ifc_output", "font"):
        monkeypatch.delitem(sys.modules, "pipe_bottom_lines." + name, raising=False)
        monkeypatch.delattr(pipe_bottom_lines, name, raising=False)
    monkeypatch.setattr(Patcher, "build_report", _no_geometry)
    patcher = Patcher(elbow_run(500.0).f, logging.getLogger("test"), "", "", json.dumps(KRAFT))
    with pytest.raises(export.IfcUnavailable, match="shapely>=2"):
        patcher.patch()


def test_the_names_the_output_shows_come_from_labels():
    import ifcopenshell.util.element
    from PipeBottomLinesIfc import Patcher
    from pipe_bottom_lines import labels

    patcher = Patcher(elbow_run(500.0).f, logging.getLogger("test"), "", "", json.dumps(KRAFT))
    patcher.patch()
    out = patcher.get_output()
    annotations = out.by_type("IfcAnnotation")
    assert {a.ObjectType for a in annotations} == {labels.LINE_TYPE, labels.POINT_TYPE}
    assert {t.Name for t in out.by_type("IfcTypeProduct")} == {labels.LINE_TYPE, labels.POINT_TYPE}
    line = [a for a in annotations if a.ObjectType == labels.LINE_TYPE][0]
    assert line.Name.startswith(labels.LAYER_PREFIX + " - ")
    assert set(ifcopenshell.util.element.get_psets(line)[labels.RUN_PSET]) >= {
        labels.RUN_LEVEL_START, labels.RUN_LEVEL_END, labels.RUN_DIAMETER}
    marker = [a for a in annotations if a.ObjectType == labels.POINT_TYPE][0]
    assert marker.Name == labels.MARKER_NAME % "+0.975"
    assert set(ifcopenshell.util.element.get_psets(marker)[labels.LEVEL_PSET]) >= {labels.LEVEL_LABEL, labels.LEVEL_NUMBER}
    assert out.by_type("IfcSite")[0].Name == labels.SITE_NAME
