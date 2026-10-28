# -*- coding: utf-8 -*-
"""Join pipe-bottom pieces into continuous 3D polylines. Pure Python, no Revit.

A piece is the pipe-bottom line of one pipe, or of one leg of a fitting, as a list of
(x, y, z) points. Pieces that share an end point and the same key (layer and
colour) are joined into one polyline. Where exactly two such ends meet, they
join. Where three or more meet (a tee, a cross), the straightest pair joins
and the others end there, so a main run stays one polyline and the branch
starts at the tee. A size change ends the run, because size is in the layer.
"""

import math

# Ends closer than this are the same node. Pipes and fittings meet exactly in
# Revit; this only absorbs rounding.
DEFAULT_TOLERANCE = 1.0 / 304.8
# A tee joins two of its legs only when they continue within this angle.
MAX_TURN_DEG = 45.0
# A vertex that deviates less than this from the line through its neighbours
# is dropped (a coupling between two straight pipes).
COLLINEAR_TOLERANCE = 0.1 / 304.8


class Chain(object):
    """A joined run. pieces is [(input index, reversed)] in run order."""

    __slots__ = ("points", "key", "closed", "pieces")

    def __init__(self, points, key, closed, pieces):
        self.points = points
        self.key = key
        self.closed = closed
        self.pieces = pieces


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _length(v):
    return math.sqrt(_dot(v, v))


def _distance(a, b):
    return _length(_sub(a, b))


def _unit(v):
    size = _length(v)
    if size < 1e-12:
        return None
    return (v[0] / size, v[1] / size, v[2] / size)


class _NodeIndex(object):
    """Merge points within a tolerance into shared node ids."""

    def __init__(self, tolerance):
        self.tolerance = tolerance
        self.cells = {}
        self.points = []

    def _cell(self, point):
        tol = self.tolerance
        return (int(math.floor(point[0] / tol)),
                int(math.floor(point[1] / tol)),
                int(math.floor(point[2] / tol)))

    def node_for(self, point):
        cx, cy, cz = self._cell(point)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for node in self.cells.get((cx + dx, cy + dy, cz + dz), ()):
                        if _distance(self.points[node], point) <= self.tolerance:
                            return node
        node = len(self.points)
        self.points.append(point)
        self.cells.setdefault((cx, cy, cz), []).append(node)
        return node


def _outward(points, end):
    """Unit direction leaving the end point into the piece."""
    if end == 0:
        origin = points[0]
        rest = points[1:]
    else:
        origin = points[-1]
        rest = list(reversed(points[:-1]))
    for point in rest:
        direction = _unit(_sub(point, origin))
        if direction is not None:
            return direction
    return None


def _pair_ends(ends, directions, max_turn_deg):
    """Pick which piece ends at one node continue into each other."""
    if len(ends) < 2:
        return []
    if len(ends) == 2:
        return [(ends[0], ends[1])]
    # Two outward directions that point opposite ways continue straight:
    # their dot product is -1. Pair the straightest first.
    limit = -math.cos(math.radians(max_turn_deg))
    candidates = []
    for i in range(len(ends)):
        for j in range(i + 1, len(ends)):
            di = directions.get(ends[i])
            dj = directions.get(ends[j])
            if di is None or dj is None:
                continue
            score = _dot(di, dj)
            if score <= limit:
                candidates.append((score, i, j))
    candidates.sort()
    used = set()
    pairs = []
    for _score, i, j in candidates:
        if i in used or j in used:
            continue
        used.add(i)
        used.add(j)
        pairs.append((ends[i], ends[j]))
    return pairs


def _drop_collinear(points, tolerance):
    if len(points) < 3:
        return points
    kept = [points[0]]
    for index in range(1, len(points) - 1):
        prev = kept[-1]
        point = points[index]
        nxt = points[index + 1]
        span = _sub(nxt, prev)
        span_len = _length(span)
        if span_len < 1e-12:
            continue
        offset = _sub(point, prev)
        along = _dot(offset, span) / span_len
        if along <= 0 or along >= span_len:
            kept.append(point)
            continue
        perp_sq = _dot(offset, offset) - along * along
        if perp_sq > tolerance * tolerance:
            kept.append(point)
    kept.append(points[-1])
    return kept


def chain_pieces(pieces, tolerance=DEFAULT_TOLERANCE, max_turn_deg=MAX_TURN_DEG,
                 collinear_tolerance=COLLINEAR_TOLERANCE):
    """pieces: list of (points, key). Returns a list of Chain in input order."""
    index = _NodeIndex(tolerance)
    usable = []
    source = []
    for original, (points, key) in enumerate(pieces):
        if points is None or len(points) < 2:
            continue
        usable.append((list(points), key))
        source.append(original)

    node_ends = {}
    directions = {}
    for number, (points, key) in enumerate(usable):
        for end in (0, 1):
            node = index.node_for(points[0] if end == 0 else points[-1])
            node_ends.setdefault((node, key), []).append((number, end))
            directions[(number, end)] = _outward(points, end)

    link = {}
    for ends in node_ends.values():
        for a, b in _pair_ends(ends, directions, max_turn_deg):
            if a[0] == b[0]:
                # Both ends of one piece on the same node: a closed piece.
                # Leave it unlinked so it stays one open polyline.
                continue
            link[a] = b
            link[b] = a

    visited = set()
    chains = []
    min_step = tolerance * 0.01

    def walk(start, start_end):
        points_out = []
        members = []
        number, entry = start, start_end
        while True:
            visited.add(number)
            members.append((source[number], entry == 1))
            seq = usable[number][0]
            if entry == 1:
                seq = list(reversed(seq))
            for point in seq:
                if points_out and _distance(points_out[-1], point) < min_step:
                    continue
                points_out.append(point)
            nxt = link.get((number, 1 - entry))
            if nxt is None or nxt[0] in visited:
                return points_out, members, nxt is not None and nxt[0] == start
            number, entry = nxt

    for number in range(len(usable)):
        if number in visited:
            continue
        if (number, 0) not in link:
            start_end = 0
        elif (number, 1) not in link:
            start_end = 1
        else:
            continue
        points_out, members, _closed = walk(number, start_end)
        chains.append((min(m[0] for m in members), points_out, usable[number][1], False, members))

    # Whatever is left is a loop with no free end.
    for number in range(len(usable)):
        if number in visited:
            continue
        points_out, members, closed = walk(number, 0)
        if closed and len(points_out) > 2 and _distance(points_out[0], points_out[-1]) <= tolerance:
            points_out = points_out[:-1]
        else:
            closed = False
        chains.append((min(m[0] for m in members), points_out, usable[number][1], closed, members))

    chains.sort(key=lambda item: item[0])
    result = []
    for _first, points_out, key, closed, members in chains:
        if collinear_tolerance:
            points_out = _drop_collinear(points_out, collinear_tolerance)
        if len(points_out) < 2:
            continue
        result.append(Chain(points_out, key, closed, members))
    return result


def arc_segment_count(arc_length, segment_length):
    """How many equal segments of about segment_length an arc is cut into (at least 2)."""
    return max(2, min(int(round(arc_length / segment_length)), 256))


def tessellate_arc(center, radius, x_axis, y_axis, start_angle, end_angle, chord_tolerance, count=None):
    """Points along a circular arc.

    With count, the arc is cut into that many equal segments. Without it,
    into as few as keep every chord within chord_tolerance of the arc.
    """
    sweep = end_angle - start_angle
    if count is not None:
        count = max(1, int(count))
    elif radius <= chord_tolerance or abs(sweep) < 1e-9:
        count = 2
    else:
        step = 2.0 * math.acos(max(-1.0, min(1.0, 1.0 - chord_tolerance / radius)))
        count = int(math.ceil(abs(sweep) / step)) if step > 1e-9 else 64
        count = max(2, min(count, 256))
    points = []
    for i in range(count + 1):
        angle = start_angle + sweep * i / float(count)
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)
        points.append((
            center[0] + radius * (cos_a * x_axis[0] + sin_a * y_axis[0]),
            center[1] + radius * (cos_a * x_axis[1] + sin_a * y_axis[1]),
            center[2] + radius * (cos_a * x_axis[2] + sin_a * y_axis[2]),
        ))
    return points
