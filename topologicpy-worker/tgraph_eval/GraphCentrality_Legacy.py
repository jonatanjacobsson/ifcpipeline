"""0.9.50-compatible legacy-Graph version of ingest_scripts/GraphCentrality.py.

The *shipped* `ingest_scripts/GraphCentrality.py` was written for topologicpy
0.9.43 and DOES NOT RUN on 0.9.50 — `Graph.ByIFCFile(transferDictionaries=True)`,
`Graph.StartVertex/EndVertex` and `Graph.CommunityDetection` were all removed,
and the centrality functions now return lists instead of graphs.

This module is the same ingest logic ported to the 0.9.50 legacy `Graph` API, so
`ingest_fidelity.py` can do a fair engine-vs-engine diff (Graph vs TGraph) where
*both* sides actually run on 0.9.50. The delta between this file and the shipped
script is exactly the migration the worker needs when it upgrades.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import List

from ingest_scripts import Element, Ingester as _Base, Relationship

try:
    from topologicpy.Graph import Graph
    from topologicpy.Topology import Topology
    from topologicpy.Dictionary import Dictionary
    from topologicpy.Edge import Edge
    HAS_TOPOLOGICPY = True
except ImportError:
    HAS_TOPOLOGICPY = False

GID_KEY = "IFC_global_id"


class Ingester(_Base):
    SCRIPT_NAME = "GraphCentrality_Legacy"
    DESCRIPTION = "0.9.50 legacy-Graph centrality (migrated from the shipped GraphCentrality)"

    def __init__(
        self,
        ifc_files: List[Path],
        log: logging.Logger,
        metric: str = "betweenness",
        normalized: bool = True,
    ):
        """Compute centrality metrics on the IFC spatial topology graph (legacy Graph, 0.9.50).

        :param metric: Centrality metric to compute: betweenness, closeness, degree, or all.
        :param normalized: Whether to normalize centrality values to [0, 1] range.
        """
        super().__init__(ifc_files, log)
        self.metric = metric
        self.normalized = normalized

    def extract(self) -> None:
        if not HAS_TOPOLOGICPY:
            self.log.warning("GraphCentrality_Legacy: TopologicPy required but not available")
            return

        t0 = time.time()

        for ifc_path in self.ifc_files:
            self.log.info("GraphCentrality_Legacy: building Graph from %s", ifc_path.name)
            try:
                # 0.9.50 signature (was transferDictionaries=True on 0.9.43).
                graph = Graph.ByIFCFile(
                    str(ifc_path), importMode="topology", dictionaryMode="basic", silent=True
                )
                if graph is None:
                    self.log.warning("GraphCentrality_Legacy: Graph.ByIFCFile returned None")
                    continue

                # 0.9.50: centrality returns a list AND stores values into vertex dicts.
                if self.metric in ("betweenness", "all"):
                    Graph.BetweennessCentrality(graph, key="betweenness_centrality",
                                                normalize=self.normalized, silent=True)
                if self.metric in ("closeness", "all"):
                    Graph.ClosenessCentrality(graph, key="closeness_centrality",
                                              normalize=self.normalized, silent=True)

                vertices = Graph.Vertices(graph)
                self.log.info("GraphCentrality_Legacy: %d vertices", len(vertices))

                for vertex in vertices:
                    d = Topology.Dictionary(vertex)
                    if not d:
                        continue
                    v_id = Dictionary.ValueAtKey(d, GID_KEY) or ""
                    if not v_id:
                        continue

                    m = {"degree": Graph.VertexDegree(graph, vertex)}
                    if self.metric in ("betweenness", "all"):
                        m["betweenness_centrality"] = Dictionary.ValueAtKey(d, "betweenness_centrality") or 0
                    if self.metric in ("closeness", "all"):
                        m["closeness_centrality"] = Dictionary.ValueAtKey(d, "closeness_centrality") or 0
                    if self.metric == "degree" and self.normalized and len(vertices) > 1:
                        m["degree_normalized"] = m["degree"] / (len(vertices) - 1)

                    self._elements.append(Element(
                        global_id=v_id,
                        ifc_class=Dictionary.ValueAtKey(d, "IFC_type") or "",
                        name=Dictionary.ValueAtKey(d, "IFC_name") or "",
                        extra={"source_file": ifc_path.name, **m},
                    ))

                for edge in Graph.Edges(graph):
                    sv = Edge.StartVertex(edge)
                    ev = Edge.EndVertex(edge)
                    s_d = Topology.Dictionary(sv) if sv else None
                    e_d = Topology.Dictionary(ev) if ev else None
                    s_id = Dictionary.ValueAtKey(s_d, GID_KEY) if s_d else ""
                    e_id = Dictionary.ValueAtKey(e_d, GID_KEY) if e_d else ""
                    if s_id and e_id:
                        self._relationships.append(Relationship(
                            subject_global_id=s_id,
                            object_global_id=e_id,
                            relationship_family="spatial",
                            relationship_type="topological_edge",
                            confidence=1.0,
                            source_kind="topologic_ingest_GraphCentrality_Legacy",
                            evidence={"source_file": ifc_path.name},
                        ))

            except Exception as exc:
                self.log.error("GraphCentrality_Legacy: failed for %s: %s", ifc_path.name, exc)

        elapsed = time.time() - t0
        self._summary = {
            "metric": self.metric,
            "normalized": self.normalized,
            "engine": "Graph",
            "elapsed_seconds": round(elapsed, 2),
        }
