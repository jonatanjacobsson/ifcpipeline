"""TGraph port of ingest_scripts/GraphCentrality.py.

Same `Ingester` contract and same output fields as the legacy script, but built
on topologicpy's new `TGraph` instead of `Graph`. Used by `ingest_fidelity.py`
to diff the relationship/element output of the two engines on the same model.

Mapping vs the legacy script:
  Graph.ByIFCFile(path, transferDictionaries=True)
      -> TGraph.ByIFCFile(path, importMode="topology", dictionaryMode="basic")
  Graph.Vertices(g) (topologic vertices, dict via Topology.Dictionary)
      -> TGraph.Vertices(tg) (dict records with a "dictionary" field)
  Graph.BetweennessCentrality(g, key="bc") returns a graph
      -> TGraph.BetweennessCentrality(tg, key=...) returns a list + stores in vertex dict
  Graph.Edges + StartVertex/EndVertex
      -> TGraph edge records carry "src"/"dst" vertex indices
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import List

from ingest_scripts import Element, Ingester as _Base, Relationship

try:
    from topologicpy.TGraph import TGraph
    HAS_TGRAPH = True
except ImportError:
    HAS_TGRAPH = False

GID_KEY = "IFC_global_id"
TYPE_KEY = "IFC_type"
NAME_KEY = "IFC_name"


class Ingester(_Base):
    SCRIPT_NAME = "GraphCentrality_TGraph"
    DESCRIPTION = "TGraph port: betweenness/closeness centrality and degree on the building spatial graph"

    def __init__(
        self,
        ifc_files: List[Path],
        log: logging.Logger,
        metric: str = "betweenness",
        normalized: bool = True,
    ):
        """Compute centrality metrics on the IFC spatial topology graph using TGraph.

        :param metric: Centrality metric to compute: betweenness, closeness, degree, or all.
        :param normalized: Whether to normalize centrality values to [0, 1] range.
        """
        super().__init__(ifc_files, log)
        self.metric = metric
        self.normalized = normalized

    def extract(self) -> None:
        if not HAS_TGRAPH:
            self.log.warning("GraphCentrality_TGraph: TGraph required but not available")
            return

        t0 = time.time()

        for ifc_path in self.ifc_files:
            self.log.info("GraphCentrality_TGraph: building TGraph from %s", ifc_path.name)
            try:
                graph = TGraph.ByIFCFile(
                    str(ifc_path), importMode="topology", dictionaryMode="basic", silent=True
                )
                if graph is None:
                    self.log.warning("GraphCentrality_TGraph: TGraph.ByIFCFile returned None")
                    continue

                # Compute requested centralities; each call stores values into the
                # vertex dictionaries (and also returns a list in vertex order).
                if self.metric in ("betweenness", "all"):
                    TGraph.BetweennessCentrality(
                        graph, key="betweenness_centrality",
                        normalize=self.normalized, silent=True,
                    )
                if self.metric in ("closeness", "all"):
                    TGraph.ClosenessCentrality(
                        graph, key="closeness_centrality",
                        normalize=self.normalized, silent=True,
                    )

                records = TGraph.Vertices(graph)
                idx2gid = {}
                self.log.info("GraphCentrality_TGraph: %d vertices", len(records))

                for r in records:
                    d = r.get("dictionary") or {}
                    idx = r.get("index")
                    v_id = d.get(GID_KEY) or ""
                    if idx is not None:
                        idx2gid[idx] = v_id
                    if not v_id:
                        continue

                    m = {}
                    try:
                        m["degree"] = TGraph.Degree(graph, idx, mode="all")
                    except Exception:
                        m["degree"] = TGraph.VertexDegree(graph, idx)
                    if self.metric in ("betweenness", "all"):
                        m["betweenness_centrality"] = d.get("betweenness_centrality", 0)
                    if self.metric in ("closeness", "all"):
                        m["closeness_centrality"] = d.get("closeness_centrality", 0)
                    if self.metric == "degree" and self.normalized and len(records) > 1:
                        m["degree_normalized"] = m["degree"] / (len(records) - 1)

                    self._elements.append(Element(
                        global_id=v_id,
                        ifc_class=d.get(TYPE_KEY) or "",
                        name=d.get(NAME_KEY) or "",
                        extra={"source_file": ifc_path.name, **m},
                    ))

                for e in _edge_records(graph):
                    s_id = idx2gid.get(e.get("src"))
                    e_id = idx2gid.get(e.get("dst"))
                    if s_id and e_id:
                        self._relationships.append(Relationship(
                            subject_global_id=s_id,
                            object_global_id=e_id,
                            relationship_family="spatial",
                            relationship_type="topological_edge",
                            confidence=1.0,
                            source_kind="topologic_ingest_GraphCentrality_TGraph",
                            evidence={"source_file": ifc_path.name},
                        ))

            except Exception as exc:
                self.log.error("GraphCentrality_TGraph: failed for %s: %s", ifc_path.name, exc)

        elapsed = time.time() - t0
        self._summary = {
            "metric": self.metric,
            "normalized": self.normalized,
            "engine": "TGraph",
            "elapsed_seconds": round(elapsed, 2),
        }


def _edge_records(graph) -> list:
    edges = TGraph.Edges(graph)
    if edges and isinstance(edges[0], dict) and "src" in edges[0]:
        return edges
    raw = getattr(graph, "_edges", None) or []
    return [e for e in raw if isinstance(e, dict) and e.get("active", True)]
