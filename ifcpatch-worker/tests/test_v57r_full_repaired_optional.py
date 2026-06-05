"""
Optional integration test: repaired **full** ``V--57_V01000R`` file next to fixtures.

The artifact is ~100MB and gitignored; generate with::

    python scripts/repair_full_magiad_ifc.py \\
        --input /path/to/V--57_V01000R.ifc

Then run pytest; this module validates **fixture_guids.manifest.json** GUIDs only.
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

WORKER_ROOT = Path(__file__).resolve().parent.parent
CUSTOM = WORKER_ROOT / "custom_recipes"
if str(CUSTOM) not in sys.path:
    sys.path.insert(0, str(CUSTOM))

from OrientFacetedBrepShells import (  # noqa: E402
    mesh_signed_volume_from_geom,
    validate_shell_manifold_orientation,
    _collect_faceted_shells_from_representation,
)

FIXTURE_DIR = WORKER_ROOT / "tests" / "fixtures" / "magicad"
MANIFEST = FIXTURE_DIR / "fixture_guids.manifest.json"
FULL_REPAIRED = FIXTURE_DIR / "V--57_V01000R_repaired.ifc"
COORD_DECIMALS = 6


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


@pytest.mark.skipif(not FULL_REPAIRED.is_file(), reason=f"Missing {FULL_REPAIRED.name} (run scripts/repair_full_magiad_ifc.py)")
class TestV57RRepairedFull:
    def test_manifest_guids_manifold_and_positive_volume(self, manifest):
        f = ifcopenshell.open(str(FULL_REPAIRED))
        ctx = ur.get_context(f, "Model", "Body", "MODEL_VIEW")
        assert ctx is not None
        settings = ifcopenshell.geom.settings()
        settings.set("context-ids", [ctx.id()])
        for gid in manifest["global_ids"]:
            p = f.by_guid(gid)
            assert p is not None, gid
            rep = ur.get_representation(p, ctx)
            assert rep is not None and rep.RepresentationType == "Brep", gid
            for shell in _collect_faceted_shells_from_representation(rep):
                ok, errs, _ = validate_shell_manifold_orientation(shell, COORD_DECIMALS)
                assert ok, f"{gid}: {errs[:5]}"
            shp = ifcopenshell.geom.create_shape(settings, p)
            v = mesh_signed_volume_from_geom(shp.geometry)
            assert v > 0, f"{gid}: volume {v}"
