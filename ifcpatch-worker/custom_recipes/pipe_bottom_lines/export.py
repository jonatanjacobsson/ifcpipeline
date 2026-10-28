# -*- coding: utf-8 -*-
"""Run bottom-of-pipe (VG) lines end to end on an IFC: pieces, joined 3D polylines, DWG."""

import math
from collections import Counter

from pipe_bottom_lines import chain, dwg, naming
from pipe_bottom_lines.ifc_geometry import Builder

# The chaining tolerances of the Revit tool, in metres.
NODE_TOLERANCE = 0.001
COLLINEAR_TOLERANCE = 0.0001


class Report(object):
    def __init__(self):
        self.elements = 0
        self.skipped = []
        self.pieces = 0
        self.long_bends = 0
        self.short_bends = 0
        self.snapped_ports = 0
        # Elements whose ports came from their body (no placed ports in the IFC).
        self.mesh_port_elements = 0
        self.connected_fittings = 0
        # Pipes, conduits and fittings looked at, and the floor level (m) under
        # which they count as in the ground; None when everything is drawn.
        self.considered = 0
        self.ground_level = None
        # The pieces and the runs (chain.Chain) they join into, for the IFC of annotations.
        self.vg_pieces = []
        self.runs = []
        self.layers = []
        self.polylines = []
        self.layer_colors = {}

    def summary(self):
        reasons = Counter(reason for _guid, reason in self.skipped)
        return {
            "elements": self.elements,
            "pieces": self.pieces,
            "polylines": len(self.polylines),
            "long_bends": self.long_bends,
            "short_bends": self.short_bends,
            "snapped_ports": self.snapped_ports,
            "mesh_port_elements": self.mesh_port_elements,
            "connected_fittings": self.connected_fittings,
            "considered": self.considered,
            "ground_level_m": None if self.ground_level is None else round(self.ground_level, 3),
            "skipped": dict(reasons),
            "skipped_examples": dict((reason, [g for g, r in self.skipped if r == reason][:20]) for reason in reasons),
            "layers": [
                {"name": name, "color": "#%02X%02X%02X" % tuple(rgb), "polylines": count, "length_m": round(length, 1)}
                for name, rgb, count, length in self.layers
            ],
        }


def layer_colors(pieces):
    """Each layer's colour: the most common colour of its pieces."""
    votes = {}
    for piece in pieces:
        votes.setdefault(piece.layer, Counter())[piece.color] += 1
    return dict((layer, counter.most_common(1)[0][0]) for layer, counter in votes.items())


def build(ifc_file, selector=None, include_connected_fittings=True, **builder_options):
    """Pipe-bottom polylines of the pipes and conduits in ifc_file (or those selector matches).

    include_connected_fittings: with a selector, also draw the fittings on the
    ports of the selected pipes and conduits.
    """
    report = Report()
    builder = Builder(ifc_file, **builder_options)
    for element in builder.candidates(selector, include_connected_fittings):
        builder.add(element)
    builder.resolve_colours()
    report.connected_fittings = builder.connected_fittings
    report.considered = builder.considered
    report.ground_level = builder.ground_level if builder.in_ground_only else None
    report.elements = builder.processed
    report.skipped = builder.skipped
    report.pieces = len(builder.pieces)
    report.long_bends = builder.long_bends
    report.short_bends = builder.short_bends
    report.snapped_ports = builder.snapped
    report.mesh_port_elements = builder.model.mesh_port_elements()
    report.vg_pieces = builder.pieces
    if not builder.pieces:
        return report

    colors = layer_colors(builder.pieces)
    keyed = [(piece.points, (piece.layer, piece.color)) for piece in builder.pieces]
    runs = chain.chain_pieces(keyed, tolerance=NODE_TOLERANCE, collinear_tolerance=COLLINEAR_TOLERANCE)
    report.runs = runs

    lengths = Counter()
    counts = Counter()
    polylines = []
    for run in runs:
        layer, rgb = run.key
        dwg_layer = naming.dwg_layer_name(layer)
        length = sum(chain._distance(a, b) for a, b in zip(run.points, run.points[1:]))
        lengths[layer] += length
        counts[layer] += 1
        polylines.append((run.points, dwg_layer, None if rgb == colors[layer] else rgb, run.closed))
    report.polylines = polylines
    report.layer_colors = dict((naming.dwg_layer_name(name), rgb) for name, rgb in colors.items())
    report.layers = [(name, colors[name], counts[name], lengths[name]) for name in sorted(colors)]
    return report


def transform(points, rotation_deg=0.0, offset=(0.0, 0.0, 0.0)):
    """Points moved into another coordinate system.

    Like IfcMapConversion: turn about the origin by rotation_deg
    (counter-clockwise, in plan), then add offset. Metres throughout.
    """
    angle = math.radians(rotation_deg)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    dx, dy, dz = offset
    return [(x * cos_a - y * sin_a + dx, x * sin_a + y * cos_a + dy, z + dz) for x, y, z in points]


def dwg_polylines(report, rotation_deg=0.0, offset=(0.0, 0.0, 0.0)):
    if not rotation_deg and not any(offset):
        return report.polylines
    return [(transform(points, rotation_deg, offset), layer, rgb, closed)
            for points, layer, rgb, closed in report.polylines]


def write_dwg(report, path, rotation_deg=0.0, offset=(0.0, 0.0, 0.0)):
    """Write the joined polylines, optionally turned and moved into another coordinate system."""
    return dwg.write(path, dwg_polylines(report, rotation_deg, offset), report.layer_colors)


class IfcUnavailable(Exception):
    pass


def ifc_writer():
    """pipe_bottom_lines.ifc_output, or IfcUnavailable saying what is missing.

    The IFC writer needs shapely 2 for the markers' lettering (pipe_bottom_lines.font)
    on top of IfcOpenShell and numpy; the DWG alone does not, so it is only
    imported here. The recipes call this before they read any geometry.
    """
    try:
        from pipe_bottom_lines import ifc_output
    except ImportError as ex:
        raise IfcUnavailable("Could not import the IFC writer: %s. It needs shapely>=2,<3 "
                             "(ifcpatch-worker/requirements.txt has it)." % ex)
    return ifc_output


def ifc(report, source, marker_size=0.15, marker_spacing=0.5, **options):
    """Each run as an IfcAnnotation line, with level markers on its joints.

    options: tube_radius (metres), line_placement, polyline, lines and
    markers, see ifc_output.build. Returns (file, lines, markers).
    """
    ifc_output = ifc_writer()
    return ifc_output.build(source, report.vg_pieces, report.runs, marker_size, marker_spacing, **options)


def write_ifc(report, source, path, marker_size=0.15, marker_spacing=0.5, **options):
    """Write the IFC of line annotations and level markers to path.

    Returns (lines, markers).
    """
    ifc_file, lines, markers = ifc(report, source, marker_size, marker_spacing, **options)
    ifc_file.write(path)
    return lines, markers
