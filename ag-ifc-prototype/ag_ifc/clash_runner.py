"""Shared IfcClash execution helpers."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any


def run_clash_set(clash_set: dict[str, Any], logger: logging.Logger | None = None) -> dict[str, Any]:
    from ifcclash.ifcclash import Clasher, ClashSettings  # type: ignore

    log = logger or logging.getLogger("ifcclash")
    output = clash_set.get("_output_path", "/tmp/ag_ifc_clash_out.json")
    settings = ClashSettings()
    settings.output = output
    settings.logger = log
    clasher = Clasher(settings)
    clasher.clash_sets = [clash_set]
    clasher.clash()
    clasher.export_json()
    with open(output, encoding="utf-8") as handle:
        results = json.load(handle)
    if not results:
        return {"name": clash_set.get("name", ""), "clashes": {}}
    return results[0]


def clash_count(result: dict[str, Any]) -> int:
    return len(result.get("clashes", {}))


def clashes_list(result: dict[str, Any]) -> list[dict[str, Any]]:
    clashes = result.get("clashes", {})
    out = []
    for key, data in clashes.items():
        item = dict(data)
        item["clash_key"] = key
        pos = item.get("p1"), item.get("p2")
        if pos[0] and pos[1]:
            item["position"] = [(pos[0][i] + pos[1][i]) / 2 for i in range(3)]
        out.append(item)
    return out


def run_clash_set_prefiltered(
    clash_set: dict[str, Any],
    logger: logging.Logger | None = None,
    *,
    tiers: tuple[str, ...] = ("solve",),
    verify_ag: bool = False,
    vendor: Any = None,
    clash_mode: str | None = None,
) -> dict[str, Any]:
    """Run IfcClash and return result with solve-tier prefilter applied."""
    from ag_ifc.clash_prefilter import prefilter_ifcclash_result

    raw = run_clash_set(clash_set, logger)
    mode = clash_mode or clash_set.get("mode", "intersection")
    return prefilter_ifcclash_result(
        raw,
        tiers=tiers,  # type: ignore[arg-type]
        verify_ag=verify_ag,
        vendor=vendor,
        clash_mode=mode,
        move_side=clash_set.get("move_side", "auto"),
    )
