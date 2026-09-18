"""Room-cell index for cross-model element-to-space queries.

Build the room solids ONCE and probe many element points against them, rather than
testing element x room pairs with Booleans. ``Topology.Contains`` is a true whole-body
test but costs ~193 ms per pair and a pairwise federated recipe has been measured to
blow a 120 s deadline down to six rooms; ``Cell.ContainmentStatus`` at tolerance 0
costs ~0.26 ms, so 243 room cells built once (~1.1 s) answer tens of thousands of
point queries in seconds.

Used by ``SpaceInteractions``. Kept inside ``ingest_scripts/`` on purpose: the bench
harness rsyncs only this package, so importing ``tasks.py`` would silently bind to the
container's stale copy.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import ifcopenshell
import ifcopenshell.geom

from ingest_scripts import safe_by_type

# A "cell" below this volume is a stray face with no interior to probe. Such a room is
# REPORTED as unusable rather than silently skipped.
MIN_CELL_VOLUME_M3 = 1e-6

# tolerance=0 is a CORRECTNESS choice, not a performance one. In topologicpy a positive
# tolerance is a metric OUTWARD DILATION of the solid by that many metres (it builds a
# prism around the query point and takes the min over nine probes), so 1e-4 would report
# a point 0.1 mm outside a wall as inside it -- and costs ~70x. `Cell.ContainmentStatus`
# defaults to 1e-4, so every call site must pass this explicitly.
PROBE_TOLERANCE_M = 0.0

# Cell.ContainmentStatus return codes.
STATUS_INSIDE = 0
STATUS_ON_BOUNDARY = 1
STATUS_OUTSIDE = 2
STATUS_ERROR = -1

Aabb = Tuple[float, float, float, float, float, float]


def cpu_allowance() -> int:
    """Usable CPUs for the geometry iterator: the cgroup v2 quota when set, else cpu_count-1.

    In a container ``os.cpu_count()`` reports the HOST's cores while the cgroup may allow
    far fewer; oversubscribing the iterator is measurably slower. (Same rule as
    ``FederatedRelationships._cpu_allowance``; duplicated rather than imported so that
    module's measured bench fingerprint stays untouched.)
    """
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()[:2]
        if quota != "max":
            return max(1, -(-int(quota) // int(period)))
    except (OSError, ValueError):
        pass
    return max(1, (os.cpu_count() or 2) - 1)


def native_settings():
    """Geometry settings that yield an OpenCascade solid per entity, in world metres."""
    from ifcopenshell import ifcopenshell_wrapper

    settings = ifcopenshell.geom.settings()
    settings.set("iterator-output", ifcopenshell_wrapper.NATIVE)
    settings.set("use-world-coords", True)
    settings.set("convert-back-units", False)
    # MANDATORY. Without it every room serializes fine and Topology.Cells returns ZERO
    # cells for ALL of them -- the script then emits no edges and reports success. The
    # `rooms_indexed` count in the summary is the canary for this.
    settings.set("reorient-shells", True)
    return settings


def mesh_settings():
    """Triangulated settings in LOCAL coords for the element side.

    No USE_WORLD_COORDS: it lets the iterator reuse tessellation across identical
    representations (~270x on a real model, and these models carry thousands of
    IfcMappedItem). Sample points are transformed individually with the element's
    placement matrix instead.
    """
    return ifcopenshell.geom.settings()


def aabb_settings():
    """Triangulated settings in WORLD coords: bounding boxes without an OCCT solid."""
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    return settings


def _verts_aabb(verts: Sequence[float]) -> Optional[Aabb]:
    if not verts:
        return None
    xs, ys, zs = verts[0::3], verts[1::3], verts[2::3]
    return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


def world_aabb(verts: Sequence[float], m: Sequence[float]) -> Aabb:
    """World AABB from local ``verts`` + a 16-float column-major placement matrix.

    Transforms the 8 corners of the local bbox, not every vertex.
    """
    xs, ys, zs = verts[0::3], verts[1::3], verts[2::3]
    lx0, ly0, lz0 = min(xs), min(ys), min(zs)
    lx1, ly1, lz1 = max(xs), max(ys), max(zs)
    wx: List[float] = []
    wy: List[float] = []
    wz: List[float] = []
    for x in (lx0, lx1):
        for y in (ly0, ly1):
            for z in (lz0, lz1):
                wx.append(m[0] * x + m[4] * y + m[8] * z + m[12])
                wy.append(m[1] * x + m[5] * y + m[9] * z + m[13])
                wz.append(m[2] * x + m[6] * y + m[10] * z + m[14])
    return (min(wx), min(wy), min(wz), max(wx), max(wy), max(wz))


def transform_point(m: Sequence[float], x: float, y: float, z: float) -> Tuple[float, float, float]:
    """Apply a 16-float column-major placement matrix to one local point."""
    return (
        m[0] * x + m[4] * y + m[8] * z + m[12],
        m[1] * x + m[5] * y + m[9] * z + m[13],
        m[2] * x + m[6] * y + m[10] * z + m[14],
    )


def point_in_aabb(point: Sequence[float], box: Aabb) -> bool:
    return (
        box[0] <= point[0] <= box[3]
        and box[1] <= point[1] <= box[4]
        and box[2] <= point[2] <= box[5]
    )


def aabb_union(boxes: List[Aabb]) -> Optional[Aabb]:
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes), min(b[1] for b in boxes), min(b[2] for b in boxes),
        max(b[3] for b in boxes), max(b[4] for b in boxes), max(b[5] for b in boxes),
    )


def aabb_overlap_fraction(a: Optional[Aabb], b: Optional[Aabb]) -> float:
    """Fraction of the smaller box's volume covered by the intersection. 0.0 when disjoint.

    Used as the coordinate-frame sanity check: two independently authored models whose
    units or placements disagree produce a perfectly well-formed result with zero edges,
    so the mismatch has to be detected and reported rather than assumed away.
    """
    if a is None or b is None:
        return 0.0
    dims = [max(0.0, min(a[i + 3], b[i + 3]) - max(a[i], b[i])) for i in range(3)]
    inter = dims[0] * dims[1] * dims[2]
    if inter <= 0:
        return 0.0
    va = max(1e-12, (a[3] - a[0]) * (a[4] - a[1]) * (a[5] - a[2]))
    vb = max(1e-12, (b[3] - b[0]) * (b[4] - b[1]) * (b[5] - b[2]))
    return inter / min(va, vb)


def cells_of(brep: str) -> List:
    """Cells of a serialized BRep, largest first.

    Always resolve to CELLS. ``Vertex.IsInternal`` on the Cluster that ``ByBREPString``
    returns prunes on an AABB built from the explicit B-Rep vertices, and a pipe has two
    of those, on its seam -- that wrongly excluded 8.9% of real curved bodies upstream.
    """
    from topologicpy.Cell import Cell
    from topologicpy.Topology import Topology

    topology = Topology.ByBREPString(brep, silent=True)
    if topology is None:
        return []
    raw = Topology.Cells(topology, silent=True) or []
    # mantissa=12: Cell.Volume defaults to 6, which rounds anything below 5e-7 to 0.0
    # and would compare it against a 1e-6 threshold.
    kept = [c for c in raw if (Cell.Volume(c, mantissa=12) or 0.0) > MIN_CELL_VOLUME_M3]
    return sorted(kept, key=lambda c: -(Cell.Volume(c, mantissa=12) or 0.0))


def probe_status(cell, vertex) -> int:
    """0 inside / 1 on boundary / 2 outside / -1 kernel error. Never raises."""
    from topologicpy.Cell import Cell

    try:
        return int(Cell.ContainmentStatus(cell, vertex, tolerance=PROBE_TOLERANCE_M))
    except Exception:
        return STATUS_ERROR


def vertex_at(point: Sequence[float]):
    from topologicpy.Vertex import Vertex

    return Vertex.ByCoordinates(float(point[0]), float(point[1]), float(point[2]))


def _storey_of(entity) -> str:
    for rel in getattr(entity, "Decomposes", []) or []:
        parent = getattr(rel, "RelatingObject", None)
        if parent is not None and parent.is_a("IfcBuildingStorey"):
            return str(getattr(parent, "Name", "") or "")
    return ""


@dataclass
class RoomCell:
    """One topologic cell of one IfcSpace. A space may resolve to several."""

    global_id: str
    name: str
    long_name: str
    storey: str
    cell: Any
    aabb: Aabb
    cell_index: int
    cell_count: int
    kind: str = "brep_cell"


@dataclass
class RoomIndex:
    cells: List[RoomCell] = field(default_factory=list)
    unusable: List[Dict[str, Any]] = field(default_factory=list)
    grid: Dict[Tuple[int, int], List[int]] = field(default_factory=dict)
    grid_m: float = 3.0
    space_count: int = 0

    @property
    def bbox(self) -> Optional[Aabb]:
        return aabb_union([c.aabb for c in self.cells])

    @classmethod
    def build(
        cls, ifc, log: logging.Logger, grid_m: float = 3.0, use_cells: bool = True
    ) -> "RoomIndex":
        """Index every IfcSpace once.

        ``use_cells=False`` degrades each room to its bounding box: coarser, but it
        needs no OpenCascade kernel, which is what the worker's post-SIGSEGV retry
        falls back to. A box answer is worse than a solid one and far better than none.
        """
        index = cls(grid_m=float(grid_m))
        settings = native_settings() if use_cells else aabb_settings()
        spaces = sorted(
            [s for s in safe_by_type(ifc, "IfcSpace") if getattr(s, "GlobalId", None)],
            key=lambda s: s.GlobalId,
        )
        index.space_count = len(spaces)

        for space in spaces:
            gid = str(space.GlobalId)
            box_only: Optional[Aabb] = None
            try:
                # Hold the shape in a local. Reading `.geometry` off the bare
                # create_shape(...) temporary is a use-after-free in ifcopenshell 0.8.x
                # that hard-kills the interpreter with no traceback.
                shape = ifcopenshell.geom.create_shape(settings, space)
                if use_cells:
                    solid = shape.geometry.as_compound(force_meters=True)
                    cells = cells_of(solid.serialize())
                else:
                    verts = shape.geometry.verts
                    cells = [None] if verts else []
                    box_only = _verts_aabb(verts) if verts else None
            except Exception as exc:
                index.unusable.append(
                    {"global_id": gid, "reason": "shape_failed", "error_type": type(exc).__name__}
                )
                continue

            if not cells:
                # Deliberately NO convex-hull fallback: one real room has a 21,223 m3
                # bbox against a 2,734 m3 true volume, and its hull would swallow the
                # neighbouring rooms. An unusable room is reported, not approximated.
                index.unusable.append({"global_id": gid, "reason": "no_cell_above_minimum_volume"})
                continue

            name = str(getattr(space, "Name", "") or "")
            long_name = str(getattr(space, "LongName", "") or "")
            storey = _storey_of(space)
            total = len(cells)
            for ci, cell in enumerate(cells):
                aabb = _cell_aabb(cell) if cell is not None else box_only
                if aabb is None:
                    index.unusable.append({"global_id": gid, "reason": "no_cell_bounds"})
                    continue
                index.cells.append(
                    RoomCell(
                        global_id=gid, name=name, long_name=long_name, storey=storey,
                        cell=cell, aabb=aabb, cell_index=ci, cell_count=total,
                        kind="brep_cell" if cell is not None else "aabb",
                    )
                )

        index._build_grid()
        log.info(
            "space_index: %d IfcSpace -> %d cells (%d unusable)",
            index.space_count, len(index.cells), len(index.unusable),
        )
        return index

    def _build_grid(self) -> None:
        self.grid = {}
        g = self.grid_m
        for idx, room in enumerate(self.cells):
            a = room.aabb
            for cx in range(int(a[0] // g), int(a[3] // g) + 1):
                for cy in range(int(a[1] // g), int(a[4] // g) + 1):
                    self.grid.setdefault((cx, cy), []).append(idx)

    def candidates(self, point: Sequence[float]) -> List[int]:
        """Room-cell indices whose AABB contains ``point``, sorted for determinism.

        Point-in-room-bbox, NOT element-bbox-vs-room-bbox: 96% of architecture elements
        have bounds meeting at least one room, so element-box filtering prunes nothing.
        """
        g = self.grid_m
        bucket = self.grid.get((int(point[0] // g), int(point[1] // g)))
        if not bucket:
            return []
        return sorted(i for i in bucket if point_in_aabb(point, self.cells[i].aabb))

    def column(self, x: float, y: float) -> List[int]:
        """Room-cell indices whose AABB contains (x, y) in plan, any height, sorted.

        The vertical relations need "which rooms are stacked under/over this point",
        which is a plan-column question, not a 3D containment one.
        """
        g = self.grid_m
        bucket = self.grid.get((int(x // g), int(y // g)))
        if not bucket:
            return []
        out = []
        for i in bucket:
            a = self.cells[i].aabb
            if a[0] <= x <= a[3] and a[1] <= y <= a[4]:
                out.append(i)
        return sorted(out)

    def candidates_near(self, box: Aabb, pad: float = 0.0) -> List[int]:
        """Room-cell indices whose AABB intersects ``box`` grown by ``pad``."""
        g = self.grid_m
        found: set = set()
        for cx in range(int((box[0] - pad) // g), int((box[3] + pad) // g) + 1):
            for cy in range(int((box[1] - pad) // g), int((box[4] + pad) // g) + 1):
                found.update(self.grid.get((cx, cy), ()))
        out = []
        for i in sorted(found):
            a = self.cells[i].aabb
            if (
                a[0] - pad <= box[3] and a[3] + pad >= box[0]
                and a[1] - pad <= box[4] and a[4] + pad >= box[1]
                and a[2] - pad <= box[5] and a[5] + pad >= box[2]
            ):
                out.append(i)
        return out


def _cell_aabb(cell) -> Optional[Aabb]:
    from topologicpy.Topology import Topology
    from topologicpy.Vertex import Vertex

    try:
        verts = Topology.Vertices(cell) or []
        if not verts:
            return None
        xs = [Vertex.X(v) for v in verts]
        ys = [Vertex.Y(v) for v in verts]
        zs = [Vertex.Z(v) for v in verts]
        return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))
    except Exception:
        return None
