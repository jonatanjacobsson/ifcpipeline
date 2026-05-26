"""IFC element geometry extraction for 3D clash routing and reasoning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

MEP_CLASSES = {
    "IfcFlowSegment",
    "IfcFlowFitting",
    "IfcFlowTerminal",
    "IfcFlowController",
    "IfcFlowMovingDevice",
    "IfcFlowStorageDevice",
    "IfcFlowTreatmentDevice",
    "IfcDuctSegment",
    "IfcDuctFitting",
    "IfcPipeSegment",
    "IfcPipeFitting",
    "IfcCableCarrierSegment",
    "IfcCableCarrierFitting",
    "IfcAirTerminal",
    "IfcBuildingElementProxy",
}

STRUCTURAL_CLASSES = {
    "IfcBeam",
    "IfcColumn",
    "IfcSlab",
    "IfcFooting",
    "IfcWall",
    "IfcPlate",
    "IfcMember",
    "IfcPile",
}

DISCIPLINE_PRIORITY = {
    "mep": 0,
    "architecture": 1,
    "landscape": 2,
    "structural": 3,
    "other": 4,
}


@dataclass
class Aabb:
    min_corner: np.ndarray
    max_corner: np.ndarray

    @property
    def center(self) -> np.ndarray:
        return (self.min_corner + self.max_corner) * 0.5

    def inflated(self, margin_m: float) -> "Aabb":
        m = float(margin_m)
        return Aabb(self.min_corner - m, self.max_corner + m)

    def contains_point(self, point: np.ndarray) -> bool:
        return bool(np.all(point >= self.min_corner) and np.all(point <= self.max_corner))


@dataclass
class ElementGeom:
    guid: str
    ifc_class: str
    discipline: str
    aabb: Aabb
    placement_origin: np.ndarray
    dominant_axis: np.ndarray | None = None


def discipline_from_class(ifc_class: str) -> str:
    if ifc_class in MEP_CLASSES:
        return "mep"
    if ifc_class in STRUCTURAL_CLASSES:
        return "structural"
    if ifc_class in {"IfcBuildingElementProxy", "IfcCovering", "IfcRoof", "IfcStair"}:
        return "architecture"
    if ifc_class in {"IfcGeographicElement", "IfcSite", "IfcPavement"}:
        return "landscape"
    return "other"


def _shape_aabb(ifc: Any, product: Any) -> Aabb | None:
    import ifcopenshell.geom

    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    try:
        shape = ifcopenshell.geom.create_shape(settings, product)
    except (RuntimeError, AttributeError):
        return None
    verts = np.array(shape.geometry.verts, dtype=float).reshape(-1, 3)
    if verts.size == 0:
        return None
    return Aabb(verts.min(axis=0), verts.max(axis=0))


def _placement_origin(product: Any) -> np.ndarray:
    import ifcopenshell.util.placement

    matrix = ifcopenshell.util.placement.get_local_placement(product.ObjectPlacement)
    return np.array(matrix[0:3, 3], dtype=float)


def _dominant_axis_from_placement(product: Any) -> np.ndarray | None:
    import ifcopenshell.util.placement

    matrix = ifcopenshell.util.placement.get_local_placement(product.ObjectPlacement)
    axis = np.array(matrix[0:3, 0], dtype=float)
    norm = np.linalg.norm(axis)
    if norm < 1e-9:
        return None
    return axis / norm


def element_geom(ifc_path: str, guid: str) -> ElementGeom | None:
    import ifcopenshell

    ifc = ifcopenshell.open(ifc_path)
    try:
        elem = ifc.by_guid(guid)
    except RuntimeError:
        return None
    ifc_class = elem.is_a()
    aabb = _shape_aabb(ifc, elem)
    origin = _placement_origin(elem)
    if aabb is None:
        half = 0.25
        aabb = Aabb(origin - half, origin + half)
    return ElementGeom(
        guid=guid,
        ifc_class=ifc_class,
        discipline=discipline_from_class(ifc_class),
        aabb=aabb,
        placement_origin=origin,
        dominant_axis=_dominant_axis_from_placement(elem),
    )


def clash_midpoint(clash: dict[str, Any]) -> np.ndarray:
    p1 = np.array(clash.get("p1") or [0.0, 0.0, 0.0], dtype=float)
    p2 = np.array(clash.get("p2") or p1, dtype=float)
    return (p1 + p2) * 0.5


def clash_separation_vector(clash: dict[str, Any]) -> np.ndarray:
    p1 = np.array(clash.get("p1") or [0.0, 0.0, 0.0], dtype=float)
    p2 = np.array(clash.get("p2") or p1, dtype=float)
    return p2 - p1


def obstacle_aabbs_for_clash(
    ifc_paths: list[str],
    clash: dict[str, Any],
    *,
    exclude_guid: str,
    inflate_m: float,
) -> list[Aabb]:
    guids = {clash.get("a_global_id"), clash.get("b_global_id")} - {None}
    obstacles: list[Aabb] = []
    for path in ifc_paths:
        for guid in guids:
            if guid == exclude_guid:
                continue
            geom = element_geom(path, str(guid))
            if geom is not None:
                obstacles.append(geom.aabb.inflated(inflate_m))
    return obstacles

@dataclass
class IndexedElement:
    guid: str
    ifc_class: str
    discipline: str
    aabb: Aabb
    source_file: str


def aabb_intersects(a: Aabb, b: Aabb) -> bool:
    return bool(
        np.all(a.max_corner >= b.min_corner) and np.all(b.max_corner >= a.min_corner)
    )


def aabb_union(a: Aabb, b: Aabb) -> Aabb:
    return Aabb(np.minimum(a.min_corner, b.min_corner), np.maximum(a.max_corner, b.max_corner))


def translate_aabb(aabb: Aabb, delta: np.ndarray) -> Aabb:
    d = np.asarray(delta, dtype=float)
    return Aabb(aabb.min_corner + d, aabb.max_corner + d)


def build_model_aabb_index(
    ifc_path: str,
    *,
    skip_types: set[str] | None = None,
) -> list[IndexedElement]:
    """Index product AABBs for fast neighbourhood clash screening."""
    import ifcopenshell

    skip = skip_types or {"IfcOpeningElement", "IfcSpace", "IfcAnnotation"}
    ifc = ifcopenshell.open(ifc_path)
    out: list[IndexedElement] = []
    for prod in ifc.by_type("IfcProduct"):
        if prod.is_a() in skip:
            continue
        gid = getattr(prod, "GlobalId", None)
        if not gid:
            continue
        ifc_class = prod.is_a()
        aabb = _shape_aabb(ifc, prod)
        if aabb is None:
            origin = _placement_origin(prod)
            half = 0.2
            aabb = Aabb(origin - half, origin + half)
        out.append(
            IndexedElement(
                guid=gid,
                ifc_class=ifc_class,
                discipline=discipline_from_class(ifc_class),
                aabb=aabb,
                source_file=ifc_path,
            )
        )
    return out


def merge_indices(indices: list[list[IndexedElement]]) -> list[IndexedElement]:
    merged: list[IndexedElement] = []
    for part in indices:
        merged.extend(part)
    return merged
