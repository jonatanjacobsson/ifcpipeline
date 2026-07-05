# ingest_bench — benchmark + output-parity harness for the ingest scripts

Times every `ingest_scripts` Ingester against real IFC files **inside the live
topologicpy-worker container** (its 2 CPU / 6 GB cgroup makes numbers
prod-representative) and fingerprints the extracted output (stable hashes over
the relationship/element sets) so performance changes can be proven
output-identical.

## Usage (from the host)

```bash
# full matrix
./run_bench.sh <tag>

# subset, with cProfile dumps (results/<ns>/results/profiles/*.txt)
./run_bench.sh <tag> --profile --only GraphCentrality_A1spaces,SpaceAdjacency_A1spaces

# cache-cold comparison run (TGraph disk cache off)
INGEST_TGRAPH_CACHE=0 ./run_bench.sh <tag>-cold

# parallel-safe namespace (separate synced code copy + results dir)
BENCH_NS=myexperiment ./run_bench.sh try1 --only KnowledgeGraphExport_A1spaces
```

`run_bench.sh` rsyncs the **worktree** `ingest_scripts/` into
`shared/output/bench/<ns>/src/` and runs `bench.py` there via `docker exec`
with `PYTHONPATH` pointing at the synced copy — the container image's `/app`
code is never touched. Results: `shared/output/bench/<ns>/results/<tag>.json`.

Each case runs in a fresh subprocess (native crashes can't poison the batch)
and records `wall_extract_s`, `peak_rss_mb`, and a `fingerprint`:
`relationship_count`, `element_count`, `rel_sha256`, `elem_sha256` (sorted
content hashes) and the summary minus timing keys. Compare against
`shared/output/bench/main/results/baseline.json` (pre-optimization baseline,
2026-07-05).

Ad-hoc case without editing `matrix.json`:

```bash
docker exec -e PYTHONPATH=/output/bench/main/src ifcpipeline-topologicpy-worker-1 \
  python /output/bench/main/src/ingest_bench/bench.py \
  --single-case '{"case_id":"x","script":"MepTopology","files":["/uploads/F.ifc"],"kwargs":{}}'
```

## Test files

The matrix uses the Nobel project models (`*_2b_BIM_XXX_*`) staged in
`shared/uploads/` (some were pulled from the SeaweedFS `uploads/` prefix), plus
a roomstamped E1 under `shared/output/topology/`. `bench_P1_roomstamped.ifc`
(238 MB) is an optional scale-test download — delete it if disk is tight.

## Caveats

- Timings from concurrent bench runs contend for the container's 2 CPUs — only
  compare serial runs. Fingerprints are valid regardless.
- The TGraph disk cache (`INGEST_TGRAPH_CACHE`, default on; see
  `ingest_scripts/topograph.py`) makes repeat graph builds near-instant: wipe
  `/tmp/tgraph_cache` in the container (or export `INGEST_TGRAPH_CACHE=0`) when
  you need cold-build numbers.
- `ZonePartition` relationship hashes from before 2026-07 are not comparable:
  igraph's louvain was entropy-seeded per process until the RNG pin in
  `topograph._louvain_labels` (seed 17) made labels deterministic.
