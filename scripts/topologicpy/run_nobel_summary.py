#!/usr/bin/env python3
import sys
sys.path.insert(0, "/app")
import tasks
from shared.classes import TopologicpyRequest, TopologyEngine

for label, engine in [("production", "topologicpy"), ("bbox_only", "bbox")]:
    req = TopologicpyRequest(
        spatial_files=["A1_2b_BIM_XXX_0003_00.ifc"],
        element_files=["E1_2b_BIM_XXX_600_00.ifc"],
        engine=TopologyEngine(engine),
        cell_mode="prism",
        distance_mode="bbox",
        resolve_unmatched_with_topologicpy=False,
        stamp=False,
        output_file=f"nobel-div-{label}.json",
    )
    out = tasks.run_roomstamp_benchmark(req.model_dump())
    s = out["summary"]
    print(
        label,
        {
            "matched": s["matched_count"],
            "unmatched": s["unmatched_count"],
            "ambiguous": s["ambiguous_match_count"],
            "seconds": round(out["benchmark"]["total_seconds"], 2),
            "topologic_containment_calls": s.get("topologic_containment_calls", 0),
        },
    )
