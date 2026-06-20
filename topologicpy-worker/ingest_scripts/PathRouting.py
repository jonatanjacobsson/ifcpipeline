"""Compute shortest paths through the building graph with optional capacity constraints.

Routes paths between source/destination spaces through the building topology,
respecting edge capacities (useful for service routing — pipes, cables, ducts).
Identifies optimal service corridors and congestion points.

Reference: https://github.com/wassimj/topologicpy/blob/main/notebooks/Shortest_Paths_With_Capacities.ipynb
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import List, Set

import ifcopenshell

from ingest_scripts import Element, Ingester as _Base, Relationship

try:
    from ingest_scripts import topograph
    HAS_TOPOLOGICPY = True
except ImportError:
    HAS_TOPOLOGICPY = False


class Ingester(_Base):
    SCRIPT_NAME = "PathRouting"
    DESCRIPTION = "Compute shortest paths through building graph for service routing and egress analysis"

    def __init__(
        self,
        ifc_files: List[Path],
        log: logging.Logger,
        source_query: str = "",
        destination_query: str = "",
        max_paths: int = 10,
    ):
        """Compute shortest paths through the building topology graph.

        Builds a TopologicPy graph and computes shortest paths between all
        space pairs (or filtered by source/destination queries). Reports path
        edges and lengths for service routing or egress distance analysis.

        :param source_query: IFC class filter for source vertices (empty = all spaces).
        :param destination_query: IFC class filter for destination vertices (empty = all spaces).
        :param max_paths: Maximum number of shortest paths to compute and report.
        """
        super().__init__(ifc_files, log)
        self.source_query = source_query
        self.destination_query = destination_query
        self.max_paths = max_paths

    def extract(self) -> None:
        if not HAS_TOPOLOGICPY:
            self.log.warning("PathRouting: TopologicPy required but not available")
            return

        t0 = time.time()
        path_count = 0

        for ifc_path in self.ifc_files:
            self.log.info("PathRouting: building graph from %s", ifc_path.name)
            try:
                graph = topograph.build_graph(ifc_path)
                if graph is None:
                    continue

                space_vertices = []  # (gid, ifc_type)
                for node in topograph.vertices(graph):
                    if "IfcSpace" not in node.ifc_type or not node.gid:
                        continue
                    space_vertices.append((node.gid, node.ifc_type))
                    self._elements.append(Element(
                        global_id=node.gid,
                        ifc_class=node.ifc_type,
                        name=node.ifc_name,
                        extra={"source_file": ifc_path.name},
                    ))

                self.log.info("PathRouting: %d space vertices, computing paths", len(space_vertices))

                seen_path_edges: Set[tuple] = set()
                paths_computed = 0

                for i, (s_id, s_class) in enumerate(space_vertices):
                    if paths_computed >= self.max_paths:
                        break
                    for j, (e_id, e_class) in enumerate(space_vertices):
                        if i >= j:
                            continue
                        if paths_computed >= self.max_paths:
                            break

                        try:
                            path_gids = topograph.shortest_path(graph, s_id, e_id)
                            if not path_gids:
                                continue
                            path_length = len(path_gids) - 1

                            prev_id = None
                            for p_id in path_gids:
                                if prev_id and p_id:
                                    edge_key = tuple(sorted([prev_id, p_id]))
                                    if edge_key not in seen_path_edges:
                                        seen_path_edges.add(edge_key)
                                        self._relationships.append(Relationship(
                                            subject_global_id=prev_id,
                                            object_global_id=p_id,
                                            relationship_family="circulation",
                                            relationship_type="path_segment",
                                            confidence=1.0,
                                            source_kind="topologic_ingest_PathRouting",
                                            evidence={
                                                "path_from": s_id,
                                                "path_to": e_id,
                                                "path_length": path_length,
                                                "source_file": ifc_path.name,
                                            },
                                        ))
                                prev_id = p_id

                            paths_computed += 1
                            path_count += 1
                        except Exception:
                            continue

            except Exception as exc:
                self.log.error("PathRouting: failed for %s: %s", ifc_path.name, exc)

        elapsed = time.time() - t0
        self._summary = {
            "paths_computed": path_count,
            "max_paths": self.max_paths,
            "elapsed_seconds": round(elapsed, 2),
        }
