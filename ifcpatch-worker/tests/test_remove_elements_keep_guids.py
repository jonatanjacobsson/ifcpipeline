"""Tests for RemoveElements KEEP_GUIDS query mode."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import ifcopenshell
import ifcopenshell.api

_RECIPE = Path(__file__).resolve().parents[1] / "custom_recipes" / "RemoveElements.py"


def _load_patcher():
    spec = importlib.util.spec_from_file_location("RemoveElements", _RECIPE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Patcher


def _minimal_ifc() -> ifcopenshell.file:
    ifc = ifcopenshell.file(schema="IFC4")
    project = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcProject", name="P")
    site = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcSite", name="Site")
    building = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcBuilding", name="B")
    storey = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcBuildingStorey", name="S")
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=project, products=[site])
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=site, products=[building])
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=building, products=[storey])
    keep = ifcopenshell.api.run(
        "root.create_entity", ifc, ifc_class="IfcWall", name="KeepMe"
    )
    drop = ifcopenshell.api.run(
        "root.create_entity", ifc, ifc_class="IfcWall", name="DropMe"
    )
    ifcopenshell.api.run(
        "spatial.assign_container",
        ifc,
        relating_structure=storey,
        products=[keep, drop],
    )
    return ifc, keep.GlobalId, drop.GlobalId


def test_keep_guids_removes_non_listed_products():
    ifc, keep_guid, drop_guid = _minimal_ifc()
    Patcher = _load_patcher()
    Patcher(ifc, query=f"KEEP_GUIDS:{keep_guid}").patch()
    assert ifc.by_guid(keep_guid)
    try:
        ifc.by_guid(drop_guid)
        raise AssertionError("expected DropMe to be removed")
    except Exception:
        pass
