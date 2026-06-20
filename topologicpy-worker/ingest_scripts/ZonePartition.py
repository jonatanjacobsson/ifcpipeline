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
    from ingest_scripts import topograph
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
        max_zone_members: int = 100,
    ):
        """Partition the building graph into zones using community detection.

        Builds a TopologicPy graph from IFC and applies graph partitioning
        algorithms to group spaces into clusters. Each cluster represents a
        suggested zone (HVAC, fire compartment, management area).

        :param method: Partitioning algorithm: community (Louvain), edge_betweenness, or fiedler.
        :param num_partitions: Target number of partitions (0 = auto-detect optimal).
        :param max_zone_members: Zones with more members than this emit a star to a
            zone anchor (O(n) relationships) instead of all-pairs (O(n^2)). On the
            larger TGraph (0.9.50) graphs a single big community could otherwise emit
            hundreds of thousands of same_zone edges and overwhelm downstream ingest.
        """
        super().__init__(ifc_files, log)
        self.method = method
        self.num_partitions = num_partitions
        self.max_zone_members = max_zone_members

    def extract(self) -> None:
        if not HAS_TOPOLOGICPY:
            self.log.warning("ZonePartition: TopologicPy required but not available")
            return

        t0 = time.time()
        total_partitions = 0

        for ifc_path in self.ifc_files:
            self.log.info("ZonePartition: building graph from %s", ifc_path.name)
            try:
                graph = topograph.build_graph(ifc_path)
                if graph is None:
                    continue

                self.log.info("ZonePartition: applying %s partitioning", self.method)
                # community/edge_betweenness/fiedler -> {gid: partition_label}
                labels = topograph.community(
                    graph, method=self.method, num_partitions=self.num_partitions
                )
                if not labels:
                    self.log.warning("ZonePartition: partitioning returned no labels")
                    continue

                partition_groups: dict = {}

                for node in topograph.vertices(graph):
                    v_id = node.gid
                    if not v_id:
                        continue
                    partition_id = labels.get(v_id)
                    zone_label = f"zone_{partition_id}" if partition_id is not None else "unassigned"

                    self._elements.append(Element(
                        global_id=v_id,
                        ifc_class=node.ifc_type,
                        name=node.ifc_name,
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
                    star = len(members) > self.max_zone_members
                    if star:
                        # Bound the O(n^2) explosion on large communities: link every
                        # member to a single stable anchor instead of all-pairs.
                        anchor = members[0]
                        pairs = ((anchor, m) for m in members[1:])
                    else:
                        pairs = (
                            (members[i], m2)
                            for i in range(len(members))
                            for m2 in members[i + 1:]
                        )
                    for m1, m2 in pairs:
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
                                "zone_topology": "star" if star else "clique",
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
