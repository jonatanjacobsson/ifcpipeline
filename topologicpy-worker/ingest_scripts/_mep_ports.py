"""MEP terminal / port / system lookups for ``SpaceInteractions``.

Shares the traversal RULES with ``MepTopology`` (``port.ContainedIn`` for the port owner,
``system.IsGroupedBy`` for membership) without importing it, so that script's measured
bench fingerprint stays untouched.

Ports are deliberately NOT the primary signal for "which room does this terminal serve":
on the real M1 model all 322 ``IfcFlowTerminal`` have zero ports reachable through
``IfcRelConnectsPortToElement`` -- the 11,663 ``IfcDistributionPort`` belong to segments
and fittings. A port-first rule returns nothing on the model that matters most.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ingest_scripts import default_mep_system_types, ifc_schema, safe_by_type, safe_by_types

# IFC2X3 has no IfcAirTerminal: every diffuser is an IfcFlowTerminal whose *type* object
# is an IfcAirTerminalType. Class alone is therefore not enough on these models.
_IFC2X3_TERMINAL_CLASSES = {
    "IfcFlowTerminal",
    "IfcElectricDistributionPoint",
}
_IFC4_TERMINAL_CLASSES = {
    "IfcAirTerminal", "IfcSanitaryTerminal", "IfcLightFixture", "IfcOutlet",
    "IfcElectricAppliance", "IfcSpaceHeater", "IfcFireSuppressionTerminal",
    "IfcCommunicationsAppliance", "IfcAudioVisualAppliance", "IfcMedicalDevice",
    "IfcFlowTerminal",
}
_TERMINAL_TYPE_SUFFIXES = (
    "TerminalType", "LightFixtureType", "OutletType", "SpaceHeaterType",
    "ElectricApplianceType", "MedicalDeviceType", "AudioVisualApplianceType",
    "CommunicationsApplianceType",
)


def terminal_classes(ifc) -> set:
    return set(_IFC2X3_TERMINAL_CLASSES if ifc_schema(ifc).upper() == "IFC2X3" else _IFC4_TERMINAL_CLASSES)


def type_name_of(entity) -> str:
    """IFC class of the entity's type object, or "" — schema-agnostic, no util import."""
    for rel in getattr(entity, "IsDefinedBy", []) or []:
        if rel.is_a("IfcRelDefinesByType"):
            type_object = getattr(rel, "RelatingType", None)
            if type_object is not None:
                return type_object.is_a()
    for rel in getattr(entity, "IsTypedBy", []) or []:
        type_object = getattr(rel, "RelatingType", None)
        if type_object is not None:
            return type_object.is_a()
    return ""


def is_terminal(entity, classes: set) -> bool:
    """A terminal is a class-allowlisted product, or one whose TYPE names it a terminal."""
    cls = entity.is_a()
    if cls in classes:
        return True
    type_name = type_name_of(entity)
    return bool(type_name) and type_name.endswith(_TERMINAL_TYPE_SUFFIXES)


def element_ports(ifc) -> Dict[str, List[Any]]:
    """{element GlobalId: [IfcDistributionPort]} via IFC2X3 ContainedIn and IFC4 Nests."""
    out: Dict[str, List[Any]] = {}
    for port in safe_by_type(ifc, "IfcDistributionPort"):
        owner = None
        for rel in getattr(port, "ContainedIn", []) or []:
            owner = getattr(rel, "RelatingElement", None)
            if owner is not None:
                break
        if owner is None:
            for rel in getattr(port, "Nests", []) or []:
                owner = getattr(rel, "RelatingObject", None)
                if owner is not None:
                    break
        gid = getattr(owner, "GlobalId", None)
        if gid:
            out.setdefault(str(gid), []).append(port)
    return out


def systems_by_member(ifc) -> Dict[str, List[Dict[str, str]]]:
    """{member GlobalId: [{globalId, name, ifcClass}]} from system.IsGroupedBy."""
    out: Dict[str, List[Dict[str, str]]] = {}
    for system in safe_by_types(ifc, default_mep_system_types(ifc)):
        sys_gid = getattr(system, "GlobalId", None)
        if not sys_gid:
            continue
        entry = {
            "globalId": str(sys_gid),
            "name": str(getattr(system, "Name", "") or ""),
            "ifcClass": system.is_a(),
        }
        for rel in getattr(system, "IsGroupedBy", []) or []:
            for member in getattr(rel, "RelatedObjects", []) or []:
                gid = getattr(member, "GlobalId", None)
                if gid:
                    out.setdefault(str(gid), []).append(entry)
    return out


def primary_system(systems: Optional[List[Dict[str, str]]]) -> Optional[Dict[str, str]]:
    """One deterministic system per element (lowest GlobalId) for the edge evidence."""
    if not systems:
        return None
    return sorted(systems, key=lambda s: s["globalId"])[0]
