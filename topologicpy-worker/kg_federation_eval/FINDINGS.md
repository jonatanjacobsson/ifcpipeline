# KnowledgeGraph federation eval — semantic merge vs geometric federation

TopologicPy **0.9.52**, run in the `ifcpipeline-topologicpy-worker` image
(`kg_federation_eval/run_eval.py`). Question: when TopologicPy can now convert to RDF,
does `KnowledgeGraph.MergeGraphs` *connect* models we couldn't connect before — and how
does it relate to the geometric `FederatedRelationships` ingest (ADR-006)?

## Setup

Three **same-project, same-discipline** electrical models (federated sub-disciplines):

| model | size | TGraph vertices | RDF triples | GlobalId IRIs | build |
|-------|------|-----------------|-------------|---------------|-------|
| E1-631-SM-Lightning | 2.3 MB | 5021 | 101 567 | 5014 | 23.8 s |
| E1-632-SM-Power | 1.9 MB | 3122 | 63 730 | 3115 | 14.2 s |
| E1-646-SM-Fire_alarm | 1.8 MB | 2946 | 59 663 | 2940 | 13.4 s |

`ifc_guid` keying gives ~100 % GlobalId-unique IRIs (5014/5021 etc.) — the linchpin fix
works at real scale, not just on the toy model.

## Results

**Semantic merge (`MergeGraphs`):**
- merged = **162 822** triples; sum of individuals 224 960 → **62 138 deduped** (28 %).
- **cross-model shared GlobalIds: 2 880** — the same elements re-exported across the
  three discipline files (shared grid / primary equipment / reference objects — a normal
  federated-IFC pattern). RDF reconciles them automatically by IRI.
- element↔element edges in the merged graph: intra-model 13 381, **cross-model ≈ 717**
  (edges that now span what were separate models, thanks to the shared IRIs).

**Geometric federation (`FederatedRelationships`):** **0** relationships.
Correct by design — it only emits *cross-discipline* pairs (`classify_pair` returns
`None` for same-discipline), and all three models are electrical.

## Interpretation — they are complementary, not competing

The naive expectation ("merge just unions models, link discovery stays geometric") is
**wrong in the shared-identity case.** The two mechanisms cover different federation cases:

| | connects by | needs | finds here |
|--|-------------|-------|-----------|
| `MergeGraphs` (semantic) | **shared identity** (GlobalId IRIs) + shared vocabulary | no geometry, any discipline | 2 880 shared elements, ~717 cross-model edges |
| `FederatedRelationships` (geometric) | **spatial overlap** across disciplines | world coords, distinct identity | 0 (same discipline) |

- Federated discipline models that **re-export shared elements** (very common) → semantic
  merge reconciles them for free, no geometry. This is real "connect topologies we
  couldn't before," and it works where geometry finds nothing.
- Models with **distinct elements that overlap in space across disciplines**
  (MEP-through-wall) → geometric federation is the only thing that finds those edges; a
  semantic union won't, because there are no shared IRIs to join on.

**Recommendation:** keep both. Run `FederatedRelationships` for cross-discipline spatial
edges (ADR-006), and use the `KnowledgeGraph` semantic merge to reconcile shared-identity
elements across federated files and to expose a unified, SPARQL-queryable multi-model
graph. Neither is the LPG system of record — both feed it (geometric edges already; the
semantic cross-model edges via `KnowledgeGraphExport`'s materialize path).

## Caveats

- The 717 cross-model edge count is approximate: each IRI is attributed to a single
  owning model (last writer wins for shared IRIs), so the exact split of cross- vs
  intra-model edges around shared elements is fuzzy. The robust, exact metric is the
  **2 880 shared GlobalIds**.
- Reasoning (`Infer`) was off for this run — the question was about merge, not inference.
- A cross-*discipline* run (e.g. architecture + electrical, same project/coords) would
  show the mirror image: geometric federation finds edges, semantic merge finds few
  shared IRIs. Worth a follow-up with the `*_2b_BIM_XXX_` A1+E1 pair.
