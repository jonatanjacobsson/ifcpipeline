"""Door- and opening-aware circulation graph between IfcSpace elements.

Accepts one or more IFC files and unions all IfcSpace and IfcDoor/opening
entities before linking — no per-file role assignment required.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

import ifcopenshell
import ifcopenshell.util.element as ifc_element_util

from ingest_scripts import Element, Ingester as _Base, Relationship

try:
    from topologicpy.Topology import Topology
    from topologicpy.Graph import Graph
    from topologicpy.Dictionary import Dictionary
    HAS_TOPOLOGICPY = True
except ImportError:
    HAS_TOPOLOGICPY = False

PORTAL_TYPES: FrozenSet[str] = frozenset(
    {
        "IfcDoor",
        "IfcDoorStandardCase",
        "IfcOpeningElement",
        "IfcOpeningStandardCase",
    }
)

WALL_TYPES: FrozenSet[str] = frozenset(
    {
        "IfcWall",
        "IfcWallStandardCase",
        "IfcWallElementedCase",
    }
)

SPACE_MARKER = "IfcSpace"


class Ingester(_Base):
    SCRIPT_NAME = "EgressCirculation"
    DESCRIPTION = (
        "Build door/opening-mediated circulation edges between IfcSpace elements "
        "from one or more IFC models"
    )

    def __init__(
        self,
        ifc_files: List[Path],
        log: logging.Logger,
        include_virtual_boundaries: bool = False,
        include_openings_without_door: bool = True,
        tolerance: float = 0.01,
        door_link_distance: float = 4.0,
        same_storey_only: bool = True,
        storey_z_tolerance: float = 2.5,
    ):
        """Extract space-to-space circulation edges through doors and openings.

        All staged IFC files are scanned for ``IfcSpace`` and door/opening entities.
        Spaces and portals found in any file are merged before edges are built.

        :param include_virtual_boundaries: Include virtual (non-physical) space boundaries.
        :param include_openings_without_door: Include bare openings (no IfcDoor fill) as portals.
        :param tolerance: Graph construction tolerance in model units (TopologicPy path).
        :param door_link_distance: Max model-unit distance from a door centroid to a space centroid.
        :param same_storey_only: Only link spaces on the same ``IfcBuildingStorey`` as the portal.
        :param storey_z_tolerance: Z fallback (model units) when storey metadata is missing.
        """
        super().__init__(ifc_files, log)
        self.include_virtual_boundaries = include_virtual_boundaries
        self.include_openings_without_door = include_openings_without_door
        self.tolerance = tolerance
        self.door_link_distance = door_link_distance
        self.same_storey_only = same_storey_only
        self.storey_z_tolerance = storey_z_tolerance

    def extract(self) -> None:
        t0 = time.time()
        seen_edges: Set[Tuple[str, str]] = set()
        portal_elements: Dict[str, str] = {}
        methods: Set[str] = set()

        models: List[Tuple[Path, ifcopenshell.file]] = []
        for ifc_path in self.ifc_files:
            self.log.info("EgressCirculation: opening %s", ifc_path.name)
            models.append((ifc_path, ifcopenshell.open(str(ifc_path))))

        if len(models) > 1:
            models = _merge_ifc_models(models, self.log)
            methods.add("federated_merge")

        space_points, space_sources = _collect_spaces(models)
        element_storey, storey_elevations = _collect_storey_maps(models)
        self.log.info(
            "EgressCirculation: %d file(s), %d spaces, %d doors across inputs",
            len(models),
            len(space_points),
            sum(len(_safe_by_type(ifc, "IfcDoor")) for _, ifc in models),
        )

        # Proximity — primary path when space boundaries are missing (common in split exports).
        if len(space_points) >= 2:
            added, portals = self._link_all_doors_to_spaces(
                models,
                space_points,
                space_sources,
                element_storey,
                storey_elevations,
                seen_edges,
                portal_elements,
            )
            if portals:
                methods.add("door_space_proximity")

        for ifc_path, ifc in models:
            space_count = len(_safe_by_type(ifc, "IfcSpace"))
            # TopologicPy on large federated merges is slow and can crash; single-file only.
            if HAS_TOPOLOGICPY and 0 < space_count <= 400 and len(self.ifc_files) == 1:
                added, _ = self._extract_from_topologic_graph(
                    ifc_path, seen_edges, portal_elements
                )
                if added:
                    methods.add("topologicpy_portal_graph")

            portal_to_spaces = self._collect_portal_spaces(ifc)
            if self._emit_ifc_portal_edges(
                ifc,
                ifc_path,
                portal_to_spaces,
                element_storey,
                storey_elevations,
                space_points,
                seen_edges,
                portal_elements,
            ):
                methods.add("ifc_portal_boundary")

        for portal_id, portal_class in portal_elements.items():
            if any(e.global_id == portal_id for e in self._elements):
                continue
            self._elements.append(
                Element(
                    global_id=portal_id,
                    ifc_class=portal_class,
                    name=portal_id,
                    extra={"role": "egress_portal"},
                )
            )

        portal_count = len(
            {rel.evidence.get("portal_global_id") for rel in self._relationships}
        )
        method_label = "+".join(sorted(methods)) if methods else "none"
        self._summary = {
            "portals_used": portal_count,
            "method": method_label,
            "input_files": [p.name for p in self.ifc_files],
            "space_count": len(space_points),
            "duration_ms": int((time.time() - t0) * 1000),
        }
        self.log.info(
            "EgressCirculation: %d egress edges via %d portals (%s)",
            len(self._relationships),
            portal_count,
            method_label,
        )

    def _extract_from_topologic_graph(
        self,
        ifc_path: Path,
        seen_edges: Set[Tuple[str, str]],
        portal_elements: Dict[str, str],
    ) -> Tuple[int, int]:
        added = 0
        portals_used = 0
        try:
            graph = Graph.ByIFCFile(str(ifc_path), tolerance=self.tolerance)
            if graph is None:
                return 0, 0

            portal_to_spaces: Dict[str, Set[str]] = defaultdict(set)
            for vertex in Graph.Vertices(graph):
                meta = _vertex_meta(vertex)
                if not meta:
                    continue
                gid, ifc_class = meta
                if not _is_portal_class(ifc_class):
                    continue

                adjacent = Graph.AdjacentVertices(graph, vertex) or []
                space_ids: Set[str] = set()
                for adj in adjacent:
                    adj_meta = _vertex_meta(adj)
                    if adj_meta and SPACE_MARKER in adj_meta[1]:
                        space_ids.add(adj_meta[0])

                if len(space_ids) >= 2:
                    portal_to_spaces[gid].update(space_ids)
                    portal_elements[gid] = ifc_class

            for portal_id, space_ids in portal_to_spaces.items():
                portals_used += 1
                portal_elem = portal_elements.get(portal_id, "Portal")
                for s1, s2 in combinations(sorted(space_ids), 2):
                    if _append_edge(
                        self._relationships,
                        seen_edges,
                        s1,
                        s2,
                        portal_id,
                        portal_elem,
                        "",
                        "topologicpy_portal_graph",
                        ifc_path.name,
                    ):
                        added += 1

        except Exception as exc:
            self.log.warning(
                "EgressCirculation: TopologicPy failed for %s: %s",
                ifc_path.name,
                exc,
            )
        return added, portals_used

    def _link_all_doors_to_spaces(
        self,
        models: List[Tuple[Path, ifcopenshell.file]],
        space_points: Dict[str, Tuple[float, float, float]],
        space_sources: Dict[str, str],
        element_storey: Dict[str, str],
        storey_elevations: Dict[str, float],
        seen_edges: Set[Tuple[str, str]],
        portal_elements: Dict[str, str],
    ) -> Tuple[int, int]:
        portals_used = 0
        added = 0
        max_dist = max(float(self.door_link_distance), 0.5)
        if len(self.ifc_files) > 1:
            max_dist = max(max_dist, 6.0)

        for ifc_path, ifc in models:
            for door in _safe_by_type(ifc, "IfcDoor"):
                door_pt = _element_centroid(door)
                if door_pt is None:
                    continue

                door_storey = _resolve_element_storey(
                    door.GlobalId,
                    door_pt,
                    element_storey,
                    storey_elevations,
                    self.storey_z_tolerance,
                )

                ranked = sorted(
                    space_points.items(),
                    key=lambda item: _planar_distance(door_pt, item[1]),
                )
                nearby = []
                for sid, pt in ranked:
                    dist = _planar_distance(door_pt, pt)
                    if dist > max_dist:
                        break
                    if self.same_storey_only and not _spaces_on_same_level(
                        door_storey,
                        door_pt,
                        sid,
                        pt,
                        element_storey,
                        storey_elevations,
                        self.storey_z_tolerance,
                    ):
                        continue
                    nearby.append((sid, pt, dist))
                if len(nearby) < 2:
                    continue
                (s1, pt1, d1), (s2, pt2, d2) = nearby[0], nearby[1]

                portals_used += 1
                portal_elements[door.GlobalId] = door.is_a()
                evidence_source = ifc_path.name
                if len(self.ifc_files) > 1:
                    evidence_source = (
                        f"{ifc_path.name}|spaces="
                        f"{space_sources.get(s1, '?')},{space_sources.get(s2, '?')}"
                    )
                if _append_edge(
                    self._relationships,
                    seen_edges,
                    s1,
                    s2,
                    door.GlobalId,
                    door.is_a(),
                    getattr(door, "Name", None) or door.GlobalId,
                    "door_space_proximity",
                    evidence_source,
                ):
                    added += 1

        return added, portals_used

    def _emit_ifc_portal_edges(
        self,
        ifc,
        ifc_path: Path,
        portal_to_spaces: Dict[str, Set[str]],
        element_storey: Dict[str, str],
        storey_elevations: Dict[str, float],
        space_points: Dict[str, Tuple[float, float, float]],
        seen_edges: Set[Tuple[str, str]],
        portal_elements: Dict[str, str],
    ) -> int:
        portals_used = 0
        for portal_id, space_ids in portal_to_spaces.items():
            if len(space_ids) < 2:
                continue
            portals_used += 1
            portal_elem = ifc.by_guid(portal_id)
            portal_class = portal_elem.is_a() if portal_elem else "Unknown"
            portal_name = getattr(portal_elem, "Name", None) or portal_id
            portal_elements[portal_id] = portal_class
            portal_pt = _element_centroid(portal_elem) if portal_elem else None
            portal_storey = _resolve_element_storey(
                portal_id,
                portal_pt,
                element_storey,
                storey_elevations,
                self.storey_z_tolerance,
            )

            for s1, s2 in combinations(sorted(space_ids), 2):
                if self.same_storey_only:
                    pt1 = space_points.get(s1)
                    pt2 = space_points.get(s2)
                    if not _spaces_on_same_level(
                        portal_storey,
                        portal_pt,
                        s1,
                        pt1,
                        element_storey,
                        storey_elevations,
                        self.storey_z_tolerance,
                    ):
                        continue
                    if not _spaces_on_same_level(
                        portal_storey,
                        portal_pt,
                        s2,
                        pt2,
                        element_storey,
                        storey_elevations,
                        self.storey_z_tolerance,
                    ):
                        continue
                    st1 = _resolve_element_storey(
                        s1, pt1, element_storey, storey_elevations, self.storey_z_tolerance
                    )
                    st2 = _resolve_element_storey(
                        s2, pt2, element_storey, storey_elevations, self.storey_z_tolerance
                    )
                    if st1 and st2 and st1 != st2:
                        continue
                _append_edge(
                    self._relationships,
                    seen_edges,
                    s1,
                    s2,
                    portal_id,
                    portal_class,
                    portal_name,
                    "ifc_portal_boundary",
                    ifc_path.name,
                )

        return portals_used

    def _collect_portal_spaces(
        self,
        ifc,
        *,
        allowed_space_ids: Optional[Set[str]] = None,
    ) -> Dict[str, Set[str]]:
        portal_to_spaces: Dict[str, Set[str]] = defaultdict(set)
        wall_to_spaces: Dict[str, Set[str]] = defaultdict(set)

        for rel in ifc.by_type("IfcRelSpaceBoundary"):
            space = rel.RelatingSpace
            element = rel.RelatedBuildingElement
            if not space or not element:
                continue
            if not self._boundary_allowed(rel):
                continue

            space_id = space.GlobalId
            if allowed_space_ids is not None and space_id not in allowed_space_ids:
                continue

            elem_class = element.is_a()
            elem_id = element.GlobalId

            if elem_class in PORTAL_TYPES:
                if elem_class in {"IfcOpeningElement", "IfcOpeningStandardCase"}:
                    if not self.include_openings_without_door and not self._opening_has_door(
                        element
                    ):
                        continue
                portal_to_spaces[elem_id].add(space_id)
            elif elem_class in WALL_TYPES:
                wall_to_spaces[elem_id].add(space_id)

        self._augment_portals_from_doors_on_walls(
            ifc, portal_to_spaces, wall_to_spaces, allowed_space_ids=allowed_space_ids
        )
        return portal_to_spaces

    def _boundary_allowed(self, rel) -> bool:
        if self.include_virtual_boundaries:
            return True
        pov = getattr(rel, "PhysicalOrVirtualBoundary", None)
        if pov is None:
            return True
        return str(pov).upper() != "VIRTUAL"

    @staticmethod
    def _opening_has_door(opening) -> bool:
        for rel in getattr(opening, "HasFillings", None) or []:
            fill = rel.RelatedBuildingElement
            if fill and fill.is_a() in {"IfcDoor", "IfcDoorStandardCase"}:
                return True
        return False

    def _augment_portals_from_doors_on_walls(
        self,
        ifc,
        portal_to_spaces: Dict[str, Set[str]],
        wall_to_spaces: Dict[str, Set[str]],
        *,
        allowed_space_ids: Optional[Set[str]] = None,
    ) -> None:
        for door in _safe_by_type(ifc, "IfcDoor"):
            door_id = door.GlobalId
            wall_id = _host_wall_global_id(door)
            if not wall_id:
                continue
            spaces = wall_to_spaces.get(wall_id)
            if not spaces or len(spaces) < 2:
                continue
            if allowed_space_ids is not None:
                spaces = {sid for sid in spaces if sid in allowed_space_ids}
            if len(spaces) < 2:
                continue
            portal_to_spaces[door_id].update(spaces)


def _merge_ifc_models(
    models: List[Tuple[Path, ifcopenshell.file]],
    log: logging.Logger,
) -> List[Tuple[Path, ifcopenshell.file]]:
    """Merge supplemental IFC products into the space-richest file (in memory).

    Copies doors, walls, openings, and other products from secondary files into
    the primary model so downstream logic sees one combined federation. Spaces
    are taken from the primary file only (duplicate GlobalIds are skipped).
    """
    ranked = sorted(
        models,
        key=lambda item: len(_safe_by_type(item[1], "IfcSpace")),
        reverse=True,
    )
    base_path, base = ranked[0]
    existing_guids = {
        getattr(element, "GlobalId", None)
        for element in base
        if getattr(element, "GlobalId", None)
    }
    copied = 0
    for other_path, other in ranked[1:]:
        for product in other.by_type("IfcProduct"):
            gid = getattr(product, "GlobalId", None)
            if not gid or gid in existing_guids:
                continue
            if product.is_a() == "IfcSpace":
                continue
            try:
                ifc_element_util.copy_deep(base, product)
                existing_guids.add(gid)
                copied += 1
            except Exception as exc:
                log.debug("EgressCirculation: merge skip %s: %s", gid, exc)

    log.info(
        "EgressCirculation: merged %d products from %d supplemental file(s) into %s",
        copied,
        len(ranked) - 1,
        base_path.name,
    )
    return [(base_path, base)]


def _collect_storey_maps(
    models: List[Tuple[Path, ifcopenshell.file]],
) -> Tuple[Dict[str, str], Dict[str, float]]:
    """Build element GlobalId → storey GlobalId and storey GlobalId → elevation."""
    element_storey: Dict[str, str] = {}
    storey_elevations: Dict[str, float] = {}

    for _, ifc in models:
        for storey in _safe_by_type(ifc, "IfcBuildingStorey"):
            gid = storey.GlobalId
            try:
                storey_elevations[gid] = float(getattr(storey, "Elevation", 0) or 0)
            except (TypeError, ValueError):
                storey_elevations[gid] = 0.0

        for element in _safe_by_type(ifc, "IfcSpace") + _safe_by_type(ifc, "IfcDoor"):
            storey_id = _element_storey_id(element)
            if storey_id:
                element_storey[element.GlobalId] = storey_id

    return element_storey, storey_elevations


def _element_storey_id(element) -> Optional[str]:
    """Resolve containing ``IfcBuildingStorey`` via decomposition or spatial contain."""
    for rel in getattr(element, "Decomposes", None) or []:
        parent = getattr(rel, "RelatingObject", None)
        if parent and parent.is_a("IfcBuildingStorey"):
            return parent.GlobalId
        if parent and parent.is_a("IfcSpace"):
            nested = _element_storey_id(parent)
            if nested:
                return nested
    for rel in getattr(element, "ContainedInStructure", None) or []:
        struct = getattr(rel, "RelatingStructure", None)
        if struct and struct.is_a("IfcBuildingStorey"):
            return struct.GlobalId
    return None


def _infer_storey_from_z(
    z: float,
    storey_elevations: Dict[str, float],
    tolerance: float,
) -> Optional[str]:
    if not storey_elevations:
        return None
    best_id = min(storey_elevations, key=lambda sid: abs(z - storey_elevations[sid]))
    if abs(z - storey_elevations[best_id]) <= tolerance:
        return best_id
    return None


def _resolve_element_storey(
    global_id: str,
    point: Optional[Tuple[float, float, float]],
    element_storey: Dict[str, str],
    storey_elevations: Dict[str, float],
    z_tolerance: float,
) -> Optional[str]:
    storey_id = element_storey.get(global_id)
    if storey_id:
        return storey_id
    if point is not None:
        return _infer_storey_from_z(point[2], storey_elevations, z_tolerance)
    return None


def _spaces_on_same_level(
    anchor_storey: Optional[str],
    anchor_point: Optional[Tuple[float, float, float]],
    space_id: str,
    space_point: Optional[Tuple[float, float, float]],
    element_storey: Dict[str, str],
    storey_elevations: Dict[str, float],
    z_tolerance: float,
) -> bool:
    space_storey = _resolve_element_storey(
        space_id, space_point, element_storey, storey_elevations, z_tolerance
    )
    if anchor_storey and space_storey:
        return anchor_storey == space_storey
    if anchor_point is not None and space_point is not None:
        return abs(anchor_point[2] - space_point[2]) <= z_tolerance
    return False


def _planar_distance(
    a: Tuple[float, float, float], b: Tuple[float, float, float]
) -> float:
    """Horizontal distance — avoids treating stacked spaces as neighbours."""
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _collect_spaces(
    models: List[Tuple[Path, ifcopenshell.file]],
) -> Tuple[Dict[str, Tuple[float, float, float]], Dict[str, str]]:
    space_points: Dict[str, Tuple[float, float, float]] = {}
    space_sources: Dict[str, str] = {}
    for ifc_path, ifc in models:
        for space in _safe_by_type(ifc, "IfcSpace"):
            gid = space.GlobalId
            if gid in space_points:
                continue
            pt = _element_centroid(space)
            if pt is None:
                continue
            space_points[gid] = pt
            space_sources[gid] = ifc_path.name
    return space_points, space_sources


def _vertex_meta(vertex) -> Optional[Tuple[str, str]]:
    d = Topology.Dictionary(vertex)
    if not d:
        return None
    gid = Dictionary.ValueAtKey(d, "IFC_global_id") or Dictionary.ValueAtKey(d, "GlobalId") or ""
    ifc_class = Dictionary.ValueAtKey(d, "IFC_type") or Dictionary.ValueAtKey(d, "IfcClass") or ""
    if not gid:
        return None
    return gid, ifc_class


def _is_portal_class(ifc_class: str) -> bool:
    if not ifc_class:
        return False
    for portal in PORTAL_TYPES:
        if portal in ifc_class:
            return True
    return False


def _append_edge(
    relationships: List[Relationship],
    seen_edges: Set[Tuple[str, str]],
    s1: str,
    s2: str,
    portal_id: str,
    portal_class: str,
    portal_name: str,
    method: str,
    source_file: str,
) -> bool:
    key = tuple(sorted((s1, s2)))
    if key in seen_edges:
        return False
    seen_edges.add(key)
    relationships.append(
        Relationship(
            subject_global_id=s1,
            object_global_id=s2,
            relationship_family="circulation",
            relationship_type="egress_connects",
            confidence=0.95,
            source_kind="topologic_ingest_EgressCirculation",
            evidence={
                "method": method,
                "portal_global_id": portal_id,
                "portal_class": portal_class,
                "portal_name": portal_name or portal_id,
                "source_file": source_file,
            },
        )
    )
    return True


def _safe_by_type(ifc, type_name: str) -> List:
    try:
        return list(ifc.by_type(type_name))
    except RuntimeError:
        return []


def _host_wall_global_id(door) -> str | None:
    for rel in getattr(door, "FillsVoids", None) or []:
        opening = getattr(rel, "RelatedOpeningElement", None) or getattr(
            rel, "RelatingOpeningElement", None
        )
        if not opening:
            continue
        void_rels = getattr(opening, "VoidsElements", None) or getattr(
            opening, "HasOpenings", None
        ) or []
        for vrel in void_rels:
            host = getattr(vrel, "RelatingBuildingElement", None) or getattr(
                vrel, "RelatedBuildingElement", None
            )
            if host and host.is_a() in WALL_TYPES:
                return host.GlobalId
    return None


def _element_centroid(element) -> Optional[Tuple[float, float, float]]:
    try:
        import ifcopenshell.geom

        settings = ifcopenshell.geom.settings()
        settings.set(settings.USE_WORLD_COORDS, True)
        shape = ifcopenshell.geom.create_shape(settings, element)
        if shape is None:
            return None
        verts = shape.geometry.verts
        if not verts:
            return None
        xs = verts[0::3]
        ys = verts[1::3]
        zs = verts[2::3]
        return (sum(xs) / len(xs), sum(ys) / len(ys), sum(zs) / len(zs))
    except Exception:
        try:
            import ifcopenshell.util.placement as placement_util

            matrix = placement_util.get_local_placement(element.ObjectPlacement)
            return (matrix[0][3], matrix[1][3], matrix[2][3])
        except Exception:
            return None


def _distance(
    a: Tuple[float, float, float], b: Tuple[float, float, float]
) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5
