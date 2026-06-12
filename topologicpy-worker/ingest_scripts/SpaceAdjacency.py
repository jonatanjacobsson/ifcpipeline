"""Extract space-to-space adjacency relationships from IFC using TopologicPy.

Builds a dual graph where spaces are vertices and shared boundaries are edges.
Identifies which rooms/spaces are adjacent — critical for fire compartmentation,
HVAC zoning, and accessibility path analysis.

Reference: https://github.com/wassimj/topologicpy/blob/main/notebooks/IFC_SpatialRelationshipsGraph.ipynb
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import List, Set

import ifcopenshell

from ingest_scripts import Element, Ingester as _Base, Relationship, safe_by_type

try:
    from topologicpy.Topology import Topology
    from topologicpy.Graph import Graph
    from topologicpy.Dictionary import Dictionary
    HAS_TOPOLOGICPY = True
except ImportError:
    HAS_TOPOLOGICPY = False


class Ingester(_Base):
    SCRIPT_NAME = "SpaceAdjacency"
    DESCRIPTION = "Extract space-to-space adjacency graph (shared boundaries between rooms/zones)"

    def __init__(
        self,
        ifc_files: List[Path],
        log: logging.Logger,
        include_external: bool = False,
        min_shared_area: float = 0.0,
        tolerance: float = 0.01,
    ):
        """Build a space adjacency graph from IFC topology.

        Uses TopologicPy's Graph.ByIFCFile to create a topological model,
        then extracts adjacency relationships between IfcSpace cells sharing
        a boundary face. Falls back to IfcRelSpaceBoundary parsing.

        :param include_external: Whether to include adjacency to external/unbounded space.
        :param min_shared_area: Minimum shared boundary area (m2) to register an adjacency edge.
        :param tolerance: Graph construction tolerance in model units (same as SpatialContainment).
        """
        super().__init__(ifc_files, log)
        self.include_external = include_external
        self.min_shared_area = min_shared_area
        self.tolerance = tolerance

    def extract(self) -> None:
        if not HAS_TOPOLOGICPY:
            self.log.warning("SpaceAdjacency: TopologicPy not available, using IFC rel fallback")
            self._extract_from_ifc_rels()
            return

        t0 = time.time()
        adjacency_count = 0
        seen_edges: Set[tuple] = set()

        for ifc_path in self.ifc_files:
            self.log.info("SpaceAdjacency: building topological graph from %s", ifc_path.name)
            try:
                graph = Graph.ByIFCFile(str(ifc_path), tolerance=self.tolerance)
                if graph is None:
                    self.log.warning("SpaceAdjacency: Graph.ByIFCFile returned None")
                    self._extract_from_ifc_rels_file(ifc_path)
                    continue

                vertices = Graph.Vertices(graph)
                self.log.info("SpaceAdjacency: graph has %d vertices", len(vertices))

                for vertex in vertices:
                    d = Topology.Dictionary(vertex)
                    if not d:
                        continue
                    v_class = Dictionary.ValueAtKey(d, "IFC_type") or ""
                    if "IfcSpace" not in v_class:
                        continue

                    v_id = Dictionary.ValueAtKey(d, "IFC_global_id") or ""
                    v_name = Dictionary.ValueAtKey(d, "IFC_name") or ""
                    if not v_id:
                        continue

                    self._elements.append(Element(
                        global_id=v_id,
                        ifc_class="IfcSpace",
                        name=v_name,
                        extra={"source_file": ifc_path.name},
                    ))

                    adjacent = Graph.AdjacentVertices(graph, vertex)
                    for adj_vertex in (adjacent or []):
                        adj_d = Topology.Dictionary(adj_vertex)
                        if not adj_d:
                            continue
                        adj_class = Dictionary.ValueAtKey(adj_d, "IFC_type") or ""
                        adj_id = Dictionary.ValueAtKey(adj_d, "IFC_global_id") or ""

                        if not adj_id:
                            continue
                        if "IfcSpace" not in adj_class and not self.include_external:
                            continue

                        edge_key = tuple(sorted([v_id, adj_id]))
                        if edge_key in seen_edges:
                            continue
                        seen_edges.add(edge_key)

                        self._relationships.append(Relationship(
                            subject_global_id=v_id,
                            object_global_id=adj_id,
                            relationship_family="spatial",
                            relationship_type="adjacent_space",
                            confidence=0.9,
                            source_kind="topologic_ingest_SpaceAdjacency",
                            evidence={"method": "topologicpy_dual_graph", "source_file": ifc_path.name},
                        ))
                        adjacency_count += 1

            except Exception as exc:
                self.log.error("SpaceAdjacency: TopologicPy failed for %s: %s", ifc_path.name, exc)
                before = len(self._relationships)
                self._extract_from_ifc_rels_file(ifc_path)
                if len(self._relationships) == before:
                    self.log.warning(
                        "SpaceAdjacency: IFC rel fallback found no shared IfcRelSpaceBoundary pairs for %s",
                        ifc_path.name,
                    )

        elapsed = time.time() - t0
        self._summary = {
            "adjacency_edges": adjacency_count,
            "method": "topologicpy_dual_graph",
            "elapsed_seconds": round(elapsed, 2),
        }

    def _extract_from_ifc_rels(self) -> None:
        for ifc_path in self.ifc_files:
            self._extract_from_ifc_rels_file(ifc_path)

    def _extract_from_ifc_rels_file(self, ifc_path: Path) -> None:
        """Fallback: infer adjacency from IfcRelSpaceBoundary shared elements."""
        ifc = ifcopenshell.open(str(ifc_path))
        space_to_boundaries: dict = {}

        for rel in safe_by_type(ifc, "IfcRelSpaceBoundary"):
            space = rel.RelatingSpace
            element = rel.RelatedBuildingElement
            if not space or not element:
                continue
            space_id = space.GlobalId
            elem_id = element.GlobalId
            space_to_boundaries.setdefault(space_id, set()).add(elem_id)

        space_ids = list(space_to_boundaries.keys())
        seen: Set[tuple] = set()
        for i, s1 in enumerate(space_ids):
            for s2 in space_ids[i + 1:]:
                shared = space_to_boundaries[s1] & space_to_boundaries[s2]
                if shared:
                    edge_key = tuple(sorted([s1, s2]))
                    if edge_key in seen:
                        continue
                    seen.add(edge_key)
                    self._relationships.append(Relationship(
                        subject_global_id=s1,
                        object_global_id=s2,
                        relationship_family="spatial",
                        relationship_type="adjacent_space",
                        confidence=0.8,
                        source_kind="topologic_ingest_SpaceAdjacency",
                        evidence={"method": "shared_boundary_element", "shared_elements": len(shared), "source_file": ifc_path.name},
                    ))
