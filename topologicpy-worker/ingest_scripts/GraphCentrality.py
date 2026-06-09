"""Compute graph centrality metrics on the building spatial graph.

Calculates betweenness centrality, closeness centrality, and degree for
each space/node in the building topology. Identifies circulation bottlenecks,
high-connectivity hubs, and isolated areas.

Reference: https://github.com/wassimj/topologicpy/blob/main/notebooks/Betweenness_Centrality.ipynb
Reference: https://github.com/wassimj/topologicpy/blob/main/notebooks/pagerank.ipynb
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import List

import ifcopenshell

from ingest_scripts import Element, Ingester as _Base, Relationship

try:
    from topologicpy.Topology import Topology
    from topologicpy.Graph import Graph
    from topologicpy.Dictionary import Dictionary
    HAS_TOPOLOGICPY = True
except ImportError:
    HAS_TOPOLOGICPY = False


class Ingester(_Base):
    SCRIPT_NAME = "GraphCentrality"
    DESCRIPTION = "Compute betweenness/closeness centrality and degree on the building spatial graph"

    def __init__(
        self,
        ifc_files: List[Path],
        log: logging.Logger,
        metric: str = "betweenness",
        normalized: bool = True,
    ):
        """Compute centrality metrics on the IFC spatial topology graph.

        Builds a TopologicPy graph from the IFC file, then computes the
        selected centrality metric for each vertex (space/element). Results
        are attached as element metadata for visualization in Graph Studio.

        :param metric: Centrality metric to compute: betweenness, closeness, degree, or all.
        :param normalized: Whether to normalize centrality values to [0, 1] range.
        """
        super().__init__(ifc_files, log)
        self.metric = metric
        self.normalized = normalized

    def extract(self) -> None:
        if not HAS_TOPOLOGICPY:
            self.log.warning("GraphCentrality: TopologicPy required but not available")
            return

        t0 = time.time()

        for ifc_path in self.ifc_files:
            self.log.info("GraphCentrality: building graph from %s", ifc_path.name)
            try:
                graph = Graph.ByIFCFile(str(ifc_path), transferDictionaries=True)
                if graph is None:
                    self.log.warning("GraphCentrality: Graph.ByIFCFile returned None")
                    continue

                vertices = Graph.Vertices(graph)
                self.log.info("GraphCentrality: computing %s centrality for %d vertices", self.metric, len(vertices))

                metrics = self._compute_metrics(graph, vertices)

                for vertex, vertex_metrics in zip(vertices, metrics):
                    d = Topology.Dictionary(vertex)
                    if not d:
                        continue
                    v_id = Dictionary.ValueAtKey(d, "IFC_global_id") or ""
                    v_class = Dictionary.ValueAtKey(d, "IFC_type") or ""
                    v_name = Dictionary.ValueAtKey(d, "IFC_name") or ""
                    if not v_id:
                        continue

                    self._elements.append(Element(
                        global_id=v_id,
                        ifc_class=v_class,
                        name=v_name,
                        extra={
                            "source_file": ifc_path.name,
                            **vertex_metrics,
                        },
                    ))

                edges = Graph.Edges(graph)
                for edge in edges:
                    sv = Graph.StartVertex(graph, edge)
                    ev = Graph.EndVertex(graph, edge)
                    s_d = Topology.Dictionary(sv)
                    e_d = Topology.Dictionary(ev)
                    s_id = Dictionary.ValueAtKey(s_d, "IFC_global_id") if s_d else ""
                    e_id = Dictionary.ValueAtKey(e_d, "IFC_global_id") if e_d else ""
                    if s_id and e_id:
                        self._relationships.append(Relationship(
                            subject_global_id=s_id,
                            object_global_id=e_id,
                            relationship_family="spatial",
                            relationship_type="topological_edge",
                            confidence=1.0,
                            source_kind="topologic_ingest_GraphCentrality",
                            evidence={"source_file": ifc_path.name},
                        ))

            except Exception as exc:
                self.log.error("GraphCentrality: failed for %s: %s", ifc_path.name, exc)

        elapsed = time.time() - t0
        self._summary = {
            "metric": self.metric,
            "normalized": self.normalized,
            "elapsed_seconds": round(elapsed, 2),
        }

    def _compute_metrics(self, graph, vertices) -> list:
        """Compute centrality for each vertex."""
        results = []
        num_vertices = len(vertices)

        if self.metric in ("betweenness", "all"):
            bc_graph = Graph.BetweennessCentrality(graph, key="bc")
            bc_vertices = Graph.Vertices(bc_graph)
        if self.metric in ("closeness", "all"):
            cc_graph = Graph.ClosenessCentrality(graph, key="cc")
            cc_vertices = Graph.Vertices(cc_graph)

        for i, vertex in enumerate(vertices):
            m = {}
            degree = Graph.VertexDegree(graph, vertex)
            m["degree"] = degree

            if self.metric in ("betweenness", "all"):
                d = Topology.Dictionary(bc_vertices[i]) if i < len(bc_vertices) else None
                bc = Dictionary.ValueAtKey(d, "bc") if d else 0
                m["betweenness_centrality"] = bc

            if self.metric in ("closeness", "all"):
                d = Topology.Dictionary(cc_vertices[i]) if i < len(cc_vertices) else None
                cc = Dictionary.ValueAtKey(d, "cc") if d else 0
                m["closeness_centrality"] = cc

            if self.metric == "degree":
                if self.normalized and num_vertices > 1:
                    m["degree_normalized"] = degree / (num_vertices - 1)

            results.append(m)

        return results
