"""Partition the building graph into zones using community detection algorithms.

Uses graph partitioning (Louvain community detection) to automatically group
spaces into logical zones based on connectivity. Useful for HVAC zoning,
fire compartment optimization, and facility management area assignment.

Reference: https://github.com/wassimj/topologicpy/blob/main/notebooks/Graph_Partition.ipynb
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
    SCRIPT_NAME = "ZonePartition"
    DESCRIPTION = "Partition building spaces into zones via community detection (Louvain/edge betweenness)"

    def __init__(
        self,
        ifc_files: List[Path],
        log: logging.Logger,
        method: str = "community",
        num_partitions: int = 0,
    ):
        """Partition the building graph into zones using community detection.

        Builds a TopologicPy graph from IFC and applies graph partitioning
        algorithms to group spaces into clusters. Each cluster represents a
        suggested zone (HVAC, fire compartment, management area).

        :param method: Partitioning algorithm: community (Louvain), edge_betweenness, or fiedler.
        :param num_partitions: Target number of partitions (0 = auto-detect optimal).
        """
        super().__init__(ifc_files, log)
        self.method = method
        self.num_partitions = num_partitions

    def extract(self) -> None:
        if not HAS_TOPOLOGICPY:
            self.log.warning("ZonePartition: TopologicPy required but not available")
            return

        t0 = time.time()
        total_partitions = 0

        for ifc_path in self.ifc_files:
            self.log.info("ZonePartition: building graph from %s", ifc_path.name)
            try:
                graph = Graph.ByIFCFile(str(ifc_path), transferDictionaries=True)
                if graph is None:
                    continue

                self.log.info("ZonePartition: applying %s partitioning", self.method)

                if self.method == "community":
                    partitioned = Graph.CommunityDetection(graph, key="partition")
                elif self.method == "edge_betweenness":
                    partitioned = Graph.EdgeBetweennessPartition(
                        graph,
                        numPartitions=self.num_partitions if self.num_partitions > 0 else None,
                        key="partition",
                    )
                elif self.method == "fiedler":
                    partitioned = Graph.FiedlerPartition(graph, key="partition")
                else:
                    self.log.warning("ZonePartition: unknown method %s, using community", self.method)
                    partitioned = Graph.CommunityDetection(graph, key="partition")

                if partitioned is None:
                    self.log.warning("ZonePartition: partitioning returned None")
                    continue

                vertices = Graph.Vertices(partitioned)
                partition_groups: dict = {}

                for vertex in vertices:
                    d = Topology.Dictionary(vertex)
                    if not d:
                        continue
                    v_id = Dictionary.ValueAtKey(d, "IFC_global_id") or ""
                    v_class = Dictionary.ValueAtKey(d, "IFC_type") or ""
                    v_name = Dictionary.ValueAtKey(d, "IFC_name") or ""
                    partition_id = Dictionary.ValueAtKey(d, "partition")

                    if not v_id:
                        continue

                    zone_label = f"zone_{partition_id}" if partition_id is not None else "unassigned"

                    self._elements.append(Element(
                        global_id=v_id,
                        ifc_class=v_class,
                        name=v_name,
                        extra={
                            "partition": partition_id,
                            "zone_label": zone_label,
                            "source_file": ifc_path.name,
                        },
                    ))

                    if partition_id is not None:
                        partition_groups.setdefault(partition_id, []).append(v_id)

                total_partitions = len(partition_groups)
                self.log.info("ZonePartition: found %d partitions", total_partitions)

                for zone_id, members in partition_groups.items():
                    for i, m1 in enumerate(members):
                        for m2 in members[i + 1:]:
                            self._relationships.append(Relationship(
                                subject_global_id=m1,
                                object_global_id=m2,
                                relationship_family="grouping",
                                relationship_type="same_zone",
                                confidence=0.8,
                                source_kind="topologic_ingest_ZonePartition",
                                evidence={
                                    "zone_id": zone_id,
                                    "method": self.method,
                                    "source_file": ifc_path.name,
                                },
                            ))

            except Exception as exc:
                self.log.error("ZonePartition: failed for %s: %s", ifc_path.name, exc)

        elapsed = time.time() - t0
        self._summary = {
            "method": self.method,
            "partitions_found": total_partitions,
            "elapsed_seconds": round(elapsed, 2),
        }
