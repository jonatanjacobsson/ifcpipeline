"""Door- and opening-aware circulation graph between IfcSpace elements.

Two routing strategies are supported:

``strategy="door_portal"`` (default)
    Builds edges through IfcDoor / IfcOpeningElement portals. Uses
    IfcRelSpaceBoundary, TopologicPy graph adjacency, and door-centroid
    proximity. Suited for models that have doors and space boundaries.

``strategy="space_adjacency"``
    Builds edges directly from the IfcSpace geometry without requiring any
    door or boundary data. Suited for room-only models (e.g. A1/architectural
    programme files) that lack IfcDoor and IfcRelSpaceBoundary:

    * Horizontal edges — two spaces on the same storey whose 2D bounding-box
      footprints share a face (touch within ``face_tolerance`` and overlap by
      at least ``min_shared_face``) get a ``bbox_face_adjacency`` edge.
    * Vertical edges — spaces with a LongName matching a stairway or lift
      keyword are grouped by normalised name; same-named instances on
      consecutive storeys are connected with ``named_stair_lift_match`` edges.
    * Storey assignment — uses IFC spatial containment first; falls back to
      parsing the three-digit prefix in the room number (e.g. "040-206" →
      storey "040"), then to Z-centroid nearest storey elevation.

Accepts one or more IFC files and unions all IfcSpace and IfcDoor/opening
entities before linking — no per-file role assignment required.
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element as ifc_element_util
import ifcopenshell.util.unit

from ingest_scripts import Element, Ingester as _Base, Relationship

try:
    from topologicpy.Topology import Topology
    from topologicpy.Graph import Graph
    from topologicpy.Dictionary import Dictionary
    HAS_TOPOLOGICPY = True
except ImportError:
    HAS_TOPOLOGICPY = False


# ---------------------------------------------------------------------------
# Portal type constants
# ---------------------------------------------------------------------------

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

# Keywords that identify a space as a vertical connector (stairway or lift).
# Matched case-insensitively against the space LongName.
_VERTICAL_KEYWORDS: Tuple[str, ...] = (
    "stair", "stairway", "trapphus", "trappa",      # stairs (EN + SV)
    "lift", "hiss", "elevator", "escalator",         # lifts/elevators
    "ramp",                                          # ramps
)

# Regex to extract the three-digit storey prefix from a room number such as
# "040-206_16,17m²" → group 1 = "040".
_ROOM_NR_RE = re.compile(r"\b(\d{3})-\d{3,}")


# ---------------------------------------------------------------------------
# Main ingester class
# ---------------------------------------------------------------------------


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
        strategy: str = "door_portal",
        # --- door_portal options ---
        include_virtual_boundaries: bool = False,
        include_openings_without_door: bool = True,
        tolerance: float = 0.01,
        door_link_distance: float = 4.0,
        same_storey_only: bool = True,
        storey_z_tolerance: float = 2.5,
        # --- space_adjacency options ---
        face_tolerance: float = 0.15,
        min_shared_face: float = 0.30,
        vertical_keywords: Optional[Tuple[str, ...]] = None,
    ):
        """Extract space-to-space circulation edges.

        :param strategy: ``"door_portal"`` (default) or ``"space_adjacency"``.
        :param include_virtual_boundaries: (door_portal) Include virtual space boundaries.
        :param include_openings_without_door: (door_portal) Include bare openings as portals.
        :param tolerance: (door_portal) Graph construction tolerance in model units.
        :param door_link_distance: (door_portal) Max distance door centroid → space centroid.
        :param same_storey_only: (door_portal) Only link two spaces that share the same
            storey (IFC containment, room-number prefix, or elevation — not door Z proximity).
        :param storey_z_tolerance: (door_portal) Z fallback tolerance in model units.
        :param face_tolerance: (space_adjacency) Max bbox gap (metres) to count as touching.
        :param min_shared_face: (space_adjacency) Min shared edge (metres) for adjacency.
        :param vertical_keywords: (space_adjacency) Override stair/lift keyword list.
        """
        super().__init__(ifc_files, log)
        self.strategy = strategy.strip().lower()
        # door_portal params
        self.include_virtual_boundaries = include_virtual_boundaries
        self.include_openings_without_door = include_openings_without_door
        self.tolerance = tolerance
        self.door_link_distance = door_link_distance
        self.same_storey_only = same_storey_only
        self.storey_z_tolerance = storey_z_tolerance
        # space_adjacency params
        self.face_tolerance = face_tolerance
        self.min_shared_face = min_shared_face
        self.vertical_keywords: Tuple[str, ...] = (
            vertical_keywords if vertical_keywords is not None else _VERTICAL_KEYWORDS
        )

    # ------------------------------------------------------------------
    # extract() dispatcher
    # ------------------------------------------------------------------

    def extract(self) -> None:
        if self.strategy == "space_adjacency":
            self._extract_space_adjacency()
        else:
            self._extract_door_portal()

    # ==================================================================
    # STRATEGY A: space_adjacency
    # ==================================================================

    def _extract_space_adjacency(self) -> None:
        """Build egress graph from IfcSpace geometry alone.

        Works without doors, space boundaries, or IFC spatial containment.
        All edges derive from:
          1. 2D bounding-box face-share between spaces on the same storey.
          2. Same-named stairway/lift spaces on consecutive storeys.
        """
        t0 = time.time()

        models: List[Tuple[Path, ifcopenshell.file]] = []
        for ifc_path in self.ifc_files:
            self.log.info("EgressCirculation[space_adjacency]: opening %s", ifc_path.name)
            models.append((ifc_path, ifcopenshell.open(str(ifc_path))))

        # Collect geometry for all spaces
        all_spaces = self._collect_space_geometry(models)
        if not all_spaces:
            self.log.warning("EgressCirculation[space_adjacency]: no spaces with geometry found")
            self._summary = {"edges": 0, "method": "space_adjacency", "spaces": 0}
            return

        self.log.info(
            "EgressCirculation[space_adjacency]: %d spaces with geometry across %d file(s)",
            len(all_spaces), len(models),
        )

        # Assign storeys from IFC containment, room-number prefix, or Z-centroid
        storey_elevations = self._collect_storey_elevations(models)
        storey_containment = self._collect_containment(models)
        self._assign_storeys(all_spaces, storey_containment, storey_elevations)

        # Emit elements
        for sp in all_spaces.values():
            self._elements.append(Element(
                global_id=sp["gid"],
                ifc_class="IfcSpace",
                name=sp["name"],
                extra={
                    "long_name": sp["long_name"],
                    "storey_key": sp["storey_key"],
                    "source_file": sp["source"],
                    "is_vertical_connector": sp["is_vc"],
                },
            ))

        seen: Set[Tuple[str, str]] = set()
        h_edges = self._build_horizontal_edges(all_spaces, seen)
        v_edges = self._build_vertical_edges(all_spaces, storey_elevations, seen)
        isolated = self._resolve_isolated(all_spaces, seen)

        elapsed = time.time() - t0
        self._summary = {
            "method": "space_adjacency",
            "strategy": "bbox_face_adjacency+named_stair_lift",
            "spaces": len(all_spaces),
            "horizontal_edges": h_edges,
            "vertical_edges": v_edges,
            "isolated_resolved": isolated,
            "total_edges": len(self._relationships),
            "input_files": [p.name for p in self.ifc_files],
            "duration_ms": int(elapsed * 1000),
        }
        self.log.info(
            "EgressCirculation[space_adjacency]: %d edges "
            "(%d horizontal, %d vertical, %d isolated resolved) in %.1fs",
            len(self._relationships), h_edges, v_edges, isolated, elapsed,
        )

    # ------------------------------------------------------------------
    # Space geometry collection
    # ------------------------------------------------------------------

    def _collect_space_geometry(
        self,
        models: List[Tuple[Path, ifcopenshell.file]],
    ) -> Dict[str, dict]:
        """Return dict gid → space metadata dict including 2D bbox and centroid."""
        result: Dict[str, dict] = {}
        for ifc_path, ifc in models:
            scale = _unit_scale(ifc)
            settings = _geom_settings()
            for sp in _safe_by_type(ifc, "IfcSpace"):
                gid = sp.GlobalId
                if gid in result:
                    continue
                try:
                    shape = ifcopenshell.geom.create_shape(settings, sp)
                    v = shape.geometry.verts
                    xs = [v[i] * scale for i in range(0, len(v), 3)]
                    ys = [v[i] * scale for i in range(1, len(v), 3)]
                    zs = [v[i] * scale for i in range(2, len(v), 3)]
                    bbox2d = (min(xs), min(ys), max(xs), max(ys))
                    cx = (min(xs) + max(xs)) / 2
                    cy = (min(ys) + max(ys)) / 2
                    cz = (min(zs) + max(zs)) / 2
                    long_name = getattr(sp, "LongName", None) or ""
                    result[gid] = {
                        "gid": gid,
                        "name": sp.Name or gid,
                        "long_name": long_name,
                        "source": ifc_path.name,
                        "bbox2d": bbox2d,
                        "cx": cx, "cy": cy, "cz": cz,
                        "storey_key": None,   # assigned later
                        "is_vc": _is_vertical_connector(long_name, self.vertical_keywords),
                    }
                except Exception as exc:
                    self.log.debug(
                        "EgressCirculation: geometry failed for %s: %s", gid, exc
                    )
        return result

    # ------------------------------------------------------------------
    # Storey assignment
    # ------------------------------------------------------------------

    def _collect_storey_elevations(
        self, models: List[Tuple[Path, ifcopenshell.file]]
    ) -> Dict[str, float]:
        """gid → elevation in metres."""
        elevations: Dict[str, float] = {}
        for _, ifc in models:
            scale = _unit_scale(ifc)
            for st in _safe_by_type(ifc, "IfcBuildingStorey"):
                try:
                    elev = float(getattr(st, "Elevation", 0) or 0) * scale
                    elevations[st.GlobalId] = elev
                except (TypeError, ValueError):
                    elevations[st.GlobalId] = 0.0
        return elevations

    def _collect_containment(
        self, models: List[Tuple[Path, ifcopenshell.file]]
    ) -> Dict[str, str]:
        """space_gid → storey_gid from IfcRelContainedInSpatialStructure."""
        mapping: Dict[str, str] = {}
        for _, ifc in models:
            for rel in _safe_by_type(ifc, "IfcRelContainedInSpatialStructure"):
                st = getattr(rel, "RelatingStructure", None)
                if not st or not st.is_a("IfcBuildingStorey"):
                    continue
                for obj in getattr(rel, "RelatedElements", []) or []:
                    if obj.is_a("IfcSpace"):
                        mapping[obj.GlobalId] = st.GlobalId
        return mapping

    def _assign_storeys(
        self,
        spaces: Dict[str, dict],
        containment: Dict[str, str],
        storey_elevations: Dict[str, float],
    ) -> None:
        """Assign a storey_key to every space (string used for grouping)."""
        # Build elevation → storey_gid lookup for Z-fallback
        sorted_elevations = sorted(storey_elevations.items(), key=lambda x: x[1])

        for sp in spaces.values():
            gid = sp["gid"]
            # 1. IFC spatial containment
            storey_gid = containment.get(gid)
            if storey_gid:
                sp["storey_key"] = storey_gid
                continue

            # 2. Room number prefix (e.g. "040-206..." → "040")
            prefix = _parse_room_prefix(sp["name"])
            if prefix:
                sp["storey_key"] = f"prefix:{prefix}"
                continue

            # 3. Z-centroid nearest storey elevation
            if sorted_elevations:
                best_gid = min(
                    sorted_elevations,
                    key=lambda kv: abs(sp["cz"] - kv[1]),
                )
                sp["storey_key"] = best_gid[0]
            else:
                sp["storey_key"] = f"z:{sp['cz']:.2f}"

    # ------------------------------------------------------------------
    # Horizontal edges (bbox face-share)
    # ------------------------------------------------------------------

    def _build_horizontal_edges(
        self,
        spaces: Dict[str, dict],
        seen: Set[Tuple[str, str]],
    ) -> int:
        """Connect spaces on the same storey that share a 2D bbox face."""
        by_storey: Dict[str, List[dict]] = defaultdict(list)
        for sp in spaces.values():
            by_storey[sp["storey_key"] or "_unknown"].append(sp)

        added = 0
        for storey_key, floor_spaces in by_storey.items():
            gids = [s["gid"] for s in floor_spaces]
            for i, sp1 in enumerate(floor_spaces):
                for sp2 in floor_spaces[i + 1:]:
                    if not _bbox2d_face_adjacent(
                        sp1["bbox2d"], sp2["bbox2d"],
                        self.face_tolerance, self.min_shared_face,
                    ):
                        continue
                    key = tuple(sorted((sp1["gid"], sp2["gid"])))
                    if key in seen:
                        continue
                    seen.add(key)
                    # Confidence: higher when neither space is a large hub
                    shared = _bbox2d_shared_edge(sp1["bbox2d"], sp2["bbox2d"])
                    self._relationships.append(Relationship(
                        subject_global_id=sp1["gid"],
                        object_global_id=sp2["gid"],
                        relationship_family="circulation",
                        relationship_type="egress_connects",
                        confidence=0.85,
                        source_kind="topologic_ingest_EgressCirculation",
                        evidence={
                            "method": "bbox_face_adjacency",
                            "shared_edge_m": round(shared, 3),
                            "storey_key": storey_key,
                            "source_file": sp1["source"],
                        },
                    ))
                    added += 1
        self.log.info(
            "EgressCirculation[space_adjacency]: %d horizontal edges (bbox face-share)", added
        )
        return added

    # ------------------------------------------------------------------
    # Vertical edges (named stairway / lift matching)
    # ------------------------------------------------------------------

    def _build_vertical_edges(
        self,
        spaces: Dict[str, dict],
        storey_elevations: Dict[str, float],
        seen: Set[Tuple[str, str]],
    ) -> int:
        """Connect same-named stairway/lift spaces on consecutive storeys."""
        # Group vertical connectors by normalised long_name
        vc_by_name: Dict[str, List[dict]] = defaultdict(list)
        for sp in spaces.values():
            if sp["is_vc"]:
                vc_by_name[_normalise_vc_name(sp["long_name"])].append(sp)

        # Build ordered storey list for consecutive detection
        storey_order = self._storey_order(spaces, storey_elevations)

        added = 0
        for norm_name, instances in vc_by_name.items():
            if len(instances) < 2:
                continue
            # Sort instances by storey elevation
            instances_sorted = sorted(
                instances,
                key=lambda sp: storey_order.get(sp["storey_key"], 0.0),
            )
            # Connect consecutive pairs (not all-to-all — stairs go floor to floor)
            for idx in range(len(instances_sorted) - 1):
                sp1 = instances_sorted[idx]
                sp2 = instances_sorted[idx + 1]
                key = tuple(sorted((sp1["gid"], sp2["gid"])))
                if key in seen:
                    continue
                seen.add(key)
                self._relationships.append(Relationship(
                    subject_global_id=sp1["gid"],
                    object_global_id=sp2["gid"],
                    relationship_family="circulation",
                    relationship_type="egress_connects",
                    confidence=0.95,
                    source_kind="topologic_ingest_EgressCirculation",
                    evidence={
                        "method": "named_stair_lift_match",
                        "connector_name": norm_name,
                        "connector_long_name": sp1["long_name"],
                        "storey_from": sp1["storey_key"],
                        "storey_to": sp2["storey_key"],
                        "source_file": sp1["source"],
                    },
                ))
                added += 1

        self.log.info(
            "EgressCirculation[space_adjacency]: %d vertical edges (%d named connectors)",
            added, len(vc_by_name),
        )
        return added

    def _storey_order(
        self,
        spaces: Dict[str, dict],
        storey_elevations: Dict[str, float],
    ) -> Dict[str, float]:
        """Return storey_key → elevation for sorting."""
        order: Dict[str, float] = {}
        for sp in spaces.values():
            sk = sp["storey_key"] or ""
            if sk in order:
                continue
            if sk in storey_elevations:
                order[sk] = storey_elevations[sk]
            elif sk.startswith("prefix:"):
                # "prefix:040" → ordinal based on numeric value
                try:
                    order[sk] = float(sk[7:])
                except ValueError:
                    order[sk] = 0.0
            elif sk.startswith("z:"):
                try:
                    order[sk] = float(sk[2:])
                except ValueError:
                    order[sk] = 0.0
            else:
                order[sk] = sp["cz"]
        return order

    # ------------------------------------------------------------------
    # Resolve isolated spaces (not connected by any edge)
    # ------------------------------------------------------------------

    def _resolve_isolated(
        self,
        spaces: Dict[str, dict],
        seen: Set[Tuple[str, str]],
    ) -> int:
        """Connect any space not yet in any edge to its nearest neighbour.

        Searches same-storey spaces first; if the storey group is a singleton
        (or all same-storey candidates are already connected), falls back to
        the nearest space across all storeys. This handles geometrically
        misplaced spaces or spaces whose storey key doesn't match any peers.
        """
        connected_gids: Set[str] = set()
        for g1, g2 in seen:
            connected_gids.add(g1)
            connected_gids.add(g2)

        by_storey: Dict[str, List[dict]] = defaultdict(list)
        for sp in spaces.values():
            by_storey[sp["storey_key"] or "_unknown"].append(sp)

        all_spaces_list = list(spaces.values())
        added = 0

        for storey_key, floor_spaces in by_storey.items():
            isolated = [s for s in floor_spaces if s["gid"] not in connected_gids]
            if not isolated:
                continue
            for iso in isolated:
                # Try same-storey neighbours first
                same_storey_candidates = [s for s in floor_spaces if s["gid"] != iso["gid"]]
                pool = same_storey_candidates if same_storey_candidates else all_spaces_list
                pool = [s for s in pool if s["gid"] != iso["gid"]]
                if not pool:
                    continue
                best = min(
                    pool,
                    key=lambda s: _planar_dist(iso["cx"], iso["cy"], s["cx"], s["cy"]),
                )
                key = tuple(sorted((iso["gid"], best["gid"])))
                if key in seen:
                    continue
                seen.add(key)
                dist = _planar_dist(iso["cx"], iso["cy"], best["cx"], best["cy"])
                self._relationships.append(Relationship(
                    subject_global_id=iso["gid"],
                    object_global_id=best["gid"],
                    relationship_family="circulation",
                    relationship_type="egress_connects",
                    confidence=0.50,
                    source_kind="topologic_ingest_EgressCirculation",
                    evidence={
                        "method": "centroid_proximity_fallback",
                        "distance_m": round(dist, 2),
                        "storey_key": storey_key,
                        "source_file": iso["source"],
                    },
                ))
                connected_gids.add(iso["gid"])
                added += 1
        if added:
            self.log.info(
                "EgressCirculation[space_adjacency]: %d isolated spaces resolved by centroid proximity",
                added,
            )
        return added

    # ==================================================================
    # STRATEGY B: door_portal  (original implementation)
    # ==================================================================

    def _extract_door_portal(self) -> None:
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

        space_points, space_sources, space_names = _collect_spaces_centroids(models)
        element_storey, storey_elevations = _collect_storey_maps(models)
        storey_stats = _storey_resolution_stats(
            space_points, space_names, element_storey, storey_elevations, self.storey_z_tolerance,
        )
        self.log.info(
            "EgressCirculation: %d file(s), %d spaces, %d doors across inputs "
            "(storey: %d IFC, %d prefix, %d Z-inferred, %d unresolved)",
            len(models),
            len(space_points),
            sum(len(_safe_by_type(ifc, "IfcDoor")) for _, ifc in models),
            storey_stats["ifc_containment"],
            storey_stats.get("prefix_key", 0),
            storey_stats["z_inferred"],
            storey_stats["unresolved"],
        )

        if len(space_points) >= 2:
            added, portals = self._link_all_doors_to_spaces(
                models,
                space_points,
                space_sources,
                space_names,
                element_storey,
                storey_elevations,
                seen_edges,
                portal_elements,
            )
            if portals:
                methods.add("door_space_proximity")

        for ifc_path, ifc in models:
            space_count = len(_safe_by_type(ifc, "IfcSpace"))
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
                space_names,
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
        cross_storey = self._count_cross_storey_door_edges(
            space_points, space_names, element_storey, storey_elevations,
        )
        self._summary = {
            "portals_used": portal_count,
            "method": method_label,
            "strategy": "door_portal",
            "input_files": [p.name for p in self.ifc_files],
            "space_count": len(space_points),
            "storey_ifc_containment": storey_stats["ifc_containment"],
            "storey_z_inferred": storey_stats["z_inferred"],
            "storey_unresolved": storey_stats["unresolved"],
            "building_storeys": len(storey_elevations),
            "cross_storey_edges": cross_storey,
            "duration_ms": int((time.time() - t0) * 1000),
        }
        self.log.info(
            "EgressCirculation: %d egress edges via %d portals (%s)",
            len(self._relationships),
            portal_count,
            method_label,
        )

    def _count_cross_storey_door_edges(
        self,
        space_points: Dict[str, Tuple[float, float, float]],
        space_names: Dict[str, str],
        element_storey: Dict[str, str],
        storey_elevations: Dict[str, float],
    ) -> int:
        """Count door-portal edges whose space endpoints belong to different storeys."""
        cross = 0
        for rel in self._relationships:
            method = (rel.evidence or {}).get("method") or ""
            if method not in ("door_space_proximity", "ifc_portal_boundary", "topologicpy_portal_graph"):
                continue
            s1, s2 = rel.subject_global_id, rel.object_global_id
            pt1 = space_points.get(s1)
            pt2 = space_points.get(s2)
            k1 = _storey_group_key(
                s1, pt1, space_names.get(s1, ""), element_storey, storey_elevations, self.storey_z_tolerance,
            )
            k2 = _storey_group_key(
                s2, pt2, space_names.get(s2, ""), element_storey, storey_elevations, self.storey_z_tolerance,
            )
            if k1 and k2 and k1 != k2:
                cross += 1
        return cross

    # ------------------------------------------------------------------
    # door_portal helpers
    # ------------------------------------------------------------------

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
                        s1, s2,
                        portal_id, portal_elem, "",
                        "topologicpy_portal_graph",
                        ifc_path.name,
                    ):
                        added += 1

        except Exception as exc:
            self.log.warning(
                "EgressCirculation: TopologicPy failed for %s: %s",
                ifc_path.name, exc,
            )
        return added, portals_used

    def _link_all_doors_to_spaces(
        self,
        models: List[Tuple[Path, ifcopenshell.file]],
        space_points: Dict[str, Tuple[float, float, float]],
        space_sources: Dict[str, str],
        space_names: Dict[str, str],
        element_storey: Dict[str, str],
        storey_elevations: Dict[str, float],
        seen_edges: Set[Tuple[str, str]],
        portal_elements: Dict[str, str],
    ) -> Tuple[int, int]:
        """Link each door to the two nearest spaces on the same storey (space-to-space)."""
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

                ranked = sorted(
                    space_points.items(),
                    key=lambda item: _planar_dist(door_pt[0], door_pt[1], item[1][0], item[1][1]),
                )
                by_storey: Dict[str, List[Tuple[str, Tuple[float, float, float], float]]] = defaultdict(list)
                for sid, pt in ranked:
                    dist = _planar_dist(door_pt[0], door_pt[1], pt[0], pt[1])
                    if dist > max_dist:
                        break
                    storey_key = _storey_group_key(
                        sid, pt, space_names.get(sid, ""),
                        element_storey, storey_elevations, self.storey_z_tolerance,
                    )
                    if self.same_storey_only and not storey_key:
                        continue
                    group_key = storey_key if self.same_storey_only else "_all"
                    by_storey[group_key].append((sid, pt, dist))

                best_pair: Optional[Tuple[str, Tuple[float, float, float], str, Tuple[float, float, float], str]] = None
                best_lead_dist = float("inf")
                for _storey_key, group in by_storey.items():
                    if len(group) < 2:
                        continue
                    group.sort(key=lambda item: item[2])
                    lead_dist = group[0][2]
                    if lead_dist < best_lead_dist:
                        best_lead_dist = lead_dist
                        s1, pt1, _ = group[0]
                        s2, pt2, _ = group[1]
                        best_pair = (s1, pt1, s2, pt2, _storey_key)

                if best_pair is None:
                    continue
                s1, _pt1, s2, _pt2, storey_key = best_pair

                portals_used += 1
                portal_elements[door.GlobalId] = door.is_a()
                evidence_source = ifc_path.name
                if len(self.ifc_files) > 1:
                    evidence_source = (
                        f"{ifc_path.name}|spaces="
                        f"{space_sources.get(s1, '?')},{space_sources.get(s2, '?')}"
                    )
                if _append_edge(
                    self._relationships, seen_edges,
                    s1, s2,
                    door.GlobalId, door.is_a(),
                    getattr(door, "Name", None) or door.GlobalId,
                    "door_space_proximity",
                    evidence_source,
                    extra_evidence={"storey_key": storey_key} if storey_key else None,
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
        space_names: Dict[str, str],
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

            for s1, s2 in combinations(sorted(space_ids), 2):
                if self.same_storey_only:
                    pt1 = space_points.get(s1)
                    pt2 = space_points.get(s2)
                    k1 = _storey_group_key(
                        s1, pt1, space_names.get(s1, ""), element_storey, storey_elevations, self.storey_z_tolerance,
                    )
                    k2 = _storey_group_key(
                        s2, pt2, space_names.get(s2, ""), element_storey, storey_elevations, self.storey_z_tolerance,
                    )
                    if not k1 or not k2 or k1 != k2:
                        continue
                _append_edge(
                    self._relationships, seen_edges,
                    s1, s2,
                    portal_id, portal_class, portal_name,
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
                    if not self.include_openings_without_door and not self._opening_has_door(element):
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


# ===========================================================================
# Module-level helpers
# ===========================================================================


def _unit_scale(ifc) -> float:
    try:
        return ifcopenshell.util.unit.calculate_unit_scale(ifc) or 1.0
    except Exception:
        return 1.0


def _geom_settings():
    settings = ifcopenshell.geom.settings()
    try:
        settings.set(settings.USE_WORLD_COORDS, True)
    except Exception:
        pass
    return settings


def _parse_room_prefix(name: str) -> Optional[str]:
    """Extract storey prefix from room number, e.g. '040-206_16m²' → '040'."""
    m = _ROOM_NR_RE.search(name or "")
    return m.group(1) if m else None


def _normalise_vc_name(long_name: str) -> str:
    """Normalise a stairway/lift long name for cross-storey matching.

    Strips leading/trailing whitespace and lowercases so "Stairway 3.1" and
    "stairway 3.1" group together.
    """
    return (long_name or "").strip().lower()


def _is_vertical_connector(long_name: str, keywords: Tuple[str, ...]) -> bool:
    ln = (long_name or "").lower()
    return any(kw in ln for kw in keywords)


def _bbox2d_face_adjacent(
    b1: Tuple[float, float, float, float],
    b2: Tuple[float, float, float, float],
    tol: float,
    min_shared: float,
) -> bool:
    """True when two 2D bboxes share a face within tolerance.

    A shared face means: gap ≤ tol on one axis AND overlap ≥ min_shared on
    the perpendicular axis.
    """
    x_gap = max(b1[0] - b2[2], b2[0] - b1[2], 0.0)
    y_gap = max(b1[1] - b2[3], b2[1] - b1[3], 0.0)
    x_ov  = max(0.0, min(b1[2], b2[2]) - max(b1[0], b2[0]))
    y_ov  = max(0.0, min(b1[3], b2[3]) - max(b1[1], b2[1]))
    return (x_gap <= tol and y_ov >= min_shared) or (y_gap <= tol and x_ov >= min_shared)


def _bbox2d_shared_edge(
    b1: Tuple[float, float, float, float],
    b2: Tuple[float, float, float, float],
) -> float:
    """Approximate shared edge length (for evidence metadata)."""
    x_ov = max(0.0, min(b1[2], b2[2]) - max(b1[0], b2[0]))
    y_ov = max(0.0, min(b1[3], b2[3]) - max(b1[1], b2[1]))
    return max(x_ov, y_ov)


def _planar_dist(x1: float, y1: float, x2: float, y2: float) -> float:
    return ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5


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
    extra_evidence: Optional[Dict[str, str]] = None,
) -> bool:
    key = tuple(sorted((s1, s2)))
    if key in seen_edges:
        return False
    seen_edges.add(key)
    evidence: Dict[str, str] = {
        "method": method,
        "portal_global_id": portal_id,
        "portal_class": portal_class,
        "portal_name": portal_name or portal_id,
        "source_file": source_file,
    }
    if extra_evidence:
        evidence.update(extra_evidence)
    relationships.append(
        Relationship(
            subject_global_id=s1,
            object_global_id=s2,
            relationship_family="circulation",
            relationship_type="egress_connects",
            confidence=0.95,
            source_kind="topologic_ingest_EgressCirculation",
            evidence=evidence,
        )
    )
    return True


def _safe_by_type(ifc, type_name: str) -> List:
    try:
        return list(ifc.by_type(type_name))
    except RuntimeError:
        return []


# ------------------------------------------------------------------
# door_portal module helpers
# ------------------------------------------------------------------


def _merge_ifc_models(
    models: List[Tuple[Path, ifcopenshell.file]],
    log: logging.Logger,
) -> List[Tuple[Path, ifcopenshell.file]]:
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
        copied, len(ranked) - 1, base_path.name,
    )
    return [(base_path, base)]


def _storey_group_key(
    global_id: str,
    point: Optional[Tuple[float, float, float]],
    space_name: str,
    element_storey: Dict[str, str],
    storey_elevations: Dict[str, float],
    z_tolerance: float,
) -> Optional[str]:
    """Comparable storey key for a space — IFC containment, room prefix, or elevation."""
    storey_gid = element_storey.get(global_id)
    if storey_gid and storey_gid in storey_elevations:
        return f"elev:{round(storey_elevations[storey_gid], 2)}"
    if storey_gid:
        return f"gid:{storey_gid}"
    prefix = _parse_room_prefix(space_name)
    if prefix:
        return f"prefix:{prefix}"
    if point is not None and storey_elevations:
        inferred = _infer_storey_from_z(point[2], storey_elevations, z_tolerance)
        if inferred and inferred in storey_elevations:
            return f"elev:{round(storey_elevations[inferred], 2)}"
        return f"z:{round(point[2], 1)}"
    return None


def _storey_resolution_stats(
    space_points: Dict[str, Tuple[float, float, float]],
    space_names: Dict[str, str],
    element_storey: Dict[str, str],
    storey_elevations: Dict[str, float],
    z_tolerance: float,
) -> Dict[str, int]:
    """How each space resolves a storey for same-level filtering."""
    ifc_containment = 0
    prefix_key = 0
    z_inferred = 0
    unresolved = 0
    for gid, pt in space_points.items():
        key = _storey_group_key(
            gid, pt, space_names.get(gid, ""), element_storey, storey_elevations, z_tolerance,
        )
        if not key:
            unresolved += 1
        elif key.startswith("prefix:"):
            prefix_key += 1
        elif gid in element_storey:
            ifc_containment += 1
        else:
            z_inferred += 1
    return {
        "ifc_containment": ifc_containment,
        "prefix_key": prefix_key,
        "z_inferred": z_inferred,
        "unresolved": unresolved,
    }


def _collect_storey_maps(
    models: List[Tuple[Path, ifcopenshell.file]],
) -> Tuple[Dict[str, str], Dict[str, float]]:
    element_storey: Dict[str, str] = {}
    storey_elevations: Dict[str, float] = {}

    for _, ifc in models:
        scale = _unit_scale(ifc)
        for storey in _safe_by_type(ifc, "IfcBuildingStorey"):
            gid = storey.GlobalId
            try:
                storey_elevations[gid] = float(getattr(storey, "Elevation", 0) or 0) * scale
            except (TypeError, ValueError):
                storey_elevations[gid] = 0.0

        for element in _safe_by_type(ifc, "IfcSpace") + _safe_by_type(ifc, "IfcDoor"):
            storey_id = _element_storey_id(element)
            if storey_id:
                element_storey[element.GlobalId] = storey_id

        for rel in _safe_by_type(ifc, "IfcRelContainedInSpatialStructure"):
            struct = getattr(rel, "RelatingStructure", None)
            if not struct or not struct.is_a("IfcBuildingStorey"):
                continue
            for obj in getattr(rel, "RelatedElements", []) or []:
                if obj.is_a() in ("IfcSpace", "IfcDoor"):
                    element_storey[obj.GlobalId] = struct.GlobalId

    return element_storey, storey_elevations


def _element_storey_id(element) -> Optional[str]:
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


def _collect_spaces_centroids(
    models: List[Tuple[Path, ifcopenshell.file]],
) -> Tuple[Dict[str, Tuple[float, float, float]], Dict[str, str], Dict[str, str]]:
    space_points: Dict[str, Tuple[float, float, float]] = {}
    space_sources: Dict[str, str] = {}
    space_names: Dict[str, str] = {}
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
            space_names[gid] = space.Name or gid
    return space_points, space_sources, space_names


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
    return any(portal in ifc_class for portal in PORTAL_TYPES)


def _host_wall_global_id(door) -> Optional[str]:
    for rel in getattr(door, "FillsVoids", None) or []:
        opening = getattr(rel, "RelatedOpeningElement", None) or getattr(
            rel, "RelatingOpeningElement", None
        )
        if not opening:
            continue
        void_rels = (
            getattr(opening, "VoidsElements", None)
            or getattr(opening, "HasOpenings", None)
            or []
        )
        for vrel in void_rels:
            host = getattr(vrel, "RelatingBuildingElement", None) or getattr(
                vrel, "RelatedBuildingElement", None
            )
            if host and host.is_a() in WALL_TYPES:
                return host.GlobalId
    return None


def _element_centroid(element) -> Optional[Tuple[float, float, float]]:
    try:
        settings = _geom_settings()
        shape = ifcopenshell.geom.create_shape(settings, element)
        if shape is None:
            return None
        verts = shape.geometry.verts
        if not verts:
            return None
        xs = verts[0::3]; ys = verts[1::3]; zs = verts[2::3]
        return (sum(xs) / len(xs), sum(ys) / len(ys), sum(zs) / len(zs))
    except Exception:
        try:
            import ifcopenshell.util.placement as pu
            m = pu.get_local_placement(element.ObjectPlacement)
            return (m[0][3], m[1][3], m[2][3])
        except Exception:
            return None
