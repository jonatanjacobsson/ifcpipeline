#!/usr/bin/env python3
"""
Build a minimal IFC fixture from a full MagiCAD export for IfcFlowFitting testing.

Regenerate after updating the source export::

    cd ifcpatch-worker
    pip install -r requirements.txt
    python scripts/build_magicad_duct_fixture.py \\
        --input /path/to/V--57_V01000.ifc

Defaults write to tests/fixtures/magicad/ under this worker.

Uses IfcPatch ``ExtractElements`` to subset the model. Downstream repair tests use
``TessellateElements``, which **does not preserve** surface styles / shape aspects.

Requires: ifcopenshell and ifcpatch (see requirements.txt, pin 0.8.4).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import ifcopenshell
import ifcpatch
import ifcopenshell.util.representation as ur

logger = logging.getLogger(__name__)

REQUIRED_GUID = "0lDxsGSSH6mA_TeU0yyyLT"
WORKER_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = WORKER_ROOT / "tests" / "fixtures" / "magicad"


def walk_shape_item(item: ifcopenshell.entity_instance, visited: set[int], acc: dict[str, Any]) -> None:
    """Accumulate open/closed shell flags and face counts from a geometric item tree."""
    eid = item.id()
    if eid in visited:
        return
    visited.add(eid)
    name = item.is_a()
    if name == "IfcMappedItem":
        ms = item.MappingSource
        if ms is not None and ms.MappedRepresentation is not None:
            for sub in ms.MappedRepresentation.Items or ():
                walk_shape_item(sub, visited, acc)
        return
    if name == "IfcShellBasedSurfaceModel":
        for shell in item.SbsmBoundary or ():
            sn = shell.is_a()
            faces = len(shell.CfsFaces or ())
            acc["face_count"] += faces
            if sn == "IfcOpenShell":
                acc["has_open_shell"] = True
            elif sn == "IfcClosedShell":
                acc["has_closed_shell"] = True
        return
    if name == "IfcFacetedBrep":
        outer = item.Outer
        if outer is not None:
            acc["face_count"] += len(outer.CfsFaces or ())
            if outer.is_a() == "IfcClosedShell":
                acc["has_closed_shell"] = True
        return
    if name in ("IfcBooleanResult", "IfcBooleanClippingResult"):
        for attr in ("FirstOperand", "SecondOperand"):
            op = getattr(item, attr, None)
            if op is not None:
                walk_shape_item(op, visited, acc)
        return


def classify_flow_fitting(ifc_file: ifcopenshell.file, product: ifcopenshell.entity_instance) -> dict[str, Any]:
    context = ur.get_context(ifc_file, "Model", "Body", "MODEL_VIEW")
    out: dict[str, Any] = {
        "global_id": product.GlobalId,
        "name": getattr(product, "Name", None),
        "has_body_representation": False,
        "representation_type": None,
        "has_open_shell": False,
        "has_closed_shell": False,
        "face_count": 0,
    }
    if not context:
        out["error"] = "no_Model_Body_context"
        return out
    rep = ur.get_representation(product, context)
    if not rep:
        out["error"] = "no_body_representation"
        return out
    out["has_body_representation"] = True
    out["representation_type"] = rep.RepresentationType
    acc = {"has_open_shell": False, "has_closed_shell": False, "face_count": 0}
    visited: set[int] = set()
    for item in rep.Items or ():
        walk_shape_item(item, visited, acc)
    out["has_open_shell"] = acc["has_open_shell"]
    out["has_closed_shell"] = acc["has_closed_shell"]
    out["face_count"] = acc["face_count"]
    # Bucket face counts to cluster similar complexity without over-fragmenting
    fc = out["face_count"]
    out["face_bucket"] = (fc // 32) * 32 if fc < 512 else 512
    return out


def cluster_key(info: dict[str, Any]) -> tuple:
    return (
        info.get("representation_type") or "",
        info.get("has_open_shell", False),
        info.get("has_closed_shell", False),
        info.get("face_bucket", 0),
    )


def pick_guids(classifications: list[dict[str, Any]], max_total: int) -> tuple[list[str], list[dict[str, Any]]]:
    """Pick diverse IfcFlowFitting GUIDs; always include REQUIRED_GUID when present."""
    by_cluster: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for c in classifications:
        by_cluster[cluster_key(c)].append(c)

    required_row = next((c for c in classifications if c["global_id"] == REQUIRED_GUID), None)
    chosen: list[dict[str, Any]] = []
    chosen_ids: set[str] = set()

    if required_row:
        chosen.append(required_row)
        chosen_ids.add(REQUIRED_GUID)

    # One sample per cluster (excluding already chosen)
    for key in sorted(by_cluster.keys(), key=lambda k: (str(k[0]), k[1], k[2], k[3])):
        for row in by_cluster[key]:
            gid = row["global_id"]
            if gid in chosen_ids:
                continue
            if len(chosen) >= max_total:
                break
            chosen.append(row)
            chosen_ids.add(gid)
            break
        if len(chosen) >= max_total:
            break

    # Fill up to max_total from remaining (stable order by GlobalId)
    if len(chosen) < max_total:
        rest = sorted(
            (c for c in classifications if c["global_id"] not in chosen_ids),
            key=lambda x: x["global_id"],
        )
        for row in rest:
            if len(chosen) >= max_total:
                break
            chosen.append(row)
            chosen_ids.add(row["global_id"])

    guids = [c["global_id"] for c in chosen]
    return guids, chosen


def main() -> int:
    parser = argparse.ArgumentParser(description="Build MagiCAD duct fitting fixture IFC + manifest.")
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Path to full MagiCAD IFC (e.g. V--57_V01000.ifc)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"Directory for fixture .ifc and manifest (default: {DEFAULT_OUT_DIR})",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=6,
        dest="max_elements",
        help="Maximum number of IfcFlowFitting instances to extract (default: 6)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    inp = args.input.resolve()
    if not inp.is_file():
        logger.error("Input file not found: %s", inp)
        return 1

    out_dir = args.output_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    fixture_ifc = out_dir / "magicad_duct_fittings_fixture.ifc"
    manifest_path = out_dir / "fixture_guids.manifest.json"

    logger.info("Loading %s", inp)
    ifc_file = ifcopenshell.open(str(inp))
    fittings = ifc_file.by_type("IfcFlowFitting")
    classifications: list[dict[str, Any]] = []
    for el in fittings:
        info = classify_flow_fitting(ifc_file, el)
        classifications.append(info)

    if not any(c["global_id"] == REQUIRED_GUID for c in classifications):
        logger.error("Required GUID %s not found among IfcFlowFitting instances.", REQUIRED_GUID)
        return 1

    guids, chosen_rows = pick_guids(classifications, args.max_elements)
    selector = ",".join(guids)
    logger.info("Extracting %d element(s): %s", len(guids), selector)

    out_model = ifcpatch.execute(
        {
            "input": str(inp),
            "file": ifc_file,
            "recipe": "ExtractElements",
            "arguments": [selector],
        }
    )
    ifcpatch.write(out_model, str(fixture_ifc))
    logger.info("Wrote %s", fixture_ifc)

    manifest = {
        "source_file": inp.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ifcopenshell_note": "Regenerate with scripts/build_magicad_duct_fixture.py after MagiCAD export changes.",
        "repair_note": "TessellateElements (tests) remeshes geometry; surface styles are not preserved.",
        "required_guid_included": REQUIRED_GUID,
        "global_ids": guids,
        "elements": [
            {
                "global_id": r["global_id"],
                "name": r.get("name"),
                "representation_type": r.get("representation_type"),
                "has_open_shell": r.get("has_open_shell"),
                "has_closed_shell": r.get("has_closed_shell"),
                "face_count": r.get("face_count"),
                "cluster": list(cluster_key(r)),
            }
            for r in chosen_rows
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    logger.info("Wrote %s", manifest_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
