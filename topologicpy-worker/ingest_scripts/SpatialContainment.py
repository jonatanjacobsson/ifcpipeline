"""Extract element-to-space spatial containment relationships.

Uses TopologicPy's Graph.ByIFCFile() to build a topology graph and
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

from ingest_scripts import Element, Ingester as _Base, Relationship

try:
    from topologicpy.Topology import Topology
    from topologicpy.Graph import Graph
    from topologicpy.Vertex import Vertex
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
    ):
        """Extract element-to-space spatial containment relationships.

        Uses TopologicPy's Graph.ByIFCFile() to build a topology graph and
        extract which building elements are contained in which spaces.
        Falls back to native IFC relationship parsing if TopologicPy is unavailable.

        :param element_query: IfcOpenShell selector query for target elements to classify.
        :param space_query: IfcOpenShell selector query for candidate space elements.
        :param tolerance: Containment tolerance in model units for TopologicPy graph construction.
        """
        super().__init__(ifc_files, log)
        self.element_query = element_query
        self.space_query = space_query
        self.tolerance = tolerance

    def extract(self) -> None:
        if not HAS_TOPOLOGICPY:
            self.log.warning("spatial: TopologicPy not available, falling back to IFC rel parsing")
            self._extract_from_ifc_rels()
            return

        t0 = time.time()
        matched = 0
        unmatched = 0

        for ifc_path in self.ifc_files:
            self.log.info("spatial: processing %s with TopologicPy", ifc_path.name)
            try:
                graph = Graph.ByIFCFile(str(ifc_path), tolerance=self.tolerance)
                if graph is None:
                    self.log.warning("spatial: Graph.ByIFCFile returned None for %s", ifc_path.name)
                    continue

                vertices = Graph.Vertices(graph)
                edges = Graph.Edges(graph)

                self.log.info("spatial: graph has %d vertices, %d edges", len(vertices), len(edges))

                for edge in edges:
                    sv = Graph.StartVertex(graph, edge)
                    ev = Graph.EndVertex(graph, edge)
                    s_dict = Topology.Dictionary(sv)
                    e_dict = Topology.Dictionary(ev)

                    s_id = s_dict.get("GlobalId", "") if s_dict else ""
                    e_id = e_dict.get("GlobalId", "") if e_dict else ""
                    s_class = s_dict.get("IfcClass", "") if s_dict else ""
                    e_class = e_dict.get("IfcClass", "") if e_dict else ""

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
        for ifc_path in self.ifc_files:
            self._extract_from_ifc_rels_file(ifc_path)

    def _extract_from_ifc_rels_file(self, ifc_path: Path) -> None:
        """Parse IfcRelContainedInSpatialStructure from a single file."""
        t0 = time.time()
        ifc = ifcopenshell.open(str(ifc_path))

        for rel in ifc.by_type("IfcRelContainedInSpatialStructure"):
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

        for rel in ifc.by_type("IfcRelAggregates"):
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
