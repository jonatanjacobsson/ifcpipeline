"""Semantic federation (KnowledgeGraph.MergeGraphs) vs geometric federation
(FederatedRelationships) — comparative eval for the LPG-vs-RDF question.

Runs inside the topologicpy-worker image (topologicpy>=0.9.52 + rdflib already
installed; ingest_scripts on PYTHONPATH=/app). Processes every *.ifc under the
mount point as ONE federated set and reports:

  semantic merge:  per-model triples, merged triples (+ dedup), cross-model
                   GlobalId overlap, and cross-model element<->element edges.
  geometric:       FederatedRelationships cross-discipline edges (by type).

The contrast it establishes: does semantic merge *discover* cross-model links, or
just union models? (Hypothesis: union + unified query/vocabulary — link discovery
stays the geometric job.)

Usage:  python run_eval.py /in
"""

from __future__ import annotations

import glob
import logging
import os
import re
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.ERROR)
log = logging.getLogger("fed_eval")

_GUID_PRED = re.compile(r"(ifc_guid|IFC_global_id|globalId|#guid$)", re.IGNORECASE)
_NON_EDGE_PRED = re.compile(
    r"(22-rdf-syntax-ns#type|rdf-schema#|2002/07/owl#|/skos/|/dc/|/dcterms/|vann#|#IFC_)",
    re.IGNORECASE,
)


def _ttl_iri_guid(ttl):
    import rdflib
    rg = rdflib.Graph()
    rg.parse(data=ttl, format="turtle")
    m = {}
    for s, p, o in rg:
        if _GUID_PRED.search(str(p)) and isinstance(o, rdflib.Literal):
            m[str(s)] = str(o)
    return rg, m


def main(indir):
    from ingest_scripts import load_script, topograph
    from topologicpy.TGraph import TGraph
    from topologicpy.KnowledgeGraph import KnowledgeGraph
    import rdflib

    models = sorted(glob.glob(os.path.join(indir, "*.ifc")))
    print("models (%d):" % len(models))
    for m in models:
        print("  -", os.path.basename(m), "%.1f MB" % (os.path.getsize(m) / 1e6))

    kgs = []
    per_model_guids = {}     # model -> set(guid)
    iri_owner = {}           # iri -> model (for cross-model edge detection)
    print("\n=== SEMANTIC (KnowledgeGraph) ===")
    for m in models:
        t0 = time.time()
        g = topograph.build_graph(m)
        # stamp ifc_guid so IRIs are GlobalId-unique (Ontology._uri_for_topology)
        for rec in TGraph.Vertices(g):
            d = rec.get("dictionary") if isinstance(rec, dict) else None
            if isinstance(d, dict):
                gid = d.get("IFC_global_id") or d.get("GlobalId")
                if gid and not d.get("ifc_guid"):
                    d["ifc_guid"] = gid
        kg = KnowledgeGraph.ByTopology(g, includeBOT=True, silent=True)
        ttl = kg.TurtleString()
        rg, iri_guid = _ttl_iri_guid(ttl)
        kgs.append(kg)
        guids = set(iri_guid.values())
        per_model_guids[m] = guids
        for iri in iri_guid:
            iri_owner[iri] = m
        print("  %-34s vertices=%4d triples=%5d guid_iris=%4d  (%.1fs)" % (
            os.path.basename(m), topograph.order(g), len(rg), len(guids), time.time() - t0))

    # merge
    merged = KnowledgeGraph.MergeGraphs(kgs)
    mttl = merged.TurtleString()
    mrg = rdflib.Graph(); mrg.parse(data=mttl, format="turtle")
    sum_individual = sum(len(rdflib.Graph().parse(data=k.TurtleString(), format="turtle"))
                         for k in kgs)
    print("\n  merged triples: %d   (sum of individuals: %d  -> %d deduped)" % (
        len(mrg), sum_individual, sum_individual - len(mrg)))

    # cross-model GlobalId overlap
    all_guids = [g for s in per_model_guids.values() for g in s]
    shared = set()
    seen = set()
    for g in all_guids:
        if g in seen:
            shared.add(g)
        seen.add(g)
    print("  cross-model shared GlobalIds: %d" % len(shared))

    # cross-model element<->element edges in the merged graph
    cross = 0
    intra = 0
    for s, p, o in mrg:
        ps = str(p)
        if _NON_EDGE_PRED.search(ps) or _GUID_PRED.search(ps):
            continue
        so, oo = iri_owner.get(str(s)), iri_owner.get(str(o))
        if so and oo:
            if so != oo:
                cross += 1
            else:
                intra += 1
    print("  element<->element edges in merged graph: intra-model=%d  cross-model=%d" % (intra, cross))

    # geometric federation on the same set
    print("\n=== GEOMETRIC (FederatedRelationships) ===")
    t0 = time.time()
    try:
        Fed = load_script("FederatedRelationships")
        fi = Fed(ifc_files=[Path(m) for m in models], log=log)
        fi.extract()
        fs = fi.get_summary()
        print("  cross-discipline relationships: %d  by_type=%s  (%.1fs)" % (
            fs.get("relationships", 0), fs.get("by_type", {}), time.time() - t0))
    except Exception as e:
        print("  FederatedRelationships failed:", repr(e))

    print("\nDONE")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/in")
