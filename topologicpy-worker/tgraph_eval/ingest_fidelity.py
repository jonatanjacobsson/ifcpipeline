"""End-to-end ingest fidelity: legacy GraphCentrality vs the TGraph port.

Runs the *real* legacy ingest script (`ingest_scripts.GraphCentrality`, built on
`Graph`) and the TGraph port (`tgraph_eval.GraphCentrality_TGraph`) on the same
IFC model, then diffs their `build_output()` payloads:

  * relationships — set overlap on (subject, object) GlobalId pairs (undirected)
  * elements      — match by GlobalId; per-metric value deltas (degree,
                    betweenness_centrality, closeness_centrality)

This answers "if we swapped the ingest engine to TGraph, would Graph Studio get
the same relationships and centrality numbers?" — the migration-readiness signal.

    python -m tgraph_eval.ingest_fidelity --models E1 --metric all
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from tgraph_eval import models

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ingest_fidelity")


def _rel_key(r: Dict[str, Any]):
    s, o = r["subject_global_id"], r["object_global_id"]
    return (s, o) if s <= o else (o, s)


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    u = len(a | b)
    return round(len(a & b) / u, 6) if u else 1.0


def _value_deltas(legacy_el: List[dict], tgraph_el: List[dict], field: str) -> Dict[str, Any]:
    la = {e["global_id"]: e.get(field) for e in legacy_el if field in e and e.get(field) is not None}
    ta = {e["global_id"]: e.get(field) for e in tgraph_el if field in e and e.get(field) is not None}
    common = sorted(set(la) & set(ta))
    out: Dict[str, Any] = {"field": field, "legacy_n": len(la), "tgraph_n": len(ta), "common": len(common)}
    if not common:
        return out
    diffs = [abs(float(la[k]) - float(ta[k])) for k in common]
    out["max_abs_diff"] = round(max(diffs), 8)
    out["mean_abs_diff"] = round(sum(diffs) / len(diffs), 8)
    out["exact_matches"] = sum(1 for d in diffs if d == 0)
    return out


def run_one(model, metric: str) -> Dict[str, Any]:
    # NB: the *shipped* ingest_scripts/GraphCentrality.py does NOT run on 0.9.50
    # (it calls Graph.ByIFCFile(transferDictionaries=True), removed in 0.9.50).
    # GraphCentrality_Legacy is that same logic migrated to the 0.9.50 Graph API,
    # so this is a fair engine-vs-engine diff with both sides on 0.9.50.
    from tgraph_eval.GraphCentrality_Legacy import Ingester as LegacyIngester
    from tgraph_eval.GraphCentrality_TGraph import Ingester as TGraphIngester

    path = Path(model.path)
    out: Dict[str, Any] = {"model": {"key": model.key, "discipline": model.discipline, "path": model.path}}

    log.info("[%s] legacy GraphCentrality ...", model.key)
    t0 = time.time()
    legacy = LegacyIngester([path], log, metric=metric)
    legacy.extract()
    legacy_out = legacy.build_output(source_files=[model.path])
    out["legacy_seconds"] = round(time.time() - t0, 2)

    log.info("[%s] TGraph GraphCentrality ...", model.key)
    t0 = time.time()
    tgr = TGraphIngester([path], log, metric=metric)
    tgr.extract()
    tgraph_out = tgr.build_output(source_files=[model.path])
    out["tgraph_seconds"] = round(time.time() - t0, 2)

    lrel = {_rel_key(r) for r in legacy_out["relationships"]}
    trel = {_rel_key(r) for r in tgraph_out["relationships"]}
    out["relationships"] = {
        "legacy_count": len(legacy_out["relationships"]),
        "tgraph_count": len(tgraph_out["relationships"]),
        "unique_legacy": len(lrel),
        "unique_tgraph": len(trel),
        "jaccard": _jaccard(lrel, trel),
        "only_legacy": len(lrel - trel),
        "only_tgraph": len(trel - lrel),
    }

    lel, tel = legacy_out["elements"], tgraph_out["elements"]
    lids = {e["global_id"] for e in lel}
    tids = {e["global_id"] for e in tel}
    out["elements"] = {
        "legacy_count": len(lel),
        "tgraph_count": len(tel),
        "id_jaccard": _jaccard(lids, tids),
        "metric_deltas": [
            _value_deltas(lel, tel, f)
            for f in ("degree", "betweenness_centrality", "closeness_centrality")
        ],
    }
    out["legacy_summary"] = legacy_out["summary"]
    out["tgraph_summary"] = tgraph_out["summary"]
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="End-to-end ingest fidelity: Graph vs TGraph")
    p.add_argument("--models", default=models.SMOKE_KEY, help="comma list of model keys")
    p.add_argument("--metric", default="all", help="betweenness|closeness|degree|all")
    p.add_argument("--out", default="/results", help="output directory")
    args = p.parse_args(argv)

    keys = [k.strip() for k in args.models.split(",") if k.strip()]
    os.makedirs(args.out, exist_ok=True)
    results = []
    for k in keys:
        rep = run_one(models.by_key(k), args.metric)
        results.append(rep)
        path = os.path.join(args.out, f"ingest_fidelity_{k}.json")
        with open(path, "w") as fh:
            json.dump(rep, fh, indent=2, default=str)
        log.info("wrote %s", path)
        print(json.dumps(rep, indent=2, default=str))

    return 0


if __name__ == "__main__":
    sys.exit(main())
