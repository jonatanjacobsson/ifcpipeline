"""Takt production zones — the zone half of the Taktology ingester (plan §§0–2 + 6).

Implements ONLY the space/quantity side of the takt production graph
(byggstyrning/Taktology ``scripts/takt_production_ingester_plan.md``): takt zones,
zone membership, per-zone/per-element quantities and zone adjacency. The process
half (wagons, tasks, sequence — plan §§3–5) is Phase C and is deliberately NOT
ingested here: it is blocked on the ADR-007 scheduling-family amendment, and per
the Taktology data-authority rule wagons/rates/crews come from a planning table,
never from IFC.

Zone resolution ladder (plan §1), first non-empty rung wins under ``zone_source=auto``:

1. ``spatial_zone`` — ``IfcSpatialZone`` tagged as a takt zone (``zone_tag`` matched
   case-insensitively in ObjectType / Name / LongName / PredefinedType or any pset
   name/value). Members come from the explicit IFC rels
   (``IfcRelReferencedInSpatialStructure`` / ``IfcRelContainedInSpatialStructure`` /
   decomposition). *Read-only ingest of author-modelled zones.*
2. ``ifc_zone`` — ``IfcZone`` grouping of ``IfcSpace`` via ``IfcRelAssignsToGroup``.
3. ``derived`` — group spaces by storey (REUSING the egress storey-assignment
   ladder: IFC containment → room-number prefix → Z-centroid, see
   ``EgressCirculation._storey_group_key``) and split by ``partition_rule``
   (``storey`` = one zone per storey; ``half`` = the B5:1 half-floor split at the
   storey's space-centroid midpoint along ``partition_axis``).

Emitted relationships (family ``spatial``, ``source_kind=topologic_ingest_TaktProduction``):

- ``in_zone``           space → zone      (mirrors the authorable IN_ZONE vocabulary,
                                           so ingested + authored zones share one shape)
- ``contains_element``  zone  → element   (zone contents; per-element area/volume in
                                           evidence — Qto_* preferred, AABB fallback)
- ``adjacent_zone``     zone  ↔ zone      (one edge per sorted pair; TopologicPy dual
                                           graph gated EXACTLY like egress, bbox
                                           face-share fallback; plus consecutive-storey
                                           vertical pairs for derived zones)

Zone nodes are emitted in ``elements`` with ``takt_zone=True``. Real
``IfcSpatialZone``/``IfcZone`` keep their IFC GlobalId/class; derived zones get the
deterministic synthetic id ``taktzone_<storey-key>[_<half>]`` and class ``IfcZone``
(the same entity kind the ADR-007 authoring registry allows for zone authoring, so
rung 3 is exactly "the IfcZone grouping nobody authored yet" — provenance stays
unambiguous via source_kind + ``evidence.rung``). The CDE backend upserts these
flagged zone elements before the generic relationship import so the edges survive
``_import_topologic_relationships`` → ``NgElementRelationship`` → Neo4j
(``IN_ZONE`` / ``CONTAINS_ELEMENT`` / ``ADJACENT_ZONE``).

Deterministic and replayable (ADR-006, plan gate B5): zones, members and adjacency
are pure functions of the IFC file(s) + parameters — sorted iteration everywhere, no
timestamps, no randomness — so re-running on the same revision reproduces the
identical edge set (the CDE upsert keys on (revision, subject, type, object)).

No geometry enters the graph (ADR-012): geometry is consulted only to *decide*
membership/adjacency and to compute scalar quantity evidence.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element as ifc_element_util
import ifcopenshell.util.unit

from ingest_scripts import Element, Ingester as _Base, Relationship, safe_by_type
# Reuse the egress builder's storey-assignment ladder + bbox helpers (plan B2:
# "reuse the storey-assignment helper") rather than reinventing them.
from ingest_scripts import EgressCirculation as _egress
from ingest_scripts import ifc_thin_spaces
# Reuse the multi-threaded geometry-iterator AABB path (27min -> 37s on SBUF).
from ingest_scripts.FederatedRelationships import (
    _settings as _iterator_settings,
    _world_aabb,
)

try:
    from ingest_scripts import topograph
    HAS_TOPOLOGICPY = True
except ImportError:
    HAS_TOPOLOGICPY = False


IN_ZONE_TYPE = "in_zone"                    # IfcSpace  --> zone
CONTAINS_ELEMENT_TYPE = "contains_element"  # zone      --> built element
ADJACENT_ZONE_TYPE = "adjacent_zone"        # zone      <-> zone (sorted pair)
SOURCE_KIND = "topologic_ingest_TaktProduction"

# Spatial structure / non-buildable classes never emitted as zone *contents*
# (spaces are members via in_zone, not contents).
NON_CONTENT_CLASSES = frozenset({
    "IfcSite", "IfcBuilding", "IfcBuildingStorey", "IfcSpace", "IfcSpatialZone",
    "IfcExternalSpatialElement",
    "IfcOpeningElement", "IfcOpeningStandardCase",
    "IfcAnnotation", "IfcGrid", "IfcVirtualElement",
    # connectivity stubs, not built work
    "IfcDistributionPort", "IfcPort",
})

# Prioritized Qto_* quantity names (plan §2: prefer Qto_* sets).
AREA_QTY_KEYS = (
    "NetFloorArea", "GrossFloorArea", "NetArea", "GrossArea",
    "NetSideArea", "GrossSideArea", "CrossSectionArea", "OuterSurfaceArea",
)
VOLUME_QTY_KEYS = ("NetVolume", "GrossVolume")

_VALID_ZONE_SOURCES = ("auto", "spatial_zone", "ifc_zone", "derived")
_VALID_PARTITION_RULES = ("storey", "half")


def _area_volume_scales(ifc) -> Tuple[float, float]:
    """(area→m², volume→m³) conversion factors for Qto values."""
    length = _egress._unit_scale(ifc)
    try:
        area = float(
            ifcopenshell.util.unit.calculate_unit_scale(ifc, "AREAUNIT") or 0
        ) or length ** 2
    except Exception:
        area = length ** 2
    try:
        volume = float(
            ifcopenshell.util.unit.calculate_unit_scale(ifc, "VOLUMEUNIT") or 0
        ) or length ** 3
    except Exception:
        volume = length ** 3
    return area, volume


def _pick_qty(qtos: Dict[str, Any], keys: Tuple[str, ...]) -> Optional[float]:
    """First positive numeric quantity, scanning ``keys`` in priority order and
    quantity sets in sorted-name order (deterministic)."""
    for key in keys:
        for qset_name in sorted(qtos):
            props = qtos.get(qset_name) or {}
            if not isinstance(props, dict):
                continue
            val = props.get(key)
            if isinstance(val, (int, float)) and val > 0:
                return float(val)
    return None


def _entity_quantities(
    entity,
    aabb: Optional[Tuple[float, ...]],
    area_scale: float,
    volume_scale: float,
) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """(area_m2, volume_m3, source) — Qto_* preferred, world-AABB geometry fallback.

    The AABB fallback is deliberately crude (max bbox face for area, bbox volume);
    it exists so every contained element carries *some* quantity evidence, and the
    source is always recorded so downstream (Phase C durations) can weight it.
    """
    area = volume = None
    try:
        qtos = ifc_element_util.get_psets(entity, qtos_only=True) or {}
    except Exception:
        qtos = {}
    if qtos:
        area = _pick_qty(qtos, AREA_QTY_KEYS)
        volume = _pick_qty(qtos, VOLUME_QTY_KEYS)
    source = "qto" if (area is not None or volume is not None) else None
    if area is not None:
        area *= area_scale
    if volume is not None:
        volume *= volume_scale
    if aabb is not None and (area is None or volume is None):
        dx = max(0.0, aabb[3] - aabb[0])
        dy = max(0.0, aabb[4] - aabb[1])
        dz = max(0.0, aabb[5] - aabb[2])
        geom_area = max(dx * dy, dx * dz, dy * dz)
        geom_volume = dx * dy * dz
        used_geom = False
        if area is None and geom_area > 0:
            area = geom_area
            used_geom = True
        if volume is None and geom_volume > 0:
            volume = geom_volume
            used_geom = True
        if used_geom:
            source = "qto+geom_aabb" if source == "qto" else "geom_aabb"
    return area, volume, source


def _zone_node_id(storey_key: str, partition: Optional[str]) -> str:
    """Deterministic synthetic GlobalId for a derived zone."""
    base = "taktzone_" + storey_key.replace(":", "-")
    return f"{base}_{partition}" if partition else base


def _matches_tag(zone, tag: str) -> bool:
    """True when ``tag`` appears (case-insensitively) in the zone's ObjectType,
    Name, LongName, PredefinedType or any pset name/value."""
    tag = tag.strip().lower()
    if not tag:
        return True
    for attr in ("ObjectType", "Name", "LongName", "PredefinedType"):
        val = getattr(zone, attr, None)
        if val and tag in str(val).lower():
            return True
    try:
        psets = ifc_element_util.get_psets(zone) or {}
    except Exception:
        return False
    for pset_name in sorted(psets):
        if tag in pset_name.lower():
            return True
        props = psets[pset_name] or {}
        if not isinstance(props, dict):
            continue
        for prop_name in sorted(props):
            if tag in str(prop_name).lower() or tag in str(props[prop_name]).lower():
                return True
    return False


class _ZoneRec:
    """Resolved takt zone: identity + membership + rollups (internal)."""

    __slots__ = (
        "zone_id", "name", "ifc_class", "rung", "storey_key", "partition",
        "source_file", "member_spaces", "direct_elements",
        "space_area", "space_volume", "element_area", "element_volume",
        "element_count",
    )

    def __init__(self, zone_id: str, name: str, ifc_class: str, rung: str,
                 source_file: str, storey_key: Optional[str] = None,
                 partition: Optional[str] = None):
        self.zone_id = zone_id
        self.name = name
        self.ifc_class = ifc_class
        self.rung = rung
        self.storey_key = storey_key
        self.partition = partition
        self.source_file = source_file
        self.member_spaces: List[str] = []
        self.direct_elements: List[str] = []   # rung-1 explicit zone contents
        self.space_area = 0.0
        self.space_volume = 0.0
        self.element_area = 0.0
        self.element_volume = 0.0
        self.element_count = 0


class Ingester(_Base):
    SCRIPT_NAME = "TaktProduction"
    DESCRIPTION = (
        "Resolve takt zones (IfcSpatialZone tagged -> IfcZone grouping -> derived "
        "storey+partition), emit in_zone/contains_element membership with Qto_* "
        "quantities and TopologicPy-gated zone adjacency"
    )

    def __init__(
        self,
        ifc_files: List[Path],
        log: logging.Logger,
        zone_source: str = "auto",
        zone_tag: str = "takt",
        partition_rule: str = "storey",
        partition_axis: str = "auto",
        include_elements: bool = True,
        quantities: bool = True,
        adjacency: bool = True,
        include_aggregate_spaces: bool = False,
        use_topologic: bool = True,
        force_ifc_native: bool = False,
        tolerance: float = 0.01,
        storey_z_tolerance: float = 2.5,
        face_tolerance: float = 0.15,
        min_shared_face: float = 0.30,
        num_threads: int = 0,
    ):
        """Resolve takt zones and their element membership from IFC models.

        Applies the Taktology zone-resolution ladder (tagged IfcSpatialZone →
        IfcZone grouping → derived storey+partition), emits in_zone (space→zone) and
        contains_element (zone→element) membership edges with per-element area/volume
        evidence (Qto_* preferred, geometry AABB fallback), and zone adjacency edges
        (TopologicPy dual graph when gated on, bbox face-share fallback). Wagons,
        tasks and sequence (the process half) are NOT ingested — Phase C.

        :param zone_source: Ladder control: auto (first non-empty rung), spatial_zone, ifc_zone, or derived.
        :param zone_tag: Marker matched case-insensitively in IfcSpatialZone ObjectType/Name/psets for rung 1 (auto mode).
        :param partition_rule: Derived-zone rule: storey (one zone per storey) or half (half-floor split per storey).
        :param partition_axis: Axis for the half split: auto (largest global centroid spread), x, or y.
        :param include_elements: Emit contains_element zone→element edges.
        :param quantities: Extract per-element/per-zone area+volume (Qto_* preferred, AABB fallback).
        :param adjacency: Emit adjacent_zone edges.
        :param include_aggregate_spaces: Include Revit apartment-aggregate anchor spaces (Name like 2-1103) as zone members.
        :param use_topologic: When False, skip the TopologicPy dual-graph adjacency pass (bbox fallback only).
        :param force_ifc_native: Internal retry flag after SIGSEGV (same as use_topologic=False).
        :param tolerance: TopologicPy graph construction tolerance in model units.
        :param storey_z_tolerance: Z tolerance (m) for centroid→storey inference.
        :param face_tolerance: Max bbox gap (m) counting as touching in the adjacency fallback.
        :param min_shared_face: Min shared bbox edge (m) for fallback adjacency.
        :param num_threads: Geometry-iterator threads (0 = auto: cpu_count-1).
        """
        super().__init__(ifc_files, log)
        zone_source = (zone_source or "auto").strip().lower()
        if zone_source not in _VALID_ZONE_SOURCES:
            raise ValueError(
                f"zone_source must be one of {_VALID_ZONE_SOURCES}, got {zone_source!r}"
            )
        partition_rule = (partition_rule or "storey").strip().lower()
        if partition_rule not in _VALID_PARTITION_RULES:
            raise ValueError(
                f"partition_rule must be one of {_VALID_PARTITION_RULES}, got {partition_rule!r}"
            )
        partition_axis = (partition_axis or "auto").strip().lower()
        if partition_axis not in ("auto", "x", "y"):
            raise ValueError(f"partition_axis must be auto|x|y, got {partition_axis!r}")
        self.zone_source = zone_source
        self.zone_tag = zone_tag or ""
        self.partition_rule = partition_rule
        self.partition_axis = partition_axis
        self.include_elements = bool(include_elements)
        self.quantities = bool(quantities)
        self.adjacency = bool(adjacency)
        self.include_aggregate_spaces = bool(include_aggregate_spaces)
        self.use_topologic = bool(use_topologic) and not bool(force_ifc_native)
        self.tolerance = float(tolerance)
        self.storey_z_tolerance = float(storey_z_tolerance)
        self.face_tolerance = float(face_tolerance)
        self.min_shared_face = float(min_shared_face)
        import os
        self.num_threads = int(num_threads) or max(1, (os.cpu_count() or 2) - 1)
        self._temp_paths: List[Path] = []

    # ------------------------------------------------------------------
    # extract
    # ------------------------------------------------------------------

    def extract(self) -> None:
        t0 = time.time()
        models: List[Tuple[Path, ifcopenshell.file]] = []
        for ifc_path in self.ifc_files:
            self.log.info("takt: opening %s", ifc_path.name)
            models.append((ifc_path.resolve(), ifcopenshell.open(str(ifc_path))))

        try:
            self._extract_inner(models, t0)
        finally:
            self._cleanup_temp_paths()

    def _extract_inner(self, models, t0: float) -> None:
        # --- shared space/storey substrate (egress storey-assignment helpers) ---
        element_storey, storey_elevations = _egress._collect_storey_maps(models)
        space_points, space_sources, space_names = _egress._collect_spaces_centroids(models)
        storey_labels = self._storey_labels(models, storey_elevations)

        space_storey_key: Dict[str, Optional[str]] = {}
        for gid in sorted(space_points):
            space_storey_key[gid] = _egress._storey_group_key(
                gid, space_points.get(gid), space_names.get(gid, ""),
                element_storey, storey_elevations, self.storey_z_tolerance,
            )

        excluded_aggregates: Set[str] = set()
        if not self.include_aggregate_spaces:
            for gid, name in space_names.items():
                if _egress._APARTMENT_AGGREGATE_RE.match((name or "").strip()):
                    excluded_aggregates.add(gid)

        member_pool = [
            gid for gid in sorted(space_points) if gid not in excluded_aggregates
        ]

        # --- rung resolution ---------------------------------------------------
        zones, rung, partition_meta = self._resolve_takt_zones(
            models, member_pool, space_points, space_names,
            space_storey_key, storey_labels,
        )
        if not zones:
            self.log.warning("takt: no takt zones resolvable (rung=%s)", rung)
            self._summary = {
                "rung": rung, "zone_count": 0, "spaces": len(space_points),
                "duration_ms": int((time.time() - t0) * 1000),
            }
            return
        self.log.info(
            "takt: %d zone(s) via rung=%s (partition_rule=%s)",
            len(zones), rung, self.partition_rule if rung == "derived" else "-",
        )

        # --- membership: spaces -------------------------------------------------
        space_zone: Dict[str, List[str]] = defaultdict(list)
        in_zone_conf = 1.0 if rung in ("spatial_zone", "ifc_zone") else 0.95
        space_bboxes = _egress._collect_space_bboxes(models)
        area_scales: Dict[int, Tuple[float, float]] = {
            id(ifc): _area_volume_scales(ifc) for _, ifc in models
        }
        # gid → (entity, (area_scale, volume_scale)) so Qto values convert with
        # the scales of the file the space actually came from.
        space_entities: Dict[str, Tuple[Any, Tuple[float, float]]] = {}
        for _, ifc in models:
            scales = area_scales[id(ifc)]
            for sp in safe_by_type(ifc, "IfcSpace"):
                gid = getattr(sp, "GlobalId", None)
                if gid and gid not in space_entities:
                    space_entities[gid] = (sp, scales)

        for zone in self._sorted_zones(zones):
            for sgid in sorted(zone.member_spaces):
                space_zone[sgid].append(zone.zone_id)
                if self.quantities:
                    a, v = self._space_quantities(
                        space_entities.get(sgid), space_bboxes.get(sgid),
                    )
                    zone.space_area += a or 0.0
                    zone.space_volume += v or 0.0
                self._relationships.append(Relationship(
                    subject_global_id=sgid,
                    object_global_id=zone.zone_id,
                    relationship_family="spatial",
                    relationship_type=IN_ZONE_TYPE,
                    confidence=in_zone_conf,
                    source_kind=SOURCE_KIND,
                    evidence={
                        "method": f"takt_zone_{rung}",
                        "rung": rung,
                        "zoneName": zone.name,
                        "storeyKey": zone.storey_key,
                        "partition": zone.partition,
                        "sourceFile": zone.source_file,
                    },
                ))

        # --- membership: elements ----------------------------------------------
        unassigned_elements = 0
        if self.include_elements:
            unassigned_elements = self._emit_element_containment(
                models, zones, rung, partition_meta, space_zone,
                space_bboxes, space_storey_key, storey_elevations, area_scales,
            )

        # --- zone nodes ----------------------------------------------------------
        for zone in self._sorted_zones(zones):
            extra: Dict[str, Any] = {
                "takt_zone": True,
                "zone_source": zone.rung,
                "space_count": len(zone.member_spaces),
                "element_count": zone.element_count,
                "source_file": zone.source_file,
            }
            if zone.storey_key:
                extra["storey_key"] = zone.storey_key
            if zone.partition:
                extra["partition"] = zone.partition
            if self.quantities:
                extra["area_m2"] = round(zone.space_area, 2)
                extra["volume_m3"] = round(zone.space_volume, 2)
                extra["element_area_m2"] = round(zone.element_area, 2)
                extra["element_volume_m3"] = round(zone.element_volume, 2)
            self._elements.append(Element(
                global_id=zone.zone_id,
                ifc_class=zone.ifc_class,
                name=zone.name,
                extra=extra,
            ))

        # --- adjacency ------------------------------------------------------------
        adjacency_methods: Set[str] = set()
        adjacent_pairs = 0
        if self.adjacency and len(zones) > 1:
            adjacent_pairs = self._emit_zone_adjacency(
                models, zones, rung, space_zone, space_bboxes,
                space_storey_key, storey_elevations, adjacency_methods,
            )

        by_type: Dict[str, int] = defaultdict(int)
        for rel in self._relationships:
            by_type[rel.relationship_type] += 1
        self._summary = {
            "rung": rung,
            "partition_rule": self.partition_rule if rung == "derived" else None,
            "partition_axis": partition_meta.get("axis") if rung == "derived" else None,
            "zone_count": len(zones),
            "spaces": len(space_points),
            "member_spaces": sum(len(z.member_spaces) for z in zones.values()),
            "excluded_aggregate_spaces": len(excluded_aggregates),
            "unassigned_elements": unassigned_elements,
            "edges_by_type": dict(sorted(by_type.items())),
            "adjacency_method": "+".join(sorted(adjacency_methods)) or None,
            "adjacent_zone_pairs": adjacent_pairs,
            "zones": [
                {
                    "id": z.zone_id,
                    "name": z.name,
                    "storey_key": z.storey_key,
                    "partition": z.partition,
                    "spaces": len(z.member_spaces),
                    "elements": z.element_count,
                    "area_m2": round(z.space_area, 2),
                    "volume_m3": round(z.space_volume, 2),
                }
                for z in self._sorted_zones(zones)
            ],
            "duration_ms": int((time.time() - t0) * 1000),
        }
        self.log.info(
            "takt: %d zones, %d edges (%s) in %dms",
            len(zones), len(self._relationships),
            ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())),
            self._summary["duration_ms"],
        )

    # ------------------------------------------------------------------
    # zone resolution ladder
    # ------------------------------------------------------------------

    def _resolve_takt_zones(
        self, models, member_pool, space_points, space_names,
        space_storey_key, storey_labels,
    ) -> Tuple[Dict[str, _ZoneRec], str, Dict[str, Any]]:
        """Apply the ladder. Returns (zones by id, rung used, partition metadata)."""
        member_set = set(member_pool)

        if self.zone_source in ("auto", "spatial_zone"):
            zones = self._zones_from_spatial_zones(models, member_set)
            if zones:
                return zones, "spatial_zone", {}
            if self.zone_source == "spatial_zone":
                return {}, "spatial_zone", {}

        if self.zone_source in ("auto", "ifc_zone"):
            zones = self._zones_from_ifc_zones(models, member_set)
            if zones:
                return zones, "ifc_zone", {}
            if self.zone_source == "ifc_zone":
                return {}, "ifc_zone", {}

        zones, meta = self._zones_derived(
            member_pool, space_points, space_storey_key, storey_labels,
        )
        return zones, "derived", meta

    def _zones_from_spatial_zones(
        self, models, member_set: Set[str]
    ) -> Dict[str, _ZoneRec]:
        """Rung 1: IfcSpatialZone tagged as takt zone (all of them when
        zone_source=spatial_zone is forced)."""
        require_tag = self.zone_source == "auto"
        zones: Dict[str, _ZoneRec] = {}
        for ifc_path, ifc in models:
            for sz in sorted(
                safe_by_type(ifc, "IfcSpatialZone"),
                key=lambda e: getattr(e, "GlobalId", "") or "",
            ):
                gid = getattr(sz, "GlobalId", None)
                if not gid or gid in zones:
                    continue
                if require_tag and not _matches_tag(sz, self.zone_tag):
                    continue
                rec = _ZoneRec(
                    zone_id=gid,
                    name=getattr(sz, "Name", None) or getattr(sz, "LongName", None) or gid,
                    ifc_class=sz.is_a(),
                    rung="spatial_zone",
                    source_file=ifc_path.name,
                )
                spaces, elements = self._spatial_zone_members(sz)
                # Author-tagged zones keep ALL their explicit space members — the
                # aggregate-space filter only applies to derived grouping.
                rec.member_spaces = sorted(spaces)
                rec.direct_elements = sorted(elements)
                zones[gid] = rec
        return zones

    @staticmethod
    def _spatial_zone_members(sz) -> Tuple[Set[str], Set[str]]:
        """Explicit members of an IfcSpatialZone: (space gids, element gids)."""
        spaces: Set[str] = set()
        elements: Set[str] = set()

        def _take(obj) -> None:
            gid = getattr(obj, "GlobalId", None)
            if not gid:
                return
            if obj.is_a("IfcSpace"):
                spaces.add(gid)
            elif obj.is_a() not in NON_CONTENT_CLASSES:
                elements.add(gid)

        for rel in getattr(sz, "ReferencesElements", None) or []:
            for obj in getattr(rel, "RelatedElements", []) or []:
                _take(obj)
        for rel in getattr(sz, "ContainsElements", None) or []:
            for obj in getattr(rel, "RelatedElements", []) or []:
                _take(obj)
        for rel in getattr(sz, "IsDecomposedBy", None) or []:
            for obj in getattr(rel, "RelatedObjects", []) or []:
                _take(obj)
        return spaces, elements

    def _zones_from_ifc_zones(
        self, models, member_set: Set[str]
    ) -> Dict[str, _ZoneRec]:
        """Rung 2: IfcZone grouping of IfcSpace via IfcRelAssignsToGroup."""
        zones: Dict[str, _ZoneRec] = {}
        for ifc_path, ifc in models:
            grouped: Dict[str, Set[str]] = defaultdict(set)
            zone_entities: Dict[str, Any] = {}
            for rel in safe_by_type(ifc, "IfcRelAssignsToGroup"):
                group = getattr(rel, "RelatingGroup", None)
                if not group or not group.is_a("IfcZone"):
                    continue
                zgid = getattr(group, "GlobalId", None)
                if not zgid:
                    continue
                zone_entities[zgid] = group
                for obj in getattr(rel, "RelatedObjects", []) or []:
                    if obj.is_a("IfcSpace") and getattr(obj, "GlobalId", None):
                        grouped[zgid].add(obj.GlobalId)
            for zgid in sorted(grouped):
                members = sorted(g for g in grouped[zgid] if g in member_set)
                if not members or zgid in zones:
                    continue
                group = zone_entities[zgid]
                rec = _ZoneRec(
                    zone_id=zgid,
                    name=getattr(group, "Name", None) or getattr(group, "LongName", None) or zgid,
                    ifc_class="IfcZone",
                    rung="ifc_zone",
                    source_file=ifc_path.name,
                )
                rec.member_spaces = members
                zones[zgid] = rec
        return zones

    def _zones_derived(
        self, member_pool, space_points, space_storey_key, storey_labels,
    ) -> Tuple[Dict[str, _ZoneRec], Dict[str, Any]]:
        """Rung 3: storey grouping + partition rule (plan B1:3, B5:1 half-floor)."""
        by_storey: Dict[str, List[str]] = defaultdict(list)
        for gid in member_pool:
            skey = space_storey_key.get(gid)
            if skey:
                by_storey[skey].append(gid)

        meta: Dict[str, Any] = {"storeys": {}}
        axis = self.partition_axis
        if self.partition_rule == "half" and axis == "auto":
            # ONE global axis (largest centroid spread) so half-labels are
            # comparable across storeys — vertical A↔A / B↔B adjacency stays sane.
            xs = [space_points[g][0] for g in member_pool if g in space_points]
            ys = [space_points[g][1] for g in member_pool if g in space_points]
            spread_x = (max(xs) - min(xs)) if xs else 0.0
            spread_y = (max(ys) - min(ys)) if ys else 0.0
            axis = "x" if spread_x >= spread_y else "y"
        meta["axis"] = axis if self.partition_rule == "half" else None

        zones: Dict[str, _ZoneRec] = {}
        source_file = self.ifc_files[0].name if self.ifc_files else ""
        for skey in sorted(by_storey):
            members = sorted(by_storey[skey])
            label = storey_labels.get(skey, skey)
            if self.partition_rule == "storey":
                zid = _zone_node_id(skey, None)
                rec = _ZoneRec(
                    zone_id=zid, name=f"Takt zone {label}", ifc_class="IfcZone",
                    rung="derived", source_file=source_file,
                    storey_key=skey, partition=None,
                )
                rec.member_spaces = members
                zones[zid] = rec
                continue

            # half split at the storey's space-centroid extent midpoint
            ax = 0 if axis == "x" else 1
            coords = [space_points[g][ax] for g in members if g in space_points]
            mid = (min(coords) + max(coords)) / 2.0 if coords else 0.0
            meta["storeys"][skey] = {"axis": axis, "mid": mid}
            halves: Dict[str, List[str]] = {"A": [], "B": []}
            for g in members:
                pt = space_points.get(g)
                side = "A" if (pt is not None and pt[ax] < mid) else "B"
                halves[side].append(g)
            for side in ("A", "B"):
                if not halves[side]:
                    continue
                zid = _zone_node_id(skey, side)
                rec = _ZoneRec(
                    zone_id=zid, name=f"Takt zone {label} {side}", ifc_class="IfcZone",
                    rung="derived", source_file=source_file,
                    storey_key=skey, partition=side,
                )
                rec.member_spaces = sorted(halves[side])
                zones[zid] = rec
        return zones, meta

    # ------------------------------------------------------------------
    # element containment (+ quantities)
    # ------------------------------------------------------------------

    def _emit_element_containment(
        self, models, zones, rung, partition_meta, space_zone,
        space_bboxes, space_storey_key, storey_elevations, area_scales,
    ) -> int:
        """Emit contains_element zone→element edges. Returns unassigned count.

        Assignment precedence (first match wins, all deterministic):
          1. explicit zone rels (rung 1 only)
          2. element explicitly contained in a member IfcSpace
          3. derived rungs: element's storey (containment else Z-inferred) +
             partition side (centroid vs the storey's split midpoint)
             rung 1/2: element centroid inside a member space's AABB
        """
        candidates: Dict[str, Any] = {}
        element_ifc: Dict[str, Any] = {}
        element_class: Dict[str, str] = {}
        elem_storey_gid: Dict[str, str] = {}
        elem_space: Dict[str, str] = {}
        for _, ifc in models:
            for product in safe_by_type(ifc, "IfcProduct"):
                gid = getattr(product, "GlobalId", None)
                if not gid or gid in candidates:
                    continue
                cls = product.is_a()
                if cls in NON_CONTENT_CLASSES:
                    continue
                candidates[gid] = product
                element_ifc[gid] = ifc
                element_class[gid] = cls
            for rel in safe_by_type(ifc, "IfcRelContainedInSpatialStructure"):
                struct = getattr(rel, "RelatingStructure", None)
                if not struct:
                    continue
                if struct.is_a("IfcBuildingStorey"):
                    for obj in getattr(rel, "RelatedElements", []) or []:
                        gid = getattr(obj, "GlobalId", None)
                        if gid:
                            elem_storey_gid.setdefault(gid, struct.GlobalId)
                elif struct.is_a("IfcSpace") and getattr(struct, "GlobalId", None):
                    for obj in getattr(rel, "RelatedElements", []) or []:
                        gid = getattr(obj, "GlobalId", None)
                        if gid:
                            elem_space.setdefault(gid, struct.GlobalId)

        aabbs = self._collect_aabbs(models, set(candidates)) if candidates else {}

        zone_by_storey: Dict[str, Dict[str, _ZoneRec]] = defaultdict(dict)
        for zone in zones.values():
            if zone.storey_key:
                zone_by_storey[zone.storey_key][zone.partition or ""] = zone

        # member-space bboxes per zone for the rung-1/2 geometric fallback
        member_space_of: Dict[str, List[str]] = defaultdict(list)
        for zone in self._sorted_zones(zones):
            for sgid in zone.member_spaces:
                member_space_of[sgid].append(zone.zone_id)

        direct: Dict[str, Set[str]] = defaultdict(set)   # element gid -> zone ids (rung 1)
        for zone in zones.values():
            for egid in zone.direct_elements:
                direct[egid].add(zone.zone_id)

        seen_pairs: Set[Tuple[str, str]] = set()
        unassigned = 0
        storey_elev_key = {
            gid: f"elev:{round(elev, 2)}" for gid, elev in storey_elevations.items()
        }

        for egid in sorted(candidates):
            entity = candidates[egid]
            aabb = aabbs.get(egid)
            assignments: List[Tuple[str, str, float, Optional[str]]] = []
            # (zone_id, method, confidence, via_space)

            if egid in direct:
                for zid in sorted(direct[egid]):
                    assignments.append((zid, "ifc_spatial_zone_rel", 1.0, None))
            elif egid in elem_space and elem_space[egid] in member_space_of:
                sgid = elem_space[egid]
                for zid in member_space_of[sgid]:
                    assignments.append((zid, "contained_in_member_space", 1.0, sgid))
            elif rung == "derived":
                zone = self._derived_zone_for_element(
                    egid, aabb, elem_storey_gid, storey_elev_key,
                    storey_elevations, zone_by_storey, partition_meta,
                )
                if zone is not None:
                    assignments.append((zone.zone_id, "storey_partition", 0.9, None))
            else:
                sgid = self._space_containing_centroid(aabb, space_bboxes, member_space_of)
                if sgid is not None:
                    for zid in member_space_of[sgid]:
                        assignments.append((zid, "centroid_in_member_space", 0.85, sgid))

            if not assignments:
                unassigned += 1
                continue

            area = volume = None
            qty_source = None
            if self.quantities:
                a_scale, v_scale = area_scales.get(id(element_ifc[egid]), (1.0, 1.0))
                area, volume, qty_source = _entity_quantities(entity, aabb, a_scale, v_scale)

            for zid, method, conf, via_space in assignments:
                pair = (zid, egid)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                zone = zones[zid]
                zone.element_count += 1
                if area is not None:
                    zone.element_area += area
                if volume is not None:
                    zone.element_volume += volume
                evidence: Dict[str, Any] = {
                    "method": method,
                    "rung": zone.rung,
                    "objectClass": element_class[egid],
                    "sourceFile": zone.source_file,
                }
                if zone.storey_key:
                    evidence["storeyKey"] = zone.storey_key
                if zone.partition:
                    evidence["partition"] = zone.partition
                if via_space:
                    evidence["viaSpace"] = via_space
                if area is not None:
                    evidence["areaM2"] = round(area, 3)
                if volume is not None:
                    evidence["volumeM3"] = round(volume, 3)
                if qty_source:
                    evidence["qtySource"] = qty_source
                self._relationships.append(Relationship(
                    subject_global_id=zid,
                    object_global_id=egid,
                    relationship_family="spatial",
                    relationship_type=CONTAINS_ELEMENT_TYPE,
                    confidence=conf,
                    source_kind=SOURCE_KIND,
                    evidence=evidence,
                ))
        if unassigned:
            self.log.info("takt: %d element(s) not assignable to a zone", unassigned)
        return unassigned

    def _derived_zone_for_element(
        self, egid, aabb, elem_storey_gid, storey_elev_key,
        storey_elevations, zone_by_storey, partition_meta,
    ) -> Optional[_ZoneRec]:
        skey: Optional[str] = None
        sgid = elem_storey_gid.get(egid)
        if sgid and sgid in storey_elev_key:
            skey = storey_elev_key[sgid]
        elif aabb is not None:
            cz = (aabb[2] + aabb[5]) / 2.0
            inferred = _egress._infer_storey_from_z(
                cz, storey_elevations, self.storey_z_tolerance
            )
            if inferred:
                skey = storey_elev_key.get(inferred)
        if not skey or skey not in zone_by_storey:
            return None
        parts = zone_by_storey[skey]
        if self.partition_rule == "storey":
            return parts.get("")
        smeta = (partition_meta.get("storeys") or {}).get(skey)
        if not smeta:
            # storey had no partition split (e.g. no member spaces) — single zone?
            return next(iter(sorted(parts.items())))[1] if parts else None
        if aabb is None:
            return None
        ax = 0 if smeta["axis"] == "x" else 1
        centroid = (
            (aabb[0] + aabb[3]) / 2.0,
            (aabb[1] + aabb[4]) / 2.0,
        )
        side = "A" if centroid[ax] < smeta["mid"] else "B"
        zone = parts.get(side)
        if zone is None and len(parts) == 1:
            # the storey's spaces all fell on one side; keep the storey whole
            zone = next(iter(parts.values()))
        return zone

    @staticmethod
    def _space_containing_centroid(
        aabb, space_bboxes, member_space_of,
    ) -> Optional[str]:
        """Member space whose AABB contains the element centroid (smallest wins)."""
        if aabb is None:
            return None
        cx = (aabb[0] + aabb[3]) / 2.0
        cy = (aabb[1] + aabb[4]) / 2.0
        cz = (aabb[2] + aabb[5]) / 2.0
        best: Optional[Tuple[float, str]] = None
        for sgid in member_space_of:
            b = space_bboxes.get(sgid)
            if b is None:
                continue
            if b[0] <= cx <= b[3] and b[1] <= cy <= b[4] and b[2] <= cz <= b[5]:
                vol = max(0.0, (b[3] - b[0]) * (b[4] - b[1]) * (b[5] - b[2]))
                key = (vol, sgid)
                if best is None or key < best:
                    best = key
        return best[1] if best else None

    def _collect_aabbs(
        self, models, wanted: Set[str]
    ) -> Dict[str, Tuple[float, ...]]:
        """World AABBs for candidate elements via the multi-threaded geometry
        iterator (FederatedRelationships pattern); placement-point fallback."""
        aabbs: Dict[str, Tuple[float, ...]] = {}
        settings = _iterator_settings()
        for ifc_path, ifc in models:
            try:
                it = ifcopenshell.geom.iterator(settings, ifc, self.num_threads)
                if not it.initialize():
                    continue
            except Exception:
                self.log.warning(
                    "takt: geometry iterator unavailable for %s", ifc_path.name,
                    exc_info=True,
                )
                continue
            while True:
                shape = it.get()
                gid = getattr(shape, "guid", None)
                if gid in wanted and gid not in aabbs:
                    verts = shape.geometry.verts
                    if verts:
                        mat = shape.transformation.matrix
                        m = list(getattr(mat, "data", mat))
                        aabbs[gid] = _world_aabb(verts, m)
                if not it.next():
                    break
        missing = [gid for gid in sorted(wanted) if gid not in aabbs]
        if missing:
            self.log.info(
                "takt: %d/%d element(s) without iterator geometry; placement fallback",
                len(missing), len(wanted),
            )
            import ifcopenshell.util.placement as _placement
            by_gid: Dict[str, Tuple[Any, float]] = {}
            for _, ifc in models:
                scale = _egress._unit_scale(ifc)  # placement is in project units; AABBs are SI m
                for product in safe_by_type(ifc, "IfcProduct"):
                    gid = getattr(product, "GlobalId", None)
                    if gid and gid not in by_gid:
                        by_gid[gid] = (product, scale)
            for gid in missing:
                product, scale = by_gid.get(gid, (None, 1.0))
                if product is None or not getattr(product, "ObjectPlacement", None):
                    continue
                try:
                    m = _placement.get_local_placement(product.ObjectPlacement)
                    x = float(m[0][3]) * scale
                    y = float(m[1][3]) * scale
                    z = float(m[2][3]) * scale
                    aabbs[gid] = (x, y, z, x, y, z)  # degenerate point AABB
                except Exception:
                    continue
        return aabbs

    # ------------------------------------------------------------------
    # quantities
    # ------------------------------------------------------------------

    @staticmethod
    def _space_quantities(
        entry: Optional[Tuple[Any, Tuple[float, float]]], bbox,
    ) -> Tuple[Optional[float], Optional[float]]:
        """(area_m2, volume_m3) for a member space — Qto preferred, bbox fallback."""
        if entry is None:
            return None, None
        entity, (a_scale, v_scale) = entry
        area, volume, _src = _entity_quantities(entity, bbox, a_scale, v_scale)
        # bbox "max face" is wrong for a space's floor area — prefer the XY footprint
        if bbox is not None and area is not None and _src and "geom" in _src:
            dx = max(0.0, bbox[3] - bbox[0])
            dy = max(0.0, bbox[4] - bbox[1])
            area = dx * dy
        return area, volume

    # ------------------------------------------------------------------
    # zone adjacency (plan §6 — TopologicPy gated exactly like egress)
    # ------------------------------------------------------------------

    def _emit_zone_adjacency(
        self, models, zones, rung, space_zone, space_bboxes,
        space_storey_key, storey_elevations, methods_out: Set[str],
    ) -> int:
        """Zone↔zone horizontal adjacency from member-space adjacency.

        The TopologicPy dual graph (gated exactly like egress) and the bbox
        face-share pass run in UNION, not preference: cell-complex adjacency only
        links spaces that share an actual face, and rooms separated by a wall
        usually don't — the bbox pass is what bridges the split line, which the
        same-storey takt-train ordering (reading B) depends on. Per-pair
        provenance is kept so the edge records which engine(s) saw it.
        """
        pair_methods: Dict[Tuple[str, str], Set[str]] = defaultdict(set)

        for ifc_path, ifc in models:
            space_count = len(safe_by_type(ifc, "IfcSpace"))
            entity_count = ifc_thin_spaces.approx_entity_count(ifc)
            topologic_ok = (
                HAS_TOPOLOGICPY
                and self.use_topologic
                and 0 < space_count <= 400
                and entity_count <= _egress._TOPOLOGIC_MAX_ENTITIES
            )
            if HAS_TOPOLOGICPY and self.use_topologic and not topologic_ok:
                self.log.info(
                    "takt: skipping TopologicPy adjacency for %s "
                    "(spaces=%d, entities=%d > gates)",
                    ifc_path.name, space_count, entity_count,
                )
            if topologic_ok:
                topo_pairs = self._topologic_space_pairs(ifc_path, ifc)
                if topo_pairs is not None:
                    methods_out.add("topologicpy_dual_graph")
                    for pair in topo_pairs:
                        pair_methods[pair].add("topologicpy_dual_graph")
            bbox_pairs = self._bbox_space_pairs(ifc, space_bboxes, space_storey_key)
            methods_out.add("bbox_face_adjacency")
            for pair in bbox_pairs:
                pair_methods[pair].add("bbox_face_adjacency")

        zone_pairs: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for (s1, s2) in sorted(pair_methods):
            for z1 in space_zone.get(s1, ()):  # sorted at insert time
                for z2 in space_zone.get(s2, ()):
                    if z1 == z2:
                        continue
                    key = tuple(sorted((z1, z2)))
                    agg = zone_pairs.setdefault(key, {"count": 0, "methods": set()})
                    agg["count"] += 1
                    agg["methods"] |= pair_methods[(s1, s2)]

        emitted = 0
        for (z1, z2), agg in sorted(zone_pairs.items()):
            self._relationships.append(Relationship(
                subject_global_id=z1,
                object_global_id=z2,
                relationship_family="spatial",
                relationship_type=ADJACENT_ZONE_TYPE,
                confidence=0.9 if "topologicpy_dual_graph" in agg["methods"] else 0.85,
                source_kind=SOURCE_KIND,
                evidence={
                    "method": "+".join(sorted(agg["methods"])),
                    "axis": "horizontal",
                    "sharedSpacePairs": agg["count"],
                },
            ))
            emitted += 1

        if rung == "derived":
            emitted += self._emit_vertical_adjacency(zones, storey_elevations)
            methods_out.add("consecutive_storey")
        return emitted

    def _topologic_space_pairs(
        self, ifc_path: Path, ifc
    ) -> Optional[Set[Tuple[str, str]]]:
        """IfcSpace adjacency pairs from the TopologicPy dual graph (thin-spaces
        copy, exactly the egress gating). None on failure → bbox fallback."""
        graph_path = ifc_path
        try:
            if not ifc_thin_spaces.is_spaces_only_file(ifc):
                thin = ifc_thin_spaces.thin_spaces_copy(ifc_path, log=self.log)
                self._temp_paths.append(thin)
                graph_path = thin
        except Exception as exc:
            self.log.warning("takt: thin spaces failed for %s (%s)", ifc_path.name, exc)
        try:
            graph = topograph.build_graph(graph_path, tolerance=self.tolerance)
            if graph is None:
                return None
            pairs: Set[Tuple[str, str]] = set()
            for node in topograph.vertices(graph):
                if "IfcSpace" not in node.ifc_type:
                    continue
                gid = node.gid
                if not gid:
                    continue
                for adj in topograph.adjacent(graph, node):
                    if "IfcSpace" not in adj.ifc_type or not adj.gid:
                        continue
                    pairs.add(tuple(sorted((gid, adj.gid))))
            return pairs
        except Exception as exc:
            self.log.warning(
                "takt: TopologicPy adjacency failed for %s: %s", ifc_path.name, exc,
            )
            return None

    def _bbox_space_pairs(
        self, ifc, space_bboxes, space_storey_key,
    ) -> Set[Tuple[str, str]]:
        """Deterministic fallback: same-storey 2D bbox face-share (egress helper)."""
        by_storey: Dict[str, List[str]] = defaultdict(list)
        for space in safe_by_type(ifc, "IfcSpace"):
            gid = getattr(space, "GlobalId", None)
            if not gid or gid not in space_bboxes:
                continue
            skey = space_storey_key.get(gid)
            if skey:
                by_storey[skey].append(gid)
        pairs: Set[Tuple[str, str]] = set()
        for skey in sorted(by_storey):
            gids = sorted(by_storey[skey])
            for i, g1 in enumerate(gids):
                b1 = space_bboxes[g1]
                for g2 in gids[i + 1:]:
                    b2 = space_bboxes[g2]
                    if _egress._bbox2d_face_adjacent(
                        (b1[0], b1[1], b1[3], b1[4]),
                        (b2[0], b2[1], b2[3], b2[4]),
                        self.face_tolerance, self.min_shared_face,
                    ):
                        pairs.add((g1, g2))
        return pairs

    def _emit_vertical_adjacency(self, zones, storey_elevations) -> int:
        """Derived zones on consecutive storeys, same partition label (or whole
        storeys), are takt-train neighbours (evidence axis=vertical)."""
        by_elev: List[Tuple[float, str]] = []
        seen_keys: Set[str] = set()
        for zone in zones.values():
            skey = zone.storey_key or ""
            if not skey.startswith("elev:") or skey in seen_keys:
                continue
            seen_keys.add(skey)
            try:
                by_elev.append((float(skey[5:]), skey))
            except ValueError:
                continue
        by_elev.sort()

        by_storey: Dict[str, Dict[str, _ZoneRec]] = defaultdict(dict)
        for zone in zones.values():
            if zone.storey_key:
                by_storey[zone.storey_key][zone.partition or ""] = zone

        emitted = 0
        for (e1, k1), (e2, k2) in zip(by_elev, by_elev[1:]):
            lower, upper = by_storey[k1], by_storey[k2]
            for label in sorted(set(lower) & set(upper)):
                z1, z2 = lower[label], upper[label]
                s, o = sorted((z1.zone_id, z2.zone_id))
                self._relationships.append(Relationship(
                    subject_global_id=s,
                    object_global_id=o,
                    relationship_family="spatial",
                    relationship_type=ADJACENT_ZONE_TYPE,
                    confidence=0.9,
                    source_kind=SOURCE_KIND,
                    evidence={
                        "method": "consecutive_storey",
                        "axis": "vertical",
                        "storeyFrom": k1,
                        "storeyTo": k2,
                        "partition": label or None,
                    },
                ))
                emitted += 1
        return emitted

    # ------------------------------------------------------------------
    # misc
    # ------------------------------------------------------------------

    @staticmethod
    def _sorted_zones(zones: Dict[str, _ZoneRec]) -> List[_ZoneRec]:
        return [zones[zid] for zid in sorted(zones)]

    def _storey_labels(self, models, storey_elevations) -> Dict[str, str]:
        """storey_key ('elev:X' / 'gid:X') → human label from IfcBuildingStorey.Name."""
        labels: Dict[str, str] = {}
        for _, ifc in models:
            for storey in sorted(
                safe_by_type(ifc, "IfcBuildingStorey"),
                key=lambda s: getattr(s, "GlobalId", "") or "",
            ):
                gid = storey.GlobalId
                name = getattr(storey, "Name", None) or gid
                if gid in storey_elevations:
                    key = f"elev:{round(storey_elevations[gid], 2)}"
                    labels.setdefault(key, str(name))
                labels.setdefault(f"gid:{gid}", str(name))
        return labels

    def _cleanup_temp_paths(self) -> None:
        import os
        for path in self._temp_paths:
            try:
                os.unlink(path)
            except OSError:
                pass
        self._temp_paths.clear()
