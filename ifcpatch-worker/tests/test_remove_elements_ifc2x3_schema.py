"""RemoveElements must not query relationship types the file's schema lacks."""
import os
import sys

import ifcopenshell

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_recipes"))
import RemoveElements as R  # noqa: E402


def test_dangling_cleanup_skips_ifc4_only_relationships_on_ifc2x3():
    f = ifcopenshell.file(schema="IFC2X3")
    wall = f.create_entity("IfcWall", GlobalId=ifcopenshell.guid.new(), Name="w")
    assert not any(t == "IfcRelDeclares" for t in (e.is_a() for e in f))
    patcher = R.Patcher(f, None, "IfcWall")
    patcher.patch()  # raised RuntimeError: Entity 'IfcRelDeclares' not found in schema 'IFC2X3'
    assert len(f.by_type("IfcWall")) == 0
