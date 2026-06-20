"""Door- and opening-aware circulation graph between IfcSpace elements.

Two routing strategies are supported:

``strategy="door_portal"`` (default)
    Builds edges through IfcDoor portals between IfcSpace rooms on the same storey:

    1. Authoring-tool space boundaries (``IfcRelSpaceBoundary`` / wall-hosted map)
    2. Plan-view point containment — sample both sides of each door in XY
    3. Centroid proximity fallback when geometry is inconclusive

    Each physical portal is modelled two-hop rather than as a direct space↔space
    edge: every room reaches the IfcDoor/opening that separates it via an
    ``egress_through`` edge, so the door is the shared middle node
    (space → door → space). Door-less heuristic links (vertical connectors,
    apartment clusters) stay direct space↔space ``egress_connects`` edges since
    there is no portal element to route through.

    Vertical travel (stairs/lifts/shafts) uses named connector labels and stacked
    footprint matching across consecutive storeys.

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
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element as ifc_element_util
import ifcopenshell.util.unit

from ingest_scripts import Element, Ingester as _Base, Relationship
from ingest_scripts import ifc_thin_spaces

try:
    # The IFC topology graph goes through the shared TGraph adapter (topograph).
    # Legacy `Graph` is kept ONLY for the geometry navmesh below
    # (NavigationGraph + ShortestPath(useAStar) over a walkable Face) — that has
    # no TGraph equivalent and is unchanged in 0.9.50.
    from ingest_scripts import topograph
    from topologicpy.Graph import Graph
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

# Two-hop portal model: a space reaches the IfcDoor/opening that separates it
# from the adjacent room (space -> door -> space). The door is the shared node.
EGRESS_THROUGH_TYPE = "egress_through"  # IfcSpace --> IfcDoor/IfcOpeningElement

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
# Revit apartment aggregate anchors (Name ``2-1103``) share XY across floors and
# must not participate in door proximity linking.
_APARTMENT_AGGREGATE_RE = re.compile(r"^\d+-\d+$")

# Skip Graph.ByIFCFile on large federated exports (SBUF ~360k–710k entities) where
# OCCT routinely hangs or SIGSEGVs even when space count is modest.
_TOPOLOGIC_MAX_ENTITIES = 120_000


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
        door_side_offset: float = 0.6,
        door_plan_tolerance: float = 0.25,
        same_storey_only: bool = True,
        storey_z_tolerance: float = 2.5,
        # --- door-less opening pass (opt-in) ---
        link_doorless_openings: bool = True,
        min_opening_width: float = 0.6,
        max_sill_height: float = 0.3,
        diagnose_space: Optional[str] = None,
        # --- navmesh clearance pass (opt-in) ---
        link_navmesh_passages: bool = False,
        human_width: float = 0.6,
        human_height: float = 1.8,
        navmesh_margin: float = 0.6,
        navmesh_compute_path: bool = False,
        # --- stair/elevator element vertical pass ---
        link_stair_elements: bool = True,
        min_stair_rise: float = 1.0,
        # --- space_adjacency options ---
        face_tolerance: float = 0.15,
        min_shared_face: float = 0.30,
        vertical_keywords: Optional[Tuple[str, ...]] = None,
        use_topologic: bool = True,
        force_ifc_native: bool = False,
        thin_spaces: bool = True,
    ):
        """Extract space-to-space circulation edges.

        :param strategy: ``"door_portal"`` (default) or ``"space_adjacency"``.
        :param include_virtual_boundaries: (door_portal) Include virtual space boundaries.
        :param include_openings_without_door: (door_portal) Include bare openings as portals.
        :param tolerance: (door_portal) Graph construction tolerance in model units.
        :param door_link_distance: (door_portal) Fallback max distance door centroid → space centroid.
        :param door_side_offset: (door_portal) Plan offset (m) from door centre to sample both sides.
        :param door_plan_tolerance: (door_portal) XY tolerance (m) for point-in-space footprint tests.
        :param same_storey_only: (door_portal) Only link two spaces that share the same
            storey (IFC containment, room-number prefix, or elevation — not door Z proximity).
        :param storey_z_tolerance: (door_portal) Z fallback tolerance in model units.
        :param link_doorless_openings: (door_portal) Also link door-less wall openings
            (open doorways) to the two spaces they separate, the same way doors are linked.
            On by default — see ``_link_openings_to_spaces``.
        :param min_opening_width: (door_portal) Passable-width guard (m) for door-less
            openings; voids whose plan extent / height are below this are ignored.
        :param max_sill_height: (door_portal) Floor-reaching threshold (m) for door-less
            openings; an opening whose bottom is more than this above its storey elevation
            is treated as a window/high vent (not egress) and skipped.
        :param link_navmesh_passages: (door_portal) Detect walkable connections between
            adjacent rooms even where *no* door or opening element is modelled, by testing
            whether a human-sized box fits through the gap between walls (clearance
            pathfinding). Off by default. See ``_link_navmesh_passages``.
        :param human_width: (navmesh) Body width (m) that must fit between walls; the wall
            clearance gate is half this value on each side. Default 0.6.
        :param human_height: (navmesh) Body height (m); recorded on the passage for a later
            3D headroom check. Not enforced in the 2D plan pass. Default 1.8.
        :param navmesh_margin: (navmesh) Local working margin (m) around the two room
            footprints — large enough to bridge a wall/door reveal, small enough to keep
            the search local (no routing around far walls). Default 0.6.
        :param navmesh_compute_path: (navmesh) When True, run TopologicPy NavigationGraph +
            A* on the walkable region to record the egress travel distance of each passage.
            Off by default — it builds a visibility graph per passage (slower); the
            shapely clearance gate alone already answers "does the box fit".
        :param link_stair_elements: (door_portal) Use IfcStair/IfcStairFlight geometry (and
            IfcTransportElement lifts) as the *measure* for vertical circulation: a stair
            spans two storeys, so the spaces its base and top footprints land in are linked
            two-hop through the stair element node. On by default; runs before the
            name/footprint connector heuristics, which then only fill connectors with no
            usable element. See ``_link_stair_element_connectors``.
        :param min_stair_rise: (door_portal) Minimum vertical span (m) for a stair/flight
            bbox to count as crossing a storey; flatter elements (and half-flights that stay
            on one level) are skipped. Default 1.0.
        :param face_tolerance: (space_adjacency) Max bbox gap (metres) to count as touching.
        :param min_shared_face: (space_adjacency) Min shared edge (metres) for adjacency.
        :param vertical_keywords: (space_adjacency) Override stair/lift keyword list.
        :param use_topologic: (door_portal) When False, skip Graph.ByIFCFile portal graph step.
        :param force_ifc_native: Internal retry flag after SIGSEGV (same as use_topologic=False).
        :param thin_spaces: When True (default), build a spaces-only IFC via RemoveElements
            (one pass) for space/Topologic work while doors are read from the full input file(s).
        """
        super().__init__(ifc_files, log)
        self.strategy = strategy.strip().lower()
        self.use_topologic = bool(use_topologic) and not bool(force_ifc_native)
        self.thin_spaces = bool(thin_spaces)
        self._temp_paths: List[Path] = []
        # Two-hop portal model state (door_portal strategy): space -> door -> space
        self._portal_space_pairs: List[Tuple[str, str, str]] = []
        # door_portal params
        self.include_virtual_boundaries = include_virtual_boundaries
        self.include_openings_without_door = include_openings_without_door
        self.tolerance = tolerance
        self.door_link_distance = door_link_distance
        self.door_side_offset = door_side_offset
        self.door_plan_tolerance = door_plan_tolerance
        self.same_storey_only = same_storey_only
        self.storey_z_tolerance = storey_z_tolerance
        # door-less opening pass params
        self.link_doorless_openings = bool(link_doorless_openings)
        self.min_opening_width = min_opening_width
        self.max_sill_height = max_sill_height
        self.diagnose_space = diagnose_space
        # navmesh clearance pass params
        self.link_navmesh_passages = bool(link_navmesh_passages)
        self.human_width = float(human_width)
        self.human_height = float(human_height)
        self.navmesh_margin = float(navmesh_margin)
        self.navmesh_compute_path = bool(navmesh_compute_path)
        # stair/elevator element vertical pass params
        self.link_stair_elements = bool(link_stair_elements)
        self.min_stair_rise = float(min_stair_rise)
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

        try:
            try:
                space_models, _ = self._prepare_space_and_portal_models()
                models = space_models
            except Exception as exc:
                self.log.warning(
                    "EgressCirculation[space_adjacency]: thin spaces prep failed (%s); opening raw inputs",
                    exc,
                )
                models = []
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
        finally:
            self._cleanup_temp_paths()

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
        self._portal_space_pairs = []
        doorless_openings_linked = 0
        navmesh_passages_linked = 0

        try:
            space_models, portal_models = self._prepare_space_and_portal_models()
            if len(self.ifc_files) > 1:
                methods.add("federated_inputs")
            if self.thin_spaces and self._temp_paths:
                methods.add("thin_spaces_remove")

            space_points, space_sources, space_names = _collect_spaces_centroids(space_models)
            element_storey, storey_elevations = _collect_storey_maps(space_models + portal_models)
            storey_stats = _storey_resolution_stats(
                space_points, space_names, element_storey, storey_elevations, self.storey_z_tolerance,
            )
            self.log.info(
                "EgressCirculation: %d space file(s), %d portal file(s), %d spaces, %d doors "
                "(storey: %d IFC, %d prefix, %d Z-inferred, %d unresolved)",
                len(space_models),
                len(portal_models),
                len(space_points),
                sum(len(_safe_by_type(ifc, "IfcDoor")) for _, ifc in portal_models),
                storey_stats["ifc_containment"],
                storey_stats.get("prefix_key", 0),
                storey_stats["z_inferred"],
                storey_stats["unresolved"],
            )

            if len(space_points) >= 2:
                space_bboxes = _collect_space_bboxes(space_models)
                # Tight footprints disambiguate bbox overlaps in door/opening side-point
                # resolution (a neighbour room's bbox can overhang a corridor-side point).
                self._space_polys = _collect_space_footprints(space_models)
                door_methods: Set[str] = set()
                added, portals = self._link_all_doors_to_spaces(
                    portal_models,
                    space_models,
                    space_bboxes,
                    space_points,
                    space_sources,
                    space_names,
                    element_storey,
                    storey_elevations,
                    seen_edges,
                    portal_elements,
                    door_methods,
                )
                methods.update(door_methods)

                # Door-less openings (open doorways) get the SAME geometric treatment as
                # doors, *after* the door pass so a pair already bridged by a door sits in
                # seen_edges and the opening is suppressed (no double-count).
                if self.link_doorless_openings:
                    opening_methods: Set[str] = set()
                    _op_added, doorless_openings_linked = self._link_openings_to_spaces(
                        portal_models,
                        space_bboxes,
                        space_points,
                        space_sources,
                        space_names,
                        element_storey,
                        storey_elevations,
                        seen_edges,
                        portal_elements,
                        opening_methods,
                    )
                    methods.update(opening_methods)

                # Clearance pathfinding: catch human-passable gaps between rooms that
                # have NO door/opening element. Runs after the element passes so a real
                # portal's pair is already in seen_edges (drives the new-vs-coincide split).
                if self.link_navmesh_passages:
                    navmesh_methods: Set[str] = set()
                    _nav_added, navmesh_passages_linked = self._link_navmesh_passages(
                        portal_models,
                        space_models,
                        space_bboxes,
                        space_points,
                        space_names,
                        element_storey,
                        storey_elevations,
                        seen_edges,
                        navmesh_methods,
                    )
                    methods.update(navmesh_methods)

                apt_added = self._link_apartment_room_clusters(
                    space_models,
                    space_points,
                    space_names,
                    element_storey,
                    storey_elevations,
                    seen_edges,
                )
                if apt_added:
                    methods.add("apartment_room_cluster")

                vert_added = self._link_vertical_connectors(
                    portal_models,
                    space_models,
                    space_bboxes,
                    space_points,
                    space_names,
                    element_storey,
                    storey_elevations,
                    seen_edges,
                    portal_elements,
                    vertical_methods := set(),
                )
                methods.update(vertical_methods)

            for ifc_path, ifc in space_models:
                space_count = len(_safe_by_type(ifc, "IfcSpace"))
                entity_count = ifc_thin_spaces.approx_entity_count(ifc)
                topologic_ok = (
                    HAS_TOPOLOGICPY
                    and self.use_topologic
                    and 0 < space_count <= 400
                    and entity_count <= _TOPOLOGIC_MAX_ENTITIES
                )
                if (
                    HAS_TOPOLOGICPY
                    and self.use_topologic
                    and not topologic_ok
                    and entity_count > _TOPOLOGIC_MAX_ENTITIES
                ):
                    self.log.info(
                        "EgressCirculation: skipping TopologicPy graph for %s "
                        "(%d entities > %d)",
                        ifc_path.name,
                        entity_count,
                        _TOPOLOGIC_MAX_ENTITIES,
                    )
                if topologic_ok:
                    added, _ = self._extract_from_topologic_graph(
                        ifc_path, seen_edges, portal_elements
                    )
                    if added:
                        methods.add("topologicpy_portal_graph")

            for ifc_path, ifc in portal_models:
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
        finally:
            self._cleanup_temp_paths()

        existing_ids = {e.global_id for e in self._elements}
        # Doors/openings are the shared egress-portal nodes the space->door->space
        # edges route through; emit them as their real IFC class.
        for portal_id, portal_class in portal_elements.items():
            if portal_id in existing_ids:
                continue
            existing_ids.add(portal_id)
            self._elements.append(
                Element(
                    global_id=portal_id,
                    ifc_class=portal_class,
                    name=portal_id,
                    extra={"role": "egress_portal"},
                )
            )

        portal_count = len(
            {
                pid
                for rel in self._relationships
                if (pid := rel.evidence.get("portal_global_id"))
            }
        )
        method_label = "+".join(sorted(methods)) if methods else "none"
        cross_storey = self._count_cross_storey_door_edges(
            space_points, space_names, element_storey, storey_elevations,
        )
        stair_element_edges = sum(
            1
            for rel in self._relationships
            if rel.evidence.get("method") in (
                "stair_element", "transport_element", "transport_element_namefallback"
            )
        )
        self._summary = {
            "portals_used": portal_count,
            "doorless_openings_linked": doorless_openings_linked,
            "navmesh_passages_linked": navmesh_passages_linked,
            "stair_element_connectors": stair_element_edges,
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

    def _prepare_space_and_portal_models(
        self,
    ) -> Tuple[List[Tuple[Path, ifcopenshell.file]], List[Tuple[Path, ifcopenshell.file]]]:
        """Resolve spaces-only thin model(s) and full portal source model(s).

        Single combined IFC: one RemoveElements pass (!IfcSpace) on a temp copy for
        spaces; doors and boundaries are read from the original file.

        Federated inputs: files that are already spaces-only are used as-is; files with
        doors/architecture supply portal geometry without a second remove pass.
        """
        space_models: List[Tuple[Path, ifcopenshell.file]] = []
        portal_models: List[Tuple[Path, ifcopenshell.file]] = []
        seen_space_paths: Set[str] = set()

        for ifc_path in self.ifc_files:
            ifc_path = ifc_path.resolve()
            self.log.info("EgressCirculation: opening %s", ifc_path.name)
            full_ifc = ifcopenshell.open(str(ifc_path))
            portal_models.append((ifc_path, full_ifc))

            space_count = len(_safe_by_type(full_ifc, "IfcSpace"))
            if space_count == 0:
                continue

            already_thin = ifc_thin_spaces.is_spaces_only_file(full_ifc)
            if already_thin or not self.thin_spaces:
                key = str(ifc_path)
                if key not in seen_space_paths:
                    space_models.append((ifc_path, full_ifc))
                    seen_space_paths.add(key)
                continue

            try:
                thin_path = ifc_thin_spaces.thin_spaces_copy(ifc_path, log=self.log)
                self._temp_paths.append(thin_path)
                space_models.append((thin_path, ifcopenshell.open(str(thin_path))))
            except Exception as exc:
                self.log.warning(
                    "EgressCirculation: thin spaces failed for %s (%s); using full file for spaces",
                    ifc_path.name,
                    exc,
                )
                key = str(ifc_path)
                if key not in seen_space_paths:
                    space_models.append((ifc_path, full_ifc))
                    seen_space_paths.add(key)

        if not space_models and portal_models:
            # Portal-only inputs — fall back to full files for space discovery.
            self.log.warning(
                "EgressCirculation: no dedicated space file; using full input(s) for spaces",
            )
            space_models = list(portal_models)

        return space_models, portal_models

    def _cleanup_temp_paths(self) -> None:
        import os

        for path in self._temp_paths:
            try:
                os.unlink(path)
            except OSError:
                pass
        self._temp_paths.clear()

    def _count_cross_storey_door_edges(
        self,
        space_points: Dict[str, Tuple[float, float, float]],
        space_names: Dict[str, str],
        element_storey: Dict[str, str],
        storey_elevations: Dict[str, float],
    ) -> int:
        """Count portal-mediated connections whose two spaces belong to different storeys."""
        cross = 0
        for s1, s2, _method in self._portal_space_pairs:
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
    # Two-hop portal linking (space -> door -> space)
    # ------------------------------------------------------------------

    def _append_portal_link(
        self,
        seen_edges: Set[Tuple[str, str]],
        space_ids: Set[str],
        portal_id: str,
        portal_class: str,
        portal_name: str,
        method: str,
        source_file: str,
        extra_evidence: Optional[Dict[str, str]] = None,
        *,
        dedup_pairs: bool = False,
    ) -> int:
        """Connect spaces through their shared door/opening: space -> door -> space.

        Emits an ``egress_through`` edge from every involved space to the portal
        element (IfcDoor/opening), which becomes the shared middle node. Requires
        a real ``portal_id`` — there is no synthetic node, so a portal-less call
        does nothing.

        Returns the number of ``egress_through`` edges added. The connected space
        *pairs* are recorded in ``seen_edges`` so later heuristic passes (vertical
        connectors, apartment clusters) don't also link the same pair.

        ``dedup_pairs=True`` makes the call a no-op when all space pairs are already
        covered — used by heuristic passes (apartment cluster, vertical connector) so
        they don't duplicate what a real portal already established. Door and opening
        passes leave this False so multiple doors between the same rooms are each
        represented.
        """
        spaces = sorted({s for s in space_ids if s})
        if len(spaces) < 2 or not portal_id:
            return 0

        pairs = [tuple(sorted(p)) for p in combinations(spaces, 2)]
        if dedup_pairs and all(p in seen_edges for p in pairs):
            return 0
        for p in pairs:
            seen_edges.add(p)
            self._portal_space_pairs.append((p[0], p[1], method))

        evidence: Dict[str, str] = {
            "method": method,
            "portal_global_id": portal_id,
            "portal_class": portal_class,
            "portal_name": portal_name or portal_id,
            "source_file": source_file,
        }
        if extra_evidence:
            evidence.update(extra_evidence)

        added = 0
        for sid in spaces:
            self._relationships.append(Relationship(
                subject_global_id=sid,
                object_global_id=portal_id,
                relationship_family="circulation",
                relationship_type=EGRESS_THROUGH_TYPE,
                confidence=0.95,
                source_kind="topologic_ingest_EgressCirculation",
                evidence=evidence,
            ))
            added += 1

        return added

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
            graph = topograph.build_graph(ifc_path, tolerance=self.tolerance)
            if graph is None:
                return 0, 0

            portal_to_spaces: Dict[str, Set[str]] = defaultdict(set)
            for node in topograph.vertices(graph):
                meta = _vertex_meta(node)
                if not meta:
                    continue
                gid, ifc_class = meta
                if not _is_portal_class(ifc_class):
                    continue

                space_ids: Set[str] = set()
                for adj in topograph.adjacent(graph, node):
                    adj_meta = _vertex_meta(adj)
                    if adj_meta and SPACE_MARKER in adj_meta[1]:
                        space_ids.add(adj_meta[0])

                if len(space_ids) >= 2:
                    portal_to_spaces[gid].update(space_ids)
                    portal_elements[gid] = ifc_class

            for portal_id, space_ids in portal_to_spaces.items():
                portals_used += 1
                portal_elem = portal_elements.get(portal_id, "Portal")
                added += self._append_portal_link(
                    seen_edges,
                    space_ids,
                    portal_id, portal_elem, "",
                    "topologicpy_portal_graph",
                    ifc_path.name,
                )

        except Exception as exc:
            self.log.warning(
                "EgressCirculation: TopologicPy failed for %s: %s",
                ifc_path.name, exc,
            )
        return added, portals_used

    def _link_all_doors_to_spaces(
        self,
        portal_models: List[Tuple[Path, ifcopenshell.file]],
        space_models: List[Tuple[Path, ifcopenshell.file]],
        space_bboxes: Dict[str, Tuple[float, float, float, float, float, float]],
        space_points: Dict[str, Tuple[float, float, float]],
        space_sources: Dict[str, str],
        space_names: Dict[str, str],
        element_storey: Dict[str, str],
        storey_elevations: Dict[str, float],
        seen_edges: Set[Tuple[str, str]],
        portal_elements: Dict[str, str],
        methods_used: Set[str],
    ) -> Tuple[int, int]:
        """Link each door to the two spaces it separates on the same storey.

        Resolution order per door:
          1. Authoring-tool links (IfcRelSpaceBoundary / wall-hosted portal map)
          2. Plan-view point containment on both sides of the door
          3. Centroid proximity fallback (legacy)
        """
        portals_used = 0
        added = 0
        portal_maps: Dict[int, Dict[str, Set[str]]] = {}
        for ifc_path, ifc in portal_models:
            portal_maps[id(ifc)] = self._collect_portal_spaces(ifc)

        for ifc_path, ifc in portal_models:
            portal_to_spaces = portal_maps[id(ifc)]
            for door in _safe_by_type(ifc, "IfcDoor"):
                pair, method = _resolve_door_space_pair(
                    door,
                    portal_to_spaces.get(door.GlobalId, set()),
                    space_bboxes,
                    space_points,
                    space_names,
                    element_storey,
                    storey_elevations,
                    self.same_storey_only,
                    self.storey_z_tolerance,
                    self.door_side_offset,
                    self.door_plan_tolerance,
                    self.door_link_distance,
                    space_polys=getattr(self, "_space_polys", None),
                )
                if not pair:
                    continue

                s1, s2 = pair
                door_pt = _element_centroid(door) or space_points.get(s1)
                storey_key = _storey_group_key(
                    door.GlobalId,
                    door_pt,
                    getattr(door, "Name", None) or "",
                    element_storey,
                    storey_elevations,
                    self.storey_z_tolerance,
                )

                portals_used += 1
                portal_elements[door.GlobalId] = door.is_a()
                methods_used.add(method)
                evidence_source = ifc_path.name
                if len(self.ifc_files) > 1:
                    evidence_source = (
                        f"{ifc_path.name}|spaces="
                        f"{space_sources.get(s1, '?')},{space_sources.get(s2, '?')}"
                    )
                added += self._append_portal_link(
                    seen_edges,
                    {s1, s2},
                    door.GlobalId, door.is_a(),
                    getattr(door, "Name", None) or door.GlobalId,
                    method,
                    evidence_source,
                    extra_evidence={"storey_key": storey_key} if storey_key else None,
                )

        self.log.info(
            "EgressCirculation: door linking resolved %d/%d doors (%s)",
            portals_used,
            sum(len(_safe_by_type(ifc, "IfcDoor")) for _, ifc in portal_models),
            "+".join(sorted(methods_used)) or "none",
        )
        return added, portals_used

    def _link_openings_to_spaces(
        self,
        portal_models: List[Tuple[Path, ifcopenshell.file]],
        space_bboxes: Dict[str, Tuple[float, float, float, float, float, float]],
        space_points: Dict[str, Tuple[float, float, float]],
        space_sources: Dict[str, str],
        space_names: Dict[str, str],
        element_storey: Dict[str, str],
        storey_elevations: Dict[str, float],
        seen_edges: Set[Tuple[str, str]],
        portal_elements: Dict[str, str],
        methods_used: Set[str],
    ) -> Tuple[int, int]:
        """Link each DOOR-LESS, floor-reaching wall opening to the two spaces it separates.

        Mirrors :meth:`_link_all_doors_to_spaces` but for IfcOpeningElement/StandardCase that
        are genuine walkable passages: no door/window fill, bottom at the floor, passable
        width. Must run AFTER the door pass so a space pair already bridged by a door is in
        ``seen_edges`` and skipped; the opening node is emitted only when it actually creates
        a new edge (so door-bridged pairs don't spawn a redundant opening portal).
        """
        opening_method = "opening_side_containment"
        openings_seen = 0
        openings_used = 0
        added = 0

        target_bbox = None
        if self.diagnose_space:
            for gid, nm in space_names.items():
                if nm == self.diagnose_space and gid in space_bboxes:
                    target_bbox = space_bboxes[gid]
                    break
            self.log.info(
                "EgressCirculation[diagnose]: target space %r bbox=%s",
                self.diagnose_space, target_bbox,
            )

        for ifc_path, ifc in portal_models:
            openings = (
                _safe_by_type(ifc, "IfcOpeningElement")
                + _safe_by_type(ifc, "IfcOpeningStandardCase")
            )
            for opening in openings:
                if target_bbox is not None and _opening_near_bbox(opening, target_bbox):
                    self._diagnose_opening(
                        opening, space_bboxes, space_points, space_names,
                        element_storey, storey_elevations,
                    )
                if not self._opening_is_doorless_passage(opening):
                    continue
                if not _opening_reaches_floor(
                    opening, storey_elevations, self.storey_z_tolerance, self.max_sill_height
                ):
                    continue
                if not _opening_passable_size(opening, self.min_opening_width):
                    continue
                openings_seen += 1

                pair, method = _resolve_opening_space_pair(
                    opening,
                    space_bboxes,
                    space_points,
                    space_names,
                    element_storey,
                    storey_elevations,
                    self.same_storey_only,
                    self.storey_z_tolerance,
                    self.door_side_offset,
                    self.door_plan_tolerance,
                    space_polys=getattr(self, "_space_polys", None),
                )
                if not pair:
                    continue

                s1, s2 = pair
                op_pt = _element_centroid(opening) or space_points.get(s1)
                storey_key = _storey_group_key(
                    opening.GlobalId,
                    op_pt,
                    getattr(opening, "Name", None) or "",
                    element_storey,
                    storey_elevations,
                    self.storey_z_tolerance,
                )
                evidence_source = ifc_path.name
                if len(self.ifc_files) > 1:
                    evidence_source = (
                        f"{ifc_path.name}|spaces="
                        f"{space_sources.get(s1, '?')},{space_sources.get(s2, '?')}"
                    )

                new = self._append_portal_link(
                    seen_edges,
                    {s1, s2},
                    opening.GlobalId,
                    opening.is_a(),
                    getattr(opening, "Name", None) or opening.GlobalId,
                    method,
                    evidence_source,
                    extra_evidence={"storey_key": storey_key} if storey_key else None,
                )
                if new:
                    portal_elements[opening.GlobalId] = opening.is_a()
                    methods_used.add(opening_method)
                    openings_used += 1
                added += new

        self.log.info(
            "EgressCirculation: door-less opening linking — %d candidate passages, "
            "%d linked → %d edges",
            openings_seen, openings_used, added,
        )
        return added, openings_used

    def _diagnose_opening(
        self, opening, space_bboxes, space_points, space_names,
        element_storey, storey_elevations,
    ) -> None:
        """Verbose per-opening trace for ``diagnose_space``; never affects edges."""
        try:
            gid = opening.GlobalId
            has_door = self._opening_has_door(opening)
            has_window = self._opening_has_window(opening)
            ctr = _element_centroid(opening)
            dims = None
            min_z = None
            try:
                shape = ifcopenshell.geom.create_shape(_geom_settings(), opening)
                v = shape.geometry.verts
                if v:
                    xs, ys, zs = v[0::3], v[1::3], v[2::3]
                    dims = (
                        round(max(xs) - min(xs), 3),
                        round(max(ys) - min(ys), 3),
                        round(max(zs) - min(zs), 3),
                    )
                    min_z = min(zs)
            except Exception:
                pass
            reaches = _reaches_floor(
                min_z, storey_elevations, self.storey_z_tolerance, self.max_sill_height
            )
            passable = _opening_passable_size(opening, self.min_opening_width)
            axis_pairs = _opening_axis_pairs(opening, self.door_side_offset)
            plc_pts = _door_plan_side_points(opening, self.door_side_offset)
            # Pick first available candidate for diagnostic display (minor axis, else placement)
            used_pts = axis_pairs[0] if axis_pairs else plc_pts
            geo_pts = bool(axis_pairs)
            picks = None
            if used_pts:
                op_storey = _storey_group_key(
                    gid, ctr, getattr(opening, "Name", None) or "",
                    element_storey, storey_elevations, self.storey_z_tolerance,
                )
                picks = []
                for pt in used_pts:
                    s = _pick_space_at_plan_point(
                        pt, op_storey, space_bboxes, space_names, space_points,
                        element_storey, storey_elevations,
                        same_storey_only=self.same_storey_only,
                        z_tolerance=self.storey_z_tolerance,
                        plan_tolerance=self.door_plan_tolerance,
                        space_polys=getattr(self, "_space_polys", None),
                    )
                    picks.append((round(pt[0], 2), round(pt[1], 2), space_names.get(s) if s else None))
            pair, _method = _resolve_opening_space_pair(
                opening, space_bboxes, space_points, space_names, element_storey,
                storey_elevations, self.same_storey_only, self.storey_z_tolerance,
                self.door_side_offset, self.door_plan_tolerance,
                space_polys=getattr(self, "_space_polys", None),
            )
            self.log.info(
                "EgressCirculation[diagnose] opening=%s ctr=%s dims=%s door=%s window=%s "
                "reaches_floor=%s(min_z=%s) passable=%s axis=%s picks=%s -> pair=%s",
                gid,
                tuple(round(c, 2) for c in ctr) if ctr else None,
                dims, has_door, has_window, reaches,
                round(min_z, 3) if min_z is not None else None,
                passable,
                "geom" if geo_pts else ("placement" if plc_pts else "none"),
                picks, pair,
            )
        except Exception as exc:  # noqa: BLE001
            self.log.warning("EgressCirculation[diagnose] failed for an opening: %s", exc)

    # ------------------------------------------------------------------
    # navmesh clearance pass
    # ------------------------------------------------------------------

    def _storey_z_band(
        self,
        storey_key: Optional[str],
        storey_elevations: Dict[str, float],
        default_height: float = 3.5,
    ) -> Tuple[Optional[float], Optional[float]]:
        """Z range [floor, ceiling) for a storey, used to clip a wall to its level.

        Returns (None, None) when the storey elevation can't be resolved, so the
        footprint is taken from the full element height (fail-open).
        """
        if not storey_key:
            return (None, None)
        elev = storey_elevations.get(storey_key)
        if elev is None and isinstance(storey_key, str) and storey_key.startswith("elev:"):
            try:
                elev = float(storey_key.split(":", 1)[1])
            except ValueError:
                elev = None
        if elev is None:
            return (None, None)
        # next storey up bounds the ceiling; else default floor-to-floor
        higher = sorted(e for e in storey_elevations.values() if e > elev + 0.1)
        top = higher[0] if higher else elev + default_height
        return (elev - 0.2, top - 0.1)

    def _link_navmesh_passages(
        self,
        portal_models: List[Tuple[Path, ifcopenshell.file]],
        space_models: List[Tuple[Path, ifcopenshell.file]],
        space_bboxes: Dict[str, Tuple[float, float, float, float, float, float]],
        space_points: Dict[str, Tuple[float, float, float]],
        space_names: Dict[str, str],
        element_storey: Dict[str, str],
        storey_elevations: Dict[str, float],
        seen_edges: Set[Tuple[str, str]],
        methods_out: Set[str],
    ) -> Tuple[int, int]:
        """Link adjacent rooms wherever a human-sized box fits through the wall gap.

        For each adjacent same-storey space pair, build a local 2D walkable region
        (the two room footprints, buffered by ``navmesh_margin``, minus the nearby
        walls inflated by half the human width). If the two rooms land in the same
        connected piece, a ``human_width``-wide body fits between the walls — an
        egress passage that exists even with no IfcDoor/IfcOpeningElement modelled.
        Emits a direct ``egress_connects`` space↔space edge (method
        ``navmesh_clearance``, ``portal_class=IfcVirtualElement`` in evidence) for
        each pair no real portal already covers; optionally records the A* travel
        distance (TopologicPy ``NavigationGraph`` + ``ShortestPath``). A pair a
        door/opening already bridges is logged as a cross-check, not re-emitted.

        Returns ``(edges_added, passages_found)``.
        """
        if not _HAS_SHAPELY:
            self.log.warning(
                "EgressCirculation: shapely unavailable — skipping navmesh passages"
            )
            return 0, 0

        settings = _geom_settings()
        human_half = self.human_width / 2.0

        # 1. Space footprints + storey grouping (footprint cached per gid).
        space_fp: Dict[str, "Polygon"] = {}
        space_storey: Dict[str, str] = {}
        for _, ifc in space_models:
            for sp in _safe_by_type(ifc, "IfcSpace"):
                gid = sp.GlobalId
                if gid in space_fp or gid not in space_points:
                    continue
                fp = _element_footprint_xy(sp, settings)
                if fp is None:
                    continue
                sk = _storey_group_key(
                    gid, space_points.get(gid), space_names.get(gid, ""),
                    element_storey, storey_elevations, self.storey_z_tolerance,
                )
                if not sk:
                    continue
                space_fp[gid] = fp
                space_storey[gid] = sk

        # 2. Wall/column footprints grouped by storey; per-storey STRtree for lookup.
        walls_by_storey: Dict[str, List["Polygon"]] = defaultdict(list)
        wall_classes = ("IfcWall", "IfcWallStandardCase", "IfcWallElementedCase", "IfcColumn")
        seen_walls: Set[str] = set()
        for _, ifc in portal_models:
            elems: List = []
            for cls in wall_classes:
                elems.extend(_safe_by_type(ifc, cls))
            for w in elems:
                wgid = w.GlobalId
                if wgid in seen_walls:
                    continue
                seen_walls.add(wgid)
                ctr = _element_centroid(w)
                sk = _storey_group_key(
                    wgid, ctr, getattr(w, "Name", None) or "",
                    element_storey, storey_elevations, self.storey_z_tolerance,
                )
                z_lo, z_hi = self._storey_z_band(sk, storey_elevations)
                fp = _element_footprint_xy(w, settings, z_lo, z_hi)
                if fp is not None:
                    walls_by_storey[sk].append(fp)
        trees: Dict[str, "STRtree"] = {
            sk: STRtree(polys) for sk, polys in walls_by_storey.items() if polys
        }

        # 3. Candidate adjacent pairs, per storey (bbox-near gate keeps pairs local).
        by_storey: Dict[str, List[str]] = defaultdict(list)
        for gid, sk in space_storey.items():
            by_storey[sk].append(gid)

        evidence_source = portal_models[0][0].name if portal_models else "navmesh"
        added = 0
        passages = 0
        coincide = 0
        for sk, members in by_storey.items():
            tree = trees.get(sk)
            walls = walls_by_storey.get(sk, [])
            members.sort()
            for i in range(len(members)):
                a = members[i]
                bbox_a = space_bboxes.get(a)
                for j in range(i + 1, len(members)):
                    b = members[j]
                    if not _bbox_xy_near(bbox_a, space_bboxes.get(b), self.navmesh_margin):
                        continue
                    exists, gap_pt, path_len = _navmesh_passage_exists(
                        space_fp[a], space_fp[b],
                        space_points[a], space_points[b],
                        tree, walls, human_half, self.navmesh_margin,
                        compute_path=self.navmesh_compute_path,
                    )
                    if not exists:
                        continue
                    passages += 1
                    pair = tuple(sorted((a, b)))
                    # A door/opening (or earlier pass) already represents this pair —
                    # count it as an independent cross-check confirmation and skip the
                    # duplicate adjacency edge.
                    if pair in seen_edges:
                        coincide += 1
                        continue
                    extra: Dict[str, str] = {
                        "portal_class": "IfcVirtualElement",
                        "clearance_width_m": f"{self.human_width:.2f}",
                        "headroom_m": f"{self.human_height:.2f}",
                        "storey_key": sk,
                    }
                    if gap_pt is not None:
                        extra["gap_xy"] = f"{gap_pt[0]:.3f},{gap_pt[1]:.3f}"
                    if path_len is not None:
                        extra["path_length_m"] = f"{path_len:.3f}"
                    # Direct space<->space egress_connects edge (no synthetic portal node:
                    # the CDE projection only persists relationships between real IFC
                    # elements — same reason the apartment/vertical heuristics are direct).
                    if _append_edge(
                        self._relationships, seen_edges, a, b,
                        "", "IfcVirtualElement",
                        f"navmesh {space_names.get(a, a)}<->{space_names.get(b, b)}",
                        "navmesh_clearance", evidence_source,
                        extra_evidence=extra,
                    ):
                        added += 1
        if added:
            methods_out.add("navmesh_clearance")
        self.log.info(
            "EgressCirculation: navmesh passages — %d passable gaps "
            "(%d new egress_connects edges, %d coincide with a door/opening)",
            passages, added, coincide,
        )
        return added, passages

    def _link_vertical_connectors(
        self,
        portal_models: List[Tuple[Path, ifcopenshell.file]],
        space_models: List[Tuple[Path, ifcopenshell.file]],
        space_bboxes: Dict[str, Tuple[float, float, float, float, float, float]],
        space_points: Dict[str, Tuple[float, float, float]],
        space_names: Dict[str, str],
        element_storey: Dict[str, str],
        storey_elevations: Dict[str, float],
        seen_edges: Set[Tuple[str, str]],
        portal_elements: Dict[str, str],
        methods_out: Set[str],
    ) -> int:
        """Connect spaces across storeys for vertical circulation.

        Order of evidence (strongest first; later passes only fill what earlier ones miss
        via ``seen_edges``):
          1. Stair/elevator ELEMENTS — IfcStair/IfcStairFlight (and IfcTransportElement)
             geometry is the measure: the element spans two storeys, so the spaces its
             base/top footprints land in are linked two-hop through the real element node.
          2. Named stair/lift SPACES matched across consecutive storeys.
          3. Stacked shaft-like footprints (same XY size, different floor).
        """
        added = 0
        if self.link_stair_elements:
            elem_added = self._link_stair_element_connectors(
                portal_models, space_bboxes, space_points, space_names,
                element_storey, storey_elevations, seen_edges, portal_elements,
            )
            added += elem_added
            if elem_added:
                methods_out.add("stair_element")

        spaces_meta: Dict[str, dict] = {}
        long_names: Dict[str, str] = {}
        for ifc_path, ifc in space_models:
            for space in _safe_by_type(ifc, "IfcSpace"):
                gid = space.GlobalId
                bbox = space_bboxes.get(gid)
                pt = space_points.get(gid)
                if not bbox or not pt:
                    continue
                long_name = getattr(space, "LongName", None) or ""
                long_names[gid] = long_name
                storey_key = _storey_group_key(
                    gid, pt, space_names.get(gid, ""), element_storey, storey_elevations,
                    self.storey_z_tolerance,
                )
                if not storey_key:
                    continue
                spaces_meta[gid] = {
                    "gid": gid,
                    "name": space_names.get(gid, gid),
                    "long_name": long_name,
                    "source": ifc_path.name,
                    "storey_key": storey_key,
                    "storey_z": _storey_sort_key(storey_key, pt[2], storey_elevations),
                    "bbox": bbox,
                    "is_named_vc": _is_vertical_connector(long_name, self.vertical_keywords),
                    "footprint": _footprint_signature(bbox),
                }

        heuristic_added = 0
        heuristic_added += self._link_named_vertical_pairs(spaces_meta, seen_edges)
        heuristic_added += self._link_stacked_footprint_pairs(spaces_meta, long_names, seen_edges)
        if heuristic_added:
            methods_out.add("vertical_connector")
        added += heuristic_added
        if added:
            self.log.info("EgressCirculation: %d vertical connector edges", added)
        return added

    def _link_stair_element_connectors(
        self,
        portal_models: List[Tuple[Path, ifcopenshell.file]],
        space_bboxes: Dict[str, Tuple[float, float, float, float, float, float]],
        space_points: Dict[str, Tuple[float, float, float]],
        space_names: Dict[str, str],
        element_storey: Dict[str, str],
        storey_elevations: Dict[str, float],
        seen_edges: Set[Tuple[str, str]],
        portal_elements: Dict[str, str],
    ) -> int:
        """Vertical links measured from stair/elevator ELEMENT geometry.

        A stair element spans two storeys; the spaces its base and top footprints land in
        (and the spaces enclosing it on each storey) are connected two-hop through the
        stair node (``space -> IfcStair -> space``). The stair/lift is a real IFC element,
        so the node persists and is selectable like a door. Elevators
        (``IfcTransportElement``) use the same model across every storey they span; when a
        lift lacks usable swept geometry its placement point + the full storey range are
        used instead (``transport_element_namefallback``).
        """
        if not storey_elevations or not space_bboxes:
            return 0
        settings = _geom_settings()
        evidence_source = portal_models[0][0].name if portal_models else "stair_element"
        z_tol = self.storey_z_tolerance
        plan_tol = self.door_plan_tolerance
        # Model coordinate envelope (from spaces) + margin. Stair/lift assembly shapes can
        # carry junk verts (origin (0,0,0), 1e+62 blow-ups from broken child reps); anything
        # outside this envelope is discarded so it can't corrupt the bbox/footprint.
        bx = [b for b in space_bboxes.values()]
        m = 5.0
        env = (
            min(b[0] for b in bx) - m, min(b[1] for b in bx) - m, min(b[2] for b in bx) - m,
            max(b[3] for b in bx) + m, max(b[4] for b in bx) + m, max(b[5] for b in bx) + m,
        )

        def verts_of(elem):
            return [
                v for v in _assembly_world_verts(elem, settings)
                if env[0] <= v[0] <= env[3] and env[1] <= v[1] <= env[4] and env[2] <= v[2] <= env[5]
            ]

        added = 0
        stairs_linked = 0
        lifts_linked = 0
        seen_elems: Set[str] = set()

        def pick(xy, storey_key):
            if xy is None or storey_key is None:
                return None
            return _pick_space_at_plan_point(
                xy, storey_key, space_bboxes, space_names, space_points,
                element_storey, storey_elevations,
                same_storey_only=True, z_tolerance=z_tol, plan_tolerance=plan_tol,
            )

        def emit(space_set, elem, method, extra):
            spaces = sorted({s for s in space_set if s})
            if len(spaces) < 2:
                return 0
            n = self._append_portal_link(
                seen_edges, set(spaces), elem.GlobalId, elem.is_a(),
                getattr(elem, "Name", None) or elem.GlobalId,
                method, evidence_source, extra_evidence=extra,
            )
            if n:
                portal_elements[elem.GlobalId] = elem.is_a()
            return n

        for _, ifc in portal_models:
            # --- stairs: IfcStair (and standalone IfcStairFlight not under an IfcStair) ---
            stair_elems = list(_safe_by_type(ifc, "IfcStair"))
            for flight in _safe_by_type(ifc, "IfcStairFlight"):
                if not _has_stair_parent(flight):
                    stair_elems.append(flight)
            for stair in stair_elems:
                if stair.GlobalId in seen_elems:
                    continue
                seen_elems.add(stair.GlobalId)
                verts = verts_of(stair)
                bbox = _verts_bbox(verts)
                if bbox is None:
                    continue
                zmin, zmax = bbox[2], bbox[5]
                if zmax - zmin < self.min_stair_rise:
                    continue  # too flat / a half-flight that stays on one level
                lower_key = _storey_key_for_z(zmin, storey_elevations, z_tol)
                upper_key = _storey_key_for_z(zmax, storey_elevations, z_tol)
                if not lower_key or not upper_key or lower_key == upper_key:
                    continue
                band = max(0.5, 0.2 * (zmax - zmin))
                base_xy = _verts_zband_centroid(verts, zmin, zmin + band)
                top_xy = _verts_zband_centroid(verts, zmax - band, zmax)
                ctr_xy = ((bbox[0] + bbox[3]) / 2.0, (bbox[1] + bbox[4]) / 2.0)
                # landing spaces (where you step off) + enclosing stairwell spaces
                space_set = {
                    pick(base_xy, lower_key), pick(top_xy, upper_key),
                    pick(ctr_xy, lower_key), pick(ctr_xy, upper_key),
                }
                n = emit(
                    space_set, stair, "stair_element",
                    {"stair_global_id": stair.GlobalId, "storey_from": lower_key,
                     "storey_to": upper_key, "rise_m": f"{zmax - zmin:.2f}"},
                )
                if n:
                    added += n
                    stairs_linked += 1

            # --- elevators: IfcTransportElement across every storey they span ---
            for lift in _safe_by_type(ifc, "IfcTransportElement"):
                if lift.GlobalId in seen_elems:
                    continue
                seen_elems.add(lift.GlobalId)
                bbox = _verts_bbox(verts_of(lift))
                ctr = _element_centroid(lift)
                if bbox is not None:
                    xy = ((bbox[0] + bbox[3]) / 2.0, (bbox[1] + bbox[4]) / 2.0)
                else:
                    xy = (ctr[0], ctr[1]) if ctr else None
                if xy is None:
                    continue
                if bbox is not None and (bbox[5] - bbox[2]) >= self.min_stair_rise:
                    z_lo, z_hi = bbox[2], bbox[5]
                    method = "transport_element"
                else:
                    # no usable swept shaft — assume it serves the whole stack at this XY
                    z_lo, z_hi = min(storey_elevations.values()), max(storey_elevations.values())
                    method = "transport_element_namefallback"
                served_keys = [
                    f"elev:{round(elev, 2)}"
                    for _, elev in sorted(storey_elevations.items(), key=lambda kv: kv[1])
                    if z_lo - z_tol <= elev <= z_hi + z_tol
                ]
                space_set = {pick(xy, key) for key in served_keys}
                n = emit(
                    space_set, lift, method,
                    {"transport_global_id": lift.GlobalId,
                     "predefined_type": getattr(lift, "PredefinedType", None) or "",
                     "served_storeys": str(len(served_keys))},
                )
                if n:
                    added += n
                    lifts_linked += 1

        if added:
            self.log.info(
                "EgressCirculation: stair/elevator element connectors — "
                "%d stairs, %d elevators → %d edges",
                stairs_linked, lifts_linked, added,
            )
        return added

    def _link_named_vertical_pairs(
        self,
        spaces_meta: Dict[str, dict],
        seen_edges: Set[Tuple[str, str]],
    ) -> int:
        vc_by_name: Dict[str, List[dict]] = defaultdict(list)
        for sp in spaces_meta.values():
            if sp["is_named_vc"]:
                vc_by_name[_normalise_vc_name(sp["long_name"])].append(sp)

        added = 0
        for instances in vc_by_name.values():
            if len(instances) < 2:
                continue
            instances_sorted = sorted(instances, key=lambda sp: sp["storey_z"])
            for idx in range(len(instances_sorted) - 1):
                sp1, sp2 = instances_sorted[idx], instances_sorted[idx + 1]
                if _append_edge(
                    self._relationships,
                    seen_edges,
                    sp1["gid"],
                    sp2["gid"],
                    "",
                    "IfcVerticalConnector",
                    sp1["long_name"] or sp1["name"],
                    "named_stair_lift_match",
                    sp1["source"],
                    extra_evidence={
                        "connector_name": _normalise_vc_name(sp1["long_name"]),
                        "storey_from": sp1["storey_key"],
                        "storey_to": sp2["storey_key"],
                    },
                ):
                    added += 1
        return added

    def _link_stacked_footprint_pairs(
        self,
        spaces_meta: Dict[str, dict],
        long_names: Dict[str, str],
        seen_edges: Set[Tuple[str, str]],
    ) -> int:
        """Match shaft-like spaces stacked in Z with similar plan footprints."""
        by_print: Dict[Tuple[float, float, float, float], List[dict]] = defaultdict(list)
        for sp in spaces_meta.values():
            ln = (long_names.get(sp["gid"]) or "").upper()
            area = _bbox_xy_area(sp["bbox"])
            if sp["is_named_vc"]:
                by_print[sp["footprint"]].append(sp)
            elif area <= 25.0 and any(k in ln for k in ("SCHAKT", "SHAFT", "HISS", "TRAPP")):
                by_print[sp["footprint"]].append(sp)

        added = 0
        for group in by_print.values():
            if len(group) < 2:
                continue
            group_sorted = sorted(group, key=lambda sp: sp["storey_z"])
            for idx in range(len(group_sorted) - 1):
                sp1, sp2 = group_sorted[idx], group_sorted[idx + 1]
                if sp1["storey_key"] == sp2["storey_key"]:
                    continue
                if _append_edge(
                    self._relationships,
                    seen_edges,
                    sp1["gid"],
                    sp2["gid"],
                    "",
                    "IfcVerticalConnector",
                    sp1["name"],
                    "stacked_footprint_match",
                    sp1["source"],
                    extra_evidence={
                        "footprint": sp1["footprint"],
                        "storey_from": sp1["storey_key"],
                        "storey_to": sp2["storey_key"],
                    },
                ):
                    added += 1
        return added

    def _link_apartment_room_clusters(
        self,
        models: List[Tuple[Path, ifcopenshell.file]],
        space_points: Dict[str, Tuple[float, float, float]],
        space_names: Dict[str, str],
        element_storey: Dict[str, str],
        storey_elevations: Dict[str, float],
        seen_edges: Set[Tuple[str, str]],
    ) -> int:
        """Connect room-level spaces within the same BIP apartment on the same storey."""
        import ifcopenshell.util.element as ifc_element_util

        long_names: Dict[str, str] = {}
        korridor_ids: Set[str] = set()
        for _, ifc in models:
            for space in _safe_by_type(ifc, "IfcSpace"):
                gid = space.GlobalId
                ln = getattr(space, "LongName", None) or ""
                long_names[gid] = ln
                if _is_korridor_space(space.Name or "", ln):
                    korridor_ids.add(gid)

        egress_adj = _build_space_adjacency(self._relationships)
        clusters: Dict[Tuple[str, str], List[str]] = defaultdict(list)
        for _, ifc in models:
            for space in _safe_by_type(ifc, "IfcSpace"):
                gid = space.GlobalId
                name = space.Name or ""
                if not _is_egress_room_space(name):
                    continue
                if gid not in space_points:
                    continue
                psets = ifc_element_util.get_psets(space, psets_only=True)
                apt = (psets.get("BIP") or {}).get("Appartment") or (psets.get("BIP") or {}).get("Apartment")
                if not apt:
                    continue
                pt = space_points[gid]
                storey_key = _storey_group_key(
                    gid, pt, name, element_storey, storey_elevations, self.storey_z_tolerance,
                )
                if not storey_key:
                    continue
                clusters[(str(apt).strip(), storey_key)].append(gid)

        added = 0
        for (apt_id, storey_key), members in clusters.items():
            if len(members) < 2:
                continue
            hub, hub_reason = _pick_apartment_cluster_hub(
                members, egress_adj, korridor_ids, long_names, space_names,
            )

            for gid in members:
                if gid == hub:
                    continue
                if _append_edge(
                    self._relationships,
                    seen_edges,
                    gid,
                    hub,
                    "",
                    "IfcApartmentCluster",
                    apt_id,
                    "apartment_room_cluster",
                    "BIP",
                    extra_evidence={
                        "apartment_id": apt_id,
                        "storey_key": storey_key,
                        "hub_space": space_names.get(hub, hub),
                        "hub_reason": hub_reason,
                    },
                ):
                    added += 1
        if added:
            self.log.info(
                "EgressCirculation: %d apartment room cluster edges", added,
            )
        return added

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

            # Group the portal's spaces by storey so a single portal only links
            # rooms that share a level, then route each group through one proxy.
            if self.same_storey_only:
                groups: Dict[str, Set[str]] = defaultdict(set)
                for sid in space_ids:
                    key = _storey_group_key(
                        sid, space_points.get(sid), space_names.get(sid, ""),
                        element_storey, storey_elevations, self.storey_z_tolerance,
                    )
                    groups[key or "_unknown"].add(sid)
            else:
                groups = {"_all": set(space_ids)}

            for grp in groups.values():
                self._append_portal_link(
                    seen_edges,
                    grp,
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

    @staticmethod
    def _opening_has_window(opening) -> bool:
        for rel in getattr(opening, "HasFillings", None) or []:
            fill = rel.RelatedBuildingElement
            if fill and fill.is_a() in {"IfcWindow", "IfcWindowStandardCase"}:
                return True
        return False

    @classmethod
    def _opening_is_doorless_passage(cls, opening) -> bool:
        """True for a wall void that is neither door- nor window-filled.

        Door-filled voids are already represented by the door pass; window-filled voids
        aren't egress. Geometry (floor-reaching + passable size, checked separately) catches
        the rest, including windows not linked to their opening via IfcRelFillsElement.
        """
        return not cls._opening_has_door(opening) and not cls._opening_has_window(opening)

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


def _storey_key_for_z(
    z: float,
    storey_elevations: Dict[str, float],
    tolerance: float,
) -> Optional[str]:
    """``elev:X`` storey key for the floor nearest ``z`` — matches the keys spaces get
    from :func:`_storey_group_key` (IFC containment → ``elev:<elevation>``), so a stair's
    base/top Z can be resolved to the same storey grouping the spaces use."""
    sid = _infer_storey_from_z(z, storey_elevations, tolerance)
    if sid is None:
        return None
    return f"elev:{round(storey_elevations[sid], 2)}"


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


def _vertex_meta(node) -> Optional[Tuple[str, str]]:
    """Extract (gid, ifc_class) from a topograph.Node (normalized accessors)."""
    gid = node.gid
    if not gid:
        return None
    return gid, (node.ifc_type or "")


def _is_portal_class(ifc_class: str) -> bool:
    if not ifc_class:
        return False
    return any(portal in ifc_class for portal in PORTAL_TYPES)


def _collect_space_bboxes(
    models: List[Tuple[Path, ifcopenshell.file]],
) -> Dict[str, Tuple[float, float, float, float, float, float]]:
    """World-coordinate axis-aligned bboxes for IfcSpace (metres, USE_WORLD_COORDS)."""
    bboxes: Dict[str, Tuple[float, float, float, float, float, float]] = {}
    settings = _geom_settings()
    for _, ifc in models:
        for space in _safe_by_type(ifc, "IfcSpace"):
            gid = space.GlobalId
            if gid in bboxes:
                continue
            try:
                shape = ifcopenshell.geom.create_shape(settings, space)
                verts = shape.geometry.verts
                if not verts:
                    continue
                xs = verts[0::3]
                ys = verts[1::3]
                zs = verts[2::3]
                bboxes[gid] = (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))
            except Exception:
                pt = _element_centroid(space)
                if pt:
                    bboxes[gid] = (pt[0] - 0.5, pt[1] - 0.5, pt[2] - 0.5, pt[0] + 0.5, pt[1] + 0.5, pt[2] + 0.5)
    return bboxes


def _bbox_xy_area(bbox: Tuple[float, float, float, float, float, float]) -> float:
    return max(0.0, bbox[3] - bbox[0]) * max(0.0, bbox[4] - bbox[1])


def _footprint_signature(
    bbox: Tuple[float, float, float, float, float, float],
    *,
    centre_tol: float = 0.35,
) -> Tuple[float, float, float, float]:
    cx = (bbox[0] + bbox[3]) / 2
    cy = (bbox[1] + bbox[4]) / 2
    w = bbox[3] - bbox[0]
    h = bbox[4] - bbox[1]
    return (
        round(cx / centre_tol) * centre_tol,
        round(cy / centre_tol) * centre_tol,
        round(w, 1),
        round(h, 1),
    )


def _storey_sort_key(
    storey_key: str,
    fallback_z: float,
    storey_elevations: Dict[str, float],
) -> float:
    if storey_key.startswith("elev:"):
        try:
            return float(storey_key[5:])
        except ValueError:
            pass
    if storey_key.startswith("z:"):
        try:
            return float(storey_key[2:])
        except ValueError:
            pass
    if storey_key.startswith("prefix:"):
        try:
            return float(storey_key[7:])
        except ValueError:
            pass
    return fallback_z


def _door_plan_side_points(
    door,
    offset_m: float,
) -> Optional[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """Two plan-view sample points on opposite sides of a door opening."""
    import ifcopenshell.util.placement as pu
    import math

    centre = _element_centroid(door)
    if centre is None:
        return None
    try:
        matrix = pu.get_local_placement(door.ObjectPlacement)
    except Exception:
        return None

    dx, dy = matrix[0][1], matrix[1][1]
    if math.hypot(dx, dy) < 0.5:
        dx, dy = matrix[0][0], matrix[1][0]
    norm = math.hypot(dx, dy) or 1.0
    dx, dy = dx / norm, dy / norm
    cx, cy = centre[0], centre[1]
    return (
        (cx + dx * offset_m, cy + dy * offset_m),
        (cx - dx * offset_m, cy - dy * offset_m),
    )


def _minor_axis_2d(
    a: float, b: float, c: float
) -> Tuple[Optional[float], Optional[float]]:
    """Unit eigenvector of the *smaller* eigenvalue of [[a, b], [b, c]].

    For an opening's horizontal footprint covariance this is the thin (through-wall)
    direction. Returns ``(None, None)`` when the footprint is too isotropic to define a
    thin axis (square-ish void), so the caller can fall back.
    """
    import math

    tr = a + c
    disc = math.sqrt(((a - c) / 2.0) ** 2 + b * b)
    lmin = tr / 2.0 - disc
    lmax = tr / 2.0 + disc
    if lmax <= 1e-9 or (lmax - lmin) <= 1e-3 * lmax:
        return None, None  # not elongated -> no reliable through-direction
    if abs(b) > 1e-12:
        vx, vy = b, (lmin - a)
    elif a <= c:
        vx, vy = 1.0, 0.0  # smaller variance along x -> thin axis is x
    else:
        vx, vy = 0.0, 1.0
    norm = math.hypot(vx, vy) or 1.0
    return vx / norm, vy / norm


def _opening_axis_pairs(
    opening,
    offset_m: float,
) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """Side-point pairs across the opening's two horizontal footprint axes, minor first.

    The through-wall (egress) direction is *not* always the thin axis: a skinny doorway is
    thin across the wall (minor axis), but a void modelled deep across the room boundary is
    long across it (major axis). So return both candidate pairs — minor (thin) first, then
    major (perpendicular) — and let the caller accept whichever straddles two rooms. Empty
    when geometry is unavailable or the footprint is too isotropic to orient.
    """
    try:
        shape = ifcopenshell.geom.create_shape(_geom_settings(), opening)
        verts = shape.geometry.verts
        if not verts:
            return []
        xs = list(verts[0::3])
        ys = list(verts[1::3])
    except Exception:
        return []
    n = len(xs)
    if n < 3:
        return []
    cx = sum(xs) / n
    cy = sum(ys) / n
    sxx = sum((x - cx) ** 2 for x in xs) / n
    syy = sum((y - cy) ** 2 for y in ys) / n
    sxy = sum((xs[i] - cx) * (ys[i] - cy) for i in range(n)) / n
    nx, ny = _minor_axis_2d(sxx, sxy, syy)
    if nx is None:
        return []
    pairs: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
    for ax, ay in ((nx, ny), (-ny, nx)):  # minor (thin) axis first, then major (perpendicular)
        pairs.append(
            (
                (cx + ax * offset_m, cy + ay * offset_m),
                (cx - ax * offset_m, cy - ay * offset_m),
            )
        )
    return pairs


def _pick_space_at_plan_point(
    point_xy: Tuple[float, float],
    door_storey_key: Optional[str],
    space_bboxes: Dict[str, Tuple[float, float, float, float, float, float]],
    space_names: Dict[str, str],
    space_points: Dict[str, Tuple[float, float, float]],
    element_storey: Dict[str, str],
    storey_elevations: Dict[str, float],
    *,
    same_storey_only: bool,
    z_tolerance: float,
    plan_tolerance: float,
    space_polys: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Space whose footprint contains ``point_xy`` on the door's storey.

    Candidates are gathered by axis-aligned bbox first. When ``space_polys`` (tight
    footprints) is supplied, the candidates are narrowed to those whose *real* footprint
    contains the point — so a neighbour room whose bbox merely overhangs the point (an
    L-shaped/offset room straddling a corridor edge) no longer wins. Smallest footprint
    breaks any remaining tie (picks the specific room over an enclosing aggregate).
    """
    px, py = point_xy
    hits: List[str] = []
    for gid, bbox in space_bboxes.items():
        if not (
            bbox[0] - plan_tolerance <= px <= bbox[3] + plan_tolerance
            and bbox[1] - plan_tolerance <= py <= bbox[4] + plan_tolerance
        ):
            continue
        pt = space_points.get(gid)
        storey_key = _storey_group_key(
            gid,
            pt,
            space_names.get(gid, ""),
            element_storey,
            storey_elevations,
            z_tolerance,
        )
        if same_storey_only and door_storey_key and storey_key != door_storey_key:
            continue
        hits.append(gid)
    if not hits:
        return None
    if space_polys and _HAS_SHAPELY:
        point = Point(px, py)
        poly_hits = [
            gid for gid in hits
            if space_polys.get(gid) is not None
            and space_polys[gid].buffer(plan_tolerance).contains(point)
        ]
        if poly_hits:
            hits = poly_hits
    return min(hits, key=lambda gid: _bbox_xy_area(space_bboxes[gid]))


def _filter_spaces_same_storey(
    space_ids: Set[str],
    door_storey_key: Optional[str],
    space_points: Dict[str, Tuple[float, float, float]],
    space_names: Dict[str, str],
    element_storey: Dict[str, str],
    storey_elevations: Dict[str, float],
    z_tolerance: float,
) -> List[str]:
    kept: List[str] = []
    for gid in space_ids:
        pt = space_points.get(gid)
        storey_key = _storey_group_key(
            gid, pt, space_names.get(gid, ""), element_storey, storey_elevations, z_tolerance,
        )
        if door_storey_key and storey_key != door_storey_key:
            continue
        kept.append(gid)
    return kept


def _door_space_proximity_fallback(
    door,
    space_points: Dict[str, Tuple[float, float, float]],
    space_names: Dict[str, str],
    element_storey: Dict[str, str],
    storey_elevations: Dict[str, float],
    *,
    same_storey_only: bool,
    z_tolerance: float,
    max_dist: float,
) -> Optional[Tuple[str, str]]:
    door_pt = _element_centroid(door)
    if door_pt is None:
        return None
    door_storey = _storey_group_key(
        door.GlobalId,
        door_pt,
        getattr(door, "Name", None) or "",
        element_storey,
        storey_elevations,
        z_tolerance,
    )
    candidates: List[Tuple[str, float]] = []
    for sid, pt in space_points.items():
        if not _is_egress_room_space(space_names.get(sid, "")):
            continue
        dist = _planar_dist(door_pt[0], door_pt[1], pt[0], pt[1])
        if dist > max_dist:
            continue
        storey_key = _storey_group_key(
            sid, pt, space_names.get(sid, ""), element_storey, storey_elevations, z_tolerance,
        )
        if same_storey_only and (not door_storey or not storey_key or storey_key != door_storey):
            continue
        candidates.append((sid, dist))
    if len(candidates) < 2:
        return None
    candidates.sort(key=lambda item: item[1])
    return candidates[0][0], candidates[1][0]


def _resolve_door_space_pair(
    door,
    portal_space_ids: Set[str],
    space_bboxes: Dict[str, Tuple[float, float, float, float, float, float]],
    space_points: Dict[str, Tuple[float, float, float]],
    space_names: Dict[str, str],
    element_storey: Dict[str, str],
    storey_elevations: Dict[str, float],
    same_storey_only: bool,
    z_tolerance: float,
    door_side_offset: float,
    plan_tolerance: float,
    link_distance: float,
    space_polys: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Tuple[str, str]], str]:
    door_pt = _element_centroid(door)
    door_storey = _storey_group_key(
        door.GlobalId,
        door_pt,
        getattr(door, "Name", None) or "",
        element_storey,
        storey_elevations,
        z_tolerance,
    )

    if portal_space_ids:
        ifc_spaces = _filter_spaces_same_storey(
            portal_space_ids, door_storey, space_points, space_names,
            element_storey, storey_elevations, z_tolerance,
        )
        if len(ifc_spaces) >= 2:
            side_pts = _door_plan_side_points(door, door_side_offset)
            if side_pts and len(ifc_spaces) > 2:
                allowed = set(ifc_spaces)
                s1 = _pick_space_at_plan_point(
                    side_pts[0], door_storey,
                    {g: space_bboxes[g] for g in allowed if g in space_bboxes},
                    space_names, space_points, element_storey, storey_elevations,
                    same_storey_only=same_storey_only, z_tolerance=z_tolerance,
                    plan_tolerance=plan_tolerance, space_polys=space_polys,
                )
                s2 = _pick_space_at_plan_point(
                    side_pts[1], door_storey,
                    {g: space_bboxes[g] for g in allowed if g in space_bboxes},
                    space_names, space_points, element_storey, storey_elevations,
                    same_storey_only=same_storey_only, z_tolerance=z_tolerance,
                    plan_tolerance=plan_tolerance, space_polys=space_polys,
                )
                if s1 and s2 and s1 != s2:
                    return tuple(sorted((s1, s2))), "ifc_portal_boundary"
            return tuple(sorted((ifc_spaces[0], ifc_spaces[1]))), "ifc_portal_boundary"

    side_pts = _door_plan_side_points(door, door_side_offset)
    if side_pts:
        s1 = _pick_space_at_plan_point(
            side_pts[0], door_storey, space_bboxes, space_names, space_points,
            element_storey, storey_elevations,
            same_storey_only=same_storey_only, z_tolerance=z_tolerance,
            plan_tolerance=plan_tolerance, space_polys=space_polys,
        )
        s2 = _pick_space_at_plan_point(
            side_pts[1], door_storey, space_bboxes, space_names, space_points,
            element_storey, storey_elevations,
            same_storey_only=same_storey_only, z_tolerance=z_tolerance,
            plan_tolerance=plan_tolerance, space_polys=space_polys,
        )
        if s1 and s2 and s1 != s2:
            return tuple(sorted((s1, s2))), "door_side_containment"

    max_dist = max(float(link_distance), 0.5)
    fallback = _door_space_proximity_fallback(
        door, space_points, space_names, element_storey, storey_elevations,
        same_storey_only=same_storey_only, z_tolerance=z_tolerance, max_dist=max_dist,
    )
    if fallback:
        return tuple(sorted(fallback)), "door_space_proximity_fallback"
    return None, ""


def _resolve_opening_space_pair(
    opening,
    space_bboxes: Dict[str, Tuple[float, float, float, float, float, float]],
    space_points: Dict[str, Tuple[float, float, float]],
    space_names: Dict[str, str],
    element_storey: Dict[str, str],
    storey_elevations: Dict[str, float],
    same_storey_only: bool,
    z_tolerance: float,
    side_offset: float,
    plan_tolerance: float,
    space_polys: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Tuple[str, str]], str]:
    """Door-less opening → the two spaces it separates, via plan-view side points only.

    Mirrors the side-point branch of :func:`_resolve_door_space_pair` but deliberately omits
    the IfcRelSpaceBoundary branch and the centroid-proximity fallback — openings are far more
    numerous/noisier than doors, so a pair is accepted only when both points (offset across
    the opening) land inside real space footprints. Exterior openings (one side outdoors)
    find no second space and drop out.
    """
    op_pt = _element_centroid(opening)
    op_storey = _storey_group_key(
        opening.GlobalId,
        op_pt,
        getattr(opening, "Name", None) or "",
        element_storey,
        storey_elevations,
        z_tolerance,
    )
    # Try footprint axes (minor first, then major) and placement-axis fallback.
    # Both PCA axes are tried because the through-direction for a wide opening may be the major
    # axis (e.g. a 0.91×3.05 m void where X is the wall thickness, Y the opening width).
    candidates = _opening_axis_pairs(opening, side_offset)
    placement = _door_plan_side_points(opening, side_offset)
    if placement:
        candidates.append(placement)
    for side_pts in candidates:
        s1 = _pick_space_at_plan_point(
            side_pts[0], op_storey, space_bboxes, space_names, space_points,
            element_storey, storey_elevations,
            same_storey_only=same_storey_only, z_tolerance=z_tolerance,
            plan_tolerance=plan_tolerance, space_polys=space_polys,
        )
        s2 = _pick_space_at_plan_point(
            side_pts[1], op_storey, space_bboxes, space_names, space_points,
            element_storey, storey_elevations,
            same_storey_only=same_storey_only, z_tolerance=z_tolerance,
            plan_tolerance=plan_tolerance, space_polys=space_polys,
        )
        if s1 and s2 and s1 != s2:
            return tuple(sorted((s1, s2))), "opening_side_containment"
    return None, ""


def _reaches_floor(
    min_z: Optional[float],
    storey_elevations: Dict[str, float],
    z_tolerance: float,
    max_sill_height: float,
) -> bool:
    """Pure floor-reaching test: opening bottom is within ``max_sill_height`` of its storey.

    The opening's storey is the nearest elevation to its bottom. A door/passage bottom sits at
    the floor (≈0 above); a window's raised sill (~0.9 m) exceeds ``max_sill_height``. Fails
    *open* (True) when the bottom or storey set is unknown so a real passage is never dropped
    on a geometry/storey error. ``z_tolerance`` is accepted for signature stability.
    """
    _ = z_tolerance
    if min_z is None or not storey_elevations:
        return True
    nearest = min(storey_elevations.values(), key=lambda e: abs(min_z - e))
    return (min_z - nearest) <= max_sill_height


def _opening_reaches_floor(
    opening,
    storey_elevations: Dict[str, float],
    z_tolerance: float,
    max_sill_height: float,
) -> bool:
    """Geometry wrapper around :func:`_reaches_floor` for an opening element."""
    try:
        shape = ifcopenshell.geom.create_shape(_geom_settings(), opening)
        verts = shape.geometry.verts
        if not verts:
            return True
        min_z = min(verts[2::3])
    except Exception:
        return True
    return _reaches_floor(min_z, storey_elevations, z_tolerance, max_sill_height)


def _opening_passable_size(opening, min_width_m: float) -> bool:
    """True when a door-less opening is big enough to walk through.

    World-bbox plan extent and height must both be ≥ ``min_width_m``. Fails *open* (True) on
    missing/failed geometry so a real opening is never dropped on a geometry error.
    """
    try:
        shape = ifcopenshell.geom.create_shape(_geom_settings(), opening)
        verts = shape.geometry.verts
        if not verts:
            return True
        xs, ys, zs = verts[0::3], verts[1::3], verts[2::3]
        plan_extent = max(max(xs) - min(xs), max(ys) - min(ys))
        height = max(zs) - min(zs)
        return plan_extent >= min_width_m and height >= min_width_m
    except Exception:
        return True


def _opening_near_bbox(opening, bbox, margin: float = 0.8) -> bool:
    """True when the opening's centroid XY falls within ``bbox`` expanded by ``margin`` (m)."""
    ctr = _element_centroid(opening)
    if ctr is None:
        return False
    x, y = ctr[0], ctr[1]
    return (bbox[0] - margin <= x <= bbox[3] + margin) and (bbox[1] - margin <= y <= bbox[4] + margin)


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


def _is_egress_room_space(name: str) -> bool:
    """True for room-level IfcSpace names; excludes apartment aggregate anchors."""
    text = (name or "").strip()
    if not text:
        return True
    return _APARTMENT_AGGREGATE_RE.match(text) is None


def _is_korridor_space(name: str, long_name: str) -> bool:
    haystack = f"{name} {long_name}".upper()
    return "KORRIDOR" in haystack


def _build_space_adjacency(
    relationships: List,
) -> Dict[str, Set[str]]:
    """Undirected adjacency from existing egress_connects relationship dicts."""
    adj: Dict[str, Set[str]] = defaultdict(set)
    for rel in relationships:
        payload = rel if isinstance(rel, dict) else rel.to_dict()
        s1 = payload.get("subject_global_id")
        s2 = payload.get("object_global_id")
        if not s1 or not s2:
            continue
        adj[s1].add(s2)
        adj[s2].add(s1)
    return adj


def _pick_apartment_cluster_hub(
    members: List[str],
    adj: Dict[str, Set[str]],
    korridor_ids: Set[str],
    long_names: Dict[str, str],
    space_names: Dict[str, str],
) -> Tuple[str, str]:
    """Pick the flat's circulation hub — prefer the room that opens to korridor.

    Priority:
      1. Room door-linked to a korridor that already has ≥2 egress neighbours
         (typical through-corridor segment).
      2. Any room adjacent to korridor in the current egress graph.
      3. LongName contains "hall".
      4. Shortest space name.
    """
    for gid in members:
        for neighbour in adj.get(gid, set()):
            if neighbour not in korridor_ids:
                continue
            if len(adj.get(neighbour, set())) >= 2:
                return gid, "korridor_entry"

    for gid in members:
        if any(nb in korridor_ids for nb in adj.get(gid, set())):
            return gid, "korridor_adjacent"

    for gid in members:
        if "hall" in (long_names.get(gid) or "").lower():
            return gid, "hall_name"

    return min(members, key=lambda g: space_names.get(g, g)), "shortest_name"


def _approx_ifc_entity_count(ifc) -> int:
    """Approximate entity count without scanning the full schema."""
    return ifc_thin_spaces.approx_entity_count(ifc)


# ---------------------------------------------------------------------------
# Navmesh / clearance pathfinding helpers (link_navmesh_passages)
# ---------------------------------------------------------------------------

try:
    from shapely.geometry import MultiPoint, Point, Polygon  # noqa: F401
    from shapely.ops import nearest_points, unary_union
    from shapely.strtree import STRtree

    _HAS_SHAPELY = True
except ImportError:  # pragma: no cover - shapely ships in the worker image
    _HAS_SHAPELY = False


def _element_footprint_xy(element, settings, z_lo=None, z_hi=None):
    """Convex-hull 2D plan footprint (shapely ``Polygon``) of a world-coord shape.

    Only verts within ``[z_lo, z_hi]`` are used when given (the storey band), so a
    full-height wall yields its footprint at that level rather than a column smear.
    Convex hull is conservative — it slightly over-covers concave/L-shaped walls,
    which narrows apparent gaps and therefore biases toward *not* inventing a
    passage through a solid wall (the safe failure for egress). Returns ``None`` on
    missing/degenerate geometry.
    """
    try:
        shape = ifcopenshell.geom.create_shape(settings, element)
        verts = shape.geometry.verts
        if not verts:
            return None
        pts = []
        for i in range(0, len(verts), 3):
            z = verts[i + 2]
            if z_lo is not None and z < z_lo:
                continue
            if z_hi is not None and z > z_hi:
                continue
            pts.append((verts[i], verts[i + 1]))
        if len(pts) < 3:
            return None
        poly = MultiPoint(pts).convex_hull
        if poly.geom_type != "Polygon" or poly.area < 1e-6:
            return None
        return poly
    except Exception:
        return None


def _assembly_world_verts(element, settings) -> List[Tuple[float, float, float]]:
    """World-coord verts of an element *and its decomposition children*.

    An ``IfcStair`` (and often ``IfcTransportElement``) is an assembly whose own shape is
    empty — the geometry lives in aggregated ``IfcStairFlight``/``IfcSlab`` (landing) parts.
    Gathering the children's verts gives the full floor-to-floor extent.
    """
    out: List[Tuple[float, float, float]] = []

    def add(el):
        try:
            verts = ifcopenshell.geom.create_shape(settings, el).geometry.verts
        except Exception:
            return
        for i in range(0, len(verts), 3):
            out.append((verts[i], verts[i + 1], verts[i + 2]))

    add(element)
    for rel in getattr(element, "IsDecomposedBy", None) or []:
        for child in getattr(rel, "RelatedObjects", None) or []:
            add(child)
    return out


def _verts_bbox(verts):
    """``(xmin,ymin,zmin,xmax,ymax,zmax)`` of a vert list, or None when empty."""
    if not verts:
        return None
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    zs = [v[2] for v in verts]
    return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


def _verts_zband_centroid(verts, z_lo: float, z_hi: float):
    """Mean XY of verts within ``[z_lo, z_hi]`` — e.g. a stair's base or top landing.
    Returns ``(x, y)`` or None."""
    xs_, ys_ = [], []
    for x, y, z in verts:
        if z_lo <= z <= z_hi:
            xs_.append(x)
            ys_.append(y)
    if not xs_:
        return None
    return (sum(xs_) / len(xs_), sum(ys_) / len(ys_))


def _has_stair_parent(flight) -> bool:
    """True when an IfcStairFlight is aggregated under an IfcStair (so the parent stair's
    full-rise geometry handles the storey crossing and the flight should be skipped)."""
    for rel in getattr(flight, "Decomposes", None) or []:
        parent = getattr(rel, "RelatingObject", None)
        if parent is not None and parent.is_a("IfcStair"):
            return True
    return False


def _collect_space_footprints(models):
    """gid → tight 2D footprint polygon (shapely), for point-in-polygon space resolution.

    Union of the space's mesh triangles projected to XY — tighter than the axis-aligned
    bbox, so a point sitting in a neighbour room's bbox *overhang* (but outside its real,
    possibly L-shaped, footprint) is correctly excluded. Used to disambiguate which space a
    door/opening side-point really falls in. Empty dict when shapely is unavailable.
    """
    if not _HAS_SHAPELY:
        return {}
    polys: Dict[str, "Polygon"] = {}
    settings = _geom_settings()
    for _, ifc in models:
        for sp in _safe_by_type(ifc, "IfcSpace"):
            gid = sp.GlobalId
            if gid in polys:
                continue
            try:
                shape = ifcopenshell.geom.create_shape(settings, sp)
                verts = shape.geometry.verts
                faces = shape.geometry.faces
                if not verts or not faces:
                    continue
                tris = []
                for i in range(0, len(faces), 3):
                    ia, ib, ic = faces[i] * 3, faces[i + 1] * 3, faces[i + 2] * 3
                    tri = Polygon([
                        (verts[ia], verts[ia + 1]),
                        (verts[ib], verts[ib + 1]),
                        (verts[ic], verts[ic + 1]),
                    ])
                    if tri.area > 1e-9:
                        tris.append(tri)
                if not tris:
                    continue
                fp = unary_union(tris).buffer(0)
                if fp.is_empty or fp.area < 1e-6:
                    continue
                polys[gid] = fp
            except Exception:
                continue
    return polys


def _bbox_xy_near(a, b, margin: float) -> bool:
    """True when two XY bboxes are within ``margin`` of each other (or overlap)."""
    if not a or not b:
        return False
    return not (
        a[3] + margin < b[0]
        or b[3] + margin < a[0]
        or a[4] + margin < b[1]
        or b[4] + margin < a[1]
    )


def _navmesh_passage_exists(
    fp_a,
    fp_b,
    seed_a,
    seed_b,
    wall_tree,
    wall_polys,
    human_half: float,
    margin: float,
    *,
    compute_path: bool = False,
):
    """Does a body of width ``2*human_half`` fit between the walls from A to B?

    Builds a local walkable region = (fp_a ∪ fp_b) buffered by ``margin``, minus the
    nearby walls inflated by ``human_half``. Routing a *point* through walls grown by
    ``human_half`` is equivalent to routing the full body through the real walls, so a
    connected walkable region ⇔ the box fits. Returns
    ``(exists, gap_xy | None, path_length_m | None)``.
    """
    if fp_a is None or fp_b is None:
        return False, None, None
    # Working region = convex hull of the two footprints (spans the threshold/wall
    # zone between them) clipped to within ``margin`` of the rooms. The hull does NOT
    # overrun the rooms' extent, so the path can't wrap around the *ends* of the
    # dividing wall — a crossing must pass through an actual gap in it.
    union = unary_union([fp_a, fp_b])
    region = union.convex_hull.intersection(union.buffer(margin, join_style=2))
    if region.is_empty:
        return False, None, None

    local_walls = []
    if wall_tree is not None and wall_polys:
        for k in wall_tree.query(region):
            poly = wall_polys[int(k)]
            if poly.intersects(region):
                local_walls.append(poly)

    if local_walls:
        inflated = unary_union(local_walls).buffer(human_half, join_style=2)
        walkable = region.difference(inflated)
    else:
        walkable = region  # nothing between them → open passage

    if walkable.is_empty:
        return False, None, None
    comps = list(walkable.geoms) if walkable.geom_type == "MultiPolygon" else [walkable]
    pa = Point(seed_a[0], seed_a[1])
    pb = Point(seed_b[0], seed_b[1])
    comp_a = None
    for c in comps:
        if c.distance(pa) <= margin:
            comp_a = c
            break
    if comp_a is None or comp_a.distance(pb) > margin:
        return False, None, None

    # passage location: where the two rooms are closest (≈ the doorway centre)
    npa, npb = nearest_points(fp_a, fp_b)
    gap_xy = ((npa.x + npb.x) / 2.0, (npa.y + npb.y) / 2.0)

    path_len = None
    if compute_path:
        path_len = _navmesh_astar_length(comp_a, seed_a, seed_b)
    return True, gap_xy, path_len


def _ring_to_wire(coords):
    """Topologic ``Wire`` from a shapely ring's coordinate list (closed, z=0)."""
    from topologicpy.Vertex import Vertex
    from topologicpy.Wire import Wire

    verts = [Vertex.ByCoordinates(float(x), float(y), 0.0) for x, y in coords[:-1]]
    if len(verts) < 3:
        return None
    return Wire.ByVertices(verts, close=True)


def _navmesh_astar_length(walkable_poly, seed_a, seed_b):
    """A* travel distance through ``walkable_poly`` via TopologicPy NavigationGraph.

    The walkable region is fed as the navigable face (its exterior is the boundary,
    interior rings are island obstacles such as columns) — partition walls are
    already subtracted, avoiding the boundary-touching-hole pitfall. Returns the
    path length in metres, or ``None`` if TopologicPy is unavailable / no route.
    """
    if not HAS_TOPOLOGICPY:
        return None
    import contextlib
    import io

    try:
        from topologicpy.Vertex import Vertex
        from topologicpy.Wire import Wire
        from topologicpy.Face import Face
        from topologicpy.Graph import Graph

        # TopologicPy prints warnings/errors straight to stdout; mute them — a failed
        # A* just yields no distance and is not worth flooding the ingest log.
        with contextlib.redirect_stdout(io.StringIO()):
            outer = _ring_to_wire(list(walkable_poly.exterior.coords))
            if outer is None:
                return None
            holes = []
            for ring in walkable_poly.interiors:
                hw = _ring_to_wire(list(ring.coords))
                if hw is not None:
                    holes.append(hw)
            face = Face.ByWires(outer, holes)
            va = Vertex.ByCoordinates(seed_a[0], seed_a[1], 0.0)
            vb = Vertex.ByCoordinates(seed_b[0], seed_b[1], 0.0)
            graph = Graph.NavigationGraph(
                face, sources=[va], destinations=[vb], tolerance=0.001
            )
            gverts = Graph.Vertices(graph)
            if not gverts:
                return None
            na = min(gverts, key=lambda u: Vertex.Distance(u, va))
            nb = min(gverts, key=lambda u: Vertex.Distance(u, vb))
            path = Graph.ShortestPath(graph, na, nb, useAStar=True)
            if path is None:
                return None
            return round(Wire.Length(path), 3)
    except Exception:
        return None
