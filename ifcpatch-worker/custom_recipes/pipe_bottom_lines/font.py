# -*- coding: utf-8 -*-
"""The level markers' lettering: a monoline font of the characters a level needs.

The glyphs are drawn the way DIN 1451 draws its digits: one stroke width
(STROKE, a seventh of the height), straight lines and round bowls, a flat
top on the 3 and 5 and straight tails on the 6 and 9, so no part of a glyph
is thinner than the stroke and small text stays readable. Each glyph is its
centre-lines (in units of the text height, baseline at 0, cap line at 1)
widened to the stroke, joined, cut to its cell and triangulated: one outline,
holes and all (0, 4, 6, 8, 9), so glyph_prism gives every glyph as one
watertight solid with no overlapping or loose faces.
"""

import math
from functools import lru_cache

import shapely
from shapely.geometry import LinearRing, LineString, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

STROKE = 0.14
# Space between two glyphs, in units of the text height.
GAP = 0.16
# Bowls are drawn in steps of this many degrees; outlines are simplified to
# this (units of the text height: 0.2 mm on 40 mm text). Coarser than print,
# fine at the size a viewer shows a marker.
ARC_STEP = 30.0
SIMPLIFY = 0.006

_H = STROKE / 2.0
_TOP = 1.0 - _H
_BOTTOM = _H


def _arc(cx, cy, rx, ry, start, end):
    """Points on an ellipse from angle start to end (degrees, either way round)."""
    steps = max(1, int(math.ceil(abs(end - start) / ARC_STEP)))
    return [(cx + rx * math.cos(math.radians(start + (end - start) * i / steps)),
             cy + ry * math.sin(math.radians(start + (end - start) * i / steps))) for i in range(steps + 1)]


def _ring(cx, cy, rx, ry):
    return _arc(cx, cy, rx, ry, 0.0, 360.0)[:-1]


def _stadium(cx, rx, bottom, top):
    """A closed bowl with straight sides: half circles of rx at bottom and top."""
    return _arc(cx, top - rx, rx, rx, 0.0, 180.0) + _arc(cx, bottom + rx, rx, rx, 180.0, 360.0)


def _rotated(strokes, cx, cy):
    """strokes turned half round about (cx, cy): the 6 into the 9."""
    return [(kind, [(2 * cx - x, 2 * cy - y) for x, y in points]) for kind, points in strokes]


# Each glyph: its advance (width) and its strokes, ("line", points),
# ("ring", points) or ("dot", [corner, opposite corner]). Strokes that end on
# the cell's edge run past it, so the cut gives them a square end there.
_SIX = [
    ("ring", _ring(0.26, 0.30, 0.19, 0.23)),
    ("line", [(0.26 - 0.19 * math.cos(math.radians(20.0)), 0.30 + 0.23 * math.sin(math.radians(20.0))),
              (0.39, 1.1)]),
]
_GLYPHS = {
    "0": (0.52, [("ring", _stadium(0.26, 0.19, _BOTTOM, _TOP))]),
    "1": (0.34, [("line", [(0.27, -0.1), (0.27, 1.0), (0.02, 0.72)])]),
    "2": (0.52, [("line", _arc(0.26, 0.72, 0.19, 0.21, 160.0, -38.0) + [(_H, _BOTTOM), (0.6, _BOTTOM)])]),
    "3": (0.52, [("line", [(0.0, _TOP), (0.44, _TOP), (0.2, 0.56)] + _arc(0.26, 0.30, 0.19, 0.23, 105.0, -155.0))]),
    "4": (0.52, [("line", [(0.37, 1.0), (_H, 0.28), (0.6, 0.28)]), ("line", [(0.37, 1.1), (0.37, -0.1)])]),
    "5": (0.52, [("line", [(0.47, _TOP), (_H, _TOP), (_H, 0.55)] + _arc(0.26, 0.30, 0.19, 0.23, 128.0, -155.0))]),
    "6": (0.52, _SIX),
    "7": (0.52, [("line", [(0.0, _TOP), (0.45, _TOP), (0.15, -0.1)])]),
    "8": (0.52, [("ring", _ring(0.26, 0.28, 0.19, 0.21)), ("ring", _ring(0.26, 0.745, 0.165, 0.185))]),
    "9": (0.52, _rotated(_SIX, 0.26, 0.5)),
    "+": (0.46, [("line", [(0.0, 0.5), (0.46, 0.5)]), ("line", [(0.23, 0.27), (0.23, 0.73)])]),
    "-": (0.38, [("line", [(0.0, 0.5), (0.38, 0.5)])]),
    ".": (0.16, [("dot", [(0.0, 0.0), (0.16, 0.16)])]),
}


def characters():
    return sorted(_GLYPHS)


@lru_cache(maxsize=None)
def glyph_outline(char):
    """(polygon, advance) of char: its strokes widened, joined and cut to its
    cell, outer ring anticlockwise. None for a character the font lacks."""
    if char not in _GLYPHS:
        return None
    advance, strokes = _GLYPHS[char]
    parts = []
    for kind, points in strokes:
        if kind == "dot":
            parts.append(box(*(points[0] + points[1])))
            continue
        line = LinearRing(points) if kind == "ring" else LineString(points)
        parts.append(line.buffer(STROKE / 2.0, cap_style="flat", join_style="mitre", mitre_limit=3.0))
    shape = unary_union(parts).intersection(box(0.0, 0.0, advance, 1.0)).simplify(SIMPLIFY)
    if shape.geom_type != "Polygon" or not shape.is_valid:
        raise ValueError("glyph %r is not one clean outline: %s" % (char, shape.geom_type))
    return orient(shape, 1.0), advance


@lru_cache(maxsize=None)
def glyph_prism(char):
    """char as one closed solid of unit relief: (points, triangles, advance).

    points are (x, y, z): x along the text, y up the glyph, z 0 at its base
    and 1 at its face; triangles index points and are wound outwards (seen
    from outside, anticlockwise). None for a character the font lacks.
    """
    found = glyph_outline(char)
    if found is None:
        return None
    polygon, advance = found
    rings = [list(polygon.exterior.coords)[:-1]] + [list(r.coords)[:-1] for r in polygon.interiors]
    flat, index, loops = [], {}, []
    for ring in rings:
        loop = []
        for x, y in ring:
            key = (round(x, 9), round(y, 9))
            if key not in index:
                index[key] = len(flat)
                flat.append((x, y))
            loop.append(index[key])
        loops.append(loop)
    top = []
    for triangle in shapely.constrained_delaunay_triangles(polygon).geoms:
        corners = [index[(round(x, 9), round(y, 9))] for x, y in list(triangle.exterior.coords)[:-1]]
        (ax, ay), (bx, by), (cx, cy) = (flat[i] for i in corners)
        if (bx - ax) * (cy - ay) - (by - ay) * (cx - ax) < 0:
            corners = [corners[0], corners[2], corners[1]]
        top.append(tuple(corners))
    n = len(flat)
    points = [(x, y, 0.0) for x, y in flat] + [(x, y, 1.0) for x, y in flat]
    triangles = [(a + n, b + n, c + n) for a, b, c in top] + [(a, c, b) for a, b, c in top]
    # Each ring has the glyph on its left (outer ring anticlockwise, holes
    # clockwise), so a wall from base p, q up to the face faces right: out.
    for loop in loops:
        for p, q in zip(loop, loop[1:] + loop[:1]):
            triangles += [(p, q, q + n), (p, q + n, p + n)]
    return points, triangles, advance


def text_prisms(text):
    """The glyphs of text side by side: ([(points, triangles), ...], width), x
    from 0 to width, y 0 to 1 (units of the text height), z 0 to 1. Characters
    the font lacks leave a space."""
    shapes, x = [], 0.0
    for number, char in enumerate(text):
        prism = glyph_prism(char)
        advance = prism[2] if prism else 0.52
        if prism:
            shapes.append(([(px + x, py, pz) for px, py, pz in prism[0]], prism[1]))
        x += advance + (GAP if number < len(text) - 1 else 0.0)
    return shapes, x
