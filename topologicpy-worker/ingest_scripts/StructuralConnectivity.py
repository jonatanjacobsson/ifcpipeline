"""Extract structural connectivity graph — member connections, load paths.

Parses IfcStructuralMember, IfcRelConnectsStructuralMember, and physical
structural element adjacency to build a graph of structural connectivity.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Set

import ifcopenshell

from ingest_scripts import Element, Ingester as _Base, Relationship, safe_by_type

try:
    from topologicpy.Topology import Topology
    from topologicpy.Graph import Graph
    HAS_TOPOLOGICPY = True
except ImportError:
    HAS_TOPOLOGICPY = False


class Ingester(_Base):
    SCRIPT_NAME = "StructuralConnectivity"
    DESCRIPTION = "Extract structural connectivity graph (member connections, load paths)"

    def __init__(
        self,
        ifc_files: List[Path],
        log: logging.Logger,
        include_analytical: bool = True,
        include_physical: bool = True,
        structural_types: str = "IfcBeam,IfcColumn,IfcSlab,IfcWall,IfcFooting,IfcPile,IfcMember",
    ):
        """Extract structural connectivity graph from IFC models.

        Parses IfcStructuralMember, IfcRelConnectsStructuralMember, and physical
        structural element adjacency to build a graph of structural connectivity
        including analytical model connections and physical element-to-element links.

        :param include_analytical: Whether to extract IfcRelConnectsStructuralMember analytical model edges.
        :param include_physical: Whether to extract IfcRelConnectsElements physical connection edges.
        :param structural_types: Comma-separated list of IFC classes considered structural members.
        """
        super().__init__(ifc_files, log)
        self.include_analytical = include_analytical
        self.include_physical = include_physical
        self.structural_types = tuple(t.strip() for t in structural_types.split(","))

    def extract(self) -> None:
        t0 = time.time()
        total_members = 0
        total_connections = 0
        seen_edges: Set[tuple] = set()

        for ifc_path in self.ifc_files:
            self.log.info("structural: processing %s", ifc_path.name)
            ifc = ifcopenshell.open(str(ifc_path))

            for cls_name in self.structural_types:
                for elem in safe_by_type(ifc, cls_name):
                    self._elements.append(Element(
                        global_id=elem.GlobalId,
                        ifc_class=elem.is_a(),
                        name=elem.Name or "",
                        extra={"source_file": ifc_path.name},
                    ))
                    total_members += 1

            if self.include_analytical:
                connections = self._extract_analytical(ifc, ifc_path, seen_edges)
                total_connections += connections

            if self.include_physical:
                physical_connections = self._extract_physical_connections(ifc, ifc_path, seen_edges)
                total_connections += physical_connections

        elapsed = time.time() - t0
        self._summary = {
            "structural_members": total_members,
            "connections_found": total_connections,
            "method": "ifc_structural_rels",
            "elapsed_seconds": round(elapsed, 2),
        }
        self.log.info("structural: %d members, %d connections in %.1fs",
                      total_members, total_connections, elapsed)

    def _extract_analytical(self, ifc, ifc_path: Path, seen: Set[tuple]) -> int:
        """Extract from IfcRelConnectsStructuralMember (analytical model)."""
        count = 0
        for rel in safe_by_type(ifc, "IfcRelConnectsStructuralMember"):
            member = rel.RelatingStructuralMember
            connection = rel.RelatedStructuralConnection
            if not member or not connection:
                continue
            if not hasattr(member, "GlobalId") or not hasattr(connection, "GlobalId"):
                continue

            edge_key = tuple(sorted([member.GlobalId, connection.GlobalId]))
            if edge_key in seen:
                continue
            seen.add(edge_key)

            self._relationships.append(Relationship(
                subject_global_id=member.GlobalId,
                object_global_id=connection.GlobalId,
                relationship_family="dependency",
                relationship_type="structural_connection",
                confidence=1.0,
                source_kind="topologic_ingest_StructuralConnectivity",
                evidence={
                    "method": "ifc_analytical_model",
                    "connection_type": connection.is_a(),
                    "source_file": ifc_path.name,
                },
            ))
            count += 1
        return count

    def _extract_physical_connections(self, ifc, ifc_path: Path, seen: Set[tuple]) -> int:
        """Extract from IfcRelConnectsElements between structural members."""
        structural_types = set(self.structural_types) | {"IfcPlate", "IfcStairFlight"}
        count = 0

        for rel in safe_by_type(ifc, "IfcRelConnectsElements"):
            elem_a = rel.RelatingElement
            elem_b = rel.RelatedElement
            if not elem_a or not elem_b:
                continue
            if elem_a.is_a() not in structural_types and elem_b.is_a() not in structural_types:
                continue
            if not hasattr(elem_a, "GlobalId") or not hasattr(elem_b, "GlobalId"):
                continue

            edge_key = tuple(sorted([elem_a.GlobalId, elem_b.GlobalId]))
            if edge_key in seen:
                continue
            seen.add(edge_key)

            self._relationships.append(Relationship(
                subject_global_id=elem_a.GlobalId,
                object_global_id=elem_b.GlobalId,
                relationship_family="dependency",
                relationship_type="physically_connected",
                confidence=0.9,
                source_kind="topologic_ingest_StructuralConnectivity",
                evidence={
                    "method": "ifc_rel_connects",
                    "source_file": ifc_path.name,
                },
            ))
            count += 1
        return count
