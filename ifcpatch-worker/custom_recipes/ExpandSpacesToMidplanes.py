"""
ExpandSpacesToMidplanes Recipe

Replace every ``IfcSpace`` footprint with one that has been expanded
outwards to the *midplane* between adjacent spaces, plus a configurable
exterior offset on faces that don't touch another space. The intent is
to plug the wall-thickness gap that ifcclash sees between MEP elements
and the room they actually serve — without depending on
``IfcRelSpaceBoundary`` (typically off in Archicad exports).

Algorithm (purely 2D, no boundary entities required):

1. Discover every ``IfcSpace`` matching ``space_selector`` and extract a
   world-XY footprint:
   - Prefer the ``FootPrint`` representation's ``IfcPolyline`` /
     ``IfcIndexedPolyCurve`` (one round-trip per polyline; this is what
     Archicad emits in addition to the Brep body).
   - Fall back to a Body extraction (``IfcArbitraryClosedProfileDef`` on
     an ``IfcExtrudedAreaSolid`` or a brep-vertex hull) when there's no
     FootPrint representation.
   - Apply the space's ``ObjectPlacement`` so all polygons live in the
     same world XY frame; capture the local z-range so we can rebuild
     the body extrusion later.
2. Densify each footprint's exterior every ``densify_step_m`` to produce
   seed points. Tag each seed with its source space index.
3. Build a single ``shapely.ops.voronoi_diagram`` over all seeds, clipped
   to a generous envelope. Each cell is finite and is mapped back to its
   seeding space.
4. Per space, compose the new footprint from three independent pieces
   so toward-neighbour expansion is bounded tighter than exterior:
   - ``inner_part`` = ``(own_voronoi ∪ original) ∩ original.buffer(
     max_midplane_extent_m)`` — the midplane region between this space
     and any neighbour, hard-capped at ``max_midplane_extent_m``.
   - ``outer_part`` = ``original.buffer(default_offset_m).difference(
     unary_union(neighbour.buffer(max_midplane_extent_m) for neighbour
     in nearby_others))`` — the pure-exterior cap, *only* in directions
     where no neighbour's halo touches the cap. ``nearby_others`` is
     resolved with a ``shapely.strtree.STRtree`` query over the
     original-footprint envelope at radius ``default_offset_m +
     max_midplane_extent_m`` (so the algorithm stays O(N log N)).
   - ``expanded`` = ``unary_union(original, inner_part, outer_part)`` —
     the union guarantees no shrinkage; ``outer_part`` guarantees the
     full exterior reach where no neighbour competes.
5. Rewrite the space's ``Body`` representation as a fresh
   ``IfcExtrudedAreaSolid`` over the expanded footprint at the original
   local z-range; preserve ``GlobalId``, ``Name``, ``LongName``,
   ``Description``, every pset, and every relationship.

Positional arguments (ifcpatch convention; empty values fall back to
defaults, matching the n8n IfcPatch node's blank-slot semantics):

    1. space_selector          (default ``IfcSpace``)
    2. default_offset_m        (default ``0.25``) — the exterior cap.
    3. densify_step_m          (default ``0.1``)
    4. min_area_m2             (default ``0.5``)
    5. preserve_z              (default ``true``; ``false`` also
                                extends the extrusion top and bottom
                                by ``default_offset_m``)
    6. max_midplane_extent_m   (default ``0.15``) — the toward-neighbour
                                cap. Bounded above by ``default_offset_m``
                                (clamped with a warning if a caller
                                exceeds it); setting it equal to
                                ``default_offset_m`` reproduces the
                                pre-2026-05-17 single-cap behaviour.

The recipe is bind-mounted at ``custom_recipes/`` and spawned in its
own subprocess by the ifcpatch worker, so editing the file picks up
immediately.

Recipe Name: ExpandSpacesToMidplanes
Author: jonatan.jacobsson + cursor agent (2026-05)
"""

from __future__ import annotations

import logging
import math
from logging import Logger
from typing import Dict, List, Optional, Tuple, Union

import ifcopenshell
import ifcopenshell.guid
import ifcopenshell.util.element
import ifcopenshell.util.placement as up
import numpy as np

try:  # shapely is added in requirements; absence is a hard failure
    from shapely.geometry import (
        GeometryCollection,
        MultiPoint,
        MultiPolygon,
        Polygon,
    )
    from shapely.ops import unary_union, voronoi_diagram
    _SHAPELY_OK = True
except Exception as _e:  # pragma: no cover - exercised by Phase 3 rebuild
    _SHAPELY_IMPORT_ERROR = _e
    _SHAPELY_OK = False


_TRUE_STRS = {"1", "true", "t", "yes", "y", "on"}


def _to_float(raw, default: float) -> float:
    """Tolerate the worker's blank-slot convention ('' or ' ' → default)."""
    if raw is None:
        return default
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if not text:
        return default
    return float(text)


def _to_bool(raw, default: bool) -> bool:
    if raw is None or raw == "":
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in _TRUE_STRS


def _unit_factor_to_meters(ifc_file: ifcopenshell.file) -> Tuple[float, str]:
    """Return ``(factor, label)`` such that ``world_value_m = file_value * factor``.

    Walks ``IfcProject.UnitsInContext`` for a ``LENGTHUNIT``. Supports
    ``IfcSIUnit`` (metre / millimetre / centimetre) and
    ``IfcConversionBasedUnit`` (inch / foot, via the .ConversionFactor
    chain). Defaults to 1.0 with label ``unknown`` if nothing matches —
    upstream is expected to use metres in that fallback.
    """
    try:
        proj = ifc_file.by_type("IfcProject")[0]
        for u in proj.UnitsInContext.Units:
            if u.is_a("IfcSIUnit") and u.UnitType == "LENGTHUNIT":
                prefix = (u.Prefix or "").upper()
                if prefix == "MILLI":
                    return 0.001, "millimetre"
                if prefix == "CENTI":
                    return 0.01, "centimetre"
                if prefix in ("", None):
                    return 1.0, "metre"
                # Other SI prefixes (KILO, DECI, …) — handle generically.
                exponents = {"KILO": 1000, "DECI": 0.1, "MICRO": 1e-6}
                if prefix in exponents:
                    return float(exponents[prefix]), f"{prefix.lower()}metre"
            if u.is_a("IfcConversionBasedUnit") and u.UnitType == "LENGTHUNIT":
                try:
                    cf = u.ConversionFactor
                    val = cf.ValueComponent.wrappedValue
                    return float(val), str(u.Name or "conversion").lower()
                except Exception:
                    return 1.0, str(u.Name or "conversion").lower()
    except Exception:
        pass
    return 1.0, "unknown"


def _extract_2d_polyline(item) -> Optional[List[Tuple[float, float]]]:
    """Pull (x, y) pairs out of a curve-set element. Returns ``None`` if
    the entity isn't a closed planar curve we can handle.
    """
    if item.is_a("IfcPolyline"):
        try:
            pts = [tuple(p.Coordinates[:2]) for p in item.Points]
        except Exception:
            return None
        if len(pts) < 3:
            return None
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        return pts
    if item.is_a("IfcIndexedPolyCurve"):
        try:
            coords = list(item.Points.CoordList)
            # IfcIndexedPolyCurve.Segments can be None (implicit straight
            # polyline through all points) or a list of IfcLineIndex /
            # IfcArcIndex segments. We approximate arcs by their endpoints
            # — good enough for footprints from Archicad/Revit.
            pts = [tuple(c[:2]) for c in coords]
        except Exception:
            return None
        if len(pts) < 3:
            return None
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        return pts
    return None


class Patcher:
    """Expand IfcSpace footprints to the midplane between neighbours."""

    def __init__(
        self,
        file: ifcopenshell.file,
        logger: Union[Logger, None] = None,
        space_selector: str = "IfcSpace",
        default_offset_m: str = "0.25",
        densify_step_m: str = "0.1",
        min_area_m2: str = "0.5",
        preserve_z: str = "true",
        max_midplane_extent_m: str = "0.15",
    ):
        if not _SHAPELY_OK:
            raise RuntimeError(
                "ExpandSpacesToMidplanes requires shapely + scipy in the "
                "worker image; current import error: "
                f"{_SHAPELY_IMPORT_ERROR!r}"
            )
        self.file = file
        self.logger = logger if logger else logging.getLogger(__name__)

        self.space_selector = (space_selector or "IfcSpace").strip() or "IfcSpace"
        self.default_offset_m = _to_float(default_offset_m, 0.25)
        self.densify_step_m = _to_float(densify_step_m, 0.1)
        self.min_area_m2 = _to_float(min_area_m2, 0.5)
        self.preserve_z = _to_bool(preserve_z, True)
        self.max_midplane_extent_m = _to_float(max_midplane_extent_m, 0.15)

        if self.default_offset_m < 0:
            raise ValueError("default_offset_m must be ≥ 0")
        if self.densify_step_m <= 0:
            raise ValueError("densify_step_m must be > 0")
        if self.min_area_m2 < 0:
            raise ValueError("min_area_m2 must be ≥ 0")
        if self.max_midplane_extent_m < 0:
            raise ValueError("max_midplane_extent_m must be ≥ 0")
        # A toward-neighbour cap larger than the exterior cap makes no
        # geometric sense (the exterior cap is the absolute maximum
        # reach in any direction). Clamp + warn instead of raising so
        # callers can set both to the same value for the "old
        # single-cap" path without juggling the inequality.
        if self.max_midplane_extent_m > self.default_offset_m:
            self.logger.warning(
                "max_midplane_extent_m=%g exceeds default_offset_m=%g; "
                "clamping to default_offset_m (no behaviour change "
                "vs the pre-split single-cap recipe)",
                self.max_midplane_extent_m, self.default_offset_m,
            )
            self.max_midplane_extent_m = self.default_offset_m

        self.internal_unit_factor, self.unit_label = _unit_factor_to_meters(self.file)
        # Convert metres → file units once so we can keep the
        # densify/offset values in the file's own units inside the loop.
        self.offset_file_units = self.default_offset_m / self.internal_unit_factor
        self.densify_step_file_units = self.densify_step_m / self.internal_unit_factor
        self.min_area_file_units = self.min_area_m2 / (self.internal_unit_factor ** 2)
        self.max_midplane_extent_file_units = (
            self.max_midplane_extent_m / self.internal_unit_factor
        )

        self.stats: Dict[str, object] = {
            "spaces_total": 0,
            "spaces_expanded": 0,
            "spaces_skipped_no_footprint": 0,
            "spaces_skipped_too_small": 0,
            "spaces_skipped_unsupported_repr": 0,
            "spaces_skipped_voronoi_failed": 0,
            "mean_area_increase_pct": 0.0,
            "max_area_increase_pct": 0.0,
            "total_voronoi_cells": 0,
            "unit_assignment": self.unit_label,
            "internal_unit_factor": self.internal_unit_factor,
            "default_offset_m": self.default_offset_m,
            "densify_step_m": self.densify_step_m,
            "max_midplane_extent_m_resolved": self.max_midplane_extent_m,
            # Populated in patch() — see _accumulate_midplane_stats().
            "mean_midplane_extent_used_m": 0.0,
            "spaces_clamped_at_midplane_cap": 0,
        }

        # Owner history cached once — same pattern as
        # PropagatePropertyFromClashPairs to avoid intermittent SIGSEGV
        # in ifcopenshell on repeated by_type() calls.
        self._owner_history_cache = None

    # ------------------------------------------------------------------
    # Footprint extraction
    # ------------------------------------------------------------------

    def _footprint_local(self, space) -> Optional[List[List[Tuple[float, float]]]]:
        """Return one or more closed (x, y) loops in the space's local frame.

        Resolution order:
          1. FootPrint representation with ``IfcGeometricCurveSet``
             containing ``IfcPolyline`` / ``IfcIndexedPolyCurve`` (typical
             Archicad path — confirmed on all 243 spaces in 0003).
          2. Body representation with ``IfcExtrudedAreaSolid`` whose
             ``SweptArea`` is an ``IfcArbitraryClosedProfileDef`` /
             ``IfcRectangleProfileDef`` (the Revit path).
          3. Body ``IfcFacetedBrep`` projected onto XY — pick the convex
             hull of all vertices.
        """
        rep = space.Representation
        if rep is None:
            return None
        # 1. FootPrint
        for srep in rep.Representations:
            if srep.RepresentationIdentifier != "FootPrint":
                continue
            loops: List[List[Tuple[float, float]]] = []
            for item in srep.Items:
                if item.is_a("IfcGeometricCurveSet"):
                    for el in item.Elements:
                        loop = _extract_2d_polyline(el)
                        if loop:
                            loops.append(loop)
                elif item.is_a("IfcPolyline") or item.is_a("IfcIndexedPolyCurve"):
                    loop = _extract_2d_polyline(item)
                    if loop:
                        loops.append(loop)
            if loops:
                return loops
        # 2. Body / IfcExtrudedAreaSolid
        for srep in rep.Representations:
            if srep.RepresentationIdentifier != "Body":
                continue
            for item in srep.Items:
                if item.is_a("IfcExtrudedAreaSolid"):
                    sa = item.SweptArea
                    if sa.is_a("IfcArbitraryClosedProfileDef"):
                        loop = _extract_2d_polyline(sa.OuterCurve)
                        if loop:
                            return [loop]
                    elif sa.is_a("IfcRectangleProfileDef"):
                        x = float(sa.XDim) / 2
                        y = float(sa.YDim) / 2
                        rect = [(-x, -y), (x, -y), (x, y), (-x, y), (-x, -y)]
                        return [rect]
        # 3. Brep XY hull fallback
        verts = self._collect_brep_xy(space)
        if verts and len(verts) >= 3:
            try:
                hull = MultiPoint(verts).convex_hull
                if hasattr(hull, "exterior"):
                    coords = list(hull.exterior.coords)
                    return [coords]
            except Exception:
                pass
        return None

    @staticmethod
    def _collect_brep_xy(space) -> List[Tuple[float, float]]:
        out: List[Tuple[float, float]] = []
        rep = space.Representation
        if rep is None:
            return out
        for srep in rep.Representations:
            if srep.RepresentationIdentifier != "Body":
                continue
            for item in srep.Items:
                if item.is_a("IfcFacetedBrep"):
                    try:
                        for face in item.Outer.CfsFaces:
                            for bound in face.Bounds:
                                poly = bound.Bound
                                if poly.is_a("IfcPolyLoop"):
                                    for p in poly.Polygon:
                                        out.append((p.Coordinates[0], p.Coordinates[1]))
                    except Exception:
                        continue
        return out

    @staticmethod
    def _local_z_range(space) -> Optional[Tuple[float, float]]:
        """Walk the body brep / extrusion for the local z-extent."""
        rep = space.Representation
        if rep is None:
            return None
        zs: List[float] = []
        for srep in rep.Representations:
            if srep.RepresentationIdentifier != "Body":
                continue
            for item in srep.Items:
                if item.is_a("IfcFacetedBrep"):
                    try:
                        for face in item.Outer.CfsFaces:
                            for bound in face.Bounds:
                                poly = bound.Bound
                                if poly.is_a("IfcPolyLoop"):
                                    for p in poly.Polygon:
                                        zs.append(float(p.Coordinates[2]))
                    except Exception:
                        continue
                elif item.is_a("IfcExtrudedAreaSolid"):
                    try:
                        base_z = float(item.Position.Location.Coordinates[2]) if item.Position else 0.0
                        depth = float(item.Depth)
                        # Extrusion direction is typically (0,0,1); the
                        # depth then runs from base_z → base_z+depth.
                        zs.append(base_z)
                        zs.append(base_z + depth)
                    except Exception:
                        continue
        if not zs:
            return None
        return (min(zs), max(zs))

    @staticmethod
    def _apply_placement_xy(matrix: np.ndarray, loop: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
        """Apply a 4×4 placement matrix to a 2D loop (z=0)."""
        out = []
        for x, y in loop:
            v = matrix @ np.array([x, y, 0.0, 1.0])
            out.append((float(v[0]), float(v[1])))
        return out

    @staticmethod
    def _apply_inverse_xy(matrix: np.ndarray, loop) -> List[Tuple[float, float]]:
        inv = np.linalg.inv(matrix)
        out = []
        for x, y in loop:
            v = inv @ np.array([x, y, 0.0, 1.0])
            out.append((float(v[0]), float(v[1])))
        return out

    @staticmethod
    def _densify_loop(loop: List[Tuple[float, float]], step: float) -> List[Tuple[float, float]]:
        """Walk the closed loop, inserting points every ``step`` along
        each edge. Returns a list of points (not closed).
        """
        if step <= 0 or len(loop) < 2:
            return [tuple(p) for p in loop[:-1]] if len(loop) > 1 else list(loop)
        out: List[Tuple[float, float]] = []
        for i in range(len(loop) - 1):
            x0, y0 = loop[i]
            x1, y1 = loop[i + 1]
            dx, dy = x1 - x0, y1 - y0
            length = math.hypot(dx, dy)
            if length == 0:
                if not out or out[-1] != (x0, y0):
                    out.append((x0, y0))
                continue
            n_steps = max(1, int(math.ceil(length / step)))
            for k in range(n_steps):
                t = k / n_steps
                out.append((x0 + t * dx, y0 + t * dy))
        return out

    # ------------------------------------------------------------------
    # Geometry writers
    # ------------------------------------------------------------------

    def _get_or_create_owner_history(self):
        if self._owner_history_cache is not None:
            return self._owner_history_cache
        existing = self.file.by_type("IfcOwnerHistory")
        if existing:
            self._owner_history_cache = existing[0]
            return self._owner_history_cache
        person = self.file.create_entity("IfcPerson", None, None, None)
        org = self.file.create_entity("IfcOrganization", None, "Unknown")
        person_org = self.file.create_entity("IfcPersonAndOrganization", person, org)
        app = self.file.create_entity(
            "IfcApplication", org, "Unknown", "Unknown", "Unknown"
        )
        self._owner_history_cache = self.file.create_entity(
            "IfcOwnerHistory", person_org, app, None, None, None, None, None, 0,
        )
        return self._owner_history_cache

    def _ensure_body_context(self):
        """Return the IfcGeometricRepresentationContext suitable for the
        new Body representation, falling back to creating a minimal one.
        """
        try:
            for ctx in self.file.by_type("IfcGeometricRepresentationSubContext"):
                if ctx.ContextIdentifier == "Body":
                    return ctx
            for ctx in self.file.by_type("IfcGeometricRepresentationContext"):
                if (ctx.ContextType or "").lower() == "model":
                    return ctx
        except Exception:
            pass
        # Last resort — create a minimal model context.
        return self.file.create_entity(
            "IfcGeometricRepresentationContext",
            None, "Model", 3, 1.0e-5, None, None,
        )

    def _build_extruded_solid(
        self,
        local_loop: List[Tuple[float, float]],
        z_min_local: float,
        z_max_local: float,
    ):
        """Make an IfcExtrudedAreaSolid in local space frame."""
        # IfcPolyline for the profile outer curve. Must be closed.
        if local_loop[0] != local_loop[-1]:
            local_loop = local_loop + [local_loop[0]]
        pts = [self.file.create_entity("IfcCartesianPoint", (float(x), float(y))) for x, y in local_loop]
        outer = self.file.create_entity("IfcPolyline", pts)
        profile = self.file.create_entity(
            "IfcArbitraryClosedProfileDef", "AREA", None, outer,
        )
        base = self.file.create_entity(
            "IfcCartesianPoint", (0.0, 0.0, float(z_min_local)),
        )
        axis_z = self.file.create_entity("IfcDirection", (0.0, 0.0, 1.0))
        axis_x = self.file.create_entity("IfcDirection", (1.0, 0.0, 0.0))
        position = self.file.create_entity(
            "IfcAxis2Placement3D", base, axis_z, axis_x,
        )
        depth = float(z_max_local - z_min_local)
        if depth <= 0:
            depth = 1.0  # degenerate — should not happen, but safe default
        extr_dir = self.file.create_entity("IfcDirection", (0.0, 0.0, 1.0))
        return self.file.create_entity(
            "IfcExtrudedAreaSolid", profile, position, extr_dir, depth,
        )

    def _replace_body_representation(
        self,
        space,
        new_solid,
    ) -> bool:
        """Swap the existing Body representation's items for the new
        extruded solid, keeping the same shape representation entity so
        ``IfcShapeAspect`` / styling references stay valid. If the
        space had no Body representation, append a fresh one.
        """
        rep = space.Representation
        ctx = self._ensure_body_context()
        if rep is None:
            rep = self.file.create_entity("IfcProductDefinitionShape", None, None, ())
            space.Representation = rep
        for srep in rep.Representations:
            if srep.RepresentationIdentifier == "Body":
                srep.Items = [new_solid]
                srep.RepresentationType = "SweptSolid"
                srep.ContextOfItems = ctx
                return True
        # No Body — append a new IfcShapeRepresentation.
        new_srep = self.file.create_entity(
            "IfcShapeRepresentation", ctx, "Body", "SweptSolid", [new_solid],
        )
        reps = list(rep.Representations)
        reps.append(new_srep)
        rep.Representations = reps
        return True

    # ------------------------------------------------------------------
    # Main entrypoint
    # ------------------------------------------------------------------

    def patch(self) -> None:
        try:
            spaces = self.file.by_type(self.space_selector)
        except Exception as e:
            raise ValueError(
                f"space_selector {self.space_selector!r} could not be resolved: {e}"
            ) from e
        self.stats["spaces_total"] = len(spaces)
        if not spaces:
            self.logger.info(
                "No %s entities in target file; output IFC will be byte-identical to input",
                self.space_selector,
            )
            return
        self.logger.info(
            "ExpandSpacesToMidplanes: %d spaces, unit=%s (factor=%g), "
            "offset=%g m, densify=%g m, min_area=%g m²",
            len(spaces), self.unit_label, self.internal_unit_factor,
            self.default_offset_m, self.densify_step_m, self.min_area_m2,
        )

        # Pre-warm the owner-history cache so the per-element loop never
        # has to call self.file.by_type() (which has been observed to
        # intermittently SIGSEGV on large files).
        self._get_or_create_owner_history()

        # ---------- 1. Collect per-space footprints in world XY ----------
        # Tuple: (space, matrix, world_polygon, z_min_local, z_max_local, orig_area_file_units)
        collected: List[Tuple[object, np.ndarray, Polygon, float, float, float]] = []
        for sp in spaces:
            loops = self._footprint_local(sp)
            if not loops:
                self.stats["spaces_skipped_no_footprint"] = int(self.stats["spaces_skipped_no_footprint"]) + 1
                continue
            try:
                placement = up.get_local_placement(sp.ObjectPlacement)
            except Exception:
                placement = np.eye(4)
            try:
                # Each loop is closed (first==last); build polygon from
                # exterior ring. If multiple loops, take their unary
                # union and pick the largest polygon (very rare for
                # Archicad spaces).
                world_loops = [self._apply_placement_xy(placement, lp) for lp in loops]
                geoms = []
                for wl in world_loops:
                    try:
                        p = Polygon(wl[:-1])
                        if not p.is_valid:
                            p = p.buffer(0)
                        if p.is_empty:
                            continue
                        geoms.append(p)
                    except Exception:
                        continue
                if not geoms:
                    self.stats["spaces_skipped_unsupported_repr"] = int(self.stats["spaces_skipped_unsupported_repr"]) + 1
                    continue
                if len(geoms) == 1:
                    poly = geoms[0]
                else:
                    poly = unary_union(geoms)
                    if isinstance(poly, MultiPolygon):
                        poly = max(poly.geoms, key=lambda g: g.area)
            except Exception as e:
                self.logger.debug("space %s footprint build failed: %s", sp.GlobalId, e)
                self.stats["spaces_skipped_unsupported_repr"] = int(self.stats["spaces_skipped_unsupported_repr"]) + 1
                continue
            area_world_m2 = poly.area * (self.internal_unit_factor ** 2)
            if area_world_m2 < self.min_area_m2:
                self.stats["spaces_skipped_too_small"] = int(self.stats["spaces_skipped_too_small"]) + 1
                continue
            zr = self._local_z_range(sp)
            if zr is None:
                # Default to 0..3 m if we can't read the brep — wrong
                # height is far less harmful than no expansion at all.
                zr = (0.0, 3.0 / self.internal_unit_factor)
            collected.append((sp, placement, poly, zr[0], zr[1], poly.area))

        if not collected:
            self.logger.warning(
                "No expandable spaces collected (no_footprint=%s, too_small=%s, unsupported=%s)",
                self.stats["spaces_skipped_no_footprint"],
                self.stats["spaces_skipped_too_small"],
                self.stats["spaces_skipped_unsupported_repr"],
            )
            return
        self.logger.info(
            "Collected %d / %d spaces for expansion", len(collected), len(spaces),
        )

        # ---------- 2. Build voronoi seed map ----------
        # Deduplicate seeds: GEOS voronoi_diagram crashes on coincident
        # points (degenerate rings). Adjacent space corners frequently
        # coincide. We quantise to ``dedup_quantum`` and keep one seed
        # per cell, but every owner whose seed fell into the cell still
        # gets credit for the resulting voronoi region. Without that
        # multi-owner mapping tiny spaces (e.g. 2 m² elec niches) lose
        # every seed to neighbours processed earlier and end up
        # ``voronoi_failed``.
        step_file = self.densify_step_m / self.internal_unit_factor
        dedup_quantum = max(step_file * 0.25, 1e-6)
        key_xy: Dict[Tuple[int, int], Tuple[float, float]] = {}
        key_owners: Dict[Tuple[int, int], List[int]] = {}
        for idx, (_, _, poly, _, _, _) in enumerate(collected):
            coords = list(poly.exterior.coords)
            dens = self._densify_loop(coords, step_file)
            for x, y in dens:
                key = (int(round(x / dedup_quantum)),
                       int(round(y / dedup_quantum)))
                if key not in key_xy:
                    key_xy[key] = (x, y)
                owners = key_owners.setdefault(key, [])
                if idx not in owners:
                    owners.append(idx)
        seeds: List[Tuple[float, float]] = list(key_xy.values())
        keys_in_order: List[Tuple[int, int]] = list(key_xy.keys())
        if len(seeds) < 4:
            self.logger.warning(
                "Too few seed points (%d) for voronoi — skipping", len(seeds),
            )
            return
        self.stats["total_voronoi_cells"] = len(seeds)
        self.logger.info(
            "voronoi: %d unique seeds (quantum=%g file units); building diagram",
            len(seeds), dedup_quantum,
        )

        # ---------- 3. Run shapely voronoi ----------
        mp = MultiPoint(seeds)
        # Envelope = bbox of all footprints, expanded by 2× offset so
        # exterior clip-buffer never reaches the envelope edge.
        minx, miny, maxx, maxy = mp.bounds
        margin = 2 * self.offset_file_units + step_file
        envelope = Polygon([
            (minx - margin, miny - margin),
            (maxx + margin, miny - margin),
            (maxx + margin, maxy + margin),
            (minx - margin, maxy + margin),
        ])
        try:
            vor = voronoi_diagram(mp, envelope=envelope, edges=False)
        except Exception as e:
            self.logger.error("voronoi_diagram failed: %s", e)
            return

        # voronoi_diagram returns a GeometryCollection of polygons; cell
        # order does *not* match input point order, so we match each cell
        # to its nearest input seed via point-in-polygon / centroid.
        cells = list(vor.geoms) if isinstance(vor, GeometryCollection) else [vor]
        # For each seed, find the cell that contains it. We use a
        # KD-tree-ish naive approach: shapely intersects/contains for
        # cells we haven't matched yet, indexed by sorted x for speed.
        from shapely.geometry import Point
        from shapely.strtree import STRtree
        tree = STRtree(cells)
        # For each unique seed, find the voronoi cell containing it.
        # Then credit the cell to *every* space whose original seed
        # collapsed into this quantised key (see dedup above). Without
        # that, tiny spaces sharing corners with larger ones never get
        # any cell assigned.
        per_space_cells: Dict[int, List[Polygon]] = {i: [] for i in range(len(collected))}
        for sidx, (x, y) in enumerate(seeds):
            pt = Point(x, y)
            try:
                cand = tree.query(pt)
            except Exception:
                cand = []
            chosen = None
            for cidx in cand:
                cell = cells[int(cidx)]
                if cell.contains(pt) or cell.intersects(pt):
                    chosen = cell
                    break
            if chosen is None:
                continue
            for owner in key_owners[keys_in_order[sidx]]:
                per_space_cells[owner].append(chosen)

        # ---------- 4. Per-space expand + clip ----------
        # Build a single STRtree over the original footprints so each
        # space's neighbour lookup is O(log N) instead of O(N). The
        # tree is shapely 2.x style: query() returns int indices.
        from shapely.strtree import STRtree
        orig_polys: List[Polygon] = [c[2] for c in collected]
        orig_tree = STRtree(orig_polys)
        midplane_extent_used_m: List[float] = []
        clamped_at_cap = 0

        increases: List[float] = []
        for idx, (sp, placement, orig_poly, z_min_local, z_max_local, orig_area) in enumerate(collected):
            chunks = per_space_cells.get(idx, [])
            if not chunks:
                self.stats["spaces_skipped_voronoi_failed"] = int(self.stats["spaces_skipped_voronoi_failed"]) + 1
                continue
            try:
                # ``own`` is the union of original + voronoi cells: it
                # reaches the midplane to every neighbour and includes
                # any concavity the boundary-seed voronoi might have
                # missed. Used for both the inner part (midplane cap)
                # and the clamping stat.
                own = unary_union([orig_poly] + chunks)

                # Inner part: midplane region, hard-capped at
                # max_midplane_extent_m so toward-neighbour expansion
                # never overruns the wall thickness budget.
                inner_cap = orig_poly.buffer(self.max_midplane_extent_file_units)
                inner_part = own.intersection(inner_cap)

                # Outer part: pure exterior cap, only kept where no
                # neighbour's max-midplane halo competes. STRtree query
                # window = default + max_midplane so every neighbour
                # that *could* clip outer_cap is in the candidate set.
                outer_cap = orig_poly.buffer(self.offset_file_units)
                query_envelope = orig_poly.buffer(
                    self.offset_file_units + self.max_midplane_extent_file_units
                )
                try:
                    cand_indices = list(orig_tree.query(query_envelope))
                except Exception:
                    cand_indices = []
                neighbour_polys: List[Polygon] = []
                for j in cand_indices:
                    j_int = int(j)
                    if j_int == idx:
                        continue
                    neighbour_polys.append(orig_polys[j_int])
                if neighbour_polys:
                    other_claim = unary_union([
                        p.buffer(self.max_midplane_extent_file_units)
                        for p in neighbour_polys
                    ])
                    outer_part = outer_cap.difference(other_claim)
                else:
                    outer_part = outer_cap

                # Union order: original first (guarantees no shrinkage
                # even if inner/outer fold back), then both pieces.
                expanded = unary_union([orig_poly, inner_part, outer_part])
            except Exception as e:
                self.logger.debug("expand for %s failed: %s", sp.GlobalId, e)
                self.stats["spaces_skipped_voronoi_failed"] = int(self.stats["spaces_skipped_voronoi_failed"]) + 1
                continue
            if expanded.is_empty:
                self.stats["spaces_skipped_voronoi_failed"] = int(self.stats["spaces_skipped_voronoi_failed"]) + 1
                continue
            # Pick exterior ring(s). For multipolygons, keep the largest.
            if isinstance(expanded, MultiPolygon):
                expanded = max(expanded.geoms, key=lambda g: g.area)
            if not isinstance(expanded, Polygon):
                self.stats["spaces_skipped_voronoi_failed"] = int(self.stats["spaces_skipped_voronoi_failed"]) + 1
                continue

            # Stat: per-space mean midplane extent used, and detect
            # cap-clamping. For each nearby neighbour we compute the
            # *actually-used* toward-neighbour extension as
            # ``min(distance/2, max_midplane_extent_m)``. Clamping is
            # detected when the nearest neighbour's midplane sits at
            # or beyond the cap distance — i.e. the cap is the binding
            # constraint, not the geometry. Isolated spaces (no
            # neighbours in the STRtree window) skip both: the cap
            # cannot bite when nothing competes, and there's no
            # midplane to average.
            if neighbour_polys:
                contributions: List[float] = []
                nearest_dist_file = float("inf")
                for p in neighbour_polys:
                    try:
                        dist_file = orig_poly.distance(p)
                    except Exception:
                        continue
                    if dist_file <= 0:
                        continue
                    nearest_dist_file = min(nearest_dist_file, dist_file)
                    dist_m = dist_file * self.internal_unit_factor
                    contributions.append(
                        min(dist_m / 2.0, self.max_midplane_extent_m)
                    )
                if contributions:
                    midplane_extent_used_m.append(
                        sum(contributions) / len(contributions)
                    )
                # Cap is "biting" when half the distance to the
                # nearest neighbour exceeds the cap by ≥10 %. The 10 %
                # buffer keeps test fixtures right at the cap (e.g. a
                # 0.30 m gap with a 0.15 m cap → midplane = 0.15 m)
                # from being flagged as clamped due to floating-point
                # noise; clamping only fires when the cap is
                # *materially* shorter than the available midplane.
                if (nearest_dist_file < float("inf")
                        and self.max_midplane_extent_m > 0
                        and (nearest_dist_file * self.internal_unit_factor) / 2.0
                            >= self.max_midplane_extent_m * 1.10):
                    clamped_at_cap += 1

            world_ring = list(expanded.exterior.coords)
            local_ring = self._apply_inverse_xy(placement, world_ring)
            if self.preserve_z:
                z_lo, z_hi = z_min_local, z_max_local
            else:
                z_lo = z_min_local - self.offset_file_units
                z_hi = z_max_local + self.offset_file_units
            try:
                solid = self._build_extruded_solid(local_ring, z_lo, z_hi)
                self._replace_body_representation(sp, solid)
            except Exception as e:
                self.logger.warning(
                    "failed to write new body for %s: %s", sp.GlobalId, e,
                )
                self.stats["spaces_skipped_voronoi_failed"] = int(self.stats["spaces_skipped_voronoi_failed"]) + 1
                continue
            if orig_area > 0:
                pct = (expanded.area - orig_area) / orig_area * 100.0
                increases.append(pct)
            self.stats["spaces_expanded"] = int(self.stats["spaces_expanded"]) + 1

        if increases:
            self.stats["mean_area_increase_pct"] = float(sum(increases) / len(increases))
            self.stats["max_area_increase_pct"] = float(max(increases))
        if midplane_extent_used_m:
            self.stats["mean_midplane_extent_used_m"] = float(
                sum(midplane_extent_used_m) / len(midplane_extent_used_m)
            )
        self.stats["spaces_clamped_at_midplane_cap"] = clamped_at_cap

        import json as _json
        self.logger.info(
            "ExpandSpacesToMidplanes done: %s",
            _json.dumps(self.stats, sort_keys=True, default=str),
        )

    def get_output(self) -> ifcopenshell.file:
        return self.file
