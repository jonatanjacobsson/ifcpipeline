"""Extract IfcSpace geometry, boundaries, storey containment, and zone memberships.

Produces Element entries for each space (with geometry metadata) and
Relationship edges for space-to-storey and space-to-zone containment.

When native ``IfcZone`` / ``IfcSpatialZone`` (Revit Areas) entities are absent,
apartment zones can be derived from BIP ``Appartment`` (Revit export spelling)
and matching aggregate ``IfcSpace`` names (e.g. ``2-1103``).
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import ifcopenshell
import ifcopenshell.util.element

from ingest_scripts import Element, Ingester as _Base, Relationship, safe_by_type

_APARTMENT_AGGREGATE_NAME_RE = re.compile(r"^\d+-\d+$")
_APARTMENT_ROOM_NAME_RE = re.compile(r"^(\d+-\d+)-\d+$")


def apartment_id_from_space(
    space: Any,
    *,
    pset_name: str = "BIP",
    property_name: str = "Appartment",
) -> Optional[str]:
    """Resolve apartment / zone id from BIP pset or ``{building}-{apt}-{room}`` name."""
    try:
        psets = ifcopenshell.util.element.get_psets(space, psets_only=True)
        props = psets.get(pset_name, {})
        for key in (property_name, "Apartment"):
            value = props.get(key)
            if value:
                return str(value).strip()
    except Exception:
        pass

    name = (getattr(space, "Name", None) or "").strip()
    room_match = _APARTMENT_ROOM_NAME_RE.match(name)
    if room_match:
        return room_match.group(1)
    if _APARTMENT_AGGREGATE_NAME_RE.match(name):
        return name
    return None


def apartment_aggregate_guids(spaces: List[Any]) -> Dict[str, str]:
    """Map apartment id -> aggregate IfcSpace GlobalId (Name is ``building-apartment``)."""
    anchors: Dict[str, str] = {}
    for space in spaces:
        name = (getattr(space, "Name", None) or "").strip()
        if not _APARTMENT_AGGREGATE_NAME_RE.match(name):
            continue
        global_id = getattr(space, "GlobalId", None)
        if global_id:
            anchors[name] = global_id
    return anchors


def group_member_space_guids(group: Any) -> List[str]:
    """GlobalIds of IfcSpace members assigned to an IfcZone / IfcSpatialZone / IfcGroup."""
    space_ids: List[str] = []
    seen: Set[str] = set()
    try:
        for rel in group.IsGroupedBy or []:
            if not rel.is_a("IfcRelAssignsToGroup"):
                continue
            for obj in rel.RelatedObjects or []:
                if not obj.is_a("IfcSpace"):
                    continue
                gid = getattr(obj, "GlobalId", None)
                if gid and gid not in seen:
                    seen.add(gid)
                    space_ids.append(gid)
    except Exception:
        pass
    return space_ids


def is_apartment_room_space(space: Any) -> bool:
    """True for room-level spaces, not apartment aggregate anchors."""
    name = (getattr(space, "Name", None) or "").strip()
    if _APARTMENT_AGGREGATE_NAME_RE.match(name):
        return False
    if _APARTMENT_ROOM_NAME_RE.match(name):
        return True
    return apartment_id_from_space(space) is not None


class Ingester(_Base):
    SCRIPT_NAME = "ExtractSpaces"
    DESCRIPTION = "Extract IfcSpace geometry, storey containment, and zone memberships from architecture IFC"

    def __init__(
        self,
        ifc_files: List[Path],
        log: logging.Logger,
        include_zones: bool = True,
        include_spatial_zones: bool = True,
        include_apartment_zones: bool = True,
        apartment_pset: str = "BIP",
        apartment_property: str = "Appartment",
        include_geometry_meta: bool = False,
    ):
        """Extract IfcSpace elements with storey and zone relationships.

        Produces Element entries for each space (with geometry metadata) and
        Relationship edges for space-to-storey and space-to-zone containment.
        Operates on architecture IFC files containing IfcSpace definitions.

        :param include_zones: Whether to include native IfcZone membership relationships.
        :param include_spatial_zones: Whether to include IfcSpatialZone (Revit Area) memberships.
        :param include_apartment_zones: Derive apartment zones from BIP Appartment codes
            and aggregate IfcSpace anchors when native zone entities are absent.
        :param apartment_pset: Property set containing the apartment / zone id.
        :param apartment_property: Property name for the apartment id (Revit BIP uses Appartment).
        :param include_geometry_meta: Whether to include bounding-box geometry metadata on each space element.
        """
        super().__init__(ifc_files, log)
        self.include_zones = include_zones
        self.include_spatial_zones = include_spatial_zones
        self.include_apartment_zones = include_apartment_zones
        self.apartment_pset = apartment_pset
        self.apartment_property = apartment_property
        self.include_geometry_meta = include_geometry_meta

    def extract(self) -> None:
        t0 = time.time()
        total_spaces = 0
        ifc_zones = 0
        spatial_zones = 0
        ifc_zone_edges = 0
        spatial_zone_edges = 0
        apartment_zones = 0
        apartment_zone_edges = 0
        apartment_edges_skipped = 0

        for ifc_path in self.ifc_files:
            self.log.info("spaces: opening %s", ifc_path.name)
            ifc = ifcopenshell.open(str(ifc_path))
            spaces = safe_by_type(ifc, "IfcSpace")
            self.log.info("spaces: found %d IfcSpace elements", len(spaces))

            for space in spaces:
                global_id = space.GlobalId
                name = space.Name or ""
                long_name = space.LongName or ""

                storey = self._get_storey(space)
                storey_id = storey.GlobalId if storey else None
                storey_name = storey.Name if storey else ""

                self._elements.append(Element(
                    global_id=global_id,
                    ifc_class="IfcSpace",
                    name=name,
                    storey=storey_name,
                    extra={
                        "long_name": long_name,
                        "storey_global_id": storey_id,
                        "source_file": ifc_path.name,
                    },
                ))

                if storey_id:
                    self._relationships.append(Relationship(
                        subject_global_id=global_id,
                        object_global_id=storey_id,
                        relationship_family="spatial",
                        relationship_type="contained_in_storey",
                        confidence=1.0,
                        source_kind="topologic_ingest_ExtractSpaces",
                        evidence={"storey_name": storey_name},
                    ))

                total_spaces += 1

            if self.include_zones:
                z_count, e_count = self._extract_native_group_zones(
                    ifc,
                    type_name="IfcZone",
                    ifc_class="IfcZone",
                    zone_kind="ifc_zone",
                    source_file=ifc_path.name,
                )
                ifc_zones += z_count
                ifc_zone_edges += e_count

            if self.include_spatial_zones:
                z_count, e_count = self._extract_native_group_zones(
                    ifc,
                    type_name="IfcSpatialZone",
                    ifc_class="IfcSpatialZone",
                    zone_kind="spatial_zone",
                    source_file=ifc_path.name,
                )
                spatial_zones += z_count
                spatial_zone_edges += e_count

            if self.include_apartment_zones:
                apt_z, apt_e, apt_skip = self._extract_apartment_zones(
                    spaces,
                    source_file=ifc_path.name,
                )
                apartment_zones += apt_z
                apartment_zone_edges += apt_e
                apartment_edges_skipped += apt_skip

        elapsed = time.time() - t0
        native_zones = ifc_zones + spatial_zones
        native_zone_edges = ifc_zone_edges + spatial_zone_edges
        zones_found = native_zones + apartment_zones
        self._summary = {
            "spaces_found": total_spaces,
            "zones_found": zones_found,
            "native_zones_found": native_zones,
            "ifc_zones_found": ifc_zones,
            "spatial_zones_found": spatial_zones,
            "ifc_zone_edges": ifc_zone_edges,
            "spatial_zone_edges": spatial_zone_edges,
            "apartment_zones_found": apartment_zones,
            "apartment_zone_edges": apartment_zone_edges,
            "apartment_zone_edges_skipped": apartment_edges_skipped,
            "elapsed_seconds": round(elapsed, 2),
        }
        self.log.info(
            "spaces: extracted %d spaces, %d IfcZone, %d IfcSpatialZone, "
            "%d apartment zones, %d native + %d apartment in_zone edges in %.1fs",
            total_spaces,
            ifc_zones,
            spatial_zones,
            apartment_zones,
            native_zone_edges,
            apartment_zone_edges,
            elapsed,
        )

    def _extract_native_group_zones(
        self,
        ifc: Any,
        *,
        type_name: str,
        ifc_class: str,
        zone_kind: str,
        source_file: str,
    ) -> Tuple[int, int]:
        """Extract IfcZone / IfcSpatialZone memberships via IfcRelAssignsToGroup."""
        zones = safe_by_type(ifc, type_name)
        if not zones:
            return 0, 0

        edge_count = 0
        for zone in zones:
            zone_id = zone.GlobalId
            zone_name = zone.Name or ""
            long_name = getattr(zone, "LongName", None) or ""
            predefined = getattr(zone, "PredefinedType", None)
            if predefined is not None and hasattr(predefined, "name"):
                predefined = predefined.name

            self._elements.append(Element(
                global_id=zone_id,
                ifc_class=ifc_class,
                name=zone_name,
                extra={
                    "long_name": long_name,
                    "predefined_type": predefined,
                    "zone_kind": zone_kind,
                    "source_file": source_file,
                },
            ))

            members = group_member_space_guids(zone)
            for member_id in members:
                self._relationships.append(Relationship(
                    subject_global_id=member_id,
                    object_global_id=zone_id,
                    relationship_family="spatial",
                    relationship_type="in_zone",
                    confidence=1.0,
                    source_kind="topologic_ingest_ExtractSpaces",
                    evidence={
                        "zone_name": zone_name,
                        "zone_kind": zone_kind,
                        "long_name": long_name,
                        "predefined_type": predefined,
                        "source_file": source_file,
                    },
                ))
                edge_count += 1

        self.log.info(
            "spaces: %s %d zones, %d in_zone edges",
            type_name,
            len(zones),
            edge_count,
        )
        return len(zones), edge_count

    def _extract_apartment_zones(
        self,
        spaces: List[Any],
        *,
        source_file: str,
    ) -> Tuple[int, int, int]:
        """Derive in_zone edges from BIP apartment codes to aggregate space anchors."""
        anchors = apartment_aggregate_guids(spaces)
        zones_used: Set[str] = set()
        edge_count = 0
        skipped = 0

        for space in spaces:
            if not is_apartment_room_space(space):
                continue

            apartment_id = apartment_id_from_space(
                space,
                pset_name=self.apartment_pset,
                property_name=self.apartment_property,
            )
            if not apartment_id:
                skipped += 1
                continue

            zone_gid = anchors.get(apartment_id)
            if not zone_gid:
                self.log.debug(
                    "spaces: no aggregate anchor for apartment %s (space %s)",
                    apartment_id,
                    getattr(space, "GlobalId", ""),
                )
                skipped += 1
                continue

            zones_used.add(apartment_id)
            long_name = getattr(space, "LongName", None) or ""
            self._relationships.append(Relationship(
                subject_global_id=space.GlobalId,
                object_global_id=zone_gid,
                relationship_family="spatial",
                relationship_type="in_zone",
                confidence=1.0,
                source_kind="topologic_ingest_ExtractSpaces",
                evidence={
                    "zone_name": apartment_id,
                    "zone_kind": "apartment",
                    "apartment_id": apartment_id,
                    "space_name": getattr(space, "Name", None) or "",
                    "long_name": long_name,
                    "source_pset": self.apartment_pset,
                    "source_property": self.apartment_property,
                    "source_file": source_file,
                },
            ))
            edge_count += 1

        if zones_used:
            self.log.info(
                "spaces: apartment zones %d anchors, %d in_zone edges (%d skipped)",
                len(zones_used),
                edge_count,
                skipped,
            )

        return len(zones_used), edge_count, skipped

    def _get_storey(self, space) -> Any:
        """Walk spatial decomposition to find containing IfcBuildingStorey."""
        try:
            decomposes = space.Decomposes
            if decomposes:
                for rel in decomposes:
                    parent = rel.RelatingObject
                    if parent.is_a("IfcBuildingStorey"):
                        return parent
                    if parent.is_a("IfcSpace"):
                        return self._get_storey(parent)
        except Exception:
            pass
        return None

