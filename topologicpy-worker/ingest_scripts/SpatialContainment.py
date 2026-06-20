"""Extract element-to-space spatial containment relationships.

Uses TopologicPy's the TGraph topograph adapter to build a topology graph and
extract which building elements are contained in which spaces.
Operates on architecture IFC (spaces source) + target IFC(s) (elements).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List

import ifcopenshell
import ifcopenshell.geom

from ingest_scripts import Element, Ingester as _Base, Relationship, safe_by_type

try:
    from ingest_scripts import topograph
    HAS_TOPOLOGICPY = True
except ImportError:
    HAS_TOPOLOGICPY = False


class Ingester(_Base):
    SCRIPT_NAME = "SpatialContainment"
    DESCRIPTION = "Extract element-to-space containment using TopologicPy spatial graph analysis"

    def __init__(
        self,
        ifc_files: List[Path],
        log: logging.Logger,
        element_query: str = "IfcElement",
        space_query: str = "IfcSpace",
        tolerance: float = 0.01,
        use_topologic: bool = True,
        force_ifc_native: bool = False,
    ):
        """Extract element-to-space spatial containment relationships.

        Uses TopologicPy's the TGraph topograph adapter to build a topology graph and
        extract which building elements are contained in which spaces.
        Falls back to native IFC relationship parsing if TopologicPy is unavailable.

        :param element_query: IfcOpenShell selector query for target elements to classify.
        :param space_query: IfcOpenShell selector query for candidate space elements.
        :param tolerance: Containment tolerance in model units for TopologicPy graph construction.
        :param use_topologic: When False, skip Graph.ByIFCFile and use IFC native rels only.
        :param force_ifc_native: Internal retry flag after SIGSEGV (same as use_topologic=False).
        """
        super().__init__(ifc_files, log)
        self.element_query = element_query
        self.space_query = space_query
        self.tolerance = tolerance
        self.use_topologic = bool(use_topologic) and not bool(force_ifc_native)

    def extract(self) -> None:
        if not HAS_TOPOLOGICPY or not self.use_topologic:
            if not self.use_topologic:
                self.log.warning(
                    "spatial: use_topologic=False, using IFC rel parsing (SIGSEGV fallback or API flag)"
                )
            else:
                self.log.warning("spatial: TopologicPy not available, falling back to IFC rel parsing")
            self._extract_from_ifc_rels()
            return

        t0 = time.time()
        matched = 0
        unmatched = 0

        for ifc_path in self.ifc_files:
            self.log.info("spatial: processing %s with TopologicPy", ifc_path.name)
            try:
                graph = topograph.build_graph(ifc_path, tolerance=self.tolerance)
                if graph is None:
                    self.log.warning("spatial: build_graph returned None for %s", ifc_path.name)
                    continue

                pairs = topograph.edge_nodes(graph)
                self.log.info(
                    "spatial: graph has %d vertices, %d edges", topograph.order(graph), len(pairs)
                )

                for sv, ev in pairs:
                    s_id, e_id = sv.gid, ev.gid
                    s_class, e_class = sv.ifc_type, ev.ifc_type

                    if not s_id or not e_id:
                        continue

                    is_spatial_edge = (
                        ("IfcSpace" in s_class and "IfcSpace" not in e_class) or
                        ("IfcSpace" in e_class and "IfcSpace" not in s_class)
                    )

                    if is_spatial_edge:
                        if "IfcSpace" in s_class:
                            space_id, elem_id = s_id, e_id
                        else:
                            space_id, elem_id = e_id, s_id

                        self._relationships.append(Relationship(
                            subject_global_id=elem_id,
                            object_global_id=space_id,
                            relationship_family="spatial",
                            relationship_type="contained_in",
                            confidence=0.9,
                            source_kind="topologic_ingest_SpatialContainment",
                            evidence={"method": "topologicpy_graph", "source_file": ifc_path.name},
                        ))
                        matched += 1
                    else:
                        self._relationships.append(Relationship(
                            subject_global_id=s_id,
                            object_global_id=e_id,
                            relationship_family="spatial",
                            relationship_type="adjacent_to",
                            confidence=0.7,
                            source_kind="topologic_ingest_SpatialContainment",
                            evidence={"method": "topologicpy_graph", "source_file": ifc_path.name},
                        ))

            except Exception as exc:
                self.log.error("spatial: TopologicPy extraction failed for %s: %s", ifc_path.name, exc)
                self._extract_from_ifc_rels_file(ifc_path)

        elapsed = time.time() - t0
        self._summary = {
            "matched_containment": matched,
            "method": "topologicpy_graph",
            "elapsed_seconds": round(elapsed, 2),
        }

    def _extract_from_ifc_rels(self) -> None:
        """Fallback: parse native IFC spatial relationships."""
        t0 = time.time()
        for ifc_path in self.ifc_files:
            self._extract_from_ifc_rels_file(ifc_path)
        self._summary = {
            "matched_containment": len(self._relationships),
            "method": "ifc_native_rel",
            "elapsed_seconds": round(time.time() - t0, 2),
        }

    def _extract_from_ifc_rels_file(self, ifc_path: Path) -> None:
        """Parse IfcRelContainedInSpatialStructure from a single file."""
        t0 = time.time()
        ifc = ifcopenshell.open(str(ifc_path))

        for rel in safe_by_type(ifc, "IfcRelContainedInSpatialStructure"):
            space = rel.RelatingStructure
            if not space or not hasattr(space, "GlobalId"):
                continue
            space_id = space.GlobalId
            for elem in rel.RelatedElements or []:
                if not hasattr(elem, "GlobalId"):
                    continue
                self._relationships.append(Relationship(
                    subject_global_id=elem.GlobalId,
                    object_global_id=space_id,
                    relationship_family="spatial",
                    relationship_type="contained_in",
                    confidence=1.0,
                    source_kind="topologic_ingest_SpatialContainment",
                    evidence={"method": "ifc_native_rel", "source_file": ifc_path.name},
                ))

        for rel in safe_by_type(ifc, "IfcRelAggregates"):
            parent = rel.RelatingObject
            if not parent or not parent.is_a("IfcSpatialStructureElement"):
                continue
            for child in rel.RelatedObjects or []:
                if not hasattr(child, "GlobalId"):
                    continue
                self._relationships.append(Relationship(
                    subject_global_id=child.GlobalId,
                    object_global_id=parent.GlobalId,
                    relationship_family="spatial",
                    relationship_type="aggregated_in",
                    confidence=1.0,
                    source_kind="topologic_ingest_SpatialContainment",
                    evidence={"method": "ifc_native_rel", "source_file": ifc_path.name},
                ))

        elapsed = time.time() - t0
        self._summary["elapsed_seconds"] = self._summary.get("elapsed_seconds", 0) + round(elapsed, 2)
        self._summary["method"] = "ifc_native_rels"
