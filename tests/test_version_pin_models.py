"""Sanity checks: version-pin fields survive Pydantic models and model_dump."""

from shared.classes import IfcDiffRequest, IfcPatchRequest, IfcTesterRequest


def test_ifcpatch_request_retains_pins():
    p = IfcPatchRequest(
        input_file="uploads/a.ifc",
        output_file="uploads/b.ifc",
        recipe="ExtractElements",
        input_version_id="ver-1",
        input_audit_id=42,
        input_version_ids={"uploads/a.ifc": "v-a"},
    )
    d = p.model_dump()
    assert d["input_version_id"] == "ver-1"
    assert d["input_audit_id"] == 42
    assert d["input_version_ids"]["uploads/a.ifc"] == "v-a"


def test_ifcdiff_request_retains_side_pins():
    r = IfcDiffRequest(
        old_file="uploads/old.ifc",
        new_file="uploads/new.ifc",
        old_version_id="oid",
        new_version_id="nid",
        input_audit_id=7,
    )
    d = r.model_dump()
    assert d["old_version_id"] == "oid"
    assert d["new_version_id"] == "nid"
    assert d["input_audit_id"] == 7


def test_ifctester_inherits_version_pin_optional():
    t = IfcTesterRequest(
        ifc_filename="uploads/m.ifc",
        ids_filename="uploads/x.ids",
        output_filename="out.json",
        input_version_id="vid",
        input_version_ids={"uploads/m.ifc": "vm"},
    )
    d = t.model_dump()
    assert d["input_version_id"] == "vid"
    assert d["input_version_ids"]["uploads/m.ifc"] == "vm"
