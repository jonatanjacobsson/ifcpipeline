"""Tests for RemoveElements host-aware opening/filling cleanup.

When a host element (e.g. a wall) is removed, the openings it hosts and the
void/fill relationships must be cleaned up, and any kept filling element
(e.g. a door) must keep its world position via a self-contained placement —
otherwise strict IFC importers (StreamBIM) drop the doors.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.placement

_RECIPE = Path(__file__).resolve().parents[1] / "custom_recipes" / "RemoveElements.py"


def _load_patcher():
    spec = importlib.util.spec_from_file_location("RemoveElements", _RECIPE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Patcher


def _model_with_wall_opening_door():
    ifc = ifcopenshell.file(schema="IFC4")
    project = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcProject", name="P")
    site = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcSite", name="Site")
    building = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcBuilding", name="B")
    storey = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcBuildingStorey", name="S")
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=project, products=[site])
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=site, products=[building])
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=building, products=[storey])

    wall = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcWall", name="HostWall")
    opening = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcOpeningElement", name="Opening")
    door = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcDoor", name="Door")
    ifcopenshell.api.run("spatial.assign_container", ifc, relating_structure=storey, products=[wall, door])

    # Build a placement chain: wall placed away from origin, opening relative to
    # the wall, door relative to the opening — so the door's world position
    # depends entirely on the wall's coordinate system.
    wall_m = np.eye(4); wall_m[:3, 3] = [10.0, 20.0, 0.0]
    ifcopenshell.api.run("geometry.edit_object_placement", ifc, product=wall, matrix=wall_m, is_si=True)
    op_m = np.eye(4); op_m[:3, 3] = [1.0, 0.0, 1.0]
    ifcopenshell.api.run("geometry.edit_object_placement", ifc, product=opening, matrix=op_m, is_si=True)
    ifcopenshell.api.run("void.add_opening", ifc, opening=opening, element=wall)
    door_m = np.eye(4); door_m[:3, 3] = [0.0, 0.0, 0.5]
    ifcopenshell.api.run("geometry.edit_object_placement", ifc, product=door, matrix=door_m, is_si=True)
    ifcopenshell.api.run("void.add_filling", ifc, opening=opening, element=door)

    return ifc, wall.GlobalId, door.GlobalId


def test_removing_host_wall_keeps_door_and_cleans_topology():
    ifc, wall_guid, door_guid = _model_with_wall_opening_door()
    door_world_before = ifcopenshell.util.placement.get_local_placement(
        ifc.by_guid(door_guid).ObjectPlacement
    )

    Patcher = _load_patcher()
    Patcher(ifc, query="IfcWall").patch()

    # Wall removed, door kept
    try:
        ifc.by_guid(wall_guid)
        raise AssertionError("expected host wall to be removed")
    except Exception:
        pass
    door = ifc.by_guid(door_guid)
    assert door is not None

    # No dangling void/fill relationships or orphaned openings remain
    assert ifc.by_type("IfcOpeningElement") == []
    assert ifc.by_type("IfcRelVoidsElement") == []
    assert ifc.by_type("IfcRelFillsElement") == []

    # No relationship left with a NULL mandatory Relating*/Related* reference
    for rel in ifc.by_type("IfcRelationship"):
        info = rel.get_info(recursive=False)
        for attr, val in info.items():
            if attr.startswith("Relating") or attr.startswith("Related"):
                assert val is not None, f"{rel.is_a()}.{attr} is NULL"

    # Door kept its exact world position and is now self-contained (absolute)
    assert door.ObjectPlacement is not None
    assert door.ObjectPlacement.PlacementRelTo is None
    door_world_after = ifcopenshell.util.placement.get_local_placement(door.ObjectPlacement)
    assert np.allclose(door_world_before, door_world_after, atol=1e-6)


def test_can_be_disabled():
    ifc, wall_guid, door_guid = _model_with_wall_opening_door()
    Patcher = _load_patcher()
    Patcher(ifc, query="IfcWall", fix_orphaned_fillings=False).patch()
    # With the fix disabled, the orphaned opening survives (legacy behaviour)
    assert ifc.by_type("IfcOpeningElement") != []
