# -*- coding: utf-8 -*-
"""The bottom-of-pipe (VG) lines as an IFC4 of annotations: one per run, and level markers.

Each joined run, the same as a 3D polyline in the DWG (one system, one
diameter, one layer), becomes one IfcAnnotation, named after its layer and on
an IfcPresentationLayerAssignment of the same name, drawn as a thin tube
along the line ("Body", TUBE_DIAMETER across, in the run's colour). Clicking
it shows the whole run. The line itself, a 3D polyline ("Annotation",
"Curve3D"), is optional: StreamBIM draws it in its own blue and straight
from end to end, past the bends, while it shows the tube where it belongs. The run and its markers share one Description, the run's own name
("Spillvatten - 75mm #4 (+12.48 → +12.65)"), so grouping or colouring
by Description in a viewer isolates one run with its markers.

Level markers are upside-down square pyramids with their tip on the pipe-bottom line,
pointing straight out of the pipe (perpendicular to it, upwards), at least
MARKER_MIN_SIZE wide and high and MARKER_OVERSIZE more than the pipe's outside
diameter, so their plate stands clear of the pipe. The plate is square to
the pipe and carries the level ("+12.75") as 3D text set into it, standing
TEXT_RELIEF (1 mm) out of it, reading along the pipe, in pipe_bottom_lines.font's
monoline lettering, one watertight solid per glyph. Each is its own
IfcAnnotation, named after the level ("VG +12.425"). Markers belong to
their run (see run_markers): its start and end, both ends of every pipe at
least the marker spacing long, each half a marker in from the joint so it
sits on its own pipe (on the joint itself
where the pipe is too steep for that, see LEVEL_TOLERANCE), and other joints
only where no marker is closer than the marker spacing along the run. A
marker shows the level of the joint it marks, the pipe's end: the same
value Revit gives a pipe as its "+VG" at its lower end.

Runs are of type LINE_TYPE ("VG Linjer") and markers of POINT_TYPE ("VG
Markör"): their ObjectType, and an IfcTypeProduct of that name each, so a
viewer can filter or colour the two apart.

They are annotations, so they carry little. Markers carry their level in
MÅTT like the pipes: "+VG" as a label in signed metres with two decimals
("+12.75") and "Höjd UK Plushöjd" as a number in the file's unit (what
StreamBIM object tags read). Runs carry their levels and diameter in the
property set build's run_pset names, "VG" by default: "VG level start", "VG
level end" and "Diameter". "Name:Prefix" gives a set whose labels follow a
prefixed property convention instead, e.g. "Entreprenör:CC_": "CC_VG level
start", "CC_VG level end", "CC_diameter". None writes no set. Lines and markers
have the colour of their run, the same as in the DWG: a colour rule's, else
their system's (its pipes' model colour or a distinct default).

The pipe-bottom line runs on the pipe's lowest wall line, square to the pipe (see
wall_points). On level and gently sloping pipes that is the DWG's line, the
centreline dropped straight down by the radius. Where a pipe turns up or down,
the line follows the bend's outer arc onto the riser's wall, so a viewer shows
it on the pipe and not along the riser's axis, inside it.

The file uses the source model's length unit, and its site the source's site
placement. Runs and markers are placed in world coordinates (absolute and
unrotated, the way StreamBIM was checked), so they line up with the model
they came from. GlobalIds come from the source element and the
point, so they stay the same from one run to the next.

With rotation_deg and offset, runs and markers go into another coordinate
system the way PipeBottomLinesDwg moves its DWG (export.transform): turned about the
origin, counter-clockwise in plan, then moved. The levels they show move
with them. The file is then in the target system itself, so the source's
map conversion is left out, and its true north and site are turned and
moved with the runs. GlobalIds do not change.
"""

import copy
import math
import uuid
from collections import OrderedDict
from decimal import ROUND_HALF_UP, Decimal

import numpy

import ifcopenshell
import ifcopenshell.guid
import ifcopenshell.util.placement
import ifcopenshell.util.unit

from pipe_bottom_lines import font, naming
from pipe_bottom_lines.labels import (
    LAYER_PREFIX, LEVEL_LABEL, LEVEL_NUMBER, LEVEL_PSET, LINE_STYLE, LINE_TYPE, MARKER_NAME, MARKER_STYLE,
    POINT_TYPE, PROJECT_DESCRIPTION, PROJECT_NAME, RUN_DIAMETER, RUN_DIAMETER_PREFIXED, RUN_LEVEL_END,
    RUN_LEVEL_START, RUN_PSET, SITE_NAME, TEXT_STYLE)

GUID_NAMESPACE = uuid.UUID("6f1d3c52-8a0e-4c47-9a53-2f6b1f0c9e11")
# The names the output shows (LINE_TYPE, POINT_TYPE, the property sets and
# the styles) come from labels.py. IFC4 has no PredefinedType on
# IfcAnnotation, so each kind is its ObjectType and an IfcTypeProduct of that name.
# Points closer than this (metres) get one marker.
POINT_MERGE = 0.001
# Pieces at least this long (metres) get a marker at both ends; other joints
# only where no marker of the run is closer than this along it.
MARKER_SPACING = 0.5
# Markers are at least this wide and high (metres), and this much more than
# the pipe's outside diameter.
MARKER_MIN_SIZE = 0.15
MARKER_OVERSIZE = 0.05
# The level text: colour, how far it stands out of the plate, and how deep
# its base sits in the plate (metres), so no face of it lies in the plate's.
TEXT_RGB = (0, 0, 0)
TEXT_RELIEF = 0.001
TEXT_SINK = 0.0005
# Marker and tube corners are rounded to this (metres); the pipe-bottom lines are not.
MESH_RESOLUTION = 0.0001

# The markers' level label ("+12.75"), like the pipes' own (FormatMattProperties): decimals.
VG_DECIMALS = 2
# The tube drawn along a pipe-bottom line: its diameter (metres) and sides.
TUBE_DIAMETER = 0.02
TUBE_SIDES = 8
# The line moves onto the pipe's wall where the run is steeper than this
# (sine of its slope); below it, it stays straight below the centreline, as
# in the DWG. Steeper than RISER_SINE it is on a riser: its wall faces the
# way the run travels into it.
WALL_SINE = math.sin(math.radians(10.0))
RISER_SINE = math.sin(math.radians(85.0))
# Run points closer than this (metres) are one point when finding its direction.
WALL_MERGE = 0.0005
# A marker shows the level of the joint it marks. It moves in off the joint
# (half a marker, onto its own pipe) only where that changes its height by
# at most this (metres); on steeper pipes and risers it sits on the joint.
LEVEL_TOLERANCE = 0.003


def _split_pset(run_pset):
    """"Name" -> (Name, ""); "Name:Prefix" -> (Name, Prefix); None -> (None, "")."""
    if not run_pset:
        return None, u""
    name, _colon, prefix = run_pset.partition(u":")
    return name.strip() or None, prefix.strip()


def _guid(key):
    return ifcopenshell.guid.compress(uuid.uuid5(GUID_NAMESPACE, key).hex)


def _rounded(z, decimals):
    """z rounded half up (0.845 -> 0.85, the way Revit rounds its +VG), after
    rounding off float noise at 0.01 mm; never -0."""
    value = Decimal(repr(round(float(z), 5))).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    return abs(value) if value == 0 else value


def vg_label(z):
    """12.7493 -> "+12.75": the +VG label, signed metres with VG_DECIMALS decimals."""
    return u"{:+f}".format(_rounded(z, VG_DECIMALS))


def run_description(layer, number, start, end):
    """The name a run and its markers share: its layer, its number on the layer, its levels."""
    prefix = LAYER_PREFIX + naming.SEPARATOR
    name = layer[len(prefix):] if layer.startswith(prefix) else layer
    return u"%s #%d (%s \u2192 %s)" % (name, number, vg_label(start[2]), vg_label(end[2]))


def level_text(z):
    """12.4253 -> "+12.425", the way levels are written on Swedish drawings."""
    return u"{:+f}".format(_rounded(z, 3))


class _Mover(object):
    """World points (metres) turned about the origin by rotation_deg,
    counter-clockwise in plan, then moved by offset: export.transform's move
    into another coordinate system, for points, directions and marker spots."""

    def __init__(self, rotation_deg=0.0, offset=(0.0, 0.0, 0.0)):
        angle = math.radians(rotation_deg)
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        self.turn = numpy.array([[cos_a, -sin_a, 0.0], [sin_a, cos_a, 0.0], [0.0, 0.0, 1.0]])
        self.offset = numpy.array(offset, dtype=float)
        self.moves = bool(rotation_deg) or bool(self.offset.any())

    def point(self, point):
        if not self.moves:
            return point
        return tuple(float(v) for v in self.turn.dot(numpy.array(point, dtype=float)) + self.offset)

    def vector(self, vector):
        return self.turn.dot(numpy.array(vector, dtype=float)) if self.moves else vector

    def spot(self, spot):
        if not self.moves:
            return spot
        return MarkerSpot(self.point(spot.point), self.vector(spot.direction), spot.size, self.vector(spot.out),
                          spot.level + float(self.offset[2]))


class _Writer(object):
    def __init__(self, source, tube_radius=TUBE_DIAMETER / 2, line_placement="world", polyline=False,
                 run_pset=RUN_PSET, mover=None):
        self.source = source
        # Into another coordinate system, or not (_Mover).
        self.mover = mover or _Mover()
        # The runs' property set and its labels' prefix ("Name:Prefix"), or None.
        self.run_pset, self.run_prefix = _split_pset(run_pset)
        # Metres; above 0 each run gets a tube of this radius as its Body.
        self.tube_radius = tube_radius
        # "world": an absolute, unrotated placement with world coordinates;
        # "site": relative to the site placement.
        self.line_placement = line_placement
        # Each run also gets its 3D polyline as its Annotation.
        self.polyline = polyline
        self._surface_styles = {}
        self.f = ifcopenshell.file(schema="IFC4")
        src_scale = ifcopenshell.util.unit.calculate_unit_scale(source)
        # Keep the source's unit when it is metres or millimetres, so values
        # and any map conversion read the same in both files.
        if abs(src_scale - 0.001) < 1e-12:
            self.unit_prefix, self.scale = "MILLI", 0.001
        else:
            self.unit_prefix, self.scale = None, 1.0
        self.same_unit = abs(src_scale - self.scale) < 1e-12
        self._curve_styles = {}
        self._layer_items = OrderedDict()
        self._properties = {}

    # -- coordinates

    def _site_frame(self, site):
        """Rotation and translation (metres) of the source site, identity without one."""
        if site is None or site.ObjectPlacement is None:
            return numpy.identity(3), numpy.zeros(3)
        m = numpy.array(ifcopenshell.util.placement.get_local_placement(site.ObjectPlacement), dtype=float)
        src_scale = ifcopenshell.util.unit.calculate_unit_scale(self.source)
        return m[:3, :3], m[:3, 3] * src_scale

    def local(self, point):
        """World metres -> site-relative coordinates in this file's unit."""
        offset = numpy.array(point, dtype=float) - self.site_origin
        return [float(v) / self.scale for v in self.site_rotation.T.dot(offset)]

    def world(self, point):
        """World metres -> world coordinates in this file's unit."""
        return [float(v) / self.scale for v in point]

    def length(self, metres):
        return float(metres) / self.scale

    # -- setup

    def setup(self):
        f = self.f
        src_project = (self.source.by_type("IfcProject") or [None])[0]
        src_site = (self.source.by_type("IfcSite") or [None])[0]
        project_key = src_project.GlobalId if src_project is not None else "project"

        length_unit = f.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Prefix=self.unit_prefix, Name="METRE")
        angle_unit = f.create_entity("IfcSIUnit", UnitType="PLANEANGLEUNIT", Name="RADIAN")
        units = f.create_entity("IfcUnitAssignment", Units=[length_unit, angle_unit])

        true_north = None
        for ctx in self.source.by_type("IfcGeometricRepresentationContext"):
            if ctx.is_a() == "IfcGeometricRepresentationContext" and ctx.ContextType == "Model" and ctx.TrueNorth:
                ratios = [float(v) for v in ctx.TrueNorth.DirectionRatios]
                turned = self.mover.vector(ratios + [0.0] * (3 - len(ratios)))
                true_north = f.create_entity("IfcDirection", DirectionRatios=[float(v) for v in turned[:len(ratios)]])
                break
        self.model_ctx = f.create_entity(
            "IfcGeometricRepresentationContext", ContextType="Model", CoordinateSpaceDimension=3,
            Precision=1e-5, WorldCoordinateSystem=self._axis((0.0, 0.0, 0.0)), TrueNorth=true_north)
        self.body_ctx = f.create_entity(
            "IfcGeometricRepresentationSubContext", ContextIdentifier="Body", ContextType="Model",
            ParentContext=self.model_ctx, TargetView="MODEL_VIEW")
        self.annotation_ctx = f.create_entity(
            "IfcGeometricRepresentationSubContext", ContextIdentifier="Annotation", ContextType="Model",
            ParentContext=self.model_ctx, TargetView="MODEL_VIEW")

        name = (src_project.Name if src_project is not None and src_project.Name else u"") or PROJECT_NAME
        self.project = f.create_entity(
            "IfcProject", GlobalId=_guid("project:" + project_key), Name=name,
            Description=PROJECT_DESCRIPTION, RepresentationContexts=[self.model_ctx], UnitsInContext=units)
        self._copy_map_conversion()

        self.site_rotation, self.site_origin = self._site_frame(src_site)
        if self.mover.moves:
            # The site moves with the runs, so site-relative coordinates stay the same.
            self.site_rotation = self.mover.turn.dot(self.site_rotation)
            self.site_origin = numpy.array(self.mover.point(self.site_origin))
        r = self.site_rotation
        site_axis = self._axis([v / self.scale for v in self.site_origin], tuple(r[:, 2]), tuple(r[:, 0]))
        self.site_placement = f.create_entity("IfcLocalPlacement", RelativePlacement=site_axis)
        self.site = f.create_entity(
            "IfcSite", GlobalId=_guid("site:" + project_key), Name=SITE_NAME,
            ObjectPlacement=self.site_placement, CompositionType="ELEMENT")
        if src_site is not None:
            for attribute in ("RefLatitude", "RefLongitude", "RefElevation"):
                value = getattr(src_site, attribute, None)
                if value is not None:
                    setattr(self.site, attribute, value)
        f.create_entity("IfcRelAggregates", GlobalId=_guid("aggregates:" + project_key),
                        RelatingObject=self.project, RelatedObjects=[self.site])
        self.project_key = project_key

    def _copy_map_conversion(self):
        # Moved runs are in the target system already: the source's conversion would apply twice.
        if not self.same_unit or self.source.schema == "IFC2X3" or self.mover.moves:
            return
        for conversion in self.source.by_type("IfcMapConversion"):
            target = conversion.TargetCRS
            crs = self.f.create_entity(
                "IfcProjectedCRS", Name=target.Name, Description=target.Description,
                GeodeticDatum=target.GeodeticDatum, VerticalDatum=target.VerticalDatum,
                MapProjection=getattr(target, "MapProjection", None), MapZone=getattr(target, "MapZone", None))
            self.f.create_entity(
                "IfcMapConversion", SourceCRS=self.model_ctx, TargetCRS=crs,
                Eastings=conversion.Eastings, Northings=conversion.Northings,
                OrthogonalHeight=conversion.OrthogonalHeight, XAxisAbscissa=conversion.XAxisAbscissa,
                XAxisOrdinate=conversion.XAxisOrdinate, Scale=conversion.Scale)
            return

    def _axis(self, origin, z=None, x=None):
        f = self.f
        location = f.create_entity("IfcCartesianPoint", Coordinates=[float(v) for v in origin])
        if z is None:
            return f.create_entity("IfcAxis2Placement3D", Location=location)
        return f.create_entity(
            "IfcAxis2Placement3D", Location=location,
            Axis=f.create_entity("IfcDirection", DirectionRatios=[float(v) for v in z]),
            RefDirection=f.create_entity("IfcDirection", DirectionRatios=[float(v) for v in x]))

    # -- styles

    def _colour(self, rgb):
        return self.f.create_entity("IfcColourRgb", Red=rgb[0] / 255.0, Green=rgb[1] / 255.0, Blue=rgb[2] / 255.0)

    def _curve_style(self, rgb):
        style = self._curve_styles.get(rgb)
        if style is None:
            style = self.f.create_entity(
                "IfcCurveStyle", Name=u"%s %02X%02X%02X" % ((LINE_STYLE,) + tuple(rgb)),
                CurveColour=self._colour(rgb),
                ModelOrDraughting=True)
            self._curve_styles[rgb] = style
        return style

    def _surface_style(self, rgb, name):
        style = self._surface_styles.get((rgb, name))
        if style is None:
            style = self.f.create_entity(
                "IfcSurfaceStyle", Name=u"%s %02X%02X%02X" % ((name,) + tuple(rgb)), Side="BOTH",
                Styles=[self.f.create_entity("IfcSurfaceStyleShading", SurfaceColour=self._colour(rgb), Transparency=0.0)])
            self._surface_styles[(rgb, name)] = style
        return style

    def _tube(self, coords, rgb):
        """A closed triangulated tube of tube_radius along coords (this file's unit)."""
        f = self.f
        pts = [numpy.array(coords[0], dtype=float)]
        for c in coords[1:]:
            c = numpy.array(c, dtype=float)
            if numpy.linalg.norm(c - pts[-1]) > 1e-6:
                pts.append(c)
        if len(pts) < 2:
            return None
        r = self.length(self.tube_radius)
        sides = TUBE_SIDES
        dirs = [(b - a) / numpy.linalg.norm(b - a) for a, b in zip(pts, pts[1:])]
        tangents = [dirs[0]]
        for d0, d1 in zip(dirs, dirs[1:]):
            t = d0 + d1
            n = numpy.linalg.norm(t)
            tangents.append(t / n if n > 1e-9 else d1)
        tangents.append(dirs[-1])
        t0 = tangents[0]
        helper = numpy.array([0.0, 0.0, 1.0]) if abs(t0[2]) < 0.9 else numpy.array([1.0, 0.0, 0.0])
        normal = numpy.cross(t0, helper)
        normal /= numpy.linalg.norm(normal)
        ring_points = []
        for p, t in zip(pts, tangents):
            normal = normal - normal.dot(t) * t
            normal /= numpy.linalg.norm(normal)
            binormal = numpy.cross(t, normal)
            for k in range(sides):
                a = 2.0 * math.pi * k / sides
                ring_points.append(self.mesh_point(p + r * (math.cos(a) * normal + math.sin(a) * binormal)))
        start_centre = len(ring_points) + 1
        ring_points.append(self.mesh_point(pts[0]))
        end_centre = len(ring_points) + 1
        ring_points.append(self.mesh_point(pts[-1]))
        faces = []
        for i in range(len(pts) - 1):
            for k in range(sides):
                a, b = i * sides + k + 1, i * sides + (k + 1) % sides + 1
                c, d = a + sides, b + sides
                faces += [(a, d, c), (a, b, d)]
        last = (len(pts) - 1) * sides
        for k in range(sides):
            a, b = k + 1, (k + 1) % sides + 1
            faces.append((start_centre, b, a))
            faces.append((end_centre, last + a, last + b))
        faces_set = f.create_entity(
            "IfcTriangulatedFaceSet",
            Coordinates=f.create_entity("IfcCartesianPointList3D", CoordList=ring_points),
            Closed=True, CoordIndex=faces)
        f.create_entity("IfcStyledItem", Item=faces_set, Styles=[self._surface_style(rgb, LINE_STYLE)])
        return f.create_entity(
            "IfcShapeRepresentation", ContextOfItems=self.body_ctx, RepresentationIdentifier="Body",
            RepresentationType="Tessellation", Items=[faces_set])

    def file_vector(self, vector):
        """A world direction or offset (metres) -> this file's frame and unit."""
        v = numpy.array(vector, dtype=float)
        if self.line_placement != "world":
            v = self.site_rotation.T.dot(v)
        return [float(c) / self.scale for c in v]

    def mesh_point(self, coords):
        """Coordinates in this file's unit, rounded to MESH_RESOLUTION."""
        digits = max(0, int(round(-math.log10(MESH_RESOLUTION / self.scale))))
        return tuple(round(float(c), digits) for c in coords)

    def _faces(self, shapes, rgb, name, closed=True, wound=False):
        """One IfcTriangulatedFaceSet of shapes ([[corner, ...], [(i, j, k), ...]]) in rgb:
        convex solids wound outwards, solids already wound outwards (wound),
        or flat faces as given."""
        f = self.f
        coords, faces = [], []
        for corners, triangles in shapes:
            base = len(coords)
            coords += [self.mesh_point(self.file_vector(c)) for c in corners]
            centre = numpy.mean(numpy.array(corners, dtype=float), axis=0)
            for tri in triangles:
                a, b, c = (numpy.array(corners[i], dtype=float) for i in tri)
                normal = numpy.cross(b - a, c - a)
                # Outward winding: the normal points away from the shape's centre.
                if closed and not wound and normal.dot((a + b + c) / 3.0 - centre) < 0:
                    tri = (tri[0], tri[2], tri[1])
                faces.append(tuple(base + i + 1 for i in tri))
        item = f.create_entity("IfcTriangulatedFaceSet",
                               Coordinates=f.create_entity("IfcCartesianPointList3D", CoordList=coords),
                               Closed=closed, CoordIndex=faces)
        f.create_entity("IfcStyledItem", Item=item, Styles=[self._surface_style(tuple(rgb), name)])
        return item

    # -- properties

    def _run_pset(self, product, key, values):
        """The run property set of product: [(property, text)], as IfcLabel; with a prefix, each label starts with it."""
        if not self.run_pset:
            return
        if self.run_prefix:
            values = [(self.run_prefix + prop, text) for prop, text in values]
        self._pset(product, self.run_pset, key, [(prop, "IfcLabel", text) for prop, text in values])

    def _pset(self, product, name, key, values):
        """A property set of product: [(property, IFC type, value)]."""
        f = self.f
        pset = f.create_entity("IfcPropertySet", GlobalId=_guid("pset:%s:%s" % (name, key)), Name=name,
                               HasProperties=[self._property(*value) for value in values])
        f.create_entity("IfcRelDefinesByProperties", GlobalId=_guid("defines:%s:%s" % (name, key)),
                        RelatedObjects=[product], RelatingPropertyDefinition=pset)

    def _property(self, prop, ifc_type, value):
        """One IfcPropertySingleValue per distinct name and value, shared between sets."""
        found = self._properties.get((prop, ifc_type, value))
        if found is None:
            found = self.f.create_entity(
                "IfcPropertySingleValue", Name=prop, NominalValue=self.f.create_entity(ifc_type, value))
            self._properties[(prop, ifc_type, value)] = found
        return found

    # -- products

    def run_line(self, key, points, layer, rgb, size_mm, description=None):
        """One joined run along points, on layer, with its levels and diameter: a tube, a 3D polyline or both."""
        f = self.f
        to_file = self.world if self.line_placement == "world" else self.local
        coords = [to_file(p) for p in points]
        shapes = []
        if self.polyline:
            polyline = f.create_entity("IfcPolyline", Points=[
                f.create_entity("IfcCartesianPoint", Coordinates=c) for c in coords])
            f.create_entity("IfcStyledItem", Item=polyline, Styles=[self._curve_style(tuple(rgb))])
            shapes.append(f.create_entity(
                "IfcShapeRepresentation", ContextOfItems=self.annotation_ctx, RepresentationIdentifier="Annotation",
                RepresentationType="Curve3D", Items=[polyline]))
        if self.tube_radius > 0:
            tube = self._tube(coords, tuple(rgb))
            if tube is not None:
                shapes.append(tube)
        self._layer_items.setdefault(layer, []).extend(shapes)
        if self.line_placement == "world":
            placement = f.create_entity("IfcLocalPlacement", RelativePlacement=self._axis((0.0, 0.0, 0.0)))
        else:
            placement = f.create_entity("IfcLocalPlacement", PlacementRelTo=self.site_placement,
                                        RelativePlacement=self._axis((0.0, 0.0, 0.0)))
        product = f.create_entity(
            "IfcAnnotation", GlobalId=_guid(key), Name=layer, Description=description, ObjectType=LINE_TYPE,
            ObjectPlacement=placement,
            Representation=f.create_entity("IfcProductDefinitionShape", Representations=shapes) if shapes else None)
        values = [
            (RUN_LEVEL_START, level_text(points[0][2])),
            (RUN_LEVEL_END, level_text(points[-1][2])),
        ]
        if size_mm:
            # A prefixed convention writes it in lower case ("CC_diameter").
            values.append((RUN_DIAMETER_PREFIXED if self.run_prefix else RUN_DIAMETER,
                           naming.size_label(size_mm)[:-2] + u" mm"))
        self._run_pset(product, key, values)
        return product

    def marker(self, cell, spot, layer, rgb, description=None):
        """A level marker at spot (a MarkerSpot): pyramid and level text."""
        f = self.f
        point = spot.point
        key = "point:%s:%d:%d:%d" % ((layer,) + tuple(cell))
        pyramid, text = marker_shapes(spot)
        items = [self._faces([pyramid], rgb, MARKER_STYLE)]
        if text:
            items.append(self._faces(text, TEXT_RGB, TEXT_STYLE, wound=True))
        shape = f.create_entity(
            "IfcShapeRepresentation", ContextOfItems=self.body_ctx, RepresentationIdentifier="Body",
            RepresentationType="Tessellation", Items=items)
        self._layer_items.setdefault(layer, []).append(shape)
        if self.line_placement == "world":
            placement = f.create_entity("IfcLocalPlacement", RelativePlacement=self._axis(self.world(point)))
        else:
            placement = f.create_entity("IfcLocalPlacement", PlacementRelTo=self.site_placement,
                                        RelativePlacement=self._axis(self.local(point)))
        product = f.create_entity(
            "IfcAnnotation", GlobalId=_guid(key), Name=MARKER_NAME % level_text(spot.level), Description=description,
            ObjectType=POINT_TYPE,
            ObjectPlacement=placement,
            Representation=f.create_entity("IfcProductDefinitionShape", Representations=[shape]))
        self._pset(product, LEVEL_PSET, key, [
            (LEVEL_LABEL, "IfcLabel", vg_label(spot.level)),
            (LEVEL_NUMBER, "IfcLengthMeasure", round(self.length(spot.level), 6)),
        ])
        return product

    def finish(self, products, part=""):
        """Contain products in the site, type them and fill the layers; part
        ("lines" or "markers") keeps a split file's containment its own."""
        f = self.f
        if products:
            f.create_entity("IfcRelContainedInSpatialStructure",
                            GlobalId=_guid("contained:" + self.project_key + (":" + part if part else "")),
                            RelatedElements=products, RelatingStructure=self.site)
        for object_type in (LINE_TYPE, POINT_TYPE):
            typed = [product for product in products if product.ObjectType == object_type]
            if not typed:
                continue
            key = "type:%s:%s" % (object_type, self.project_key)
            product_type = f.create_entity("IfcTypeProduct", GlobalId=_guid(key), Name=object_type,
                                           ApplicableOccurrence="IfcAnnotation")
            f.create_entity("IfcRelDefinesByType", GlobalId=_guid("defines" + key),
                            RelatedObjects=typed, RelatingType=product_type)
        for layer, shapes in self._layer_items.items():
            f.create_entity("IfcPresentationLayerAssignment", Name=layer, AssignedItems=shapes)


def point_key(point):
    """The grid cell a point merges in; also what its marker's GlobalId comes from."""
    return tuple(int(round(v / POINT_MERGE)) for v in point)


def _length(points):
    total = 0.0
    for a, b in zip(points, points[1:]):
        total += ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5
    return total


class MarkerSpot(object):
    """Where a marker goes: its tip, the run's direction there, its size and
    which way is out of the pipe (metres, unit vectors)."""

    __slots__ = ("point", "direction", "size", "out", "level")

    def __init__(self, point, direction, size, out, level=None):
        self.point = tuple(point)
        # The VG level it shows (metres): its joint's, within LEVEL_TOLERANCE of the tip's.
        self.level = point[2] if level is None else level
        self.direction = direction
        self.size = size
        self.out = out


def _unit(v):
    v = numpy.array(v, dtype=float)
    n = numpy.linalg.norm(v)
    return v / n if n > 1e-12 else v


def run_pieces(run, pieces):
    """[(piece, its points in run order)] of a run."""
    return [(pieces[number], list(reversed(pieces[number].points)) if reverse else list(pieces[number].points))
            for number, reverse in run.pieces]


def wall_points(points, radii):
    """points (a run's VG points in order: the centreline dropped straight
    down by the pipe radius, radii in metres at each) moved onto the pipe's
    lowest wall line, square to the pipe.

    Returns [(before, after)] per point: where it ends the step into it and
    where it starts the step out of it. They differ only at a corner where a
    level or gently sloping step meets a steep one: the gentle step keeps its
    bottom line to the corner, the steep one starts on its own wall.

    Points on level or gently sloping pipes (up to WALL_SINE) stay. A bend
    that turns up or down takes its outer arc. A riser takes the wall the
    bottom line wraps onto from the run before it: the one facing the way
    the run travels into it going up, the one behind going down (from the run
    after it only where the run starts with the riser). The whole riser keeps
    that wall. Straight down from a riser's centreline is its axis, inside
    the pipe, and so is most of a bend's dropped centreline."""
    pts = [numpy.array(p, dtype=float) for p in points]
    if len(pts) < 2:
        return [(tuple(p), tuple(p)) for p in pts]
    steps = [_unit(b - a) for a, b in zip(pts, pts[1:])]
    steps = [s if numpy.linalg.norm(s) > 0.5 else None for s in steps]

    def gentle(s):
        return abs(s[2]) < WALL_SINE

    # The direction on each side of each point, in run order: the step into
    # it and the step out of it, both their average except at a corner.
    sides = []
    for index in range(len(pts)):
        into = steps[index - 1] if index > 0 else None
        out = steps[index] if index < len(steps) else None
        both = [s for s in (into, out) if s is not None]
        if len(both) == 2 and gentle(into) != gentle(out):
            sides += [into, out]
        else:
            mean = _unit(sum(both)) if both else numpy.zeros(3)
            sides += [mean, mean]

    # Each side's way the run travels level: the last one that is not a
    # riser before it, else the first after it.
    def level_of(t):
        if abs(t[2]) >= RISER_SINE:
            return None
        level = numpy.array([t[0], t[1], 0.0])
        return level if numpy.linalg.norm(level) > 1e-6 else None

    before_level, after_level = [None] * len(sides), [None] * len(sides)
    found = None
    for k, t in enumerate(sides):
        found = level_of(t) if level_of(t) is not None else found
        before_level[k] = found
    found = None
    for k in range(len(sides) - 1, -1, -1):
        found = level_of(sides[k]) if level_of(sides[k]) is not None else found
        after_level[k] = found

    down = numpy.array([0.0, 0.0, -1.0])
    up = -down

    def moved(k, p, radius):
        t = sides[k]
        if abs(t[2]) < WALL_SINE or not radius:
            return tuple(p)
        if abs(t[2]) < RISER_SINE:
            wall = _unit(down + t[2] * t)
        else:
            level = before_level[k] if before_level[k] is not None else after_level[k]
            if level is None:
                level = numpy.array([1.0, 0.0, 0.0])
            wall = _unit(level) * (1.0 if t[2] > 0 else -1.0)
        return tuple(p + radius * (up + wall))

    return [(moved(2 * index, p, radius), moved(2 * index + 1, p, radius))
            for index, (p, radius) in enumerate(zip(pts, radii))]


def wall_run(run, pieces):
    """A run's points, and pieces with its own pieces' points, moved onto the
    pipe wall (wall_points). Other pieces are the same objects."""
    seq, radii, alias = [], [], {}
    for piece, pts in run_pieces(run, pieces):
        radius = (piece.size_mm or 0.0) / 2000.0
        for p in pts:
            p = tuple(p)
            if seq and _length([seq[-1], p]) < WALL_MERGE:
                alias.setdefault(p, len(seq) - 1)
                continue
            alias.setdefault(p, len(seq))
            seq.append(p)
            radii.append(radius)
    on_wall = wall_points(seq, radii)

    def move(p, side):
        p = tuple(p)
        index = alias.get(p)
        if index is None:
            return p
        target = on_wall[index][side]
        return tuple(target[i] + p[i] - seq[index][i] for i in range(3))

    shown = list(pieces)
    for number, reverse in run.pieces:
        piece = copy.copy(pieces[number])
        points = list(piece.points)
        # A piece starts on its first point's after side and ends on its
        # last point's before side (in run order, so turned for a reversed piece).
        first, last = (1, 0) if not reverse else (0, 1)
        piece.points = ([move(points[0], first)] + [move(p, 0) for p in points[1:-1]]
                        + [move(points[-1], last)]) if len(points) > 1 else [move(points[0], 0)]
        shown[number] = piece
    line = []
    for p in run.points:
        for side in (0, 1):
            q = move(p, side)
            if not line or _length([line[-1], q]) > 1e-9:
                line.append(q)
    return line, shown


def _along(points, distance):
    """The point distance (metres) along points, and the direction there."""
    for a, b in zip(points, points[1:]):
        step = _length([a, b])
        if step <= 1e-12:
            continue
        if distance <= step:
            t = distance / step
            return tuple(a[i] + (b[i] - a[i]) * t for i in range(3)), _unit(numpy.subtract(b, a))
        distance -= step
    a, b = points[-2], points[-1]
    return tuple(b), _unit(numpy.subtract(b, a))


def marker_width(piece, min_size=MARKER_MIN_SIZE):
    """At least min_size, and MARKER_OVERSIZE more than the pipe's outside diameter (metres)."""
    return max(min_size, (piece.size_mm or 0.0) / 1000.0 + MARKER_OVERSIZE)


def _out_of(run_pcs, index, direction):
    """Which way is out of the pipe at a marker on piece index: straight up,
    square to the pipe; on a riser (steeper than RISER_SINE) level, out of the
    wall the line runs on (see wall_points): the way the run travels on the
    nearest piece that is not a riser, turned round where the riser goes down.
    The marker then stands beside the riser, pointing at its wall."""
    up = numpy.array([0.0, 0.0, 1.0])
    out = up - up.dot(direction) * direction
    if abs(direction[2]) < RISER_SINE and numpy.linalg.norm(out) > 1e-6:
        return _unit(out)
    sign = 1.0 if direction[2] > 0 else -1.0
    for distance in range(1, len(run_pcs)):
        for other in (index - distance, index + distance):
            if 0 <= other < len(run_pcs):
                pts = run_pcs[other][1]
                level = numpy.subtract(pts[-1], pts[0], dtype=float)
                length = numpy.linalg.norm(level)
                if length > 1e-9 and abs(level[2]) < RISER_SINE * length:
                    level[2] = 0.0
                    return _unit(level) * sign
    return numpy.array([1.0, 0.0, 0.0]) * sign


def run_markers(run, pieces, spacing=MARKER_SPACING, min_size=MARKER_MIN_SIZE, is_pipe=None):
    """The markers of one run, as MarkerSpots in run order.

    The run's start and end, and both ends of every pipe at least spacing
    long (is_pipe(piece); fittings never count, however long their bend),
    always get one, half a marker in from the joint so it sits on its own
    pipe: two pipes either side of a short fitting each show their end, and
    their markers do not overlap. Other joints get one only where no marker
    of the run is closer than spacing along it. A spacing of 0 or below puts
    a marker on every joint."""
    if is_pipe is None:
        is_pipe = lambda piece: piece.kind == "line"
    run_pcs = run_pieces(run, pieces)
    starts = [0.0]
    for _piece, pts in run_pcs:
        starts.append(starts[-1] + _length(pts))
    spots = []   # (along, MarkerSpot)

    def add(index, from_end, along_joint, on_joint=False):
        piece, pts = run_pcs[index]
        size = marker_width(piece, min_size)
        length = starts[index + 1] - starts[index]
        joint = pts[-1] if from_end else pts[0]
        ordered = list(reversed(pts)) if from_end else pts
        inset = size / 2.0 if length >= size and not on_joint else 0.0
        point, direction = _along(ordered, inset)
        if abs(point[2] - joint[2]) > LEVEL_TOLERANCE:
            inset = 0.0
            point, direction = _along(ordered, inset)
        if from_end:
            direction = -direction
            along = along_joint - inset
        else:
            along = along_joint + inset
        spots.append((along, MarkerSpot(point, direction, size, _out_of(run_pcs, index, direction), joint[2])))

    last = len(run_pcs) - 1

    def first_pipe(indices):
        """The first pipe from a run end, when only fittings shorter than the
        spacing come before it: a tee leg, a reducer half, a cap."""
        skipped = 0.0
        for index in indices:
            if is_pipe(run_pcs[index][0]):
                return index
            skipped += starts[index + 1] - starts[index]
            if skipped >= spacing:
                return None
        return None

    if spacing <= 0:
        for index in range(len(run_pcs)):
            add(index, False, starts[index], on_joint=True)
        add(last, True, starts[-1], on_joint=True)
    else:
        # The run's ends: on the end of its first and last pipe, so a run
        # that starts in a tee or ends in a reducer shows the pipe's level,
        # outside the fitting, not the fitting's middle.
        head = first_pipe(range(len(run_pcs)))
        if head is None:
            add(0, False, 0.0)
        else:
            add(head, False, starts[head])
        tail = first_pipe(range(last, -1, -1))
        if tail is None:
            add(last, True, starts[-1])
        else:
            add(tail, True, starts[tail + 1])
        for index in range(len(run_pcs)):
            if starts[index + 1] - starts[index] >= spacing and is_pipe(run_pcs[index][0]):
                if index > 0:
                    add(index, False, starts[index])
                if index < last:
                    add(index, True, starts[index + 1])
        for index in range(1, len(run_pcs)):
            along = starts[index]
            if all(abs(along - kept) >= spacing - 1e-9 for kept, _spot in spots):
                add(index, False, along)
    unique = OrderedDict()
    for along, spot in sorted(spots, key=lambda item: item[0]):
        unique.setdefault(point_key(spot.point), spot)
    return list(unique.values())


def reading_direction(direction):
    """The pipe's direction, turned so text along it reads left to right
    seen from above (east, else north, else up)."""
    d = _unit(direction)
    for component in (d[0], d[1], d[2]):
        if abs(component) > 1e-6:
            return d if component > 0 else -d
    return d


def marker_shapes(spot):
    """(pyramid, text) around the tip at the origin, in metres: the pyramid a
    convex shape ([corners], [triangles]), its plate square to the pipe; the
    level text one solid per glyph ([corners], [triangles], wound outwards),
    set into the plate and standing TEXT_RELIEF out of it, reading along the pipe."""
    size = spot.size
    out = _unit(spot.out)
    along = reading_direction(spot.direction - out * out.dot(spot.direction))
    side = numpy.cross(out, along)
    top = out * size
    half = size / 2.0
    corners = [numpy.zeros(3)] + [top + along * x + side * y for x, y in ((-half, -half), (half, -half), (half, half), (-half, half))]
    pyramid = ([tuple(c) for c in corners], [(0, 1, 2), (0, 2, 3), (0, 3, 4), (0, 4, 1), (1, 2, 3), (1, 3, 4)])
    glyphs, width = font.text_prisms(vg_label(spot.level))
    height = min(0.42 * size, 0.86 * size / width) if width else 0.0
    text = []
    for points, triangles in glyphs:
        corners = [tuple(top + along * ((x - width / 2.0) * height) + side * ((y - 0.5) * height)
                         + out * (z * (TEXT_RELIEF + TEXT_SINK) - TEXT_SINK)) for x, y, z in points]
        text.append((corners, triangles))
    return pyramid, text


def build(source, pieces, runs, marker_size=MARKER_MIN_SIZE, marker_spacing=MARKER_SPACING,
          tube_radius=TUBE_DIAMETER / 2, line_placement="world", polyline=False, lines=True, markers=True,
          run_pset=RUN_PSET, rotation_deg=0.0, offset=(0.0, 0.0, 0.0)):
    """An ifcopenshell.file with each run (a chain.Chain over pieces) as a line
    annotation, a tube of tube_radius (metres) and with polyline its 3D
    polyline, and level markers on its joints. Without lines it holds only
    the markers, without markers only the lines. The runs' levels and
    diameter go in the property set run_pset ("Name" or "Name:Prefix"; None:
    none). With rotation_deg
    and offset, everything is turned and moved into another coordinate
    system first (_Mover).

    Returns (file, lines, markers), the counts written.
    """
    if not lines and not markers:
        raise ValueError("Neither lines nor markers to write")
    if lines and tube_radius <= 0 and not polyline:
        raise ValueError("A run needs a tube or its polyline: the tube diameter is 0 and the polyline is off")
    mover = _Mover(rotation_deg, offset)
    writer = _Writer(source, tube_radius, line_placement, polyline, run_pset, mover)
    writer.setup()
    # A piece's id: its element and its place among that element's pieces.
    ids = []
    ordinals = {}
    for piece in pieces:
        ordinal = ordinals.get(piece.guid, 0)
        ordinals[piece.guid] = ordinal + 1
        ids.append((piece.guid, ordinal))
    products = []
    # (layer, marker cell) -> (spot, rgb, description): each run's own markers;
    # two runs on one layer with a marker on the very same point show it once.
    numbers = {}
    spots = OrderedDict()
    segments = {}

    def is_pipe(piece):
        found = segments.get(piece.guid)
        if found is None:
            try:
                found = source.by_guid(piece.guid).is_a("IfcFlowSegment")
            except RuntimeError:
                found = piece.kind == "line"
            segments[piece.guid] = found
        return found

    for run in runs:
        members = [pieces[number] for number, _reverse in run.pieces]
        layer = naming.dwg_layer_name(members[0].layer)
        rgb = run.key[1]
        # The run's id: its first piece by element and order, whichever way the run points.
        key = "run:%s:%d" % min(ids[number] for number, _reverse in run.pieces)
        points, shown = wall_run(run, pieces)
        points = [mover.point(p) for p in points + ([points[0]] if run.closed else [])]
        numbers[layer] = numbers.get(layer, 0) + 1
        description = run_description(layer, numbers[layer], points[0], points[-1])
        if lines:
            products.append(writer.run_line(key, points, layer, rgb, members[0].size_mm, description))
        if markers:
            for spot in run_markers(run, shown, marker_spacing, marker_size, is_pipe):
                # Keyed by the unmoved point, so a marker's GlobalId does not depend on the move.
                spots.setdefault((layer, point_key(spot.point)), (mover.spot(spot), rgb, description))
    for (layer, cell), (spot, rgb, description) in spots.items():
        products.append(writer.marker(cell, spot, layer, rgb, description))
    writer.finish(products, "" if lines and markers else ("lines" if lines else "markers"))
    return writer.f, len(runs) if lines else 0, len(spots)
