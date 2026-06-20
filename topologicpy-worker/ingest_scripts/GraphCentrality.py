"""Compute graph centrality metrics on the building spatial graph.

Calculates betweenness centrality, closeness centrality, and degree for
each space/node in the building topology. Identifies circulation bottlenecks,
high-connectivity hubs, and isolated areas.

Built on the shared ``topograph`` TGraph adapter (supersedes legacy ``Graph``).

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
    from ingest_scripts import topograph
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

        Builds a TGraph from the IFC file, then computes the selected centrality
        metric for each vertex (space/element). Results are attached as element
        metadata for visualization in Graph Studio.

        Note: betweenness is O(V*E); on MEP/architecture models TGraph builds a
        much larger (decomposed) graph, so prefer ``closeness`` or ``degree``
        there, or pre-prune the graph.

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
                graph = topograph.build_graph(ifc_path)
                if graph is None:
                    self.log.warning("GraphCentrality: build_graph returned None")
                    continue

                nodes = topograph.vertices(graph)
                self.log.info(
                    "GraphCentrality: computing %s centrality for %d vertices", self.metric, len(nodes)
                )

                degrees = topograph.degree_map(graph)
                bc = topograph.betweenness(graph, normalize=self.normalized) \
                    if self.metric in ("betweenness", "all") else {}
                cc = topograph.closeness(graph, normalize=self.normalized) \
                    if self.metric in ("closeness", "all") else {}

                for node in nodes:
                    if not node.gid:
                        continue
                    m = {"degree": degrees.get(node.gid, 0)}
                    if self.metric in ("betweenness", "all"):
                        m["betweenness_centrality"] = bc.get(node.gid, 0)
                    if self.metric in ("closeness", "all"):
                        m["closeness_centrality"] = cc.get(node.gid, 0)
                    if self.metric == "degree" and self.normalized and len(nodes) > 1:
                        m["degree_normalized"] = m["degree"] / (len(nodes) - 1)

                    self._elements.append(Element(
                        global_id=node.gid,
                        ifc_class=node.ifc_type,
                        name=node.ifc_name,
                        extra={"source_file": ifc_path.name, **m},
                    ))

                for s_id, e_id in topograph.edges(graph):
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
