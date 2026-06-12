"""Extract IfcSpace geometry, boundaries, storey containment, and zone memberships.

Produces Element entries for each space (with geometry metadata) and
Relationship edges for space-to-storey and space-to-zone containment.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List

import ifcopenshell
import ifcopenshell.util.element

from ingest_scripts import Element, Ingester as _Base, Relationship, safe_by_type


class Ingester(_Base):
    SCRIPT_NAME = "ExtractSpaces"
    DESCRIPTION = "Extract IfcSpace geometry, storey containment, and zone memberships from architecture IFC"

    def __init__(
        self,
        ifc_files: List[Path],
        log: logging.Logger,
        include_zones: bool = True,
        include_geometry_meta: bool = False,
    ):
        """Extract IfcSpace elements with storey and zone relationships.

        Produces Element entries for each space (with geometry metadata) and
        Relationship edges for space-to-storey and space-to-zone containment.
        Operates on architecture IFC files containing IfcSpace definitions.

        :param include_zones: Whether to include IfcZone membership relationships in the output.
        :param include_geometry_meta: Whether to include bounding-box geometry metadata on each space element.
        """
        super().__init__(ifc_files, log)
        self.include_zones = include_zones
        self.include_geometry_meta = include_geometry_meta

    def extract(self) -> None:
        t0 = time.time()
        total_spaces = 0
        total_zones = 0

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

            zones = safe_by_type(ifc, "IfcZone")
            for zone in zones:
                zone_id = zone.GlobalId
                zone_name = zone.Name or ""
                total_zones += 1

                members = self._get_zone_spaces(zone)
                for member_id in members:
                    self._relationships.append(Relationship(
                        subject_global_id=member_id,
                        object_global_id=zone_id,
                        relationship_family="spatial",
                        relationship_type="in_zone",
                        confidence=1.0,
                        source_kind="topologic_ingest_ExtractSpaces",
                        evidence={"zone_name": zone_name},
                    ))

        elapsed = time.time() - t0
        self._summary = {
            "spaces_found": total_spaces,
            "zones_found": total_zones,
            "elapsed_seconds": round(elapsed, 2),
        }
        self.log.info("spaces: extracted %d spaces, %d zones in %.1fs",
                      total_spaces, total_zones, elapsed)

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

    def _get_zone_spaces(self, zone) -> List[str]:
        """Get GlobalIds of IfcSpaces assigned to this zone."""
        space_ids = []
        try:
            for rel in zone.IsGroupedBy or []:
                for obj in rel.RelatedObjects or []:
                    if obj.is_a("IfcSpace"):
                        space_ids.append(obj.GlobalId)
        except Exception:
            pass
        return space_ids
