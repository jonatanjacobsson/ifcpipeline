"""
Benchmark: IfcOpenShell **kernel** ``reorient-shells`` vs custom **OrientFacetedBrepShells** recipe.

IfcConvert / ``geom.create_shape`` (same engine)
------------------------------------------------
The CLI flag ``--reorient-shells`` and Python ``settings.set("reorient-shells", True)``
tell the **IfcGeom iterator** to orient ``IfcConnectedFaceSet`` / shell topology so
surface normals are consistent when the IFC data is poorly wound. This runs inside
the C++ geometry pipeline (Open Cascade / CGAL), **not** by rewriting IFC entities.

- Docs: https://docs.ifcopenshell.org/ifcconvert/usage.html (Geometry options)
- Python settings: https://docs.ifcopenshell.org/ifcopenshell/geometry_settings.html

Upstream implementation lives in the IfcOpenShell **ifcgeom** C++ sources (iterator
settings and kernel processing of face sets), not in the Python ifcconvert wrapper.

Custom **OrientFacetedBrepShells** recipe
-----------------------------------------
Operates on **IFC** ``IfcPolyLoop`` order after ``TessellateElements``, then applies a
signed-volume flip. That **changes the SPF file**, so viewers that do not use the
kernel option still see corrected solids.

When to use which
-----------------
- Need **correct mesh in Python / OBJ / GLB** without editing IFC: use
  ``reorient-shells`` on ``create_shape`` or ``IfcConvert --reorient-shells``.
- Need **portable IFC** for Solibri / StreamBIM: use ``TessellateElements`` +
  ``OrientFacetedBrepShells`` (optionally keep kernel reorient on for exports).

Tests below compare volumes and (where available) run ``IfcConvert`` smoke tests.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.representation as ur
import ifcpatch

WORKER_ROOT = Path(__file__).resolve().parent.parent
CUSTOM = WORKER_ROOT / "custom_recipes"
if str(CUSTOM) not in sys.path:
    sys.path.insert(0, str(CUSTOM))

from OrientFacetedBrepShells import (  # noqa: E402
    Patcher as OrientPatcher,
    mesh_signed_volume_from_geom,
)

FIXTURE_DIR = WORKER_ROOT / "tests" / "fixtures" / "magicad"
FIXTURE_IFC = FIXTURE_DIR / "magicad_duct_fittings_fixture.ifc"
MANIFEST = FIXTURE_DIR / "fixture_guids.manifest.json"

IFCCONVERT_CANDIDATES = ("IfcConvert", "ifcconvert")


def _find_ifcconvert() -> str | None:
    for name in IFCCONVERT_CANDIDATES:
        p = shutil.which(name)
        if p:
            return p
    return None


def _load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _tessellate_only() -> ifcopenshell.file:
    f = ifcopenshell.open(str(FIXTURE_IFC))
    return ifcpatch.execute(
        {
            "input": str(FIXTURE_IFC),
            "file": f,
            "recipe": "TessellateElements",
            "arguments": ["IfcFlowFitting", False],
        }
    )


def _volume_for_product(ifc: ifcopenshell.file, guid: str, *, reorient_shells: bool) -> float:
    context = ur.get_context(ifc, "Model", "Body", "MODEL_VIEW")
    assert context is not None
    s = ifcopenshell.geom.settings()
    s.set("context-ids", [context.id()])
    s.set("reorient-shells", reorient_shells)
    p = ifc.by_guid(guid)
    shp = ifcopenshell.geom.create_shape(s, p)
    return mesh_signed_volume_from_geom(shp.geometry)


@pytest.fixture(scope="module")
def manifest() -> dict:
    assert MANIFEST.is_file()
    return _load_manifest()


class TestKernelReorientShells:
    """``reorient-shells`` on ``geom.create_shape`` (same semantics as IfcConvert)."""

    def test_tessellated_without_kernel_reorient_can_have_small_signed_volume(
        self, manifest,
    ):
        """Regression: MagiCAD tessellation alone may yield inconsistent winding; volume can be tiny."""
        ifc = _tessellate_only()
        gid = manifest["global_ids"][0]
        v_off = _volume_for_product(ifc, gid, reorient_shells=False)
        v_on = _volume_for_product(ifc, gid, reorient_shells=True)
        assert v_on > 0.05, "Kernel reorient should yield a plausible positive volume for bend"
        # Without kernel reorient, the first sample was ~10x smaller in magnitude than with.
        assert v_off < v_on * 0.5, (
            "Expected kernel reorient to increase/fix signed volume vs off; "
            f"got off={v_off} on={v_on}"
        )

    def test_kernel_reorient_all_fixture_products_positive_volume(self, manifest):
        ifc = _tessellate_only()
        for gid in manifest["global_ids"]:
            v = _volume_for_product(ifc, gid, reorient_shells=True)
            assert v > 0, f"{gid}: kernel reorient should give positive mesh volume, got {v}"


class TestRecipeVsKernel:
    """After ``OrientFacetedBrepShells``, kernel ``reorient-shells`` is redundant for volume."""

    def test_orient_recipe_then_kernel_reorient_same_volume(self, manifest):
        f = ifcopenshell.open(str(FIXTURE_IFC))
        out = ifcpatch.execute(
            {
                "input": str(FIXTURE_IFC),
                "file": f,
                "recipe": "TessellateElements",
                "arguments": ["IfcFlowFitting", False],
            }
        )
        OrientPatcher(out, logging.getLogger("orient"), "IfcFlowFitting").patch()
        for gid in manifest["global_ids"]:
            va = _volume_for_product(out, guid=gid, reorient_shells=False)
            vb = _volume_for_product(out, guid=gid, reorient_shells=True)
            assert abs(va - vb) < 1e-4 * max(abs(va), 1.0), f"{gid}: {va} vs {vb}"


@pytest.mark.skipif(not _find_ifcconvert(), reason="IfcConvert binary not on PATH (install in Docker worker image)")
class TestIfcConvertCli:
    """Smoke-test the same flag the worker uses (see ifcconvert-worker Dockerfile)."""

    def test_ifcconvert_reorient_shells_produces_obj(self, tmp_path):
        exe = _find_ifcconvert()
        assert exe
        ifc = _tessellate_only()
        mid = Path(tmp_path / "tess.ifc")
        ifc.write(str(mid))
        out_obj = tmp_path / "out.obj"
        logf = tmp_path / "convert.log"
        # Mirror worker: geometry-only conversion; --reorient-shells matches docs.
        cmd = [
            exe,
            "-y",
            "--reorient-shells",
            str(mid),
            str(out_obj),
            "--log-file",
            str(logf),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, (r.stdout, r.stderr)
        assert out_obj.is_file() and out_obj.stat().st_size > 100
