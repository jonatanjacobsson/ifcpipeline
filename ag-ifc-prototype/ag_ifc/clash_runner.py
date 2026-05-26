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
