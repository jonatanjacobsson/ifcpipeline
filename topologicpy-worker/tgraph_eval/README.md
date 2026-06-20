# TGraph evaluation suite

Evaluate topologicpy **0.9.50+**'s new Python-native **`TGraph`** against the
legacy **`Graph`** API (and **NetworkX** as an independent oracle) for
**accuracy, fidelity and speed**, across IFC models of different **disciplines**
and **sizes**, before deciding whether to migrate the worker's ingest scripts.

> Branch: `eval/tgraph-0.9.50`. The worker still ships **0.9.43** on `main`; this
> branch bumps `topologicpy-worker/requirements.txt` to `>=0.9.50`. The eval runs
> in an **isolated side image** — it never touches the live `topologicpy-worker`
> container, the RQ queue, or any compose stack.

## Why TGraph

TGraph stores graphs as explicit Python records (stable integer indices, per-node
/ per-edge dictionaries, direct adjacency) instead of wrapping Topologic core
objects. The vendor's published numbers (4.6k-vertex graph) claim large speedups
while matching `Graph`/NetworkX correctness:

| Operation | Graph | TGraph |
|---|---|---|
| Edge retrieval | 10.25 s | 3.10 ms |
| Shortest path | 1.63 s | 3.21 ms |
| Graph metrics | 21.29 s | 1.08 ms |

This suite measures whether that holds on **real federated IFC building graphs**.

## What it measures

- **Fidelity** — do both engines build the *same* building graph? Vertex/edge
  count parity and **Jaccard overlap of the vertex & edge sets keyed on
  `IFC_global_id`**. (1.0 = identical graph; anything less is a real difference
  in how the two `ByIFCFile` implementations interpret the model.)
- **Accuracy** — do the graph algorithms agree numerically? Per-vertex centrality
  value deltas (max/mean abs diff) and **rank correlation**, computed three ways:
  `Graph` vs `TGraph`, and each vs **NetworkX** (the oracle, via
  `TGraph.NetworkXGraph`). Plus bridge/cut-vertex set agreement and
  shortest-path hop-count agreement.
- **Speed** — wall-clock per operation (median of `--repeats`), the **TGraph/Graph
  speedup factor**, and peak RSS.

Operations covered: `construct`, `vertices`, `edges`, `degree`, `betweenness`,
`closeness`, `shortest_path`, `bridges`, `cut_vertices`, `community`, `adjacent`.

## Model matrix (`models.py`)

| Key | Discipline | Size | Heavy |
|---|---|---|---|
| `E1` | Electrical | ~18 MB | |
| `S2` | Structural | ~18 MB | |
| `M1` | Mechanical | ~29 MB | |
| `A1` | Architecture | ~50 MB | |
| `P1` | Plumbing | ~125 MB | ✓ |
| `AX` | Architecture | ~142 MB | ✓ |

Heavy models are excluded unless `--heavy` is passed (legacy betweenness on them
can run for minutes). Smoke model = `E1`.

## Build

Build context is the **ifcpipeline repo root** (so the Dockerfile can copy
`topologicpy-worker/...`):

```bash
cd ~/apps/ifcpipeline
docker build -f topologicpy-worker/tgraph_eval/Dockerfile.eval \
             -t topologicpy-tgraph-eval:0.9.50 .
```

## Run

The harness reads models from three read-only mounts and writes reports to
`/results`. Resource-bound the container and isolate its network so it can't
contend with the live stacks:

```bash
cd ~/apps/ifcpipeline

RES=$PWD/topologicpy-worker/tgraph_eval/results
COMMON="--rm --network none --cpus=4 --memory=12g \
  -v $PWD/shared/uploads:/uploads:ro \
  -v $PWD/../test-output/temp:/models_extra:ro \
  -v $PWD/../idswidget/shared/uploads/6:/models_xl:ro \
  -v $RES:/results \
  topologicpy-tgraph-eval:0.9.50"

# 0. verify the adapters against the real TGraph return shapes
docker run $COMMON --probe

# 1. smoke: one small model (E1), all ops
docker run $COMMON --smoke

# 2. curated non-heavy matrix (E1,S2,M1,A1)
docker run $COMMON --full

# 3. add the 125/142 MB models (long run, memory-bound)
docker run $COMMON --heavy --timeout 1800

# targeted: specific models / ops / repeats
docker run $COMMON --models E1,M1 --ops betweenness,closeness,shortest_path --repeats 3

# end-to-end ingest fidelity (legacy GraphCentrality vs the TGraph port)
docker run $COMMON --entrypoint python topologicpy-tgraph-eval:0.9.50 \
  -m tgraph_eval.ingest_fidelity --models E1 --metric all
```

> The `--memory=12g` cap turns a runaway centrality into an OOM kill of the
> throwaway container rather than host-wide memory pressure. Per-op `--timeout`
> (default 600 s) records a `timeout` status and moves on.

## Output (`results/`)

- `<key>.json` — full structured report per model.
- `summary.csv` — one row per (model, op): times, speedup, statuses, accuracy.
- `summary.md` — human-readable tables (construction & fidelity overview, then a
  per-model op table). Also printed to stdout.
- `ingest_fidelity_<key>.json` — relationship-set Jaccard + per-metric value
  deltas between the legacy and TGraph ingest scripts.

## Reading the report

- **`vertex_jaccard` / `edge_jaccard` < 1.0** → the two `ByIFCFile` paths produce
  different graphs. Inspect `vertices_only_legacy` / `vertices_only_tgraph` in the
  JSON. This is a **finding**, not a harness bug — it's exactly the fidelity
  question.
- **`speedup` (TG/G)** > 1 → TGraph faster on that op. `construct` speedup matters
  most for the ingest path (it dominates wall-clock on large models).
- **`pearson` / `spearman` ≈ 1.0** and small **`max_abs_diff`** vs NetworkX →
  TGraph's centrality is correct. Low correlation → investigate.
- **`status: timeout|error`** → op exceeded `--timeout` or raised; the rest of the
  run still completes.

## API-shift notes (encapsulated in `bench_core.py` adapters)

| Concern | Legacy `Graph` | `TGraph` |
|---|---|---|
| Construct | `ByIFCFile(p, transferDictionaries=True)` | `ByIFCFile(p, importMode="topology", dictionaryMode="basic")` |
| Vertices | topologic `Vertex` objects | `dict` records `{"index","dictionary":{…}}` |
| Edges | objects + `StartVertex`/`EndVertex` | index-based records with `"src"`/`"dst"` |
| Centrality | returns a *graph* (value in vertex dict) | returns a *list* + stores in vertex dict; `nxCompatible=True` |
| Community | `CommunityDetection` | `CommunityPartition` (louvain → python-igraph) |
| Bridges/cut | `Bridges/CutVertices(g, key=…)` → graph | `Bridges/CutVertices(g)` → list of records |

## Files

- `bench_core.py` — adapters + measurement engine (timeout-guarded).
- `models.py` — discipline×size matrix.
- `run_eval.py` — CLI driver, `--probe`, report writers.
- `GraphCentrality_TGraph.py` — TGraph port of `ingest_scripts/GraphCentrality.py`.
- `ingest_fidelity.py` — end-to-end legacy-vs-TGraph ingest diff.
- `Dockerfile.eval`, `requirements.eval.txt` — the isolated image.
