# -*- coding: utf-8 -*-
"""Bottom-of-pipe (VG, vattengång) pieces from the pipes, conduits and fittings of an IFC.

The IFC twin of geometry.py in the pyRevit VG-linjer tool, so a DWG written
from an IFC export matches the one the VG-linjer button writes from Revit.

Where Revit reads connectors, this reads IfcDistributionPort. A port's
placement gives the connector origin, its Z axis the connector direction,
and IfcRelConnectsPorts what sits on the other side. A pipe or conduit runs
between its two ports. Its outside diameter comes from the swept profile of
its body (a circle, or the polygon Revit writes for one), and failing that
from its CrossSectionArea quantity.

The rules are the ones in geometry.py:

- The pipe-bottom line is the outside bottom of the pipe: the centreline dropped
  straight down by half the outside diameter.
- A straight fitting or a tee becomes lines from each port to the fitting
  centre. An elbow becomes the circular arc between its two ports.
- A conduit bend (an SRN böj) has a short straight end at each port and a
  tighter arc between them: its line follows that, the arc read from the
  bend's body (unlike geometry.py, whose one port-to-port arc runs 20-36 mm
  inside it at the middle). Without a body it is the port-to-port arc.
- The branch leg of a tee or Y ends where its line reaches the outside of
  the main pipe, not inside it at the centre (unlike geometry.py).
- A conduit bend at least 300 mm long along its arc is cut into DWG segments
  of about 200 mm. A shorter one is one straight segment between its ends.
- Groups: with group properties, the first one an element has, else the
  first one found along its connected line. Without them (auto), or where
  the line has none, the line's system: its System Type / Systemtyp /
  System Classification, its IfcSystem, or its System Name, the element's
  own first, then the most common along the line. A line nothing names is
  a group of its own, "Ledning 1", "Ledning 2", ... numbered west to east.
  A line is everything connected port to port through pipes, conduits and
  fittings, above ground too; a walk along it stops at trays, terminals,
  equipment and chambers.
- A fitting leg takes its group and size from the pipe or conduit on that
  port, so a fitting never lands on another layer than its pipes. A port on
  another fitting takes the pipe of a port the same size: one of its own
  fitting's ports, else one of the other fitting's, judged by the ring each
  fitting's body has in the port's plane (unlike geometry.py, which takes the
  pipe past the other fitting, the wrong size past a reducer).

Where an element has no port with a placement (MagiCAD's IFC2X3 writes no
ports; Civil 3D's IFC4X3 writes them without placement), its ports come from
its body instead (MeshPort): the round rims of an open tube (MagiCAD's pipes
and fittings), else, on a pipe or conduit, the two end rings of a closed
straight prism (Civil 3D's pipes). Mesh ports of two elements that meet are
connected, and a pipe's outside diameter is its rings' when nothing else
gives one. A selected IfcBuildingElementProxy whose body is such a tube, at
least PROXY_PIPE_RATIO times as long as it is wide, is a pipe (a CAD drawing
turned IFC has nothing else); a well, a short fat cylinder, is not.

Everything here is in metres, in the IFC's world coordinates.
"""

import math
import re

import numpy

import ifcopenshell.geom
import ifcopenshell.util.element
import ifcopenshell.util.placement
import ifcopenshell.util.selector
import ifcopenshell.util.system
import ifcopenshell.util.unit

from pipe_bottom_lines import naming, palette
from pipe_bottom_lines.labels import LINE_LABEL
from pipe_bottom_lines.chain import arc_segment_count, tessellate_arc

MM = 0.001
MIN_LENGTH = 1.0 * MM
MIN_DIAMETER = 1.0 * MM
MAX_DIAMETER = 10.0
AXIS_TOL = 30.0 * MM
MAX_ARM = 15.24
MAX_CAP = 0.6096
PARALLEL_DOT = 0.995
ARC_CHORD_TOL = 1.0 * MM
ELBOW_FIT_TOL = 20.0 * MM
# Two connected ports closer than this are one point. Revit writes some
# sloped fittings' ports about 1 mm from the conduit port they connect to.
SNAP_TOL = 5.0 * MM
# A conduit bend at least this long along its arc is cut into DWG segments of
# about BEND_SEGMENT. A shorter conduit bend is one straight segment.
LONG_BEND_MIN = 300.0 * MM
BEND_SEGMENT = 200.0 * MM
# A fitting's ring at a port: its body's vertices this close to the port's
# plane. Two ports of one fitting whose bores differ by less than
# RING_MATCH_TOL are the same size (a MagiCAD Y's two 160 ends: 174.4 and
# 176.0 mm; the nearest other size, 1.5 mm more on a 32/40 reducer).
PORT_RING_SLAB = 1.0 * MM
RING_MATCH_TOL = 1.0 * MM
# A conduit bend's arc, read from its body: the bisector from the axis corner
# crosses the bend's outer and inner wall, which must be its diameter apart
# within this part of it; the centreline lies midway. Straight ends shorter
# than BEND_LEG_TOL (negative: an arc wider than the ports allow) are none.
BEND_WALL_TOL = 0.25
BEND_LEG_TOL = 2.0 * MM

# Where a line's system name is read from when no group property is given
# (auto): these properties, then its IfcSystem, then SYSTEM_NAME_PROPERTIES.
PIPE_SYSTEM_PROPERTIES = ("System Type", "Systemtyp", "System Classification")
SYSTEM_NAME_PROPERTIES = ("System Name", "SystemName", "Systemnamn")
# The elements a line runs through; a walk along it stops at anything else
# (trays, terminals, equipment, chambers), so two lines meeting at a tank stay two.
LINE_KINDS = ("pipe", "conduit", "pipe-fitting", "conduit-fitting")
# Words in a type, name, system or property value that put an element in the
# ground: "P2, PP-rör släta markavloppsrör", "X8, Kulvertrör", ...
GROUND_WORDS = (u"markavlopp", u"markrör", u"markledning", u"kulvert", u"i mark", u"under mark",
                u"undermark", u"bottenplatta", u"underplatta", u"under platta", u"nedgrävd")
# Ports Revit writes for piping and conduit connectors. Electrical (CABLE) and
# duct ports are not part of a run.
USABLE_PORT_TYPES = (None, "PIPE", "CABLECARRIER", "NOTDEFINED", "USERDEFINED")
# Ports read from a body (see MeshPort): mesh vertices closer than MESH_WELD
# are one; a rim or end ring is round, and flat, when its radii and its
# distances from its plane vary by at most ROUND_TOL of its radius and it has
# MESH_RING_MIN_POINTS corners; two elements' mesh ports within MESH_PORT_TOL
# are connected (MagiCAD's pipe and socket rims share their centre).
MESH_WELD = 0.1 * MM
ROUND_TOL = 0.05
MESH_RING_MIN_POINTS = 6
MESH_PORT_TOL = 2.0 * MM
# The kinds whose elements get mesh ports when they have no placed one.
MESH_PORT_KINDS = ("pipe", "conduit", "pipe-fitting", "cable-fitting")
# A proxy is a pipe when its body is a straight round tube at least this many
# times as long as its outside diameter.
PROXY_PIPE_RATIO = 10.0
TRAY_SEGMENT_TYPES = ("CABLETRAYSEGMENT", "CABLELADDERSEGMENT", "CABLETRUNKINGSEGMENT")


# ------------------------------------------------------------------ vectors

def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _mul(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _length(a):
    return math.sqrt(_dot(a, a))


def _distance(a, b):
    return _length(_sub(a, b))


def _unit(a):
    size = _length(a)
    if size < 1e-12:
        return None
    return (a[0] / size, a[1] / size, a[2] / size)


def _drop(point, depth):
    return (point[0], point[1], point[2] - depth)


def _mean(points):
    n = float(len(points))
    return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n, sum(p[2] for p in points) / n)


# ------------------------------------------------------------------- pieces

class Group(object):
    """What a piece belongs to: the layer name parts and the colour."""

    __slots__ = ("parts", "color", "color_from", "model_colour")

    def __init__(self, parts, color, color_from, model_colour=None):
        self.parts = tuple(parts)
        self.color = color
        self.color_from = color_from
        # The pipe's or conduit's own colour in the model, a vote for its system's colour.
        self.model_colour = model_colour


# Distinct colours handed out per system, in system name order, when a system
# has no colour rule and its pipes carry no colour of their own in the model.
SYSTEM_COLOURS = (
    (0, 114, 178), (213, 94, 0), (0, 158, 115), (204, 121, 167), (230, 159, 0), (86, 180, 233),
    (148, 103, 189), (140, 86, 75), (214, 39, 40), (23, 190, 207), (188, 189, 34), (31, 119, 180),
)


def is_real_colour(rgb):
    """A colour someone chose: not black, white or a grey (Revit's exports
    write pipe materials without a colour as black)."""
    return rgb is not None and max(rgb) - min(rgb) > 40


class Line(object):
    """A connected line: its member elements, the group property and system
    name most common along it (False until looked up), and its label when
    nothing names it."""

    __slots__ = ("members", "group_text", "system_text", "label")

    def __init__(self):
        self.members = []
        self.group_text = False
        self.system_text = False
        self.label = None


class Piece(object):
    """One VG polyline piece: points in metres and what it belongs to."""

    __slots__ = ("points", "group", "size_mm", "guid", "kind", "owner_guid")

    def __init__(self, points, group, size_mm, guid, kind, owner_guid=None):
        self.points = points
        self.group = group
        self.size_mm = size_mm
        # The element the piece is drawn from.
        self.guid = guid
        # "line", "arc" or "bend" (a long conduit bend cut into segments).
        self.kind = kind
        # The pipe or conduit whose group and size the piece takes: the
        # element itself, or for a fitting leg the pipe on that port.
        self.owner_guid = owner_guid or guid

    @property
    def layer(self):
        return naming.layer_name(self.group.parts, self.size_mm)

    @property
    def color(self):
        if self.group.color is not None:
            return self.group.color
        return palette.color_for_name(naming.layer_name(self.group.parts))


class Conn(object):
    def __init__(self, port, origin, axis):
        self.port = port
        self.origin = origin
        self.axis = axis
        # Outside radius in metres, and where it came from.
        self.radius = None
        self.source = None
        # The pipe or conduit on this port, when one was found.
        self.owner = None


class MeshPort(object):
    """A port read from an element's body, for an element without placed ports:
    its origin (the ring's centre), axis (out of the element) and the ring's
    radius, in metres. It stands in for an IfcDistributionPort."""

    PredefinedType = None

    def __init__(self, ident, element, origin, axis, radius):
        self._id = ident
        self.element = element
        self.origin = origin
        self.axis = axis
        self.radius = radius

    def id(self):
        return self._id


def _welded(verts, faces):
    """verts and faces with the vertices closer than MESH_WELD made one."""
    keys = numpy.round(verts / MESH_WELD).astype(numpy.int64)
    _keys, first, inverse = numpy.unique(keys, axis=0, return_index=True, return_inverse=True)
    return verts[first], numpy.asarray(inverse).reshape(-1)[faces]


def _round_ring(points, centre, axis):
    """The radius of points about the line through centre along axis, when
    they make a round ring square to it (see ROUND_TOL), else None."""
    offsets = points - centre
    along = offsets @ axis
    radial = numpy.linalg.norm(offsets - numpy.outer(along, axis), axis=1)
    radius = float(radial.mean())
    if radius * 2.0 < MIN_DIAMETER or len(points) < MESH_RING_MIN_POINTS:
        return None
    if float(radial.max() - radial.min()) > ROUND_TOL * radius:
        return None
    return radius


def mesh_rims(verts, faces):
    """[(centre, axis, radius)] of the round open rims of a mesh: the ends of
    an open tube, each axis pointing out of the body."""
    verts, faces = _welded(verts, faces)
    edges = numpy.sort(numpy.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
    edges = edges[edges[:, 0] != edges[:, 1]]
    if not len(edges):
        return []
    unique, counts = numpy.unique(edges, axis=0, return_counts=True)
    parent = {}

    def root(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in unique[counts == 1]:
        parent[root(int(a))] = root(int(b))
    loops = {}
    for vertex in list(parent):
        loops.setdefault(root(vertex), []).append(vertex)
    middle = verts.mean(axis=0)
    rims = []
    for members in loops.values():
        points = verts[members]
        centre = points.mean(axis=0)
        if len(points) < MESH_RING_MIN_POINTS:
            continue
        axis = numpy.linalg.svd(points - centre, full_matrices=False)[2][2]
        radius = _round_ring(points, centre, axis)
        if radius is None or float(numpy.abs((points - centre) @ axis).max()) > ROUND_TOL * radius:
            continue
        if float((centre - middle) @ axis) < 0:
            axis = -axis
        rims.append((centre, axis, radius))
    return rims


def _tube_ends(mesh):
    """The two ends of a straight or bent tube body (verts, faces): its two
    round rims when open, else its two prism end rings; or []."""
    ends = mesh_rims(*mesh)
    if len(ends) != 2:
        ends = prism_ends(_welded(*mesh)[0]) or []
    return ends


def prism_ends(verts):
    """[(centre, axis, radius)] of the two end rings of a closed straight round
    prism (a Civil 3D pipe: two rings of corners), axes out of the body, or None."""
    middle = verts.mean(axis=0)
    for direction in numpy.linalg.svd(verts - middle, full_matrices=False)[2]:
        along = (verts - middle) @ direction
        order = numpy.argsort(along)
        split = int(numpy.argmax(numpy.diff(along[order]))) + 1
        low, high = verts[order[:split]], verts[order[split:]]
        if len(low) != len(high):
            continue
        c0, c1 = low.mean(axis=0), high.mean(axis=0)
        length = float(numpy.linalg.norm(c1 - c0))
        if length < MIN_LENGTH:
            continue
        axis = (c1 - c0) / length
        r0, r1 = _round_ring(low, c0, axis), _round_ring(high, c1, axis)
        if r0 is None or r1 is None or abs(r0 - r1) > ROUND_TOL * max(r0, r1):
            continue
        return [(c0, -axis, r0), (c1, axis, r1)]
    return None


class ColorRule(object):
    """A named colour for the elements a selector matches, like a Revit view filter."""

    def __init__(self, name, rgb, selector):
        self.name = name
        self.rgb = rgb
        self.selector = selector
        self.ids = set()


def parse_color(value):
    """"#FFFF00", "FFFF00" or [255, 255, 0] -> (255, 255, 0)."""
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return tuple(int(c) for c in value)
    text = str(value or "").strip().lstrip("#")
    if not re.match(r"^[0-9A-Fa-f]{6}$", text):
        raise ValueError("Not a colour: %r" % (value,))
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


def parse_color_rules(rules):
    """[{"name", "color", "selector"}] -> [ColorRule], first match wins."""
    parsed = []
    for rule in rules or ():
        name = naming.clean_part(rule.get("name"))
        selector = (rule.get("selector") or "").strip()
        if not name or not selector:
            raise ValueError("A colour rule needs a name and a selector: %r" % (rule,))
        parsed.append(ColorRule(name, parse_color(rule.get("color")), selector))
    return parsed


# --------------------------------------------------------------- arc helper

def circle_through(start, end, mid):
    """The circular arc from start through mid to end.

    Returns (center, radius, x_axis, y_axis, sweep) with the arc running from
    angle 0 (start) to sweep around x_axis/y_axis, or None when the points are
    in a line.
    """
    ab = _sub(mid, start)
    ac = _sub(end, start)
    normal = _cross(ab, ac)
    nn = _dot(normal, normal)
    if nn < 1e-18:
        return None
    offset = _add(_mul(_cross(normal, ab), _dot(ac, ac)), _mul(_cross(ac, normal), _dot(ab, ab)))
    center = _add(start, _mul(offset, 1.0 / (2.0 * nn)))
    radius = _distance(center, start)
    x_axis = _unit(_sub(start, center))
    z_axis = _unit(normal)
    if x_axis is None or z_axis is None:
        return None
    y_axis = _cross(z_axis, x_axis)
    to_end = _sub(end, center)
    sweep = math.atan2(_dot(to_end, y_axis), _dot(to_end, x_axis))
    if sweep <= 0:
        sweep += 2.0 * math.pi
    return center, radius, x_axis, y_axis, sweep


def arc_points(arc, chord_tolerance=ARC_CHORD_TOL, count=None):
    center, radius, x_axis, y_axis, sweep = arc
    return tessellate_arc(center, radius, x_axis, y_axis, 0.0, sweep, chord_tolerance, count)


# ------------------------------------------------------------ model reading

def _profile_diameter(profile):
    """Outside diameter of a circular profile, in project units."""
    if profile.is_a("IfcCircleProfileDef"):
        return 2.0 * profile.Radius
    if profile.is_a("IfcArbitraryClosedProfileDef"):
        curve = profile.OuterCurve
        if curve.is_a("IfcPolyline"):
            points = [p.Coordinates for p in curve.Points]
        elif curve.is_a("IfcIndexedPolyCurve"):
            points = list(curve.Points.CoordList)
        else:
            return None
        if len(points) < 6:
            return None
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        cx = (min(xs) + max(xs)) * 0.5
        cy = (min(ys) + max(ys)) * 0.5
        radii = [math.hypot(p[0] - cx, p[1] - cy) for p in points]
        # Revit writes a circle as a polygon with its corners on the circle.
        if max(radii) - min(radii) > 0.02 * max(radii):
            return None
        return 2.0 * max(radii)
    return None


def _item_diameter(item):
    if item.is_a("IfcMappedItem"):
        for inner in item.MappingSource.MappedRepresentation.Items:
            found = _item_diameter(inner)
            if found:
                return found
        return None
    if item.is_a("IfcBooleanResult"):
        return _item_diameter(item.FirstOperand)
    if item.is_a("IfcSweptDiskSolid"):
        return 2.0 * item.Radius
    profile = getattr(item, "SweptArea", None)
    if profile is not None:
        return _profile_diameter(profile)
    return None


def _body_items(element):
    rep = element.Representation
    if rep is None:
        return []
    items = []
    for representation in rep.Representations:
        if representation.RepresentationIdentifier == "Body":
            items.extend(representation.Items)
    return items


def _fold(text):
    text = naming.py_text(text).lower()
    for src, dst in ((u"ä", u"a"), (u"å", u"a"), (u"ö", u"o"), (u"é", u"e")):
        text = text.replace(src, dst)
    return text


def _sane_diameter(value):
    return value is not None and MIN_DIAMETER <= value <= MAX_DIAMETER


def _surface_colour(style):
    for item in getattr(style, "Styles", None) or ():
        colour = getattr(item, "SurfaceColour", None)
        if colour is not None:
            return (int(round(colour.Red * 255)), int(round(colour.Green * 255)), int(round(colour.Blue * 255)))
    return None


def _styled_colour(item):
    for styled in getattr(item, "StyledByItem", None) or ():
        for style in styled.Styles:
            styles = style.Styles if style.is_a("IfcPresentationStyleAssignment") else [style]
            for each in styles:
                if each.is_a("IfcSurfaceStyle"):
                    found = _surface_colour(each)
                    if found:
                        return found
    return None


class Model(object):
    """Cached reads of one IFC: ports, placements, property sets, diameters."""

    def __init__(self, ifc_file):
        self.file = ifc_file
        self.scale = ifcopenshell.util.unit.calculate_unit_scale(ifc_file)
        try:
            self.area_scale = ifcopenshell.util.unit.calculate_unit_scale(ifc_file, "AREAUNIT")
        except Exception:
            self.area_scale = 1.0
        self._ports = {}
        self._port_origins = {}
        self._psets = {}
        self._diameters = {}
        self._kinds = {}
        self._colours = {}
        self._texts = {}
        self._vertices = {}
        self._geom_settings = None
        # Ports read from bodies (MeshPort), per element, and what each meets.
        self._mesh_ports = {}
        self._mesh_links = None
        self._mesh_port_ids = 0
        self._proxy_pipes = {}

    # -- placement and ports

    def matrix(self, product):
        return ifcopenshell.util.placement.get_local_placement(product.ObjectPlacement)

    def location(self, product):
        if product.ObjectPlacement is None:
            return None
        m = self.matrix(product)
        return (float(m[0][3]) * self.scale, float(m[1][3]) * self.scale, float(m[2][3]) * self.scale)

    def conns(self, element):
        """Fresh Conn objects for the usable ports of element."""
        key = element.id()
        cached = self._ports.get(key)
        if cached is None:
            cached = []
            for port in ifcopenshell.util.system.get_ports(element):
                if getattr(port, "PredefinedType", None) not in USABLE_PORT_TYPES:
                    continue
                if port.ObjectPlacement is None:
                    continue
                m = self.matrix(port)
                origin = (float(m[0][3]) * self.scale, float(m[1][3]) * self.scale, float(m[2][3]) * self.scale)
                axis = _unit((float(m[0][2]), float(m[1][2]), float(m[2][2])))
                if axis is None:
                    continue
                cached.append((port, origin, axis))
                self._port_origins[port.id()] = origin
            if not cached and self._base_kind(element) in MESH_PORT_KINDS:
                for port in self.mesh_ports(element):
                    cached.append((port, port.origin, port.axis))
                    self._port_origins[port.id()] = port.origin
            self._ports[key] = cached
        return [Conn(port, origin, axis) for port, origin, axis in cached]

    def mesh_ports(self, element):
        """The MeshPorts of element's body: an open tube's round rims (a pipe
        has two), else a pipe's or conduit's two prism end rings; or none."""
        key = element.id()
        found = self._mesh_ports.get(key)
        if found is None:
            found = []
            mesh = self.body_mesh(element)
            ends = []
            if mesh is not None:
                if self._base_kind(element) in ("pipe", "conduit"):
                    ends = _tube_ends(mesh)
                else:
                    ends = mesh_rims(*mesh)
            for centre, axis, radius in ends:
                self._mesh_port_ids -= 1
                found.append(MeshPort(self._mesh_port_ids, element, tuple(float(v) for v in centre),
                                      tuple(float(v) for v in axis), radius))
            self._mesh_ports[key] = found
        return found

    def mesh_port_elements(self):
        """How many elements got their ports from their body."""
        return sum(1 for ports in self._mesh_ports.values() if ports)

    def _link_mesh_ports(self):
        """Connect each mesh port to the nearest mesh port of another element
        within MESH_PORT_TOL, over every pipe, conduit and fitting."""
        self._mesh_links = {}
        # Element id -> the elements whose mesh ports link to it: a nearest
        # match need not be mutual, and a line walk must go both ways.
        self._mesh_back = {}
        ports = []
        for ifc_class in ("IfcFlowSegment", "IfcFlowFitting", "IfcBuildingElementProxy"):
            for element in self.file.by_type(ifc_class):
                ports.extend(conn.port for conn in self.conns(element) if isinstance(conn.port, MeshPort))
        cells = {}
        for port in ports:
            cells.setdefault(tuple(int(math.floor(v / MESH_PORT_TOL)) for v in port.origin), []).append(port)
        for port in ports:
            cx, cy, cz = (int(math.floor(v / MESH_PORT_TOL)) for v in port.origin)
            best = None
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        for other in cells.get((cx + dx, cy + dy, cz + dz), ()):
                            if other.element.id() == port.element.id():
                                continue
                            gap = _distance(port.origin, other.origin)
                            if gap <= MESH_PORT_TOL and (best is None or gap < best[0]):
                                best = (gap, other)
            if best is not None:
                self._mesh_links[port.id()] = (best[1].element, best[1])
                self._mesh_back.setdefault(best[1].element.id(), []).append(port.element)

    def port_origin(self, port):
        if isinstance(port, MeshPort):
            return port.origin
        if port.id() not in self._port_origins:
            element = ifcopenshell.util.system.get_port_element(port)
            if element is not None:
                self.conns(element)
        return self._port_origins.get(port.id())

    def neighbour(self, port):
        """The element on the other side of port, and the port it lands on."""
        if isinstance(port, MeshPort):
            if self._mesh_links is None:
                self._link_mesh_ports()
            return self._mesh_links.get(port.id(), (None, None))
        other = ifcopenshell.util.system.get_connected_port(port)
        if other is None:
            return None, None
        return ifcopenshell.util.system.get_port_element(other), other

    def neighbours(self, element):
        found = []
        for conn in self.conns(element):
            owner, _other = self.neighbour(conn.port)
            if owner is not None and owner.id() != element.id():
                found.append(owner)
        return found

    def linked(self, element):
        """neighbours(element), plus the elements whose mesh ports link to it."""
        found = self.neighbours(element)
        if self._mesh_links is not None:
            found = found + [other for other in self._mesh_back.get(element.id(), ()) if other.id() != element.id()]
        return found

    def body_mesh(self, element):
        """(vertices, triangles) of element's meshed body, in metres in world coordinates, or None."""
        key = element.id()
        if key not in self._vertices:
            if self._geom_settings is None:
                self._geom_settings = ifcopenshell.geom.settings()
                self._geom_settings.set("use-world-coords", True)
            try:
                # Keep the shape while reading: its geometry dies with it.
                shape = ifcopenshell.geom.create_shape(self._geom_settings, element)
                verts = numpy.array(shape.geometry.verts, dtype=float).reshape(-1, 3)
                faces = numpy.array(shape.geometry.faces, dtype=int).reshape(-1, 3)
                found = (verts, faces) if len(verts) else None
            except Exception:
                found = None
            self._vertices[key] = found
        return self._vertices[key]

    def body_vertices(self, element):
        """The vertices of element's meshed body, in metres in world coordinates, or None."""
        mesh = self.body_mesh(element)
        return mesh[0] if mesh is not None else None

    def port_ring(self, element, port):
        """Radius (metres) of the smallest ring of element's body in the plane of port, or None.

        On a socket that is its bore. It is no pipe size (a fitting family
        has its own sockets), but two ports of one fitting with the same ring
        are the same size.
        """
        verts = self.body_vertices(element)
        if verts is None:
            return None
        for conn in self.conns(element):
            if conn.port == port:
                origin = numpy.array(conn.origin)
                axis = numpy.array(conn.axis)
                offsets = verts - origin
                along = offsets @ axis
                offsets = offsets[numpy.abs(along) < PORT_RING_SLAB]
                radial = numpy.linalg.norm(offsets - numpy.outer(offsets @ axis, axis), axis=1)
                radial = radial[radial > MIN_DIAMETER]
                return float(radial.min()) if len(radial) else None
        return None

    # -- what an element is

    def kind(self, element):
        """"pipe", "conduit", "pipe-fitting", "conduit-fitting", or None."""
        key = element.id()
        if key not in self._kinds:
            self._kinds[key] = self._kind(element)
        return self._kinds[key]

    def _base_kind(self, element):
        element_type = ifcopenshell.util.element.get_type(element)
        predefined = getattr(element, "PredefinedType", None)
        if predefined in (None, "NOTDEFINED", "USERDEFINED") and element_type is not None:
            predefined = getattr(element_type, "PredefinedType", predefined)
        if element.is_a("IfcPipeSegment") or (element_type is not None and element_type.is_a("IfcPipeSegmentType")):
            return "pipe"
        if element.is_a("IfcPipeFitting") or (element_type is not None and element_type.is_a("IfcPipeFittingType")):
            return "pipe-fitting"
        if element.is_a("IfcCableCarrierSegment") or (
                element_type is not None and element_type.is_a("IfcCableCarrierSegmentType")):
            if predefined == "CONDUITSEGMENT":
                return "conduit"
            if predefined in TRAY_SEGMENT_TYPES:
                return "tray"
            return None
        if element.is_a("IfcCableCarrierFitting") or (
                element_type is not None and element_type.is_a("IfcCableCarrierFittingType")):
            return "cable-fitting"
        if element.is_a("IfcBuildingElementProxy") and self._proxy_pipe(element):
            return "pipe"
        return None

    def _proxy_pipe(self, element):
        """True when a proxy's body is a straight round tube, PROXY_PIPE_RATIO
        times as long as it is wide (see PROXY_PIPE_RATIO)."""
        key = element.id()
        found = self._proxy_pipes.get(key)
        if found is None:
            found = False
            mesh = self.body_mesh(element)
            ends = _tube_ends(mesh) if mesh is not None else []
            if len(ends) == 2:
                length = float(numpy.linalg.norm(ends[1][0] - ends[0][0]))
                found = length >= PROXY_PIPE_RATIO * 2.0 * max(ends[0][2], ends[1][2])
            self._proxy_pipes[key] = found
        return found

    def _kind(self, element):
        base = self._base_kind(element)
        if base != "cable-fitting":
            return base
        # Revit writes conduit and cable tray fittings alike. A fitting is a
        # conduit fitting when it reaches a conduit, directly or through one
        # more fitting, before it reaches a tray.
        frontier = [element]
        seen = set([element.id()])
        for _depth in range(2):
            next_frontier = []
            for current in frontier:
                for owner in self.neighbours(current):
                    if owner.id() in seen:
                        continue
                    seen.add(owner.id())
                    owner_kind = self._base_kind(owner)
                    if owner_kind == "conduit":
                        return "conduit-fitting"
                    if owner_kind == "tray":
                        return None
                    if owner_kind == "cable-fitting":
                        next_frontier.append(owner)
            frontier = next_frontier
        return None

    def is_curve(self, element):
        return self.kind(element) in ("pipe", "conduit")

    def is_fitting(self, element):
        return self.kind(element) in ("pipe-fitting", "conduit-fitting")

    def is_conduit_family(self, element):
        return self.kind(element) in ("conduit", "conduit-fitting")

    # -- properties

    def psets(self, element):
        key = element.id()
        found = self._psets.get(key)
        if found is None:
            found = ifcopenshell.util.element.get_psets(element)
            self._psets[key] = found
        return found

    def property_text(self, element, spec):
        """Value of "Pset.Property", or of "Property" in any property set, as text."""
        psets = self.psets(element)
        if "." in spec:
            pset_name, prop = spec.split(".", 1)
            candidates = [psets.get(pset_name) or {}]
        else:
            prop = spec
            candidates = list(psets.values())
        for props in candidates:
            if prop in props:
                value = props[prop]
                if value is None or isinstance(value, bool):
                    continue
                text = naming.py_text(value).strip()
                if text:
                    return text
        return None

    def first_property(self, element, specs):
        for spec in specs:
            text = self.property_text(element, spec)
            if text:
                return text
        return None

    def outside_diameter(self, element):
        """Outside diameter in metres: body profile, then CrossSectionArea, then a named property."""
        key = element.id()
        if key in self._diameters:
            return self._diameters[key]
        found = None
        for item in _body_items(element):
            value = _item_diameter(item)
            if value:
                found = value * self.scale
                break
        if not _sane_diameter(found):
            found = None
            for props in self.psets(element).values():
                area = props.get("CrossSectionArea")
                if isinstance(area, (int, float)) and area > 0:
                    found = math.sqrt(4.0 * area * self.area_scale / math.pi)
                    break
        if not _sane_diameter(found):
            found = self._named_outside_diameter(element)
        if not _sane_diameter(found) and self._base_kind(element) in ("pipe", "conduit"):
            radii = [conn.port.radius for conn in self.conns(element) if isinstance(conn.port, MeshPort)]
            found = 2.0 * max(radii) if radii else None
        if not _sane_diameter(found):
            found = None
        self._diameters[key] = found
        return found

    def _named_outside_diameter(self, element):
        for props in self.psets(element).values():
            for name, value in props.items():
                folded = _fold(name)
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    continue
                if any(key in folded for key in ("outside diam", "outer diam", "ytterdiam", "utvandig diam")):
                    return value * self.scale
        return None

    def colour(self, element):
        """The element's surface colour in the IFC (body styles, then material), or None."""
        key = element.id()
        if key in self._colours:
            return self._colours[key]
        found = None
        rep = element.Representation
        for representation in (rep.Representations if rep is not None else ()):
            for item in representation.Items:
                items = [item]
                if item.is_a("IfcMappedItem"):
                    items = list(item.MappingSource.MappedRepresentation.Items)
                for each in items:
                    found = found or _styled_colour(each)
        if found is None:
            material = ifcopenshell.util.element.get_material(element, should_skip_usage=True)
            for definition in (getattr(material, "HasRepresentation", None) or ()):
                for representation in definition.Representations:
                    for item in representation.Items:
                        found = found or _styled_colour(item)
        self._colours[key] = found
        return found

    # -- in the ground

    def text(self, element):
        """Everything an element says about itself, lower case: names, type, systems, text values."""
        key = element.id()
        found = self._texts.get(key)
        if found is None:
            parts = [element.Name, element.ObjectType]
            element_type = ifcopenshell.util.element.get_type(element)
            if element_type is not None:
                parts.append(element_type.Name)
            for system in ifcopenshell.util.system.get_element_systems(element) or ():
                parts.extend((system.Name, system.ObjectType))
            for props in self.psets(element).values():
                parts.extend(value for value in props.values() if isinstance(value, str))
            found = u"\n".join(naming.py_text(part) for part in parts if part).lower()
            self._texts[key] = found
        return found

    def mid_height(self, element):
        conns = self.conns(element)
        if not conns:
            return None
        return sum(conn.origin[2] for conn in conns) / float(len(conns))

    def lowest_floor(self, elements):
        """Floor level (metres) of the lowest storey holding at least 1 % of elements, or None."""
        counts = {}
        for element in elements:
            storey = ifcopenshell.util.element.get_container(element)
            if storey is not None and storey.is_a("IfcBuildingStorey") and storey.ObjectPlacement is not None:
                counts[storey] = counts.get(storey, 0) + 1
        levels = [self.location(storey)[2] for storey, count in counts.items()
                  if count >= 0.01 * len(elements)]
        return min(levels) if levels else None

    def ground_floor(self):
        """Floor level (metres) of the storey at the project's ±0, the entrance floor, or None."""
        storeys = [storey for storey in self.file.by_type("IfcBuildingStorey")
                   if storey.Elevation is not None and storey.ObjectPlacement is not None]
        if not storeys:
            return None
        return self.location(min(storeys, key=lambda storey: abs(storey.Elevation)))[2]


# ----------------------------------------------------------- fitting shapes

def axis_intersection(p1, d1, p2, d2):
    w0 = _sub(p1, p2)
    b = _dot(d1, d2)
    d = _dot(d1, w0)
    e = _dot(d2, w0)
    denom = 1.0 - b * b
    if abs(denom) < 1e-8:
        return None
    t = (b * e - d) / denom
    s = (e - b * d) / denom
    q1 = _add(p1, _mul(d1, t))
    q2 = _add(p2, _mul(d2, s))
    return _mean([q1, q2]), _distance(q1, q2)


def connector_span(conns):
    span = 0.0
    for i in range(len(conns)):
        for j in range(i + 1, len(conns)):
            span = max(span, _distance(conns[i].origin, conns[j].origin))
    return span


def find_center(conns, loc_pt):
    pts = []
    # Nearly parallel axes meet far from the fitting. Keep the bend point
    # within a few fitting lengths of the ports.
    arm_limit = min(max(connector_span(conns) * 3.0, 0.6096), MAX_ARM)
    for i in range(len(conns)):
        for j in range(i + 1, len(conns)):
            if abs(_dot(conns[i].axis, conns[j].axis)) >= PARALLEL_DOT:
                continue
            hit = axis_intersection(conns[i].origin, conns[i].axis, conns[j].origin, conns[j].axis)
            if hit is None:
                continue
            mid, dist = hit
            if dist > AXIS_TOL:
                continue
            if _distance(mid, conns[i].origin) > arm_limit or _distance(mid, conns[j].origin) > arm_limit:
                continue
            pts.append(mid)
    if pts:
        center = _mean(pts)
        spread = max(_distance(p, center) for p in pts)
        if spread > AXIS_TOL and loc_pt is not None:
            return loc_pt
        return center
    return loc_pt


def elbow_centerline_arc(origin_a, origin_b, vertex):
    """Circular bend through the port ends, bulging away from the axis corner.

    Returns (start, end, mid) of the centreline arc, or None when the two
    ports do not sit on one circle within 20 mm.
    """
    toward_a = _sub(vertex, origin_a)
    toward_b = _sub(vertex, origin_b)
    len_a = _length(toward_a)
    len_b = _length(toward_b)
    if len_a < MIN_LENGTH or len_b < MIN_LENGTH:
        return None
    tangent_a = _unit(toward_a)
    tangent_b = _unit(_sub(origin_b, vertex))
    cosang = _dot(tangent_a, tangent_b)
    if cosang > 0.999 or cosang < -0.999:
        return None
    phi = math.acos(max(-1.0, min(1.0, cosang)))
    tan_half = math.tan(phi * 0.5)
    if tan_half < 1e-6:
        return None
    bend_r = ((len_a + len_b) * 0.5) / tan_half
    normal = _unit(_cross(tangent_a, tangent_b))
    if normal is None:
        return None
    left = _unit(_cross(normal, tangent_a))
    if left is None:
        return None
    center = _add(origin_a, _mul(left, bend_r))
    other = _sub(origin_a, _mul(left, bend_r))
    if abs(_distance(other, origin_b) - bend_r) < abs(_distance(center, origin_b) - bend_r):
        center = other
    if abs(_distance(center, origin_b) - bend_r) > ELBOW_FIT_TOL:
        return None
    mid_dir = _unit(_add(_sub(origin_a, center), _sub(origin_b, center)))
    if mid_dir is None:
        return None
    return origin_a, origin_b, _add(center, _mul(mid_dir, bend_r))


def ray_hits(origin, direction, mesh):
    """Distances along the ray from origin in direction (a unit vector) at
    which it crosses mesh's triangles, nearest first."""
    verts, faces = mesh
    if not len(faces):
        return []
    o = numpy.array(origin, dtype=float)
    d = numpy.array(direction, dtype=float)
    a, b, c = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
    e1, e2 = b - a, c - a
    p = numpy.cross(d, e2)
    det = (e1 * p).sum(1)
    ok = numpy.abs(det) > 1e-15
    inv = numpy.where(ok, 1.0 / numpy.where(ok, det, 1.0), 0.0)
    s = o - a
    u = (s * p).sum(1) * inv
    q = numpy.cross(s, e1)
    v = (q * d).sum(1) * inv
    t = (e2 * q).sum(1) * inv
    hit = ok & (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1.0 + 1e-9) & (t > 1e-9)
    return sorted(set(round(float(x), 9) for x in t[hit]))


def bend_with_legs(origin_a, origin_b, vertex, mid_distance):
    """A bend whose centreline crosses its bisector mid_distance from the axis
    corner: a straight end from each port, then the arc tangent to both ends.

    Returns (arc_start, arc_end, arc_mid), the arc's ends on the straight ends
    from origin_a and origin_b, or None when no such arc fits between them.
    """
    toward_a = _sub(vertex, origin_a)
    toward_b = _sub(vertex, origin_b)
    len_a = _length(toward_a)
    len_b = _length(toward_b)
    if len_a < MIN_LENGTH or len_b < MIN_LENGTH or mid_distance <= 0:
        return None
    tangent_a = _unit(toward_a)
    tangent_b = _unit(_sub(origin_b, vertex))
    cosang = _dot(tangent_a, tangent_b)
    if cosang > 0.999 or cosang < -0.999:
        return None
    half = math.acos(max(-1.0, min(1.0, cosang))) * 0.5
    radius = mid_distance / (1.0 / math.cos(half) - 1.0)
    tangent = radius * math.tan(half)
    if min(len_a, len_b) - tangent < -BEND_LEG_TOL:
        return None
    tangent = min(tangent, len_a, len_b)
    bisector = _unit(_add(_mul(toward_a, -1.0 / len_a), _mul(toward_b, -1.0 / len_b)))
    if bisector is None:
        return None
    start = _sub(vertex, _mul(tangent_a, tangent))
    end = _add(vertex, _mul(tangent_b, tangent))
    return start, end, _add(vertex, _mul(bisector, mid_distance))


def deviation_from_axis_deg(vec, axis):
    size = _length(vec)
    if size < 1e-12:
        return None
    cosine = max(-1.0, min(1.0, _dot(vec, axis) / size))
    angle = math.degrees(math.acos(cosine))
    return 180.0 - angle if angle > 90.0 else angle


def same_size(c0, c1):
    if c0.radius is None or c1.radius is None:
        return True
    return abs(c0.radius - c1.radius) * 2.0 < 0.5 * MM


def cap_segment(conn, loc_pt):
    """A cap has one port: run from it to as far past the cap's origin."""
    if loc_pt is None:
        return None
    toward = _sub(loc_pt, conn.origin)
    direction = _mul(conn.axis, -1.0)
    travel = _dot(toward, direction)
    if travel < 0:
        travel = _dot(toward, conn.axis)
        direction = conn.axis
    if travel < MIN_LENGTH:
        return None
    length = travel * 2.0
    if length > MAX_CAP:
        return None
    return conn.origin, _add(conn.origin, _mul(direction, length))


def fitting_segments(conns, loc_pt):
    """Centre-line segments (p0, r0, p1, r1, conn) of a fitting, its kind and a skip reason."""
    if len(conns) == 1:
        seg = cap_segment(conns[0], loc_pt)
        if seg is None:
            return [], "cap", "cap-length-unknown"
        radius = conns[0].radius
        return [(seg[0], radius, seg[1], radius, conns[0])], "cap", None

    if len(conns) == 2 and abs(_dot(conns[0].axis, conns[1].axis)) >= PARALLEL_DOT:
        c0, c1 = conns
        dev = deviation_from_axis_deg(_sub(c1.origin, c0.origin), c0.axis)
        if dev is None or dev <= 20.0:
            if same_size(c0, c1):
                return [(c0.origin, c0.radius, c1.origin, c1.radius, c0)], "straight", None
            # A reducer: each half carries the size of its own end.
            mid = _mean([c0.origin, c1.origin])
            mid_r = (c0.radius + c1.radius) * 0.5
            return [
                (c0.origin, c0.radius, mid, mid_r, c0),
                (mid, mid_r, c1.origin, c1.radius, c1),
            ], "reducer", None
        if loc_pt is not None:
            return [
                (c0.origin, c0.radius, loc_pt, c0.radius, c0),
                (c1.origin, c1.radius, loc_pt, c1.radius, c1),
            ], "parallel-offset", None
        return [(c0.origin, c0.radius, c1.origin, c1.radius, c0)], "parallel-offset", None

    center = find_center(conns, loc_pt)
    if center is None:
        return [], "unresolved", "no-center"
    main = main_pair(conns) if len(conns) > 2 else None
    segments = []
    for conn in conns:
        if _distance(conn.origin, center) < MIN_LENGTH:
            continue
        end = center
        if main is not None and conn not in main:
            end = branch_end(conn, center, main)
        segments.append((conn.origin, conn.radius, end, conn.radius, conn))
    if not segments:
        return [], "branched", "zero-length-legs"
    return segments, "branched", None


def main_pair(conns):
    """The two ports of a tee, Y or cross the main pipe runs straight through, or None."""
    best = None
    for i in range(len(conns)):
        for j in range(i + 1, len(conns)):
            a, b = conns[i], conns[j]
            if abs(_dot(a.axis, b.axis)) < PARALLEL_DOT:
                continue
            dev = deviation_from_axis_deg(_sub(b.origin, a.origin), a.axis)
            if dev is None or dev > 20.0:
                continue
            if best is None or dev < best[0]:
                best = (dev, (a, b))
    return best[1] if best else None


def branch_end(conn, center, main):
    """Where a branch leg from conn toward center should end: where its VG
    line reaches the outside of the main pipe, or center when it never does
    (a branch as big as the main meets its bottom at the centre) or starts
    inside it (a short riser on top of the main)."""
    direction = _unit(_sub(main[1].origin, main[0].origin))
    if direction is None:
        return center
    # The main's size on the branch's side of the centre (they differ in a reducing tee).
    toward = _sub(conn.origin, center)
    radius = max(main, key=lambda c: _dot(_sub(c.origin, center), toward)).radius
    start = _drop(conn.origin, conn.radius)
    step = _sub(_drop(center, conn.radius), start)

    def across(v):
        return _sub(v, _mul(direction, _dot(v, direction)))

    w = across(_sub(start, center))
    d = across(step)
    a = _dot(d, d)
    b = 2.0 * _dot(w, d)
    c = _dot(w, w) - radius * radius
    if a < 1e-12 or c <= 0.0:
        return center
    disc = b * b - 4.0 * a * c
    if disc <= 0.0:
        return center
    t = (-b - math.sqrt(disc)) / (2.0 * a)
    if not 0.0 < t < 1.0:
        return center
    return _add(conn.origin, _mul(_sub(center, conn.origin), t))


def vg_line(p0, r0, p1, r1):
    a = _drop(p0, r0)
    b = _drop(p1, r1)
    if _distance(a, b) < MIN_LENGTH:
        return None
    return [a, b]


# ------------------------------------------------------------------ builder

class Builder(object):
    """Turn the pipes, conduits and fittings of an IFC into pipe-bottom pieces.

    group_properties: property specs ("Pset.Property" or "Property") whose
    first value names a conduit's group, like CQ BSAB in Revit. color_rules:
    [ColorRule], the first one whose selector matches an element gives its
    colour and adds its name to the layer, like a Revit view filter. Without
    a rule, a group takes its pipe's or conduit's colour in the model.

    in_ground: draw only what lies in the ground (see in_ground()).
    ground_level: the floor level (metres) under which elements are in the
    ground; None takes the lowest storey's.
    """

    def __init__(self, ifc_file, group_properties=(), color_rules=(),
                 size_property=None, long_bend_min=LONG_BEND_MIN, bend_segment=BEND_SEGMENT,
                 in_ground=True, ground_words=GROUND_WORDS, ground_level=None):
        self.model = Model(ifc_file)
        self.group_properties = tuple(group_properties)
        self.color_rules = list(color_rules)
        self.size_property = size_property
        self.long_bend_min = long_bend_min
        self.bend_segment = bend_segment
        self.in_ground_only = in_ground
        self.ground_words = tuple(word.lower() for word in ground_words)
        self.ground_level = ground_level
        self.ground_floor = None
        self.considered = 0
        self.pieces = []
        self.skipped = []
        self.processed = 0
        self.long_bends = 0
        self.short_bends = 0
        self.snapped = 0
        self.connected_fittings = 0
        self._groups = {}
        # Element id -> its Line; and the groups waiting for a line label.
        self._lines = {}
        self._unnamed = []
        for rule in self.color_rules:
            rule.ids = set(e.id() for e in ifcopenshell.util.selector.filter_elements(ifc_file, rule.selector))

    def in_ground(self, element, floor, ground_floor):
        """True when the element's midpoint is below floor (the lowest storey's
        floor), or when a type, name, system or property says it is in the
        ground and its midpoint is below ground_floor (the storey at ±0), so a
        kulvert pipe rising into the building is left out."""
        middle = self.model.mid_height(element)
        if middle is None:
            return False
        if floor is not None and middle <= floor:
            return True
        if ground_floor is not None and middle > ground_floor:
            return False
        text = self.model.text(element)
        return any(word in text for word in self.ground_words)

    def candidates(self, selector=None, include_connected_fittings=True):
        """The elements to draw: every pipe, conduit and fitting, or those selector
        matches, and of those only the ones in the ground unless in_ground is off.

        With include_connected_fittings, a chosen pipe or conduit brings the
        fittings on its ports along, so a selector on conduits alone, or a
        kulvert pipe found by its type name, still draws its bends.
        """
        if selector:
            elements = ifcopenshell.util.selector.filter_elements(self.model.file, selector)
        else:
            elements = []
            for ifc_class in ("IfcFlowSegment", "IfcFlowFitting"):
                elements.extend(self.model.file.by_type(ifc_class))
        result = [e for e in elements if self.model.is_curve(e) or self.model.is_fitting(e)]
        self.considered = len(result)
        if self.in_ground_only:
            if self.ground_level is None:
                self.ground_level = self.model.lowest_floor(result)
            self.ground_floor = self.model.ground_floor()
            result = [e for e in result if self.in_ground(e, self.ground_level, self.ground_floor)]
        if (selector or self.in_ground_only) and include_connected_fittings:
            chosen = set(e.id() for e in result)
            for element in [e for e in result if self.model.is_curve(e)]:
                for owner in self.model.neighbours(element):
                    if owner.id() not in chosen and self.model.is_fitting(owner):
                        chosen.add(owner.id())
                        result.append(owner)
                        self.connected_fittings += 1
        return sorted(result, key=lambda e: e.id())

    # -- groups

    def group_of(self, element):
        key = element.id()
        found = self._groups.get(key)
        if found is None:
            found = self._group(element)
            self._groups[key] = found
        return found

    def line_of(self, element):
        """The Line element is part of: everything connected to it port to
        port through pipes, conduits and fittings (LINE_KINDS)."""
        found = self._lines.get(element.id())
        if found is None:
            found = Line()
            frontier = [element]
            self._lines[element.id()] = found
            while frontier:
                current = frontier.pop()
                found.members.append(current)
                for owner in self.model.linked(current):
                    if owner.id() not in self._lines and self.model.kind(owner) in LINE_KINDS:
                        self._lines[owner.id()] = found
                        frontier.append(owner)
        return found

    def _line_text(self, line, read):
        """The most common text read(member) gives along line, or None."""
        tally = {}
        for member in line.members:
            text = read(member)
            if text:
                tally[text] = tally.get(text, 0) + 1
        if not tally:
            return None
        return min(tally, key=lambda text: (-tally[text], text))

    def _system_name(self, element):
        """element's own system name: a PIPE_SYSTEM_PROPERTIES value, its IfcSystem, or a SYSTEM_NAME_PROPERTIES value."""
        text = self.model.first_property(element, PIPE_SYSTEM_PROPERTIES)
        if text:
            return text
        for system in ifcopenshell.util.system.get_element_systems(element) or []:
            for value in (getattr(system, "ObjectType", None), system.Name):
                if value:
                    return value
        return self.model.first_property(element, SYSTEM_NAME_PROPERTIES)

    def _group_name(self, element):
        """The group property (element, then line), else the system name (element, then line), else None."""
        if self.group_properties:
            text = self.model.first_property(element, self.group_properties)
            if text:
                return text
            line = self.line_of(element)
            if line.group_text is False:
                line.group_text = self._line_text(
                    line, lambda member: self.model.first_property(member, self.group_properties))
            if line.group_text:
                return line.group_text
        text = self._system_name(element)
        if text:
            return text
        line = self.line_of(element)
        if line.system_text is False:
            line.system_text = self._line_text(line, self._system_name)
        return line.system_text

    def _rule_for(self, element):
        for rule in self.color_rules:
            if element.id() in rule.ids:
                return rule
        return None

    def _group(self, element):
        rule = self._rule_for(element)
        name = self._group_name(element)
        parts = [naming.clean_part(name) if name else u""]
        if rule is not None:
            if self.model.is_conduit_family(element):
                parts.append(rule.name)
            group = Group(parts, rule.rgb, u"rule " + rule.name)
        else:
            # The colour is settled per system in resolve_colours(). A fitting's own
            # colour is no vote (MagiCAD writes its fittings a default grey).
            colour = self.model.colour(element) if self.model.is_curve(element) else None
            group = Group(parts, None, None, colour)
        if not name:
            # Labelled "Ledning N" in name_lines(), once every line is known.
            self._unnamed.append((self.line_of(element), group))
        return group

    def name_lines(self):
        """Give each line nothing names its own group, "Ledning 1", ...,
        numbered west to east (then south to north) by its westernmost port."""
        lines = []
        for line, _group in self._unnamed:
            if line.label is None and line not in lines:
                lines.append(line)
        for number, line in enumerate(sorted(lines, key=self._line_west), 1):
            line.label = LINE_LABEL % number
        for line, group in self._unnamed:
            group.parts = (line.label,) + group.parts[1:]
        self._unnamed = []

    def _line_west(self, line):
        origins = [conn.origin for member in line.members for conn in self.model.conns(member)]
        if not origins:
            return (0.0, 0.0, min(member.GlobalId for member in line.members))
        x, y, _z = min(origins)
        return (round(x, 3), round(y, 3), min(member.GlobalId for member in line.members))

    def resolve_colours(self):
        """One colour per system: its pipes' colour in the model when they carry
        a real one, else a distinct default. Colour rules keep theirs."""
        self.name_lines()
        votes = {}
        for group in self._groups.values():
            if group.color is None:
                tally = votes.setdefault(group.parts, {})
                if is_real_colour(group.model_colour):
                    tally[group.model_colour] = tally.get(group.model_colour, 0) + 1
        chosen = {}
        for number, parts in enumerate(sorted(votes)):
            tally = votes[parts]
            if tally:
                chosen[parts] = (max(tally, key=tally.get), u"model")
            else:
                chosen[parts] = (SYSTEM_COLOURS[number % len(SYSTEM_COLOURS)], u"system default")
        for group in self._groups.values():
            if group.color is None:
                group.color, group.color_from = chosen[group.parts]

    # -- sizes

    def size_mm(self, element, outside):
        if self.size_property:
            text = self.model.property_text(element, self.size_property)
            if text:
                match = re.search(r"[-+]?\d+(?:[.,]\d+)?", text)
                if match:
                    value = float(match.group(0).replace(",", "."))
                    # A number property is in project units; text like "110 mm" is millimetres.
                    if not re.search(r"[A-Za-z]", text):
                        value = value * self.model.scale / MM
                    return round(value, 1)
        if outside is None:
            return None
        return round(outside / MM, 1)

    # -- pieces

    def add(self, element):
        if self.model.is_curve(element):
            reason = self._add_curve(element)
        elif self.model.is_fitting(element):
            reason = self._add_fitting(element)
        else:
            reason = "not-a-pipe-or-conduit"
        self.processed += 1
        if reason:
            self.skipped.append((element.GlobalId, reason))

    def _bend_legs(self, element, conns, vertex):
        """bend_with_legs for a two-port bend, its arc read from its body: the
        bisector from the axis corner crosses the outer and inner wall a
        diameter apart, the centreline midway. On a bend of a few degrees the
        corner lies inside the pipe, its outer wall behind it. None without a
        fitting body."""
        mesh = self.model.body_mesh(element)
        if mesh is None:
            return None
        a, b = conns[0].origin, conns[1].origin
        bisector = _unit(_add(_unit(_sub(a, vertex)) or (0.0, 0.0, 0.0), _unit(_sub(b, vertex)) or (0.0, 0.0, 0.0)))
        if bisector is None:
            return None
        ahead = ray_hits(vertex, bisector, mesh)
        if len(ahead) % 2:
            behind = ray_hits(vertex, _mul(bisector, -1.0), mesh)
            if not behind:
                return None
            outer, inner = -behind[0], ahead[0]
        elif ahead:
            outer, inner = ahead[0], ahead[1]
        else:
            return None
        diameter = conns[0].radius + conns[1].radius
        if abs((inner - outer) - diameter) > BEND_WALL_TOL * diameter:
            return None
        return bend_with_legs(a, b, vertex, (outer + inner) * 0.5)

    def _piece(self, points, group, size_mm, element, kind, owner=None):
        if not points:
            return False
        owner_guid = owner.GlobalId if owner is not None else None
        self.pieces.append(Piece(points, group, size_mm, element.GlobalId, kind, owner_guid))
        return True

    def _snapped_conns(self, element):
        """The ports of element, each moved onto the port it connects to when within SNAP_TOL.

        A fitting moves onto the pipe or conduit it connects to; two pipes, or
        two fittings, meet halfway. Both sides of a joint then end on the same
        point and join into one polyline.
        """
        conns = self.model.conns(element)
        own_curve = self.model.is_curve(element)
        for conn in conns:
            owner, landed = self.model.neighbour(conn.port)
            if owner is None or landed is None:
                continue
            other = self.model.port_origin(landed)
            if other is None:
                continue
            gap = _distance(other, conn.origin)
            if gap < 1e-9 or gap > SNAP_TOL:
                continue
            other_curve = self.model.is_curve(owner)
            if own_curve and not other_curve:
                continue
            conn.origin = other if other_curve and not own_curve else _mean([conn.origin, other])
            self.snapped += 1
        return conns

    def _curve_ends(self, element):
        conns = self._snapped_conns(element)
        if len(conns) == 2 and _distance(conns[0].origin, conns[1].origin) >= MIN_LENGTH:
            return conns[0].origin, conns[1].origin
        return self._extrusion_ends(element)

    def _extrusion_ends(self, element):
        """Start and end of the body's extrusion axis, when the element has no two ports."""
        for item in _body_items(element):
            if not item.is_a("IfcExtrudedAreaSolid"):
                continue
            matrix = numpy.array(self.model.matrix(element))
            if item.Position is not None:
                matrix = matrix @ numpy.array(ifcopenshell.util.placement.get_axis2placement(item.Position))
            dx, dy, dz = item.ExtrudedDirection.DirectionRatios
            ends = []
            for local in ((0.0, 0.0, 0.0), (dx * item.Depth, dy * item.Depth, dz * item.Depth)):
                world = matrix @ numpy.array(local + (1.0,))
                ends.append((float(world[0]) * self.model.scale,
                             float(world[1]) * self.model.scale,
                             float(world[2]) * self.model.scale))
            return ends[0], ends[1]
        return None

    def _add_curve(self, element):
        ends = self._curve_ends(element)
        if ends is None:
            return "no-ports-or-axis"
        outside = self.model.outside_diameter(element)
        if outside is None:
            return "no-diameter"
        radius = outside * 0.5
        points = vg_line(ends[0], radius, ends[1], radius)
        if points is None:
            return "curve-too-short"
        self._piece(points, self.group_of(element), self.size_mm(element, outside), element, "line")
        return None

    def _curve_on(self, port, self_id):
        """The pipe or conduit right on a port: (owner, outside)."""
        owner, _landed = self.model.neighbour(port)
        if owner is None or owner.id() == self_id or not self.model.is_curve(owner):
            return None, None
        outside = self.model.outside_diameter(owner)
        return (owner, outside) if outside is not None else (None, None)

    def _same_size_curve(self, fitting, port):
        """The pipe or conduit on another port of fitting with the same ring as port: (owner, outside)."""
        ring = self.model.port_ring(fitting, port)
        if ring is None:
            return None, None
        for other in self.model.conns(fitting):
            if other.port == port:
                continue
            owner, outside = self._curve_on(other.port, fitting.id())
            if owner is None:
                continue
            other_ring = self.model.port_ring(fitting, other.port)
            if other_ring is not None and abs(other_ring - ring) < RING_MATCH_TOL:
                return owner, outside
        return None, None

    def _connected_curve(self, element, port):
        """The pipe or conduit whose size a fitting's port has: (owner, outside, source)."""
        owner, landed = self.model.neighbour(port)
        if owner is None or owner.id() == element.id():
            return None, None, None
        if self.model.is_curve(owner):
            found = self._curve_on(port, element.id())
            return found + ("connected-curve",)
        if not self.model.is_fitting(owner):
            return None, None, None
        # A port on another fitting (a Y on a reducer): the pipe on a port of
        # the same size, on this fitting, else on the other one.
        found = self._same_size_curve(element, port)
        if found[0] is None:
            found = self._same_size_curve(owner, landed)
        if found[0] is not None:
            return found + ("same-size-port",)
        # Failing that, the pipe or conduit past the other fitting.
        for other in self.model.conns(owner):
            if other.port == landed:
                continue
            found = self._curve_on(other.port, owner.id())
            if found[0] is not None:
                return found + ("past-fitting",)
        return None, None, None

    def _add_fitting(self, element):
        conns = self._snapped_conns(element)
        if not conns:
            return "no-ports"
        for conn in conns:
            owner, outside, source = self._connected_curve(element, conn.port)
            if outside is not None:
                conn.radius = outside * 0.5
                conn.owner = owner
                conn.source = source
        known = [conn.radius for conn in conns if conn.radius is not None]
        if known and any(conn.radius is None for conn in conns):
            fill = sum(known) / float(len(known))
            for conn in conns:
                if conn.radius is None:
                    conn.radius = fill
                    conn.source = "sibling-radius"
        if any(conn.radius is None for conn in conns):
            return "no-diameter"

        def leg_group(conn):
            return self.group_of(conn.owner if conn.owner is not None else element)

        def leg_size(conn):
            if conn.owner is not None:
                return self.size_mm(conn.owner, conn.radius * 2.0)
            return round(conn.radius * 2.0 / MM, 1)

        is_conduit_fitting = self.model.kind(element) == "conduit-fitting"
        loc_pt = self.model.location(element)
        segments, kind, reason = fitting_segments(conns, loc_pt)
        if len(conns) == 2 and kind == "branched" and segments:
            center = segments[0][2]
            bend = elbow_centerline_arc(conns[0].origin, conns[1].origin, center)
            if bend is not None:
                drop_r = (conns[0].radius + conns[1].radius) * 0.5
                arc = circle_through(_drop(bend[0], drop_r), _drop(bend[1], drop_r), _drop(bend[2], drop_r))
                group = leg_group(conns[0])
                size = leg_size(conns[0])
                owner = conns[0].owner
                if arc is not None and is_conduit_fitting:
                    head, tail = [], []
                    legs = self._bend_legs(element, conns, center)
                    if legs is not None:
                        fitted = circle_through(_drop(legs[0], drop_r), _drop(legs[1], drop_r), _drop(legs[2], drop_r))
                        if fitted is not None:
                            arc = fitted
                            if _distance(conns[0].origin, legs[0]) > MIN_LENGTH:
                                head = [_drop(conns[0].origin, drop_r)]
                            if _distance(conns[1].origin, legs[1]) > MIN_LENGTH:
                                tail = [_drop(conns[1].origin, drop_r)]
                    length = arc[1] * arc[4]
                    length_all = length + _distance(conns[0].origin, legs[0]) + _distance(conns[1].origin, legs[1]) \
                        if head or tail else length
                    if length_all < self.long_bend_min:
                        chord = vg_line(conns[0].origin, conns[0].radius, conns[1].origin, conns[1].radius)
                        if self._piece(chord, group, size, element, "line", owner):
                            self.short_bends += 1
                            return None
                    else:
                        count = arc_segment_count(length, self.bend_segment)
                        if self._piece(head + arc_points(arc, count=count) + tail, group, size, element, "bend", owner):
                            self.long_bends += 1
                            return None
                if arc is not None and self._piece(arc_points(arc), group, size, element, "arc", owner):
                    return None
        made = 0
        for p0, r0, p1, r1, conn in segments:
            if self._piece(vg_line(p0, r0, p1, r1), leg_group(conn), leg_size(conn), element, "line", conn.owner):
                made += 1
        if made:
            return None
        return reason or "no-segments"
