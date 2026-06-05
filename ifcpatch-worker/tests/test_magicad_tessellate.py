"""
MagiCAD duct fitting fixture tests: TessellateElements + OrientFacetedBrepShells.

Requires: ifcopenshell, ifcpatch (../requirements.txt), pytest (requirements-dev.txt).
numpy is pulled in via ifcopenshell for signed-volume checks.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.representation as ur
import ifcopenshell.util.shape
import ifcpatch

WORKER_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = WORKER_ROOT / "scripts"
CUSTOM = WORKER_ROOT / "custom_recipes"
for p in (SCRIPTS, CUSTOM):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from build_magicad_duct_fixture import (  # noqa: E402
    REQUIRED_GUID,
    classify_flow_fitting,
    walk_shape_item,
)
from OrientFacetedBrepShells import (  # noqa: E402
    Patcher as OrientPatcher,
    mesh_signed_volume_from_geom,
    validate_shell_manifold_orientation,
    _collect_faceted_shells_from_representation,
)
from _magaid_shell_repair import (  # noqa: E402
    is_whole_ifc_element_scope,
    merge_mep_preset_with_extras,
    parse_types_arg,
)

FIXTURE_DIR = WORKER_ROOT / "tests" / "fixtures" / "magicad"
FIXTURE_IFC = FIXTURE_DIR / "magicad_duct_fittings_fixture.ifc"
MANIFEST = FIXTURE_DIR / "fixture_guids.manifest.json"
COORD_DECIMALS = 6


@pytest.fixture(scope="module")
def manifest() -> dict:
    assert MANIFEST.is_file(), f"Missing manifest: {MANIFEST}"
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def fixture_path() -> Path:
    assert FIXTURE_IFC.is_file(), f"Missing fixture: {FIXTURE_IFC}"
    return FIXTURE_IFC


def _collect_openshell_flags(ifc_file, product) -> tuple[bool, int]:
    """Return (has_open_shell, face_count) from body geometry."""
    context = ur.get_context(ifc_file, "Model", "Body", "MODEL_VIEW")
    if not context:
        return False, 0
    rep = ur.get_representation(product, context)
    if not rep:
        return False, 0
    acc = {"has_open_shell": False, "has_closed_shell": False, "face_count": 0}
    visited: set[int] = set()
    for item in rep.Items or ():
        walk_shape_item(item, visited, acc)
    return acc["has_open_shell"], acc["face_count"]


def _assert_manifold_and_volume(
    ifc_file: ifcopenshell.file,
    manifest: dict,
    *,
    expect_positive_volume: bool,
) -> None:
    context = ur.get_context(ifc_file, "Model", "Body", "MODEL_VIEW")
    assert context is not None
    settings = ifcopenshell.geom.settings()
    settings.set("context-ids", [context.id()])
    for gid in manifest["global_ids"]:
        p = ifc_file.by_guid(gid)
        assert p is not None
        rep = ur.get_representation(p, context)
        assert rep is not None
        for shell in _collect_faceted_shells_from_representation(rep):
            ok, errs, _bnd = validate_shell_manifold_orientation(shell, COORD_DECIMALS)
            assert ok, f"{gid}: manifold orientation errors: {errs[:5]}"
        shp = ifcopenshell.geom.create_shape(settings, p)
        vol = mesh_signed_volume_from_geom(shp.geometry)
        if expect_positive_volume:
            assert vol > 0, f"{gid}: expected positive mesh signed volume, got {vol}"


class TestIfcElementCollapse:
    """MEP preset + IfcElement must collapse to batched IfcElement-only scope."""

    def test_merge_mep_preset_with_extras_collapses_if_ifcelement_in_extras(self):
        assert merge_mep_preset_with_extras("IFC2X3", ["IfcElement"]) == ["IfcElement"]

    def test_parse_types_arg_collapses_when_ifcelement_with_other_types(self):
        assert parse_types_arg("IfcBeam, IfcElement") == ["IfcElement"]

    def test_is_whole_ifc_element_scope_true_when_ifcelement_mixed(self):
        assert is_whole_ifc_element_scope(["IfcFlowFitting", "IfcElement"]) is True


class TestMagiCADFixturePreconditions:
    def test_manifest_lists_required_guid(self, manifest):
        assert REQUIRED_GUID in manifest["global_ids"]

    def test_fixture_has_flow_fittings_with_risky_geometry(self, fixture_path, manifest):
        f = ifcopenshell.open(str(fixture_path))
        assert f.schema == "IFC2X3"
        opens = 0
        for gid in manifest["global_ids"]:
            p = f.by_guid(gid)
            assert p is not None, gid
            assert p.is_a("IfcFlowFitting")
            info = classify_flow_fitting(f, p)
            assert info.get("has_body_representation") is True, gid
            has_open, _ = _collect_openshell_flags(f, p)
            if has_open:
                opens += 1
            row = next((e for e in manifest["elements"] if e["global_id"] == gid), None)
            if row and row.get("has_open_shell"):
                assert has_open is True, f"Manifest expected open shell for {gid}"
        assert opens >= 1, "Fixture should include at least one IfcOpenShell body for regression"


class TestTessellateElementsRepair:
    @pytest.mark.parametrize("force_faceted_brep", [False, True])
    def test_tessellate_yields_faceted_brep_closed_shell(self, fixture_path, manifest, force_faceted_brep):
        f = ifcopenshell.open(str(fixture_path))
        out = ifcpatch.execute(
            {
                "input": str(fixture_path),
                "file": f,
                "recipe": "TessellateElements",
                "arguments": ["IfcFlowFitting", force_faceted_brep],
            }
        )
        assert out is not None
        context = ur.get_context(out, "Model", "Body", "MODEL_VIEW")
        assert context is not None
        for gid in manifest["global_ids"]:
            p = out.by_guid(gid)
            assert p is not None
            rep = ur.get_representation(p, context)
            assert rep is not None
            assert rep.RepresentationType == "Brep"
            items = list(rep.Items or ())
            assert len(items) >= 1
            for item in items:
                assert item.is_a() == "IfcFacetedBrep"
                shell = item.Outer
                assert shell.is_a() == "IfcClosedShell"
                assert len(shell.CfsFaces or ()) > 0

    def test_geom_create_shape_after_tessellate(self, fixture_path, manifest):
        f = ifcopenshell.open(str(fixture_path))
        out = ifcpatch.execute(
            {
                "input": str(fixture_path),
                "file": f,
                "recipe": "TessellateElements",
                "arguments": ["IfcFlowFitting", False],
            }
        )
        context = ur.get_context(out, "Model", "Body", "MODEL_VIEW")
        settings = ifcopenshell.geom.settings()
        settings.set("context-ids", [context.id()])
        for gid in manifest["global_ids"]:
            p = out.by_guid(gid)
            shape = ifcopenshell.geom.create_shape(settings, p)
            geom = shape.geometry
            verts = ifcopenshell.util.shape.get_vertices(geom)
            faces = ifcopenshell.util.shape.get_faces(geom)
            assert verts is not None and len(verts) > 0
            assert faces is not None and len(faces) > 0


class TestOrientFacetedBrepShells:
    """Custom recipe: manifold edge consistency + positive global volume."""

    def test_tessellate_then_orient_passes_manifold_and_volume(self, fixture_path, manifest):
        f = ifcopenshell.open(str(fixture_path))
        out = ifcpatch.execute(
            {
                "input": str(fixture_path),
                "file": f,
                "recipe": "TessellateElements",
                "arguments": ["IfcFlowFitting", False],
            }
        )
        OrientPatcher(out, logging.getLogger("orient"), "IfcFlowFitting", coord_decimals=COORD_DECIMALS).patch()
        _assert_manifold_and_volume(out, manifest, expect_positive_volume=True)

    def test_manifold_inconsistency_common_before_orient(self, fixture_path, manifest):
        """After tessellation, at least one shell typically has paired edges with same winding (Solibri-style issue)."""
        f = ifcopenshell.open(str(fixture_path))
        out = ifcpatch.execute(
            {
                "input": str(fixture_path),
                "file": f,
                "recipe": "TessellateElements",
                "arguments": ["IfcFlowFitting", False],
            }
        )
        context = ur.get_context(out, "Model", "Body", "MODEL_VIEW")
        any_bad = False
        for gid in manifest["global_ids"]:
            p = out.by_guid(gid)
            rep = ur.get_representation(p, context)
            for shell in _collect_faceted_shells_from_representation(rep):
                ok, errs, _ = validate_shell_manifold_orientation(shell, COORD_DECIMALS)
                if not ok:
                    any_bad = True
                    break
            if any_bad:
                break
        assert any_bad, "Expected pre-orient tessellated shells to show manifold winding issues for regression"
