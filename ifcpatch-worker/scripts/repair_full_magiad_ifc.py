#!/usr/bin/env python3
"""
Repair a full MagiCAD (or similar) IFC: ``TessellateElements`` → ``OrientFacetedBrepShells``.

Implementation is shared with the ``MagiadTessellateAndOrient`` IfcPatch recipe (``_magaid_shell_repair.py``).

Writes e.g. ``tests/fixtures/magicad/V--57_V01000R_repaired.ifc`` (large; see .gitignore).

Validate **fixture GUIDs** from ``fixture_guids.manifest.json`` (manifold + positive mesh volume).
A strict check on *every* element matching ``--types`` in a city-scale model will fail for
degenerate/zero-thickness pieces; use ``--strict-all`` only if you expect all to be solid.

Example::

    python scripts/repair_full_magiad_ifc.py \\
        --input /path/to/V--57_V01000R.ifc \\
        --output tests/fixtures/magicad/V--57_V01000R_repaired.ifc

Wider MEP scope::

    python scripts/repair_full_magiad_ifc.py \\
        --input model.ifc --output out.ifc \\
        --types "IfcFlowFitting, IfcFlowSegment, IfcFlowTerminal"

When **multiple** IFC types are selected, ``TessellateElements`` runs **once per type** (same
result as a combined selector, but smaller batches — avoids kernel instability on very large models).

If the selector is exactly **IfcElement**, ``TessellateElements`` runs in **batches** of instances
(sorted by entity ``#id``), using a comma-joined GlobalId selector (union). **IfcSite** is never
included (not an ``IfcElement`` in IFC; we also skip defensively if it appears). Use
``--ifc-element-batch-size`` to tune batch size vs. memory.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.representation as ur
import ifcopenshell.util.selector as sel

WORKER_ROOT = Path(__file__).resolve().parent.parent
CUSTOM = WORKER_ROOT / "custom_recipes"
if str(CUSTOM) not in sys.path:
    sys.path.insert(0, str(CUSTOM))

from OrientFacetedBrepShells import (  # noqa: E402
    mesh_signed_volume_from_geom,
    validate_shell_manifold_orientation,
    _collect_faceted_shells_from_representation,
)
from _magaid_shell_repair import (  # noqa: E402
    COORD_DECIMALS_DEFAULT,
    DEFAULT_IFC_ELEMENT_BATCH_SIZE,
    merge_mep_preset_with_extras,
    parse_types_arg,
    run_tessellate_and_orient,
    types_to_selector,
)

DEFAULT_MANIFEST = WORKER_ROOT / "tests" / "fixtures" / "magicad" / "fixture_guids.manifest.json"
COORD_DECIMALS = COORD_DECIMALS_DEFAULT


def validate_manifest_guids(ifc: ifcopenshell.file, manifest_path: Path) -> tuple[bool, list[str]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    gids = manifest.get("global_ids") or []
    ctx = ur.get_context(ifc, "Model", "Body", "MODEL_VIEW")
    assert ctx is not None
    settings = ifcopenshell.geom.settings()
    settings.set("context-ids", [ctx.id()])
    errors: list[str] = []
    for gid in gids:
        p = ifc.by_guid(gid)
        if p is None:
            errors.append(f"{gid}: not found in repaired file")
            continue
        rep = ur.get_representation(p, ctx)
        if not rep or rep.RepresentationType != "Brep":
            errors.append(f"{gid}: no Body Brep")
            continue
        for shell in _collect_faceted_shells_from_representation(rep):
            ok, errs, _bnd = validate_shell_manifold_orientation(shell, COORD_DECIMALS)
            if not ok:
                errors.append(f"{gid} manifold: {errs[:2]}")
        try:
            shp = ifcopenshell.geom.create_shape(settings, p)
            v = mesh_signed_volume_from_geom(shp.geometry)
            if v <= 0:
                errors.append(f"{gid}: signed volume {v}")
        except Exception as e:
            errors.append(f"{gid}: geom {e}")
    return len(errors) == 0, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Tessellate + orient full MagiCAD IFC.")
    parser.add_argument("--input", type=Path, required=True, help="Source .ifc (e.g. V--57_V01000R.ifc)")
    parser.add_argument(
        "--output",
        type=Path,
        default=WORKER_ROOT / "tests" / "fixtures" / "magicad" / "V--57_V01000R_repaired.ifc",
        help="Output repaired .ifc",
    )
    parser.add_argument(
        "--types",
        type=str,
        default="IfcFlowFitting",
        metavar="SELECTOR",
        help=(
            "IFC product class name(s) for TessellateElements + OrientFacetedBrepShells: "
            "comma- or space-separated (default: IfcFlowFitting). "
            "Examples: 'IfcBeam', 'IfcWall, IfcSlab', 'IfcElement' (all building elements, batched; excludes IfcSite)."
        ),
    )
    parser.add_argument(
        "--ifc-element-batch-size",
        type=int,
        default=DEFAULT_IFC_ELEMENT_BATCH_SIZE,
        metavar="N",
        help=(
            "When --types is IfcElement only: number of GlobalIds per TessellateElements batch "
            f"(default: {DEFAULT_IFC_ELEMENT_BATCH_SIZE}). Ignored otherwise."
        ),
    )
    parser.add_argument(
        "--preset-mep-flow",
        action="store_true",
        help=(
            "Use bundled MEP preset for the file’s IFC schema (IFC2×3: fixed distribution classes; "
            "IFC4+: every concrete IfcDistributionElement leaf, e.g. IfcDuctSilencer, IfcDamper, …; "
            "plus ancillary IfcDiscreteAccessory, … — not IfcCovering; use --types to include coverings) "
            "and merge any extra --types."
        ),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="JSON with global_ids to validate (default: small duct fixture manifest)",
    )
    parser.add_argument(
        "--strict-all",
        action="store_true",
        help="Also require every matching-product Body Brep (see --types) to have positive volume (usually fails on large models)",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger("repair")

    inp = args.input.resolve()
    out = args.output.resolve()
    if not inp.is_file():
        log.error("Input not found: %s", inp)
        return 1

    log.info("Loading %s", inp)
    f = ifcopenshell.open(str(inp))

    if args.preset_mep_flow:
        type_list = merge_mep_preset_with_extras(f.schema, parse_types_arg(args.types))
    else:
        type_list = parse_types_arg(args.types)
    selector = types_to_selector(type_list)
    run_tessellate_and_orient(
        f,
        log,
        str(inp),
        type_list=type_list,
        ifc_element_batch_size=int(args.ifc_element_batch_size),
        coord_decimals=COORD_DECIMALS,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    f.write(str(out))
    log.info("Wrote %s", out)

    ok, errs = validate_manifest_guids(f, args.manifest.resolve())
    if ok:
        log.info("Manifest GUID checks passed (%s)", args.manifest.name)
    else:
        log.error("Manifest GUID checks failed: %s", errs[:10])
        return 2

    if args.strict_all:
        ctx = ur.get_context(f, "Model", "Body", "MODEL_VIEW")
        settings = ifcopenshell.geom.settings()
        settings.set("context-ids", [ctx.id()])
        elements = sel.filter_elements(f, selector)
        products = [e for e in elements if e.is_a("IfcProduct")]
        bad = []
        brep_checked = 0
        for p in products:
            rep = ur.get_representation(p, ctx)
            if not rep or rep.RepresentationType != "Brep":
                continue
            brep_checked += 1
            try:
                shp = ifcopenshell.geom.create_shape(settings, p)
                v = mesh_signed_volume_from_geom(shp.geometry)
                if v <= 0:
                    bad.append((p.GlobalId, v))
            except Exception as e:
                bad.append((p.GlobalId, str(e)))
        if bad:
            log.error("--strict-all: %d failure(s), e.g. %s", len(bad), bad[:5])
            return 3
        log.info(
            "--strict-all: all matching Brep products have positive volume (%d Body Breps checked, %d products in selector)",
            brep_checked,
            len(products),
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
