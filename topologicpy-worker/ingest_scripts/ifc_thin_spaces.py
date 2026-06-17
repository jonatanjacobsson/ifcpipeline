"""Build a spaces-only IFC via ifcpatch RemoveElements (one pass, in-place on a copy).

Keeps IfcSpace instances plus protected spatial containers (site/building/storey)
with all property sets and aggregate relationships intact — unlike copy_deep extract.
"""

from __future__ import annotations

import importlib.util
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Optional

import ifcopenshell


def _remove_elements_recipe_paths() -> list[Path]:
    here = Path(__file__).resolve()
    return [
        here.parent / "vendor" / "RemoveElements.py",
        here.parents[2] / "ifcpatch-worker" / "custom_recipes" / "RemoveElements.py",
        Path("/app/custom_recipes/RemoveElements.py"),
    ]


def load_remove_elements_patcher():
    recipe_path: Path | None = None
    for candidate in _remove_elements_recipe_paths():
        if candidate.is_file():
            recipe_path = candidate
            break
    if recipe_path is None:
        tried = ", ".join(str(p) for p in _remove_elements_recipe_paths())
        raise ImportError(f"RemoveElements recipe not found (tried: {tried})")
    spec = importlib.util.spec_from_file_location("RemoveElements", recipe_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load RemoveElements from {recipe_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Patcher


def safe_by_type(ifc, type_name: str) -> list:
    try:
        return list(ifc.by_type(type_name))
    except RuntimeError:
        return []


def approx_entity_count(ifc) -> int:
    try:
        return sum(
            len(ifc.by_type(t))
            for t in (
                "IfcSpace",
                "IfcDoor",
                "IfcWall",
                "IfcWallStandardCase",
                "IfcSlab",
                "IfcColumn",
                "IfcBeam",
                "IfcMember",
                "IfcFlowTerminal",
                "IfcFlowSegment",
                "IfcBuildingStorey",
                "IfcBuilding",
                "IfcSite",
                "IfcProject",
            )
        )
    except Exception:
        try:
            return len(list(ifc))
        except Exception:
            return 0


def is_spaces_only_file(ifc) -> bool:
    """True when the file has spaces and no door products (already federated spaces export)."""
    return bool(safe_by_type(ifc, "IfcSpace")) and not safe_by_type(ifc, "IfcDoor")


def thin_spaces_copy(
    source: Path,
    *,
    output: Optional[Path] = None,
    log: Optional[logging.Logger] = None,
) -> Path:
    """Copy ``source`` and remove all products except IfcSpace (+ protected storeys)."""
    logger = log or logging.getLogger(__name__)
    source = source.resolve()
    if output is None:
        fd, raw = tempfile.mkstemp(suffix="_spaces.ifc", prefix=f"{source.stem}_")
        import os

        os.close(fd)
        output = Path(raw)
    else:
        output = output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)

    shutil.copy2(source, output)
    ifc = ifcopenshell.open(str(output))
    Patcher = load_remove_elements_patcher()
    # Doors/walls are being deleted — skip host-aware filling rebase and heavy geom GC.
    Patcher(
        ifc,
        logger=logger,
        query="!IfcSpace",
        clean_geometry=False,
        clean_orphaned_types=False,
        fix_orphaned_fillings=False,
    ).patch()
    ifc.write(str(output))
    spaces = len(safe_by_type(ifc, "IfcSpace"))
    logger.info(
        "ifc_thin_spaces: %s → %s (%d spaces, ~%d entities)",
        source.name,
        output.name,
        spaces,
        approx_entity_count(ifc),
    )
    return output
