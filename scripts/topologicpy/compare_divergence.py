#!/usr/bin/env python3
"""Compare roomstamp matching: production topologicpy vs bbox baseline."""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Dict, List, Optional, Tuple

import ifcopenshell

# Run from /app in worker image
sys.path.insert(0, "/app")
import tasks  # noqa: E402
from shared.classes import TopologicpyRequest, TopologyEngine, TopologySampleStrategy  # noqa: E402

SPATIAL = os.environ.get("COMPARE_SPATIAL", "A1_2b_BIM_XXX_0003_00.ifc")
ELEMENT = os.environ.get("COMPARE_ELEMENT", "E1_2b_BIM_XXX_600_00.ifc")
MAX_ELEMENTS = int(os.environ.get("COMPARE_MAX_ELEMENTS", "0")) or None


def _load_run(
    engine: str,
    *,
    cell_mode: Optional[str] = "prism",
    distance_mode: Optional[str] = "bbox",
) -> Tuple[Dict[str, Optional[str]], dict, float]:
    """Return element_global_id -> chosen_space_global_id (or None)."""
    request = TopologicpyRequest(
        spatial_files=[SPATIAL],
        element_files=[ELEMENT],
        engine=TopologyEngine(engine),
        sample_strategy=TopologySampleStrategy.PLACEMENT,
        stamp=False,
        report_detail="summary",
        resolve_ambiguous_with_topologicpy=True,
        resolve_unmatched_with_topologicpy=True,
        cell_mode=cell_mode,
        distance_mode=distance_mode,
    )
    tuning = tasks._tuning_from_request(request)
    stats = tasks._RunStats()
    topologicpy = tasks._topologicpy_status()
    selected = tasks._selected_engine(request, topologicpy)

    spatial_path = f"/uploads/{SPATIAL.lstrip('/uploads/')}"
    element_path = f"/uploads/{ELEMENT.lstrip('/uploads/')}"

    settings = tasks._geometry_settings()
    start = time.perf_counter()

    spaces: List[tasks.SpaceCandidate] = []
    for filename, path in [(SPATIAL, spatial_path)]:
        model = ifcopenshell.open(path)
        collected, _ = tasks._collect_spaces(
            model, filename, request.space_query, request.include_zones, settings
        )
        spaces.extend(collected)

    tasks._prebuild_space_cells(spaces, request.tolerance, selected, stats, tuning)
    space_index = tasks.SpaceIndex(spaces)

    element_model = ifcopenshell.open(element_path)
    elements, _ = tasks._collect_elements(
        element_model,
        ELEMENT,
        request.element_query,
        settings,
        request.sample_strategy,
        MAX_ELEMENTS,
    )

    mapping: Dict[str, Optional[str]] = {}
    for element in elements:
        matches = tasks._match_element_to_spaces(
            element,
            spaces,
            request.tolerance,
            selected,
            space_index=space_index,
            resolve_ambiguous=request.resolve_ambiguous_with_topologicpy,
            resolve_unmatched=request.resolve_unmatched_with_topologicpy,
            stats=stats,
            tuning=tuning,
        )
        chosen = matches[0].global_id if matches else None
        mapping[element.global_id] = chosen

    elapsed = time.perf_counter() - start
    meta = {
        "engine_selected": selected,
        "cell_mode": tuning.cell_mode,
        "distance_mode": tuning.distance_mode,
        "element_count": len(elements),
        "space_count": len(spaces),
        "elapsed_seconds": round(elapsed, 3),
        "elements_per_second": round(len(elements) / elapsed, 2) if elapsed else len(elements),
        "topologic_containment_calls": stats.topologic_containment_calls,
        "topologic_distance_calls": stats.topologic_distance_calls,
        "bbox_resolutions": stats.bbox_distance_resolutions,
    }
    return mapping, meta, elapsed


def main() -> None:
    print("=== Roomstamp divergence compare ===")
    print(f"spatial={SPATIAL} element={ELEMENT} max_elements={MAX_ELEMENTS or 'all'}")

    prod_map, prod_meta, _ = _load_run(
        "topologicpy",
        cell_mode="prism",
        distance_mode="bbox",
    )
    print("\n[production] topologicpy + prism + bbox distance")
    print(json.dumps(prod_meta, indent=2))

    bbox_map, bbox_meta, _ = _load_run("bbox")
    print("\n[baseline] engine=bbox")
    print(json.dumps(bbox_meta, indent=2))

    all_ids = sorted(set(prod_map) | set(bbox_map))
    diverged: List[dict] = []
    prod_only = bbox_only = different_space = agreed = 0

    for gid in all_ids:
        p = prod_map.get(gid)
        b = bbox_map.get(gid)
        if p == b:
            agreed += 1
            continue
        diverged.append({"element_global_id": gid, "production_space": p, "bbox_space": b})
        if p and not b:
            prod_only += 1
        elif b and not p:
            bbox_only += 1
        else:
            different_space += 1

    summary = {
        "total_elements": len(all_ids),
        "agreed": agreed,
        "diverged": len(diverged),
        "diverged_pct": round(100.0 * len(diverged) / len(all_ids), 2) if all_ids else 0,
        "production_matched_bbox_unmatched": prod_only,
        "bbox_matched_production_unmatched": bbox_only,
        "both_matched_different_space": different_space,
        "sample_divergences": diverged[:25],
    }

    print("\n=== Divergence summary ===")
    print(json.dumps(summary, indent=2))

    out_path = os.environ.get("COMPARE_OUTPUT", "/output/topology/e1_divergence_compare.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "spatial": SPATIAL,
                "element": ELEMENT,
                "production": prod_meta,
                "bbox_baseline": bbox_meta,
                "summary": summary,
                "divergences": diverged,
            },
            handle,
            indent=2,
        )
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
