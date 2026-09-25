# topologicpy 0.9.65 → 0.9.80 (topologicpy-worker)

Pins: `topologicpy==0.9.80` + `topologic_core==8.0.4` (bumped together), backend
forced with `ENV TOPOLOGICPY_CORE_BACKEND=topologic_core`, and the image build
fails unless `kernel_smoke.py` finds a working kernel. Gated 2026-09-25.

## 0.9.70+ regressions and how the worker handles them

| | Regression on 0.9.80 + topologic_core | Our exposure | Handling |
|---|---|---|---|
| a | `KnowledgeGraph.ByTopology`: every literal serialised as a Python repr (`"_RDFLiteral(lexical='…')"`, 54,181 of 62,064 triples on A1); no `rdf:type`/BOT typing and no element↔element edges because the wheel does not ship `ontology/topologicpy.ttl`; IFC data properties moved to `dict:*`; 5 of 15 prefixes | `KnowledgeGraphExport` (opt-in) | `ingest_scripts/_kg_compat.py` fixes the literals at the source and restores the 0.9.65 vocabulary additively from the TGraph records; the TTL is written in the 0.9.65 format. No-op on 0.9.65 (byte-identical TTL, A1 and E1) |
| b | `Vertex.Distance` to room cells up to 0.66 m too far (`Vertex.Project` rewrite) | roomstamp `distance_mode=topologic` only; live and remote default is `bbox` | `tasks._guard_distance_mode`: `topologic` → `bbox` on ≥ 0.9.70, with a warning and `distance_mode_requested`/`distance_mode_note` in the report; override with `IFCTOPOLOGY_ALLOW_TOPOLOGIC_DISTANCE=1` |
| c | `CellComplex.ByCells` silently returns 0 cells | no call site; 0 calls in every gate run | `ingest_scripts/_topologic_guards.cellcomplex_by_cells`; a test fails on any direct call |
| d | `Topology.InternalVertex` ignores `timeout` | no call site; 0 calls in every gate run | `_topologic_guards.internal_vertex` (30 s daemon-thread timeout); same static test |
| e | TGraph disk-cache key includes the topologicpy version | every TGraph script | expected: one cold `ByIFCFile` per IFC file after deploy (A1 about +0.4 s, E1 about +1.7 s); stale 0.9.65 entries age out under the 1 GB LRU cap. Cache round trip on 0.9.80: warm = cold = cache-off fingerprints |

### Who consumes the KnowledgeGraphExport TTL

Nobody in production (checked 2026-09-25):

- **Code.** The only parser is this script's own `_materialize_edges` (`reason=True`). CDE `analysis_ops.py:1613` only passes the `artifacts` paths through. ADR-015 in cde and the hub is documentation only.
- **Clients.** n8n workflows (`n8n-workflows/`, `n8n/`), the `n8n-nodes-*` packages and the hub never send `KnowledgeGraphExport`.
- **Live data.** There are no `output/topology/kg/` objects in SeaweedFS, no CDE relationships with `topologic_reason_rdfs` or `topologic_ingest_KnowledgeGraphExport`, and no retained RQ job that ran it.

### Known remaining KG differences, by design

- `rdfs:label` now carries the IFC Name, not the vertex index. The index is still in `top:index`.
- The per-node provenance literal `top:generatedByMethod "TGraph.ByMeshData"` is not re-created.
- The 0.9.80 `dict:*` mirrors are kept, so there are more triples in total.
- With `reason=True`, 0.9.80 infers nothing: the wheel ships no ontology axioms, and the script logs a warning.
  - A1: materialized relationships 5,420 = 5,420, identical (subject, object, type).
  - E1: 53,287 vs 55,388. The missing edges are the 2,101 `isConnectedPortOf` edges that 0.9.65 derived via `owl:inverseOf`.
  - 26 further E1 edges differ in both versions. They differ only because two GUIDs (`…B$` and `…B_`) mint the same IRI, and which one wins changes from run to run. This is a pre-existing problem.

## Gates

Old = this branch's parent (fix/topologicpy-guards) on 0.9.65. New = this branch on 0.9.80. Both use topologic_core 8.0.4 and the topologic_core backend.

| Gate | Expected | 0.9.65 | 0.9.80 |
|---|---|---|---|
| Image build + kernel smoke (`ifcpipeline-topologicpy-worker:eval-0980`) | OK | OK (`:eval-guards`) | OK: TopologicCoreBackend, OCC not importable |
| ingest_bench, 17 cases, fingerprints | identical | baseline (= origin/main on all 17) | 15/17 identical. Both SpaceInteractions cases have identical rel count and `rel_sha256`; `rel_evidence_sha256` differs only by the embedded engine version (masked: identical) |
| SpaceInteractions E1×A1 | 915 identical | 915 | 915, identical with the version masked |
| `rooms_indexed` on A1 0003 | 243 | 243 | 243 |
| roomstamp cells on A1 (tol 0.01) | 237 mesh + 4 hull, `aae5349af340` | 237 + 4, `aae5349af340`, 53,316.696 m³ | 237 + 4, `aae5349af340`, 53,316.696 m³ |
| roomstamp end-to-end, 536 elements (E1-640/646 × A1), mesh/bbox and prism/bbox | identical rows | baseline | identical |
| roomstamp end-to-end, mesh/topologic | guard | Vertex.Distance, 1,301 calls | downgraded to bbox; rows = mesh/bbox, which differs from 0.9.65 topologic mode in 4/536 matched rooms |
| KG TTL on A1 vs 0.9.65 (golden 66,235 triples) | equivalent | byte-identical to origin/main | all golden triples present except 3,200 index labels + 3,202 `generatedByMethod`; `rdf:type` 3,692/3,692, `connectsTo` 1,721, `aggregates` 249, `hasPropertySet` 1,231, `ifcGUID` 3,201, 15 prefixes, 0 corrupted literals; 93,086 triples |
| KG TTL on E1 (golden 287,760) | equivalent | byte-identical | same pattern: `rdf:type` 10,872/10,872, `connectsTo` 18,840/18,840; missing only 12,232 labels + 9,549 `generatedByMethod` |
| Regressed-API calls during all runs | 0 | `ByCells` 0, `InternalVertex` 0 | 0, 0; `Vertex.Project` 0 (the regressed path is never reached) |
| pytest `tests/` | no new failures | 9 pre-existing failures (egress door linking, upload basename; same on origin/main) | same 9 |

Timings on 0.9.80 were within noise:

- SpaceInteractions E1×A1: 54.0 s → 54.2 s.
- Roomstamp cells: 11.2 s → 10.4 s.
- KG export on A1: 1.5 s → 2.4 s, because of the compat pass.

### Commands

`$OLD` is a checkout of the parent branch (fix/topologicpy-guards). `$S` is a scratch dir with `venv068` (0.9.68, used with the extracted 0.9.65 wheel first on `PYTHONPATH`) and `venv080` (0.9.80). Both have topologic_core 8.0.4 and ifcopenshell 0.8.4. `$U` is `shared/uploads`.

```bash
docker build -f topologicpy-worker/Dockerfile -t ifcpipeline-topologicpy-worker:eval-0980 .   # runs kernel_smoke.py
export TOPOLOGICPY_CORE_BACKEND=topologic_core INGEST_TGRAPH_CACHE=0
# ingest_bench (matrix = ingest_bench/matrix.json cases with host paths), old vs new
PYTHONPATH=$S/src/0.9.65:$OLD/topologicpy-worker $S/venv068/bin/python ingest_bench/bench.py --matrix matrix_local.json --out bench_065.json
PYTHONPATH=topologicpy-worker $S/venv080/bin/python ingest_bench/bench.py --matrix matrix_local.json --out bench_080.json
# roomstamp cells, SpaceInteractions canary, KG counts (also runs inside the image)
PYTHONPATH=.:topologicpy-worker $S/venv080/bin/python topologicpy-worker/ingest_bench/upgrade_gates.py $U
docker run --rm --network none -v $U:/uploads:ro -v $PWD/topologicpy-worker/ingest_bench/upgrade_gates.py:/tmp/g.py:ro \
  ifcpipeline-topologicpy-worker:eval-0980 python /tmp/g.py /uploads
python3 -m pytest tests -q
```

## Deploy

1. Run `make rebuild-ifc SVC=topologicpy-worker`.
2. Check the container:
   - `docker exec ifcpipeline-topologicpy-worker-1 python /app/kernel_smoke.py` prints `OK`, `topologicpy 0.9.80`, `TopologicCoreBackend`;
   - the first job logs `[topologicpy] runtime …`.
3. Run one A1 SpaceInteractions job and check `rooms_indexed=243`.
4. Run `make verify`.
5. For w1: the image goes out through `make remote-deploy`. Follow the CLAUDE.md DHCP/firewall triage first.

**Rollback:** the parent branch pins 0.9.65. The TGraph cache is keyed by version, so rolling back needs no cache cleanup.
