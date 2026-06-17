"""Ingest element-to-space relationships from RoomStamp property sets.

Reads ``Pset_IfcPipelineRoomStamp`` (or a custom pset) written by the
topologicpy roomstamp job and emits ``contained_in_space`` graph edges.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, List, Set

import ifcopenshell.util.element

from ingest_scripts import Ingester as _Base, Relationship, safe_by_types

_DEFAULT_PSET = "Pset_IfcPipelineRoomStamp"
_SPACE_GUID_KEYS = ("SpaceGlobalId", "RoomGlobalId")
_STATUS_SKIP = frozenset({"", "Unmatched"})


class Ingester(_Base):
    SCRIPT_NAME = "ExtractRoomStamp"
    DESCRIPTION = (
        "Extract element-to-space containment from RoomStamp property sets "
        "written by the topologicpy roomstamp job"
    )

    def __init__(
        self,
        ifc_files: List[Path],
        log: logging.Logger,
        pset_name: str = _DEFAULT_PSET,
        element_query: str = "IfcElement",
        min_confidence: float = 0.0,
        include_ambiguous: bool = True,
    ):
        """Parse RoomStamp psets on building elements and link them to spaces.

        :param pset_name: Property set name produced by roomstamp (default Pset_IfcPipelineRoomStamp).
        :param element_query: IfcOpenShell selector for elements to scan.
        :param min_confidence: Skip matches below this SpatialMatchConfidence threshold.
        :param include_ambiguous: When false, skip Resolved/Proximity-only stamps.
        """
        super().__init__(ifc_files, log)
        self.pset_name = pset_name
        self.element_query = element_query
        self.min_confidence = float(min_confidence)
        self.include_ambiguous = include_ambiguous

    def extract(self) -> None:
        t0 = time.time()
        elements_scanned = 0
        stamped_elements = 0
        edges_created = 0
        skipped_unmatched = 0
        skipped_low_confidence = 0
        skipped_ambiguous = 0
        methods_seen: Set[str] = set()

        for ifc_path in self.ifc_files:
            self.log.info("roomstamp_ingest: opening %s", ifc_path.name)
            ifc = ifcopenshell.open(str(ifc_path))
            try:
                elements = list(ifcopenshell.util.selector.filter(ifc, self.element_query))
            except Exception:
                elements = safe_by_types(ifc, ["IfcElement"])

            self.log.info("roomstamp_ingest: scanning %d elements", len(elements))

            for element in elements:
                elements_scanned += 1
                global_id = getattr(element, "GlobalId", None)
                if not global_id:
                    continue

                props = self._read_stamp_props(element)
                if not props:
                    continue

                status = str(props.get("SpatialMatchStatus") or "").strip()
                if status in _STATUS_SKIP:
                    skipped_unmatched += 1
                    continue

                if not self.include_ambiguous and status not in ("Contained",):
                    skipped_ambiguous += 1
                    continue

                confidence = self._parse_confidence(props.get("SpatialMatchConfidence"))
                if confidence is not None and confidence < self.min_confidence:
                    skipped_low_confidence += 1
                    continue

                space_gid = self._space_guid(props)
                if not space_gid:
                    skipped_unmatched += 1
                    continue

                method = str(props.get("SpatialMatchMethod") or "")
                methods_seen.add(method or status)

                self._relationships.append(Relationship(
                    subject_global_id=global_id,
                    object_global_id=space_gid,
                    relationship_family="spatial",
                    relationship_type="contained_in_space",
                    confidence=confidence if confidence is not None else 0.9,
                    source_kind="topologic_ingest_ExtractRoomStamp",
                    evidence={
                        "spatial_match_status": status,
                        "spatial_match_method": method,
                        "spatial_match_confidence": confidence,
                        "space_name": props.get("SpaceName") or props.get("RoomName") or "",
                        "space_long_name": props.get("SpaceLongName") or props.get("RoomLongName") or "",
                        "building_storey_name": props.get("BuildingStoreyName") or "",
                        "zone_names": props.get("ZoneNames") or "",
                        "pset_name": self.pset_name,
                        "source_file": ifc_path.name,
                    },
                ))
                stamped_elements += 1
                edges_created += 1

        elapsed = time.time() - t0
        self._summary = {
            "elements_scanned": elements_scanned,
            "stamped_elements": stamped_elements,
            "contained_in_space_edges": edges_created,
            "skipped_unmatched": skipped_unmatched,
            "skipped_low_confidence": skipped_low_confidence,
            "skipped_ambiguous": skipped_ambiguous,
            "methods_seen": sorted(methods_seen),
            "pset_name": self.pset_name,
            "elapsed_seconds": round(elapsed, 2),
        }
        self.log.info(
            "roomstamp_ingest: %d/%d elements stamped, %d contained_in_space edges in %.1fs",
            stamped_elements,
            elements_scanned,
            edges_created,
            elapsed,
        )

    def _read_stamp_props(self, element: Any) -> dict[str, str]:
        try:
            psets = ifcopenshell.util.element.get_psets(element, psets_only=True)
        except Exception:
            return {}
        raw = psets.get(self.pset_name)
        if not isinstance(raw, dict):
            return {}
        return {str(k): "" if v is None else str(v) for k, v in raw.items()}

    @staticmethod
    def _space_guid(props: dict[str, str]) -> str:
        for key in _SPACE_GUID_KEYS:
            value = (props.get(key) or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _parse_confidence(raw: Any) -> float | None:
        if raw is None or raw == "":
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
