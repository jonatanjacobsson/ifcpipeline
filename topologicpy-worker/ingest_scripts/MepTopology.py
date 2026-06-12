"""Extract MEP distribution system topology — flow connections, port connectivity.

Parses IfcDistributionSystem, IfcFlowSegment, IfcFlowFitting, IfcPort
relationships to build a graph of MEP connectivity.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Set

import ifcopenshell

from ingest_scripts import (
    Element,
    Ingester as _Base,
    Relationship,
    default_mep_system_types,
    ifc_schema,
    safe_by_type,
    safe_by_types,
)

_DEFAULT_SYSTEM_QUERY = "IfcDistributionSystem"


class Ingester(_Base):
    SCRIPT_NAME = "MepTopology"
    DESCRIPTION = "Extract MEP distribution system topology (flow connections, port connectivity)"

    def __init__(
        self,
        ifc_files: List[Path],
        log: logging.Logger,
        system_query: str = "IfcDistributionSystem",
        include_port_connections: bool = True,
        include_element_connections: bool = True,
    ):
        """Extract MEP distribution system topology from IFC models.

        Parses IfcDistributionSystem, IfcFlowSegment, IfcFlowFitting, and IfcPort
        relationships to build a graph of MEP connectivity. Discovers system
        memberships, port-to-port connections, and element-level flow links.

        :param system_query: IFC class to use for system discovery (e.g. IfcDistributionSystem or IfcSystem).
        :param include_port_connections: Whether to include IfcRelConnectsPorts-based connectivity edges.
        :param include_element_connections: Whether to include IfcRelConnectsElements-based flow edges between MEP components.
        """
        super().__init__(ifc_files, log)
        self.system_query = system_query
        self.include_port_connections = include_port_connections
        self.include_element_connections = include_element_connections

    def extract(self) -> None:
        t0 = time.time()
        total_systems = 0
        total_connections = 0
        seen_edges: Set[tuple] = set()
        schemas_seen: List[str] = []
        system_types_used: List[str] = []

        for ifc_path in self.ifc_files:
            self.log.info("mep: processing %s", ifc_path.name)
            ifc = ifcopenshell.open(str(ifc_path))
            schemas_seen.append(ifc_schema(ifc))

            if self.system_query != _DEFAULT_SYSTEM_QUERY:
                file_system_types = [self.system_query]
                systems = safe_by_type(ifc, self.system_query)
            else:
                file_system_types = default_mep_system_types(ifc)
                systems = safe_by_types(ifc, file_system_types)
            system_types_used.extend(file_system_types)

            for system in systems:
                sys_id = system.GlobalId
                sys_name = system.Name or ""
                total_systems += 1

                self._elements.append(Element(
                    global_id=sys_id,
                    ifc_class=system.is_a(),
                    name=sys_name,
                    extra={"source_file": ifc_path.name, "role": "system"},
                ))

                for rel in system.IsGroupedBy or []:
                    for member in rel.RelatedObjects or []:
                        if not hasattr(member, "GlobalId"):
                            continue
                        self._relationships.append(Relationship(
                            subject_global_id=member.GlobalId,
                            object_global_id=sys_id,
                            relationship_family="dependency",
                            relationship_type="member_of_system",
                            confidence=1.0,
                            source_kind="topologic_ingest_MepTopology",
                            evidence={"system_name": sys_name, "source_file": ifc_path.name},
                        ))

            if self.include_port_connections:
                ports = safe_by_type(ifc, "IfcDistributionPort")
                port_to_element: Dict[int, str] = {}
                for port in ports:
                    for rel in port.ContainedIn or []:
                        if hasattr(rel, "RelatingElement") and rel.RelatingElement:
                            port_to_element[port.id()] = rel.RelatingElement.GlobalId

                for rel in safe_by_type(ifc, "IfcRelConnectsPorts"):
                    port_a = rel.RelatingPort
                    port_b = rel.RelatedPort
                    if not port_a or not port_b:
                        continue

                    elem_a_id = port_to_element.get(port_a.id())
                    elem_b_id = port_to_element.get(port_b.id())
                    if not elem_a_id or not elem_b_id or elem_a_id == elem_b_id:
                        continue

                    edge_key = tuple(sorted([elem_a_id, elem_b_id]))
                    if edge_key in seen_edges:
                        continue
                    seen_edges.add(edge_key)

                    self._relationships.append(Relationship(
                        subject_global_id=elem_a_id,
                        object_global_id=elem_b_id,
                        relationship_family="dependency",
                        relationship_type="connected_port",
                        confidence=1.0,
                        source_kind="topologic_ingest_MepTopology",
                        evidence={"method": "ifc_port_connection", "source_file": ifc_path.name},
                    ))
                    total_connections += 1

            if self.include_element_connections:
                for rel in safe_by_type(ifc, "IfcRelConnectsElements"):
                    elem_a = rel.RelatingElement
                    elem_b = rel.RelatedElement
                    if not elem_a or not elem_b:
                        continue
                    a_id = elem_a.GlobalId
                    b_id = elem_b.GlobalId

                    is_mep = (
                        elem_a.is_a("IfcFlowSegment") or elem_a.is_a("IfcFlowFitting") or
                        elem_b.is_a("IfcFlowSegment") or elem_b.is_a("IfcFlowFitting")
                    )
                    if not is_mep:
                        continue

                    edge_key = tuple(sorted([a_id, b_id]))
                    if edge_key in seen_edges:
                        continue
                    seen_edges.add(edge_key)

                    self._relationships.append(Relationship(
                        subject_global_id=a_id,
                        object_global_id=b_id,
                        relationship_family="dependency",
                        relationship_type="flow_connected",
                        confidence=1.0,
                        source_kind="topologic_ingest_MepTopology",
                        evidence={"method": "ifc_rel_connects", "source_file": ifc_path.name},
                    ))
                    total_connections += 1

        elapsed = time.time() - t0
        unique_system_types = list(dict.fromkeys(system_types_used))
        self._summary = {
            "systems_found": total_systems,
            "connections_found": total_connections,
            "ifc_schema": schemas_seen[0] if len(schemas_seen) == 1 else schemas_seen,
            "system_types_used": unique_system_types,
            "elapsed_seconds": round(elapsed, 2),
        }
        self.log.info("mep: extracted %d systems, %d connections in %.1fs",
                      total_systems, total_connections, elapsed)
