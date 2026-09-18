import json
import logging
import os
import sys
from collections import Counter
from pathlib import Path

import ifcopenshell
import ifcopenshell.guid
import ifcopenshell.util.element
import pytest

WORKER_ROOT = Path(__file__).resolve().parent.parent
CUSTOM = WORKER_ROOT / "custom_recipes"
if str(CUSTOM) not in sys.path:
    sys.path.insert(0, str(CUSTOM))

from SetColorBySelector import Patcher  # noqa: E402
from SetAttributeBySelector import Patcher as AttributePatcher  # noqa: E402

M1_PATH = Path(os.environ.get("TEST_M1_IFC", "/tmp/M1-570-MM-Complete_model.ifc"))
M1_GUID = "3Mh0VWV5f2GxWAJIhp2UhR"
F1_YELLOW = "D8BE07"
U1_BLUE = "2B54BF"


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


def _owner_history(ifc):
    person = ifc.create_entity("IfcPerson", FamilyName="Test")
    org = ifc.create_entity("IfcOrganization", Name="TestOrg")
    pao = ifc.create_entity("IfcPersonAndOrganization", ThePerson=person, TheOrganization=org)
    app = ifc.create_entity(
        "IfcApplication",
        ApplicationDeveloper=org,
        Version="1",
        ApplicationFullName="test",
        ApplicationIdentifier="test",
    )
    return ifc.create_entity(
        "IfcOwnerHistory",
        OwningUser=pao,
        OwningApplication=app,
        ChangeAction="ADDED",
        CreationDate=0,
    )


def _context(ifc):
    origin_pt = ifc.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0, 0.0))
    origin = ifc.create_entity("IfcAxis2Placement3D", Location=origin_pt)
    return origin_pt, origin, ifc.create_entity(
        "IfcGeometricRepresentationContext",
        ContextType="Model",
        CoordinateSpaceDimension=3,
        Precision=1.0e-5,
        WorldCoordinateSystem=origin,
    )


def _tetra_brep(ifc):
    p1 = ifc.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0, 0.0))
    p2 = ifc.create_entity("IfcCartesianPoint", Coordinates=(1.0, 0.0, 0.0))
    p3 = ifc.create_entity("IfcCartesianPoint", Coordinates=(0.0, 1.0, 0.0))
    p4 = ifc.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0, 1.0))

    def face(a, b, c):
        loop = ifc.create_entity("IfcPolyLoop", Polygon=(a, b, c))
        bound = ifc.create_entity("IfcFaceOuterBound", Bound=loop, Orientation=True)
        return ifc.create_entity("IfcFace", Bounds=(bound,))

    shell = ifc.create_entity(
        "IfcClosedShell",
        CfsFaces=(face(p1, p2, p3), face(p1, p2, p4), face(p2, p3, p4), face(p3, p1, p4)),
    )
    return ifc.create_entity("IfcFacetedBrep", Outer=shell)


def _add_bip_system(ifc, element, system_name: str, owner):
    prop = ifc.create_entity(
        "IfcPropertySingleValue",
        Name="SystemName",
        NominalValue=ifc.create_entity("IfcLabel", system_name),
        Unit=None,
    )
    pset = ifc.create_entity(
        "IfcPropertySet",
        GlobalId=ifcopenshell.guid.new(),
        OwnerHistory=owner,
        Name="BIP",
        HasProperties=(prop,),
    )
    ifc.create_entity(
        "IfcRelDefinesByProperties",
        GlobalId=ifcopenshell.guid.new(),
        OwnerHistory=owner,
        RelatedObjects=(element,),
        RelatingPropertyDefinition=pset,
    )


def _proxy(ifc, owner, placement, representation, name: str):
    return ifc.create_entity(
        "IfcBuildingElementProxy",
        GlobalId=ifcopenshell.guid.new(),
        OwnerHistory=owner,
        Name=name,
        ObjectPlacement=placement,
        Representation=representation,
    )


def _style_hexes(element):
    hexes = []
    pds = getattr(element, "Representation", None)
    if not pds:
        return hexes
    for rep in pds.Representations or ():
        for item in rep.Items or ():
            items = [item]
            if item.is_a("IfcMappedItem") and item.MappingSource and item.MappingSource.MappedRepresentation:
                items = list(item.MappingSource.MappedRepresentation.Items or ())
            for geo in items:
                for styled in getattr(geo, "StyledByItem", None) or ():
                    for assignment in styled.Styles or ():
                        styles = assignment.Styles if assignment.is_a("IfcPresentationStyleAssignment") else (assignment,)
                        for style in styles:
                            if not style.is_a("IfcSurfaceStyle"):
                                continue
                            for shading in style.Styles or ():
                                colour = getattr(shading, "SurfaceColour", None)
                                if colour is None:
                                    continue
                                hexes.append(
                                    "{:02X}{:02X}{:02X}".format(
                                        int(round(colour.Red * 255)),
                                        int(round(colour.Green * 255)),
                                        int(round(colour.Blue * 255)),
                                    )
                                )
    return hexes


def test_shared_mapping_source_keeps_per_element_colour():
    ifc = ifcopenshell.file(schema="IFC2X3")
    owner = _owner_history(ifc)
    origin_pt, origin, ctx = _context(ifc)
    brep = _tetra_brep(ifc)
    mapped_rep = ifc.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=ctx,
        RepresentationIdentifier="Body",
        RepresentationType="Brep",
        Items=(brep,),
    )
    mapping_source = ifc.create_entity(
        "IfcRepresentationMap",
        MappingOrigin=origin,
        MappedRepresentation=mapped_rep,
    )
    transform = ifc.create_entity("IfcCartesianTransformationOperator3D", LocalOrigin=origin_pt)
    placement = ifc.create_entity("IfcLocalPlacement", RelativePlacement=origin)

    def mapped_product(name, system):
        mapped_item = ifc.create_entity(
            "IfcMappedItem",
            MappingSource=mapping_source,
            MappingTarget=transform,
        )
        body = ifc.create_entity(
            "IfcShapeRepresentation",
            ContextOfItems=ctx,
            RepresentationIdentifier="Body",
            RepresentationType="MappedRepresentation",
            Items=(mapped_item,),
        )
        pds = ifc.create_entity("IfcProductDefinitionShape", Representations=(body,))
        elem = _proxy(ifc, owner, placement, pds, name)
        _add_bip_system(ifc, elem, system, owner)
        return elem

    f1 = mapped_product("F1-proxy", "F1")
    u1 = mapped_product("U1-proxy", "U1")

    ops = json.dumps(
        [
            {"selectors": "BIP.SystemName=F1", "hex": "#D8BE07"},
            {"selectors": "BIP.SystemName=U1", "hex": "#2B54BF"},
        ]
    )
    Patcher(ifc, logging.getLogger("test"), ops).patch()

    assert _style_hexes(f1) == [F1_YELLOW]
    assert _style_hexes(u1) == [U1_BLUE]
    f1_ms = f1.Representation.Representations[0].Items[0].MappingSource
    u1_ms = u1.Representation.Representations[0].Items[0].MappingSource
    assert f1_ms.id() != u1_ms.id()
    assert f1_ms.MappedRepresentation.Items[0].id() != u1_ms.MappedRepresentation.Items[0].id()


def test_shared_boolean_result_operand_does_not_leak_colour():
    ifc = ifcopenshell.file(schema="IFC2X3")
    owner = _owner_history(ifc)
    origin_pt, origin, ctx = _context(ifc)
    shared_operand = _tetra_brep(ifc)
    other = _tetra_brep(ifc)
    boolean_item = ifc.create_entity(
        "IfcBooleanResult",
        Operator="DIFFERENCE",
        FirstOperand=shared_operand,
        SecondOperand=other,
    )
    mapped_rep = ifc.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=ctx,
        RepresentationIdentifier="Body",
        RepresentationType="Brep",
        Items=(boolean_item,),
    )
    mapping_source = ifc.create_entity(
        "IfcRepresentationMap",
        MappingOrigin=origin,
        MappedRepresentation=mapped_rep,
    )
    transform = ifc.create_entity("IfcCartesianTransformationOperator3D", LocalOrigin=origin_pt)
    placement = ifc.create_entity("IfcLocalPlacement", RelativePlacement=origin)

    def mapped_product(name, system):
        mapped_item = ifc.create_entity(
            "IfcMappedItem",
            MappingSource=mapping_source,
            MappingTarget=transform,
        )
        body = ifc.create_entity(
            "IfcShapeRepresentation",
            ContextOfItems=ctx,
            RepresentationIdentifier="Body",
            RepresentationType="MappedRepresentation",
            Items=(mapped_item,),
        )
        pds = ifc.create_entity("IfcProductDefinitionShape", Representations=(body,))
        elem = _proxy(ifc, owner, placement, pds, name)
        _add_bip_system(ifc, elem, system, owner)
        return elem

    f1 = mapped_product("F1-bool", "F1")
    u1 = mapped_product("U1-bool", "U1")
    Patcher(
        ifc,
        logging.getLogger("test"),
        json.dumps(
            [
                {"selectors": "BIP.SystemName=F1", "hex": "#D8BE07"},
                {"selectors": "BIP.SystemName=U1", "hex": "#2B54BF"},
            ]
        ),
    ).patch()

    assert _style_hexes(f1) == [F1_YELLOW]
    assert _style_hexes(u1) == [U1_BLUE]


def test_shared_direct_representation_splits_per_colour():
    ifc = ifcopenshell.file(schema="IFC2X3")
    owner = _owner_history(ifc)
    _origin_pt, origin, ctx = _context(ifc)
    brep = _tetra_brep(ifc)
    shared_rep = ifc.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=ctx,
        RepresentationIdentifier="Body",
        RepresentationType="Brep",
        Items=(brep,),
    )
    shared_pds = ifc.create_entity("IfcProductDefinitionShape", Representations=(shared_rep,))
    placement = ifc.create_entity("IfcLocalPlacement", RelativePlacement=origin)
    f1 = _proxy(ifc, owner, placement, shared_pds, "F1-direct")
    u1 = _proxy(ifc, owner, placement, shared_pds, "U1-direct")
    _add_bip_system(ifc, f1, "F1", owner)
    _add_bip_system(ifc, u1, "U1", owner)

    Patcher(
        ifc,
        logging.getLogger("test"),
        json.dumps(
            [
                {"selectors": "BIP.SystemName=F1", "hex": "#D8BE07"},
                {"selectors": "BIP.SystemName=U1", "hex": "#2B54BF"},
            ]
        ),
    ).patch()

    assert _style_hexes(f1) == [F1_YELLOW]
    assert _style_hexes(u1) == [U1_BLUE]
    assert f1.Representation.id() != u1.Representation.id() or (
        f1.Representation.Representations[0].id() != u1.Representation.Representations[0].id()
    )


def test_set_attribute_skips_write_when_nothing_matches():
    ifc, _wall = _make_ifc_with_bip_bsabe()
    patcher = AttributePatcher(
        ifc,
        logging.getLogger("test"),
        '{"selector": "IfcElement, BIP.TypeID != NULL", "attribute": "Name", "from": "BIP.TypeID"}',
    )
    patcher.patch()
    assert patcher.stats["elements_modified"] == 0
    assert patcher.skip_output_write is True


@pytest.mark.skipif(not M1_PATH.is_file(), reason="M1-570 fixture not present")
def test_m1_570_guid_is_f1_yellow():
    ifc = ifcopenshell.open(str(M1_PATH))
    ops = json.dumps(
        [
            {"selectors": "IfcAirTerminal", "hex": "#04AD04"},
            {"selectors": "BIP.SystemName=A1", "hex": "#792824"},
            {"selectors": "BIP.SystemName=C1R", "hex": "#C67933"},
            {"selectors": "BIP.SystemName=C1T", "hex": "#E6752B"},
            {"selectors": "BIP.SystemName=F1", "hex": "#D8BE07"},
            {"selectors": "BIP.SystemName=T1", "hex": "#E62B2B"},
            {"selectors": "BIP.SystemName=U1", "hex": "#2B54BF"},
        ]
    )
    Patcher(ifc, logging.getLogger("m1"), ops).patch()

    target = ifc.by_guid(M1_GUID)
    target_hexes = _style_hexes(target)
    assert target_hexes
    assert set(target_hexes) == {F1_YELLOW}

    f1_hexes = Counter()
    u1_hexes = Counter()
    for element in ifc.by_type("IfcElement"):
        pset = ifcopenshell.util.element.get_pset(element, "BIP") or {}
        system = pset.get("SystemName")
        hexes = set(_style_hexes(element))
        if system == "F1":
            f1_hexes.update(hexes or ["(none)"])
        elif system == "U1":
            u1_hexes.update(hexes or ["(none)"])

    assert F1_YELLOW in f1_hexes
    assert U1_BLUE not in f1_hexes
    assert U1_BLUE in u1_hexes
    assert F1_YELLOW not in u1_hexes
    assert f1_hexes.keys() <= {F1_YELLOW}
    assert u1_hexes.keys() <= {U1_BLUE}
    f1_count = sum(1 for e in ifc.by_type("IfcElement") if (ifcopenshell.util.element.get_pset(e, "BIP") or {}).get("SystemName") == "F1")
    u1_count = sum(1 for e in ifc.by_type("IfcElement") if (ifcopenshell.util.element.get_pset(e, "BIP") or {}).get("SystemName") == "U1")
    assert f1_count == 1646
    assert u1_count == 202
