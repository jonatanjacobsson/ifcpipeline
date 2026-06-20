"""Identify critical connections (bridges) and cut vertices in the building graph.

A bridge is an edge whose removal disconnects the graph — in a building, this
represents a critical circulation path (corridor, stairwell) that, if blocked,
isolates parts of the building. Cut vertices are nodes with the same property.
Essential for fire safety, accessibility compliance, and egress analysis.

Reference: https://github.com/wassimj/topologicpy/blob/main/notebooks/Graph_Bridges_Cuts.ipynb
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
    SCRIPT_NAME = "BridgesAndCuts"
    DESCRIPTION = "Identify critical connections (bridges) and cut vertices for egress/fire safety analysis"

    def __init__(
        self,
        ifc_files: List[Path],
        log: logging.Logger,
        include_bridges: bool = True,
        include_cut_vertices: bool = True,
    ):
        """Find bridges and cut vertices in the building topology graph.

        Bridges are edges that, if removed, disconnect the graph.
        Cut vertices are nodes that, if removed, disconnect the graph.
        Both indicate critical circulation points for fire safety and egress.

        :param include_bridges: Whether to identify and report bridge edges.
        :param include_cut_vertices: Whether to identify and report cut vertices (articulation points).
        """
        super().__init__(ifc_files, log)
        self.include_bridges = include_bridges
        self.include_cut_vertices = include_cut_vertices

    def extract(self) -> None:
        if not HAS_TOPOLOGICPY:
            self.log.warning("BridgesAndCuts: TopologicPy required but not available")
            return

        t0 = time.time()
        bridge_count = 0
        cut_count = 0

        for ifc_path in self.ifc_files:
            self.log.info("BridgesAndCuts: building graph from %s", ifc_path.name)
            try:
                graph = topograph.build_graph(ifc_path)
                if graph is None:
                    continue

                if self.include_bridges:
                    for s_id, e_id in topograph.bridges(graph):
                        self._relationships.append(Relationship(
                            subject_global_id=s_id,
                            object_global_id=e_id,
                            relationship_family="safety",
                            relationship_type="bridge_connection",
                            confidence=1.0,
                            source_kind="topologic_ingest_BridgesAndCuts",
                            evidence={"method": "graph_bridge_detection", "source_file": ifc_path.name},
                        ))
                        bridge_count += 1

                if self.include_cut_vertices:
                    for node in topograph.cut_vertices(graph):
                        if not node.gid:
                            continue
                        self._elements.append(Element(
                            global_id=node.gid,
                            ifc_class=node.ifc_type,
                            name=node.ifc_name,
                            extra={
                                "is_cut_vertex": True,
                                "criticality": "high",
                                "source_file": ifc_path.name,
                            },
                        ))
                        cut_count += 1

            except Exception as exc:
                self.log.error("BridgesAndCuts: failed for %s: %s", ifc_path.name, exc)

        elapsed = time.time() - t0
        self._summary = {
            "bridges_found": bridge_count,
            "cut_vertices_found": cut_count,
            "elapsed_seconds": round(elapsed, 2),
        }
        self.log.info("BridgesAndCuts: %d bridges, %d cut vertices in %.1fs",
                      bridge_count, cut_count, elapsed)
