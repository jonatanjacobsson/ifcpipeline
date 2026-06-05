import logging
import sys
from pathlib import Path

import ifcopenshell
import ifcopenshell.guid

WORKER_ROOT = Path(__file__).resolve().parent.parent
CUSTOM = WORKER_ROOT / "custom_recipes"
if str(CUSTOM) not in sys.path:
    sys.path.insert(0, str(CUSTOM))

from SetColorBySelector import Patcher  # noqa: E402


def _make_ifc_with_bip_bsabe(value: str = "640"):
    ifc_file = ifcopenshell.file(schema="IFC4")
    wall = ifc_file.create_entity("IfcWall", GlobalId=ifcopenshell.guid.new(), Name="Wall")
    prop = ifc_file.create_entity(
        "IfcPropertySingleValue",
        Name="BSABe",
        NominalValue=ifc_file.create_entity("IfcLabel", value),
        Unit=None,
    )
    pset = ifc_file.create_entity(
        "IfcPropertySet",
        GlobalId=ifcopenshell.guid.new(),
        Name="BIP",
        HasProperties=(prop,),
    )
    ifc_file.create_entity(
        "IfcRelDefinesByProperties",
        GlobalId=ifcopenshell.guid.new(),
        RelatedObjects=(wall,),
        RelatingPropertyDefinition=pset,
    )
    return ifc_file, wall


def test_simple_property_selector_falls_back_when_ifcopenshell_selector_fails(monkeypatch):
    ifc_file, wall = _make_ifc_with_bip_bsabe()
    patcher = Patcher(ifc_file, logging.getLogger(__name__))

    def fail_selector(*args, **kwargs):
        raise AttributeError("'entity_instance' object has no attribute 'is_a'")

    monkeypatch.setattr("ifcopenshell.util.selector.filter_elements", fail_selector)

    matched = patcher._select_elements("IfcElement, BIP.BSABe=640")

    assert matched == {wall}
