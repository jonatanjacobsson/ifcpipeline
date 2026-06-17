"""Extract space measures and functional classifications as graph relationships.

Inspired by TopologicPy spatial graph notebooks — surfaces quantitative room
data (Qto base quantities, BIP labels) and Swedish functional categories such
as centralort, korridor, and trapphus as importable CDE edges.

Each measure uses a distinct ``relationship_type`` (e.g. ``measure_gross_floor_area``)
so relationship refs stay unique when the object is the containing storey.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import ifcopenshell.util.element

from ingest_scripts import Ingester as _Base, Relationship, safe_by_type

# (function_slug, patterns matched against long_name / space_name / reference)
_SPACE_FUNCTION_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    (
        "centralort",
        (
            r"ELCENTRAL",
            r"UNDERCENTRAL",
            r"FLÄKTRUM",
            r"FLAKTRUM",
            r"TEKNICENTRAL",
            r"EL\s*CENTRAL",
        ),
    ),
    ("korridor", (r"KORRIDOR",)),
    ("trapphus", (r"TRAPPHUS",)),
    ("hisshall", (r"HISSHALL", r"\bHIS\b")),
    ("schakt", (r"SCHAKT", r"SHAFT")),
    ("soprum", (r"SOPRUM",)),
    ("lokal", (r"\bLOKAL\b", r"COMMERCIAL")),
    ("bostad", (r"\bBOSTAD\b", r"ROK\b")),
)

# Qto / BIP measures exported as typed spatial relationships (object = storey).
_MEASURE_SPECS: Tuple[Tuple[str, str, str, str], ...] = (
    ("measure_gross_floor_area", "Qto_SpaceBaseQuantities", "GrossFloorArea", "m2"),
    ("measure_net_floor_area", "Qto_SpaceBaseQuantities", "NetFloorArea", "m2"),
    ("measure_gross_ceiling_area", "Qto_SpaceBaseQuantities", "GrossCeilingArea", "m2"),
    ("measure_height", "Qto_SpaceBaseQuantities", "Height", "m"),
    ("measure_gross_volume", "Qto_SpaceBaseQuantities", "GrossVolume", "m3"),
    ("measure_space_number", "BIP", "SpaceNumber", ""),
    ("measure_storey_name", "BIP", "StoreyName", ""),
    ("measure_space_name", "BIP", "SpaceName", ""),
    ("measure_apartment", "BIP", "Appartment", ""),
)


def classify_space_function(
    *,
    long_name: str = "",
    space_name: str = "",
    reference: str = "",
) -> Optional[str]:
    """Return a functional slug (e.g. centralort) when labels match known patterns."""
    haystack = " ".join(
        part.strip().upper()
        for part in (long_name, space_name, reference)
        if part and part.strip()
    )
    if not haystack:
        return None
    for slug, patterns in _SPACE_FUNCTION_RULES:
        for pattern in patterns:
            if re.search(pattern, haystack, flags=re.IGNORECASE):
                return slug
    return None


def _numeric_value(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    if hasattr(raw, "wrappedValue"):
        raw = raw.wrappedValue
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


class Ingester(_Base):
    SCRIPT_NAME = "ExtractSpaceAttributes"
    DESCRIPTION = (
        "Extract space measures (Qto/BIP) and functional classifications "
        "(centralort, korridor, trapphus, etc.) as graph relationships"
    )

    def __init__(
        self,
        ifc_files: List[Path],
        log: logging.Logger,
        include_measures: bool = True,
        include_space_functions: bool = True,
    ):
        """Extract quantitative and functional space attributes as relationships.

        Measures and classifications link each ``IfcSpace`` to its containing
        ``IfcBuildingStorey`` so both endpoints exist in the CDE projection.

        :param include_measures: Emit Qto base quantities and BIP SpaceNumber edges.
        :param include_space_functions: Emit ``classified_as_*`` edges for known room types.
        """
        super().__init__(ifc_files, log)
        self.include_measures = include_measures
        self.include_space_functions = include_space_functions

    def extract(self) -> None:
        t0 = time.time()
        spaces_processed = 0
        measure_edges = 0
        function_edges = 0
        functions_by_slug: Dict[str, int] = {}

        for ifc_path in self.ifc_files:
            self.log.info("space_attributes: opening %s", ifc_path.name)
            ifc = ifcopenshell.open(str(ifc_path))
            spaces = safe_by_type(ifc, "IfcSpace")
            self.log.info("space_attributes: found %d IfcSpace elements", len(spaces))

            for space in spaces:
                global_id = space.GlobalId
                name = (getattr(space, "Name", None) or "").strip()
                long_name = (getattr(space, "LongName", None) or "").strip()
                storey = self._get_storey(space)
                storey_id = getattr(storey, "GlobalId", None) if storey else None
                if not storey_id:
                    spaces_processed += 1
                    continue

                psets = ifcopenshell.util.element.get_psets(space, psets_only=False)
                reference = str((psets.get("Pset_SpaceCommon") or {}).get("Reference") or "")

                if self.include_space_functions:
                    function_slug = classify_space_function(
                        long_name=long_name,
                        space_name=name,
                        reference=reference,
                    )
                    if function_slug:
                        rel_type = f"classified_as_{function_slug}"
                        self._relationships.append(Relationship(
                            subject_global_id=global_id,
                            object_global_id=storey_id,
                            relationship_family="spatial",
                            relationship_type=rel_type,
                            confidence=1.0,
                            source_kind="topologic_ingest_ExtractSpaceAttributes",
                            evidence={
                                "function": function_slug,
                                "long_name": long_name,
                                "space_name": name,
                                "reference": reference,
                                "storey_name": getattr(storey, "Name", None) or "",
                                "source_file": ifc_path.name,
                            },
                        ))
                        function_edges += 1
                        functions_by_slug[function_slug] = functions_by_slug.get(function_slug, 0) + 1

                if self.include_measures:
                    for rel_type, pset_name, prop_name, unit in _MEASURE_SPECS:
                        props = psets.get(pset_name, {})
                        raw = props.get(prop_name)
                        if raw is None or raw == "":
                            continue
                        value = _numeric_value(raw)
                        if value is None and not isinstance(raw, str):
                            continue
                        self._relationships.append(Relationship(
                            subject_global_id=global_id,
                            object_global_id=storey_id,
                            relationship_family="spatial",
                            relationship_type=rel_type,
                            confidence=1.0,
                            source_kind="topologic_ingest_ExtractSpaceAttributes",
                            evidence={
                                "measure": prop_name,
                                "value": value if value is not None else str(raw),
                                "unit": unit,
                                "pset": pset_name,
                                "space_name": name,
                                "long_name": long_name,
                                "source_file": ifc_path.name,
                            },
                        ))
                        measure_edges += 1

                spaces_processed += 1

        elapsed = time.time() - t0
        self._summary = {
            "spaces_processed": spaces_processed,
            "measure_edges": measure_edges,
            "function_edges": function_edges,
            "functions_by_slug": functions_by_slug,
            "elapsed_seconds": round(elapsed, 2),
        }
        self.log.info(
            "space_attributes: %d spaces, %d measure edges, %d function edges in %.1fs",
            spaces_processed,
            measure_edges,
            function_edges,
            elapsed,
        )

    def _get_storey(self, space: Any) -> Any:
        try:
            for rel in space.Decomposes or []:
                parent = rel.RelatingObject
                if parent.is_a("IfcBuildingStorey"):
                    return parent
                if parent.is_a("IfcSpace"):
                    nested = self._get_storey(parent)
                    if nested is not None:
                        return nested
        except Exception:
            pass
        return None
