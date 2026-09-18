"""Relate every element to EVERY space it passes through, bounds, or serves.

The single-room scripts answer "which one room is this in". A 16 m cable tray crosses
six; a wall between two rooms belongs to both; an air terminal serves the room it
discharges into. This emits all three, across a federated pair of models (a spaces IFC
plus one or more discipline IFCs), because IfcRel* are intra-file and these models keep
their rooms in a separate file with zero IfcRelSpaceBoundary.

Shape: build the room cells ONCE (see ``_space_index``), reduce each element to a set of
interior sample points, and probe. Probing costs ~0.26 ms; the pairwise Boolean
alternative costs ~193 ms per pair and has been measured to blow a 120 s deadline.

WHAT AN EDGE MEANS, EXACTLY
  ``passes_through``  at least one sampled interior point of the element lies strictly
                      inside that room's solid. Sampled, never exhaustive: for chord
                      ``c`` and spacing ``s`` the hit probability is min(1, c/s), so an
                      element that only clips a room corner can be missed, and one whose
                      axis never enters the room can be missed at ANY spacing. Every
                      result carries ``min_detectable_chord_m``. This is strictly
                      stronger than a bounding-box centroid rule and strictly weaker
                      than whole-solid containment; ``extent`` is reserved and null until
                      an exact Boolean pass fills it.
  ``bounds``          a point offset outward from one of the element's faces lands inside
                      the room, and no interior point did. Restricted to building
                      elements -- a duct does not bound a room.
  ``serves_space``    an MEP terminal resolves to a room by containment, then by a short
                      downward/upward probe, then from a connected port.
  ``above_space``     a centreline station outside every room has a room directly BELOW
                      it within ``vertical_reach_m`` -- the element runs in that room's
                      ceiling void (measured on Nobel: 0.5-1.0 m above the room top).
  ``below_space``     a station with NO room below it has one directly above within
                      ``below_reach_m``: the element sits directly beneath that room's
                      floor -- a raised-floor void, or the slab zone (a wall or a duct
                      of the storey below whose own room is not modelled). The gap is
                      recorded; the relation does not claim which. Deliberately
                      asymmetric: a tray in storey N's ceiling void is also ~0.9 m under
                      storey N+1's floor, so "down first, and never up once down has
                      answered" is what keeps a tray from being related to a room it only
                      shares a slab with, and the short up-reach (0.6 m) bounds the rest.
                      Lowest confidence of the five; filter on it and on the gap.
"""

from __future__ import annotations

import logging
import math
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import ifcopenshell
import ifcopenshell.geom

from ingest_scripts import Ingester as _Base, Relationship, safe_by_type
from ingest_scripts import _mep_ports, _space_index
from ingest_scripts._space_index import (
    STATUS_INSIDE,
    RoomIndex,
    cpu_allowance,
    mesh_settings,
    point_in_aabb,
    probe_status,
    transform_point,
    vertex_at,
    world_aabb,
)

SOURCE_KIND = "topologic_ingest_SpaceInteractions"
SCRIPT_VERSION = "1.0"

# Never subjects: openings are voids, ports/annotations/grids carry no body, and a space
# relating to itself is meaningless.
SKIP_CLASSES = {
    "IfcOpeningElement", "IfcOpeningStandardCase", "IfcSpace", "IfcSpatialZone",
    "IfcDistributionPort", "IfcAnnotation", "IfcGrid", "IfcSite", "IfcBuilding",
    "IfcBuildingStorey",
}

# Only these can BOUND a room. Keeping MEP out of the bounds pass is also what keeps the
# large discipline models cheap.
BOUNDS_CLASSES = {
    "IfcWall", "IfcWallStandardCase", "IfcWallElementedCase", "IfcSlab", "IfcRoof",
    "IfcCovering", "IfcCurtainWall", "IfcPlate", "IfcColumn", "IfcBeam", "IfcMember",
    "IfcDoor", "IfcWindow", "IfcRailing", "IfcStair", "IfcStairFlight", "IfcRamp",
    "IfcRampFlight",
}

_CONF_PASSES_MULTI = 0.90
_CONF_PASSES_SINGLE = 0.75
_CONF_BOUNDS = 0.70
_CONF_ABOVE = 0.65
_CONF_BELOW = 0.60
_CONF_DEGRADED = 0.50
_SERVES_CONFIDENCE = {
    "containment": 0.85,
    "port_ray": 0.80,
    "ray_down": 0.70,
    "ray_up": 0.65,
}


# --- pure geometry helpers (unit-tested without IFC or topologicpy) ----------------

def _bbox_of(verts: Sequence[float]) -> Tuple[float, float, float, float, float, float]:
    xs, ys, zs = verts[0::3], verts[1::3], verts[2::3]
    return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


def _far_enough(point: Sequence[float], kept: List[Tuple[float, float, float]], min_sep: float) -> bool:
    if min_sep <= 0:
        return True
    limit = min_sep * min_sep
    for other in kept:
        dx = point[0] - other[0]
        dy = point[1] - other[1]
        dz = point[2] - other[2]
        if dx * dx + dy * dy + dz * dz < limit:
            return False
    return True


def axis_centreline_points(
    verts: Sequence[float],
    spacing_m: float = 0.25,
    max_samples: int = 48,
    min_sep_m: float = 0.05,
) -> Tuple[List[Tuple[float, float, float]], int, float]:
    """Evenly spaced interior points along the element's longest local axis.

    One point per station, at the mean of the cross-section's vertices where the station
    has any and at the body's overall perpendicular centre where it does not. That
    second half matters: a duct is often exported as an 8-vertex box, so its vertices sit
    only at the two ends. Bucketing existing vertices (which is what the roomstamp
    sampler does) would then yield two points at the extremities and nothing in between,
    and a 16 m run crossing six rooms would report the first and the last. Interpolating
    stations guarantees coverage that depends on the element's LENGTH, not on how finely
    its exporter happened to tessellate it.

    Adaptive by construction: a 0.2 m fitting gets one point, a 16 m tray gets many, so a
    compact element is never over-sampled.

    Returns ``(points, axis, length)`` with axis 0=x, 1=y, 2=z.
    """
    if not verts or len(verts) < 3:
        return [], 0, 0.0
    box = _bbox_of(verts)
    extents = (box[3] - box[0], box[4] - box[1], box[5] - box[2])
    axis = max(range(3), key=lambda i: extents[i])
    length = float(extents[axis])

    n = 1 if spacing_m <= 0 else int(length / spacing_m) + 1
    n = max(1, min(int(max_samples), n))

    lo, hi = box[axis], box[axis + 3]
    span = hi - lo
    perp = [i for i in range(3) if i != axis]

    count = len(verts) // 3
    sums = [[0.0, 0.0] for _ in range(n)]
    counts = [0] * n
    totals = [0.0, 0.0]
    for i in range(count):
        point = (verts[i * 3], verts[i * 3 + 1], verts[i * 3 + 2])
        slot = 0
        if span > 0:
            slot = int((point[axis] - lo) / span * n)
            slot = n - 1 if slot >= n else (0 if slot < 0 else slot)
        for k, a in enumerate(perp):
            sums[slot][k] += point[a]
            totals[k] += point[a]
        counts[slot] += 1

    overall = [totals[k] / count for k in range(2)] if count else [0.0, 0.0]

    points: List[Tuple[float, float, float]] = []
    for i in range(n):
        station = lo + (i + 0.5) * span / n if span > 0 else lo
        centre = (
            [sums[i][k] / counts[i] for k in range(2)] if counts[i] else overall
        )
        coords = [0.0, 0.0, 0.0]
        coords[axis] = station
        coords[perp[0]] = centre[0]
        coords[perp[1]] = centre[1]
        candidate = (coords[0], coords[1], coords[2])
        if _far_enough(candidate, points, min_sep_m):
            points.append(candidate)
    return points, axis, length


def face_probe_points(
    verts: Sequence[float],
    faces: Sequence[int],
    min_area_m2: float = 0.05,
    max_faces: int = 24,
    per_bucket: int = 6,
    min_sep_m: float = 0.5,
) -> List[Tuple[Tuple[float, float, float], Tuple[float, float, float]]]:
    """(centroid, unit normal) for the element's dominant planar faces.

    Triangles are bucketed by quantized normal, so a wall collapses to its two large
    faces and a slab to top/bottom. Slivers are dropped. Returns at most ``max_faces``.
    """
    if not verts or not faces or len(faces) < 3:
        return []
    tri_count = len(faces) // 3
    vert_count = len(verts) // 3
    buckets: Dict[Tuple[float, float, float], List[Tuple[float, Tuple[float, float, float], Tuple[float, float, float]]]] = {}

    for t in range(tri_count):
        i0, i1, i2 = faces[t * 3], faces[t * 3 + 1], faces[t * 3 + 2]
        if max(i0, i1, i2) >= vert_count:
            continue
        ax, ay, az = verts[i0 * 3], verts[i0 * 3 + 1], verts[i0 * 3 + 2]
        bx, by, bz = verts[i1 * 3], verts[i1 * 3 + 1], verts[i1 * 3 + 2]
        cx, cy, cz = verts[i2 * 3], verts[i2 * 3 + 1], verts[i2 * 3 + 2]
        ux, uy, uz = bx - ax, by - ay, bz - az
        vx, vy, vz = cx - ax, cy - ay, cz - az
        nx = uy * vz - uz * vy
        ny = uz * vx - ux * vz
        nz = ux * vy - uy * vx
        norm = math.sqrt(nx * nx + ny * ny + nz * nz)
        area = 0.5 * norm
        if area < min_area_m2 or norm <= 0:
            continue
        normal = (nx / norm, ny / norm, nz / norm)
        centroid = ((ax + bx + cx) / 3.0, (ay + by + cy) / 3.0, (az + bz + cz) / 3.0)
        key = (round(normal[0], 1) + 0.0, round(normal[1], 1) + 0.0, round(normal[2], 1) + 0.0)
        buckets.setdefault(key, []).append((area, centroid, normal))

    out: List[Tuple[Tuple[float, float, float], Tuple[float, float, float]]] = []
    for key in sorted(buckets, key=lambda k: (-math.fsum(a for a, _, _ in buckets[k]), k)):
        kept: List[Tuple[float, float, float]] = []
        for _area, centroid, normal in sorted(buckets[key], key=lambda item: (-item[0], item[1])):
            if len(kept) >= per_bucket or len(out) >= max_faces:
                break
            if _far_enough(centroid, kept, min_sep_m):
                kept.append(centroid)
                out.append((centroid, normal))
        if len(out) >= max_faces:
            break
    return out


def classify_probe(status: int) -> Optional[str]:
    """Only a strictly-interior probe decides anything.

    Status 1 (ON boundary) is never a verdict: whether a coplanar point returns 1 or 2 is
    decided by sub-micron float differences and by whatever tolerance was baked into the
    room solid when its shell was sewn. It is counted as a health metric instead.
    """
    return "inside" if status == STATUS_INSIDE else None


# --- the ingester ------------------------------------------------------------------

class Ingester(_Base):
    SCRIPT_NAME = "SpaceInteractions"
    DESCRIPTION = (
        "Relate each element to every space it passes through, bounds, or serves "
        "(federated: spaces IFC + discipline IFC)"
    )

    def __init__(
        self,
        ifc_files: List[Path],
        log: logging.Logger,
        space_file: str = "",
        element_query: str = "IfcProduct",
        sample_spacing_m: float = 0.25,
        max_samples: int = 48,
        include_passes_through: bool = True,
        include_bounds: bool = True,
        include_serves_space: bool = True,
        bounds_offset_m: float = 0.10,
        bounds_max_faces: int = 24,
        serves_reach_m: float = 1.5,
        serves_step_m: float = 0.10,
        grid_m: float = 3.0,
        tolerance: float = 0.0,
        min_sample_sep_m: float = 0.05,
        num_threads: int = 0,
        include_vertical: bool = True,
        vertical_reach_m: float = 1.25,
        below_reach_m: float = 0.6,
        use_topologic: bool = True,
        force_ifc_native: bool = False,
    ):
        """Relate elements to every space they pass through, bound, or serve.

        Pass the spaces IFC and the discipline IFC(s) together in ``input_files``.

        :param space_file: Basename of the model holding the IfcSpace rooms ("" = the input file with the most).
        :param element_query: IFC class queried for subject elements.
        :param sample_spacing_m: Sample pitch along the element's longest axis; also the smallest reliably detectable chord.
        :param max_samples: Hard cap on sample points per element.
        :param include_passes_through: Emit passes_through edges.
        :param include_bounds: Emit bounds edges (building elements only).
        :param include_serves_space: Emit serves_space edges for MEP terminals.
        :param bounds_offset_m: Outward offset from a face when testing whether it bounds a room.
        :param bounds_max_faces: Hard cap on face probe points per element.
        :param serves_reach_m: How far a terminal may reach to find the room it serves.
        :param serves_step_m: Step length along the serves probe ray.
        :param grid_m: Broad-phase grid cell size for the room index.
        :param tolerance: Probe tolerance; 0.0 is a correctness choice (a positive value dilates the room solid).
        :param min_sample_sep_m: Discard sample points closer together than this.
        :param num_threads: Geometry-iterator threads (0 = cgroup CPU quota).
        :param include_vertical: Emit above_space / below_space for ceiling- and floor-void runs.
        :param vertical_reach_m: How far a void station looks DOWN for the room whose ceiling void it is in.
        :param below_reach_m: How far a station with nothing below looks UP for the room whose floor it is directly beneath.
        :param use_topologic: When False, fall back to room bounding boxes instead of solids.
        :param force_ifc_native: Same as use_topologic=False; set by the worker's SIGSEGV retry.
        """
        super().__init__(ifc_files, log)
        self.space_file = str(space_file or "").strip()
        self.element_query = str(element_query or "IfcProduct")
        self.sample_spacing_m = float(sample_spacing_m)
        self.max_samples = int(max_samples)
        self.include_passes_through = bool(include_passes_through)
        self.include_bounds = bool(include_bounds)
        self.include_serves_space = bool(include_serves_space)
        self.bounds_offset_m = float(bounds_offset_m)
        self.bounds_max_faces = int(bounds_max_faces)
        self.serves_reach_m = float(serves_reach_m)
        self.serves_step_m = float(serves_step_m)
        self.grid_m = float(grid_m)
        self.tolerance = float(tolerance)
        self.min_sample_sep_m = float(min_sample_sep_m)
        self.num_threads = int(num_threads) or cpu_allowance()
        self.include_vertical = bool(include_vertical)
        self.vertical_reach_m = float(vertical_reach_m)
        self.below_reach_m = float(below_reach_m)
        self.use_topologic = bool(use_topologic) and not bool(force_ifc_native)

        self._probe_count = 0
        self._boundary_hits = 0
        self._probe_errors = 0
        # (subject, type, object) already emitted. Two things produce the same edge
        # twice: an element exported into several discipline sub-models, and a room
        # whose solid resolves to two cells. The CDE upsert on relationship_ref would
        # collapse them, but the payload and every count would be inflated first.
        self._seen_edges: set = set()
        self._duplicate_edges = 0

    # -- file resolution ------------------------------------------------------------

    def _resolve_space_file(self) -> Optional[Path]:
        if self.space_file:
            for path in self.ifc_files:
                if path.name == self.space_file:
                    return path
            self.log.warning(
                "space_interactions: space_file %r not among inputs; auto-detecting",
                self.space_file,
            )
        best: Optional[Path] = None
        best_count = 0
        for path in self.ifc_files:
            try:
                count = len(safe_by_type(ifcopenshell.open(str(path)), "IfcSpace"))
            except Exception:
                continue
            if count > best_count:
                best, best_count = path, count
        return best

    # -- probing --------------------------------------------------------------------

    def _probe(self, index: RoomIndex, point: Sequence[float]) -> List[int]:
        """Room-cell indices strictly containing ``point``."""
        candidates = index.candidates(point)
        if not candidates:
            return []
        hits: List[int] = []
        # One topologic Vertex per POINT, reused across every candidate room.
        vertex = None
        for ci in candidates:
            room = index.cells[ci]
            self._probe_count += 1
            if room.cell is None:
                if point_in_aabb(point, room.aabb):
                    hits.append(ci)
                continue
            if vertex is None:
                vertex = vertex_at(point)
            status = probe_status(room.cell, vertex)
            if status == STATUS_INSIDE:
                hits.append(ci)
            elif status == _space_index.STATUS_ON_BOUNDARY:
                self._boundary_hits += 1
            elif status == _space_index.STATUS_ERROR:
                self._probe_errors += 1
        return hits

    def _march(
        self, index: RoomIndex, origin: Sequence[float], direction: Sequence[float],
        reach_m: Optional[float] = None, step_m: Optional[float] = None,
    ) -> Optional[Tuple[int, float]]:
        """First room reached from ``origin`` along ``direction``. Returns (cell index, distance)."""
        reach = self.serves_reach_m if reach_m is None else reach_m
        step_len = self.serves_step_m if step_m is None else step_m
        steps = max(1, int(reach / max(1e-6, step_len)))
        for step in range(1, steps + 1):
            dist = step * step_len
            point = (
                origin[0] + direction[0] * dist,
                origin[1] + direction[1] * dist,
                origin[2] + direction[2] * dist,
            )
            hits = self._probe(index, point)
            if hits:
                return hits[0], round(dist, 4)
        return None

    def _room_in_column(
        self, index: RoomIndex, point: Sequence[float], downward: bool, reach_m: float
    ) -> Optional[Tuple[int, float]]:
        """Nearest room whose top (downward) or floor (upward) is within reach of ``point``.

        Uses the room AABBs analytically instead of marching: the candidate is probed just
        inside its own top/floor, so it costs one or two kernel calls and returns the exact
        gap. The second, deeper probe covers rooms whose ceiling is not flat at that XY.
        """
        x, y, z = point[0], point[1], point[2]
        best: Optional[Tuple[float, int, float]] = None  # (gap, cell index, probe z)
        for ci in index.column(x, y):
            a = index.cells[ci].aabb
            if downward:
                gap = z - a[5]
                probe_z = a[5]
            else:
                gap = a[2] - z
                probe_z = a[2]
            if gap < 0 or gap > reach_m:
                continue
            if best is None or gap < best[0]:
                best = (gap, ci, probe_z)
        if best is None:
            return None
        gap, ci, probe_z = best
        room = index.cells[ci]
        for inset in (0.05, 0.3):
            pz = probe_z - inset if downward else probe_z + inset
            self._probe_count += 1
            if room.cell is None:
                if point_in_aabb((x, y, pz), room.aabb):
                    return ci, round(gap, 4)
                continue
            if probe_status(room.cell, vertex_at((x, y, pz))) == STATUS_INSIDE:
                return ci, round(gap, 4)
        return None

    # -- main ------------------------------------------------------------------------

    def extract(self) -> None:
        t0 = time.time()
        space_path = self._resolve_space_file()
        if space_path is None:
            self._summary = {
                "error": "no_space_model",
                "rooms_indexed": 0,
                "hint": "Pass the IFC holding the IfcSpace rooms alongside the discipline model.",
            }
            self.log.error("space_interactions: no input file contains IfcSpace")
            return

        space_ifc = ifcopenshell.open(str(space_path))
        index = RoomIndex.build(
            space_ifc, self.log, grid_m=self.grid_m, use_cells=self.use_topologic
        )
        if not index.cells:
            self._summary = {
                "error": "no_room_cells",
                "spaces_total": index.space_count,
                "rooms_indexed": 0,
                "rooms_unusable": len(index.unusable),
                "hint": "Every IfcSpace failed to resolve to a solid cell.",
            }
            self.log.error("space_interactions: 0 room cells from %d IfcSpace", index.space_count)
            return

        element_paths = [p for p in self.ifc_files if p != space_path] or [space_path]
        settings = mesh_settings()

        seen_gids: set = set()
        stats = {
            "elements_with_geometry": 0,
            "element_occurrences": 0,
            "duplicate_elements_skipped": 0,
            "elements_without_candidate_room": 0,
            "terminals": 0,
        }
        by_type: Dict[str, int] = {}
        by_method: Dict[str, int] = {}
        multi_room = 0
        max_rooms = 0
        # Running union, not a list: at project scale a box per element is pure ballast
        # and this pass streams geometry precisely so it never has to hold it.
        elements_box: Optional[Tuple[float, ...]] = None

        for path in element_paths:
            ifc = ifcopenshell.open(str(path))
            terminal_classes = _mep_ports.terminal_classes(ifc)
            ports = _mep_ports.element_ports(ifc) if self.include_serves_space else {}
            systems = _mep_ports.systems_by_member(ifc) if self.include_serves_space else {}
            unit_scale = _unit_scale(ifc)

            for record in self._iter_geometry(ifc, settings, path):
                gid, entity, verts, faces, matrix = record
                stats["element_occurrences"] += 1
                if gid in seen_gids:
                    # The same GUID exported into several sub-models: the first input
                    # file that carries it wins, so the choice follows input order.
                    stats["duplicate_elements_skipped"] += 1
                    continue
                seen_gids.add(gid)
                stats["elements_with_geometry"] += 1

                box = world_aabb(verts, matrix)
                elements_box = _union(elements_box, box)
                pad = self.bounds_offset_m if self.include_bounds else 0.0
                if not index.candidates_near(box, pad=pad):
                    stats["elements_without_candidate_room"] += 1
                    continue

                emitted = self._relate_element(
                    index=index,
                    gid=gid,
                    entity=entity,
                    verts=verts,
                    faces=faces,
                    matrix=matrix,
                    box=box,
                    source_file=path.name,
                    space_file=space_path.name,
                    terminal_classes=terminal_classes,
                    ports=ports,
                    systems=systems,
                    unit_scale=unit_scale,
                    stats=stats,
                )
                for kind, method in emitted:
                    by_type[kind] = by_type.get(kind, 0) + 1
                    by_method[method] = by_method.get(method, 0) + 1
                passes_here = sum(1 for kind, _ in emitted if kind == "passes_through")
                if passes_here > 1:
                    multi_room += 1
                max_rooms = max(max_rooms, passes_here)

        # Iterator completion order varies with thread count, so the emitted order must
        # not. Sorting here is what makes the output content-hash reproducible.
        self._relationships.sort(
            key=lambda r: (r.subject_global_id, r.relationship_type, r.object_global_id)
        )

        rooms_box = index.bbox
        overlap = _space_index.aabb_overlap_fraction(rooms_box, elements_box)
        frame_suspect = overlap <= 0.0
        if frame_suspect:
            self.log.warning(
                "space_interactions: room and element bounds do not overlap "
                "(rooms=%s elements=%s) -- check units/placement between the two models",
                rooms_box, elements_box,
            )

        self._summary = {
            "method": "interior_sample_probe" if self.use_topologic else "aabb_sample_probe",
            "precision": "sampled",
            "space_file": space_path.name,
            "element_files": [p.name for p in element_paths],
            "spaces_total": index.space_count,
            "rooms_indexed": len(index.cells),
            "rooms_unusable": len(index.unusable),
            "unusable_rooms": index.unusable[:50],
            "min_detectable_chord_m": self.sample_spacing_m,
            "sample_spacing_m": self.sample_spacing_m,
            "probe_tolerance_m": self.tolerance,
            "bounds_offset_m": self.bounds_offset_m if self.include_bounds else None,
            "serves_reach_m": self.serves_reach_m if self.include_serves_space else None,
            "vertical_reach_m": self.vertical_reach_m if self.include_vertical else None,
            "below_reach_m": self.below_reach_m if self.include_vertical else None,
            "num_threads": self.num_threads,
            "probes": self._probe_count,
            "probe_errors": self._probe_errors,
            "duplicate_edges_merged": self._duplicate_edges,
            "boundary_status_hits": self._boundary_hits,
            "multi_room_elements": multi_room,
            "max_rooms_per_element": max_rooms,
            "by_type": dict(sorted(by_type.items())),
            "by_method": dict(sorted(by_method.items())),
            "frame_check": {
                "roomsBbox": list(rooms_box) if rooms_box else None,
                "elementsBbox": list(elements_box) if elements_box else None,
                "overlapFraction": round(overlap, 6),
                "suspect": frame_suspect,
            },
            "engine": _engine_versions(),
            "script_version": SCRIPT_VERSION,
            "elapsed_seconds": round(time.time() - t0, 3),
            **{k: v for k, v in stats.items()},
        }
        self.log.info(
            "space_interactions: %d edges from %d elements against %d room cells in %.1fs",
            len(self._relationships), stats["elements_with_geometry"],
            len(index.cells), time.time() - t0,
        )

    # -- per-element ------------------------------------------------------------------

    def _relate_element(
        self, *, index, gid, entity, verts, faces, matrix, box, source_file, space_file,
        terminal_classes, ports, systems, unit_scale, stats,
    ) -> List[Tuple[str, str]]:
        ifc_class = entity.is_a()
        emitted: List[Tuple[str, str]] = []

        # Probe BOTH ways before emitting anything. A room the element enters and also
        # touches is reported once, as passes_through, carrying `alsoBounds` -- which is
        # how the centre-line modelling convention (rooms drawn to the middle of the
        # wall, so the wall's interior really is inside the room) stays visible instead
        # of being indistinguishable from plain containment.
        hits: Dict[int, Dict[str, Any]] = {}
        sample_total = 0
        axis = 0
        local_points: List[Tuple[float, float, float]] = []
        # Stations inside no room at all: the only ones that may look up or down. A
        # station inside room A must not find the storey below through A's floor.
        void_stations: List[Tuple[float, float, float]] = []
        if self.include_passes_through:
            local_points, axis, _length = axis_centreline_points(
                verts, self.sample_spacing_m, self.max_samples, self.min_sample_sep_m
            )
            sample_total = len(local_points)
            for local in local_points:
                world = transform_point(matrix, *local)
                station_hits = self._probe(index, world)
                if not station_hits:
                    void_stations.append(world)
                for ci in station_hits:
                    slot = hits.setdefault(ci, {"count": 0, "first": None, "last": None})
                    slot["count"] += 1
                    if slot["first"] is None:
                        slot["first"] = world
                    slot["last"] = world

        # bounds: outward face-offset probes, building elements only. A duct does not
        # bound a room, and skipping MEP is also what keeps the big models cheap.
        bound_hits: Dict[int, int] = {}
        bound_probe_count = 0
        if self.include_bounds and ifc_class in BOUNDS_CLASSES:
            probes = face_probe_points(verts, faces, max_faces=self.bounds_max_faces)
            bound_probe_count = len(probes)
            for local_centroid, local_normal in probes:
                world_c = transform_point(matrix, *local_centroid)
                # Rotate the normal only (no translation column).
                nx = matrix[0] * local_normal[0] + matrix[4] * local_normal[1] + matrix[8] * local_normal[2]
                ny = matrix[1] * local_normal[0] + matrix[5] * local_normal[1] + matrix[9] * local_normal[2]
                nz = matrix[2] * local_normal[0] + matrix[6] * local_normal[1] + matrix[10] * local_normal[2]
                mag = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
                nx, ny, nz = nx / mag, ny / mag, nz / mag
                for sign in (1.0, -1.0):
                    point = (
                        world_c[0] + nx * self.bounds_offset_m * sign,
                        world_c[1] + ny * self.bounds_offset_m * sign,
                        world_c[2] + nz * self.bounds_offset_m * sign,
                    )
                    for ci in self._probe(index, point):
                        bound_hits[ci] = bound_hits.get(ci, 0) + 1

        entered = {index.cells[ci].global_id for ci in hits}
        touching = {index.cells[ci].global_id for ci in bound_hits}

        for ci in sorted(hits, key=lambda i: index.cells[i].global_id):
            room = index.cells[ci]
            info = hits[ci]
            method = "interior_sample_probe" if self.use_topologic else "aabb_sample_probe"
            confidence = (
                _CONF_DEGRADED if not self.use_topologic
                else (_CONF_PASSES_MULTI if info["count"] >= 2 else _CONF_PASSES_SINGLE)
            )
            emitted_now = self._emit(
                subject=gid, room=room, rel_type="passes_through", confidence=confidence,
                evidence=self._evidence(
                    method=method, room=room, ifc_class=ifc_class, entity=entity,
                    source_file=source_file, space_file=space_file,
                    samples={
                        "total": sample_total, "hits": info["count"],
                        "hitShare": round(info["count"] / sample_total, 6) if sample_total else None,
                        "strategy": "axis_centreline_centroid",
                        "spacingM": self.sample_spacing_m,
                        "axis": "xyz"[axis],
                        "budgetExhausted": sample_total >= self.max_samples,
                    },
                    probe={"toleranceM": self.tolerance, "offsetM": None, "rayDistanceM": None},
                    extra={
                        "hitSpanM": _distance(info["first"], info["last"]),
                        "firstHitPoint": _round3(info["first"]),
                        "alsoBounds": room.global_id in touching,
                    },
                ),
            )
            if emitted_now:
                emitted.append(("passes_through", method))

        for ci in sorted(bound_hits, key=lambda i: index.cells[i].global_id):
            room = index.cells[ci]
            if room.global_id in entered:
                continue  # already reported as passes_through, flagged alsoBounds
            emitted_now = self._emit(
                subject=gid, room=room, rel_type="bounds", confidence=_CONF_BOUNDS,
                evidence=self._evidence(
                    method="surface_offset_probe", room=room, ifc_class=ifc_class,
                    entity=entity, source_file=source_file, space_file=space_file,
                    samples={
                        "total": bound_probe_count, "hits": bound_hits[ci],
                        "hitShare": (
                            round(bound_hits[ci] / bound_probe_count, 6)
                            if bound_probe_count else None
                        ),
                        "strategy": "face_normal_offset",
                        "spacingM": None,
                        "axis": None,
                        "budgetExhausted": bound_probe_count >= self.bounds_max_faces,
                    },
                    probe={
                        "toleranceM": self.tolerance,
                        "offsetM": self.bounds_offset_m,
                        "rayDistanceM": None,
                    },
                ),
            )
            if emitted_now:
                emitted.append(("bounds", "surface_offset_probe"))

        # above_space / below_space -- from every VOID centreline station, so a tray
        # running over three rooms' ceilings relates to all three. Down first; a station
        # that found the room it is above never looks up, because what is above it is the
        # next storey through the slab. Rooms the element enters are excluded.
        if self.include_vertical and void_stations:
            vertical: Dict[Tuple[str, int], Dict[str, Any]] = {}
            for world in void_stations:
                # The gap is from the element's FACE, not its centreline: bottom face
                # down to the room top, top face up to the room floor.
                found = self._room_in_column(
                    index, (world[0], world[1], box[2]), downward=True, reach_m=self.vertical_reach_m
                )
                rel_type = "above_space"
                if found is None:
                    found = self._room_in_column(
                        index, (world[0], world[1], box[5]), downward=False, reach_m=self.below_reach_m
                    )
                    rel_type = "below_space"
                if found is None:
                    continue
                ci, gap = found
                if index.cells[ci].global_id in entered:
                    continue
                slot = vertical.setdefault((rel_type, ci), {"hits": 0, "min": gap})
                slot["hits"] += 1
                slot["min"] = min(slot["min"], gap)

            for (rel_type, ci) in sorted(vertical, key=lambda k: (k[0], index.cells[k[1]].global_id)):
                room = index.cells[ci]
                info = vertical[(rel_type, ci)]
                method = "ray_down" if rel_type == "above_space" else "ray_up"
                emitted_now = self._emit(
                    subject=gid, room=room, rel_type=rel_type,
                    confidence=_CONF_ABOVE if rel_type == "above_space" else _CONF_BELOW,
                    evidence=self._evidence(
                        method=method, room=room, ifc_class=ifc_class, entity=entity,
                        source_file=source_file, space_file=space_file,
                        samples={
                            "total": len(local_points), "hits": info["hits"],
                            "hitShare": round(info["hits"] / len(local_points), 6),
                            "strategy": "axis_centreline_vertical_column",
                            "spacingM": self.sample_spacing_m,
                            "axis": "xyz"[axis],
                            "budgetExhausted": len(local_points) >= self.max_samples,
                        },
                        probe={
                            "toleranceM": self.tolerance, "offsetM": None,
                            "rayDistanceM": info["min"],
                        },
                        extra={
                            "verticalReachM": (
                                self.vertical_reach_m if rel_type == "above_space" else self.below_reach_m
                            ),
                        },
                    ),
                )
                if emitted_now:
                    emitted.append((rel_type, method))

        # 3. serves_space -- terminals only, first rung that resolves wins.
        if self.include_serves_space and _mep_ports.is_terminal(entity, terminal_classes):
            stats["terminals"] += 1
            resolved = self._serves(index, gid, box, hits, ports, unit_scale)
            if resolved is not None:
                ci, method, distance = resolved
                room = index.cells[ci]
                emitted_now = self._emit(
                    subject=gid, room=room, rel_type="serves_space",
                    confidence=_SERVES_CONFIDENCE.get(method, 0.6),
                    evidence=self._evidence(
                        method=method, room=room, ifc_class=ifc_class, entity=entity,
                        source_file=source_file, space_file=space_file,
                        samples=None,
                        probe={
                            "toleranceM": self.tolerance, "offsetM": None,
                            "rayDistanceM": distance,
                        },
                        extra={
                            "system": _mep_ports.primary_system(systems.get(gid)),
                            "terminalType": _mep_ports.type_name_of(entity) or None,
                        },
                    ),
                )
                if emitted_now:
                    emitted.append(("serves_space", method))

        return emitted

    def _serves(self, index, gid, box, hits, ports, unit_scale):
        if hits:
            ci = sorted(hits, key=lambda i: index.cells[i].global_id)[0]
            return ci, "containment", None

        centre = ((box[0] + box[3]) / 2.0, (box[1] + box[4]) / 2.0, (box[2] + box[5]) / 2.0)
        down = self._march(index, (centre[0], centre[1], box[2]), (0.0, 0.0, -1.0))
        up = self._march(index, (centre[0], centre[1], box[5]), (0.0, 0.0, 1.0))
        if down and up:
            return (down[0], "ray_down", down[1]) if down[1] <= up[1] else (up[0], "ray_up", up[1])
        if down:
            return down[0], "ray_down", down[1]
        if up:
            return up[0], "ray_up", up[1]

        for port in ports.get(gid, []):
            origin = _port_point(port, unit_scale)
            if origin is None:
                continue
            dx, dy, dz = centre[0] - origin[0], centre[1] - origin[1], centre[2] - origin[2]
            mag = math.sqrt(dx * dx + dy * dy + dz * dz)
            if mag <= 1e-9:
                continue
            found = self._march(index, centre, (dx / mag, dy / mag, dz / mag))
            if found:
                return found[0], "port_ray", found[1]
        return None

    # -- emission ---------------------------------------------------------------------

    def _emit(self, *, subject: str, room, rel_type: str, confidence: float, evidence: Dict[str, Any]) -> bool:
        key = (subject, rel_type, room.global_id)
        if key in self._seen_edges:
            self._duplicate_edges += 1
            return False
        self._seen_edges.add(key)
        self._relationships.append(
            Relationship(
                subject_global_id=subject,
                object_global_id=room.global_id,
                relationship_family="spatial",
                relationship_type=rel_type,
                confidence=confidence,
                source_kind=SOURCE_KIND,
                evidence=evidence,
            )
        )
        return True

    def _evidence(
        self, *, method, room, ifc_class, entity, source_file, space_file,
        samples, probe, extra=None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "method": method,
            "precision": "sampled",
            # RESERVED, always present so a consumer can branch on it today. An exact
            # Boolean pass later fills {"overlapVolumeM3": ..., "method": ...} and flips
            # `precision` to "exact" -- additive, no migration, no re-run of this pass.
            "extent": None,
            "minDetectableChordM": self.sample_spacing_m,
            "probe": probe,
            "subjectClass": ifc_class,
            "objectClass": "IfcSpace",
            "objectName": room.name,
            "objectLongName": room.long_name,
            "storey": room.storey,
            "sourceFile": source_file,
            "spaceFile": space_file,
            "cellKind": room.kind,
            "roomCellIndex": room.cell_index,
            "roomCells": room.cell_count,
            "engine": _engine_versions(),
            "scriptVersion": SCRIPT_VERSION,
            "state": "assumed",
        }
        if samples is not None:
            payload["samples"] = samples
        if extra:
            payload.update({k: v for k, v in extra.items() if v is not None or k == "extent"})
        return payload

    # -- geometry streaming -------------------------------------------------------------

    def _iter_geometry(self, ifc, settings, path: Path) -> Iterable[Tuple[str, Any, Any, Any, List[float]]]:
        """Stream (gid, entity, verts, faces, matrix). Nothing is retained between items."""
        by_gid = {
            e.GlobalId: e
            for e in safe_by_type(ifc, self.element_query)
            if getattr(e, "GlobalId", None) and e.is_a() not in SKIP_CLASSES
        }
        if not by_gid:
            return
        try:
            iterator = ifcopenshell.geom.iterator(settings, ifc, self.num_threads)
            if not iterator.initialize():
                return
        except Exception:
            self.log.warning("space_interactions: geometry iterator unavailable for %s", path.name, exc_info=True)
            return
        while True:
            shape = iterator.get()
            gid = getattr(shape, "guid", None)
            entity = by_gid.get(gid)
            if entity is not None:
                geometry = shape.geometry
                verts = geometry.verts
                if verts:
                    matrix = shape.transformation.matrix
                    yield gid, entity, verts, geometry.faces, list(getattr(matrix, "data", matrix))
            if not iterator.next():
                break


# --- small helpers -------------------------------------------------------------------

def _union(current, box):
    """Grow an AABB by one more box."""
    if current is None:
        return box
    return (
        min(current[0], box[0]), min(current[1], box[1]), min(current[2], box[2]),
        max(current[3], box[3]), max(current[4], box[4]), max(current[5], box[5]),
    )


def _distance(a: Optional[Sequence[float]], b: Optional[Sequence[float]]) -> Optional[float]:
    if a is None or b is None:
        return None
    return round(math.dist(a, b), 4)


def _round3(point: Optional[Sequence[float]]) -> Optional[List[float]]:
    if point is None:
        return None
    return [round(float(v), 6) for v in point]


def _unit_scale(ifc) -> float:
    try:
        import ifcopenshell.util.unit

        return float(ifcopenshell.util.unit.calculate_unit_scale(ifc))
    except Exception:
        return 1.0


def _port_point(port, unit_scale: float) -> Optional[Tuple[float, float, float]]:
    try:
        import ifcopenshell.util.placement

        placement = getattr(port, "ObjectPlacement", None)
        if placement is None:
            return None
        matrix = ifcopenshell.util.placement.get_local_placement(placement)
        return (
            float(matrix[0][3]) * unit_scale,
            float(matrix[1][3]) * unit_scale,
            float(matrix[2][3]) * unit_scale,
        )
    except Exception:
        return None


def _engine_versions() -> Dict[str, str]:
    versions = {"ifcopenshell": getattr(ifcopenshell, "version", "") or ""}
    try:
        import topologicpy

        versions["topologicpy"] = getattr(topologicpy, "__version__", "") or ""
    except Exception:
        versions["topologicpy"] = ""
    return versions
