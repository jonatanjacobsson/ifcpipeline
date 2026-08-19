"""SetPropertyBySelector: measure data types and numeric formatting.

The Nobel IDS requires BIP.Area as IFCAREAMEASURE, BIP.Volume as
IFCVOLUMEMEASURE and BIP.Length/Width/Height as IFCTEXT, while the source
BaseQuantities are floats. Before measure support the recipe rejected those
data types outright and flattened every measure to IfcReal.
"""

import logging
import sys
from pathlib import Path

import ifcopenshell
import ifcopenshell.guid
import pytest

WORKER_ROOT = Path(__file__).resolve().parent.parent
CUSTOM = WORKER_ROOT / "custom_recipes"
if str(CUSTOM) not in sys.path:
    sys.path.insert(0, str(CUSTOM))

from SetPropertyBySelector import Patcher  # noqa: E402

LOG = logging.getLogger("test")


def _make_ifc_with_base_quantities(length=5.3930839, area=2.835, volume=0.19845):
    ifc_file = ifcopenshell.file(schema="IFC4")
    wall = ifc_file.create_entity("IfcWall", GlobalId=ifcopenshell.guid.new(), Name="Wall")
    quantities = [
        ifc_file.create_entity("IfcQuantityLength", Name="Length", LengthValue=length),
        ifc_file.create_entity("IfcQuantityArea", Name="Area", AreaValue=area),
        ifc_file.create_entity("IfcQuantityVolume", Name="Volume", VolumeValue=volume),
    ]
    qto = ifc_file.create_entity(
        "IfcElementQuantity",
        GlobalId=ifcopenshell.guid.new(),
        Name="BaseQuantities",
        Quantities=tuple(quantities),
    )
    ifc_file.create_entity(
        "IfcRelDefinesByProperties",
        GlobalId=ifcopenshell.guid.new(),
        RelatedObjects=(wall,),
        RelatingPropertyDefinition=qto,
    )
    return ifc_file, wall


def _written(ifc_file, pset_name, property_name):
    """Return (ifc_type, value) of a written IfcPropertySingleValue, or None."""
    for pset in ifc_file.by_type("IfcPropertySet"):
        if pset.Name != pset_name:
            continue
        for prop in pset.HasProperties or []:
            if prop.Name == property_name and prop.NominalValue is not None:
                return prop.NominalValue.is_a(), prop.NominalValue.wrappedValue
    return None


def _run(ifc_file, *ops):
    patcher = Patcher(ifc_file, LOG, *ops)
    patcher.patch()
    return patcher


def test_area_is_written_as_area_measure():
    ifc_file, _ = _make_ifc_with_base_quantities()
    _run(ifc_file, '{"selector": "IfcWall", "property": "BIP.Area",'
                   ' "data_type": "IfcAreaMeasure", "from": "BaseQuantities.Area"}')
    assert _written(ifc_file, "BIP", "Area") == ("IfcAreaMeasure", 2.835)


def test_volume_is_written_as_volume_measure():
    ifc_file, _ = _make_ifc_with_base_quantities()
    _run(ifc_file, '{"selector": "IfcWall", "property": "BIP.Volume",'
                   ' "data_type": "IfcVolumeMeasure", "from": "BaseQuantities.Volume"}')
    assert _written(ifc_file, "BIP", "Volume") == ("IfcVolumeMeasure", 0.19845)


def test_length_to_text_in_millimetres():
    ifc_file, _ = _make_ifc_with_base_quantities()
    _run(ifc_file, '{"selector": "IfcWall", "property": "BIP.Length", "data_type": "IfcText",'
                   ' "from": "BaseQuantities.Length", "scale": 1000, "decimals": 0}')
    assert _written(ifc_file, "BIP", "Length") == ("IfcText", "5393")


def test_length_to_text_in_metres_with_decimals():
    ifc_file, _ = _make_ifc_with_base_quantities()
    _run(ifc_file, '{"selector": "IfcWall", "property": "BIP.Length", "data_type": "IfcText",'
                   ' "from": "BaseQuantities.Length", "decimals": 3}')
    assert _written(ifc_file, "BIP", "Length") == ("IfcText", "5.393")


def test_aliased_measure_type_is_accepted():
    ifc_file, _ = _make_ifc_with_base_quantities()
    _run(ifc_file, '{"selector": "IfcWall", "property": "BIP.Length",'
                   ' "data_type": "IfcPositiveLengthMeasure", "from": "BaseQuantities.Length"}')
    written = _written(ifc_file, "BIP", "Length")
    assert written is not None and written[0] == "IfcPositiveLengthMeasure"


@pytest.mark.parametrize("bad_type", ["IfcWall", "IfcValue", "IfcNotAType"])
def test_non_value_data_types_are_rejected(bad_type):
    """Entities, selects and unknown names are not writable NominalValues."""
    ifc_file, _ = _make_ifc_with_base_quantities()
    patcher = _run(ifc_file, '{"selector": "IfcWall", "property": "BIP.Length",'
                             ' "data_type": "%s", "from": "BaseQuantities.Length"}' % bad_type)
    assert patcher.stats["operations_total"] == 0
    assert _written(ifc_file, "BIP", "Length") is None


def test_omitted_data_type_still_infers_ifcreal():
    """Unchanged legacy behaviour: inference flattens measures to IfcReal."""
    ifc_file, _ = _make_ifc_with_base_quantities()
    _run(ifc_file, '{"selector": "IfcWall", "property": "BIP.Area",'
                   ' "from": "BaseQuantities.Area"}')
    assert _written(ifc_file, "BIP", "Area") == ("IfcReal", 2.835)


def test_malformed_numeric_format_skips_the_operation():
    ifc_file, _ = _make_ifc_with_base_quantities()
    patcher = _run(ifc_file, '{"selector": "IfcWall", "property": "BIP.Length", "data_type": "IfcText",'
                             ' "from": "BaseQuantities.Length", "decimals": "abc"}')
    assert patcher.stats["operations_total"] == 0


@pytest.mark.parametrize(
    "value,scale,decimals,expected",
    [
        (5.3930839, 1000, 0, 5393),
        (5.3930839, None, 3, 5.393),
        (5.3930839, 1000, None, 5393.0839),
        (5.3930839, None, None, 5.3930839),
        ("not-a-number", 1000, 0, "not-a-number"),
    ],
)
def test_apply_numeric_format(value, scale, decimals, expected):
    result = Patcher._apply_numeric_format(value, scale, decimals)
    if isinstance(expected, float):
        assert result == pytest.approx(expected)
    else:
        assert result == expected
