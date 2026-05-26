"""Optional IfcClash smoke tests against shared example models."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any


def run_ifcclash_smoke(
    examples_dir: Path,
    clash_sets: list[dict[str, Any]],
    tolerance: float = 0.01,
) -> dict[str, Any]:
    try:
        from ifcclash.ifcclash import ClashSettings, Clasher  # type: ignore
    except ImportError:
        return {
            "skipped": True,
            "reason": "ifcopenshell/ifcclash not installed (pip install -r requirements-ifc.txt)",
        }

    if not examples_dir.is_dir():
        return {"skipped": True, "reason": f"examples dir missing: {examples_dir}"}

    prepared_sets = []
    for cs in clash_sets:
        entry = {"name": cs["name"], "a": [], "b": [], "tolerance": tolerance}
        for side in ("a", "b"):
            for src in cs.get(side, []):
                fname = src["file"]
                fpath = examples_dir / fname
                if not fpath.exists():
                    return {
                        "skipped": True,
                        "reason": f"missing example IFC: {fpath}",
                    }
                item = {"file": str(fpath)}
                if "selector" in src:
                    item["selector"] = src["selector"]
                entry[side].append(item)
        prepared_sets.append(entry)

    with tempfile.TemporaryDirectory(prefix="ag-ifc-clash-") as tmp:
        out_path = Path(tmp) / "clash_output.json"
        settings = ClashSettings()
        settings.output = str(out_path)
        clasher = Clasher(settings)
        clasher.clash_sets = prepared_sets
        clasher.clash()
        try:
            clasher.export()
        except AttributeError:
            clasher.export_json(str(out_path))

        if not out_path.exists():
            return {"ok": False, "error": "clash export produced no file"}

        with out_path.open(encoding="utf-8") as handle:
            results = json.load(handle)

    clash_count = sum(len(s.get("clashes", {})) for s in results)
    return {
        "skipped": False,
        "ok": True,
        "clash_set_count": len(results),
        "clash_count": clash_count,
        "sets": [{"name": s.get("name"), "clashes": len(s.get("clashes", {}))} for s in results],
    }
