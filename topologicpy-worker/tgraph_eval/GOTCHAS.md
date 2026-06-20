# TGraph 0.9.50 eval — gotchas log

Running log of surprises found while building/running the eval. Continuously
appended. Newest insights also feed [FINDINGS.md](FINDINGS.md).

## API / breaking changes (topologicpy 0.9.50)

1. **`Graph.ByIFCFile` dropped `transferDictionaries`.** 0.9.50 unified it onto
   `(file, importMode="topology", dictionaryMode="basic", …)`. The shipped ingest
   scripts (17 call sites) break with `TypeError: unexpected keyword argument
   'transferDictionaries'`.
2. **`Graph.StartVertex` / `Graph.EndVertex` removed.** Use `Edge.StartVertex(e)` /
   `Edge.EndVertex(e)` (edges from `Graph.Edges` are topologic Edges).
3. **`Graph.CommunityDetection` removed** → `Graph.CommunityPartition(g)` (returns a
   label list; louvain backend needs `python-igraph`).
4. **Centrality / bridges / cut-vertices now return *lists*, not graphs.**
   `Graph.BetweennessCentrality(g, key=…)` returns `list[float]` and stores values in
   the vertex dicts in place; `Graph.Bridges`/`CutVertices` return lists of records.
   Old code that did `cg = Graph.Bridges(...)` then walked a flagged graph breaks.
5. **TGraph is index-based.** `TGraph.Vertices` → dict records
   `{"index","dictionary":{…},"representation","active"}`; `TGraph.Edges` → records with
   integer `"src"`/`"dst"` (no StartVertex/EndVertex). Map indices→GlobalId via the
   vertex records.
6. **TGraph centrality returns a `list` aligned to active-vertex order** AND stores
   under `key` in each vertex dict — read it back from the dict (robust to ordering).

## Fidelity / semantics

7. **The TGraph-vs-Graph fidelity gap is DISCIPLINE-DEPENDENT** — do not generalize from
   one model. E1 (Electrical): TGraph builds 4× the vertices (Jaccard 0.25). S2
   (Structural): **identical** graphs (Jaccard 1.0). So TGraph is a true drop-in for some
   disciplines and a re-baseline for others (MEP/electrical with nested elements). Always
   check fidelity per discipline before assuming equivalence.
8. **The 4× larger graph makes O(V·E) algorithms slower on TGraph**, not faster:
   betweenness — legacy 116 s vs TGraph **timeout >600 s**. (But closeness was ~parity:
   legacy 31.6 s vs TGraph 32.2 s — TGraph's closeness is ~4× more efficient per node,
   netting to parity on the 4× graph. So it's op-dependent.)
9. **shortest_path endpoints** chosen from the TGraph/NetworkX graph may not be connected
   in the (smaller, different) legacy graph → legacy returns `None`. Pick endpoints that
   exist in both graphs for a strict comparison.

## Harness / ops gotchas

10. **Degree the naive way is O(V·E) and hangs.** `Graph.VertexDegree(g, v)` per vertex
    over thousands of vertices stalls past any sane timeout. Compute degree O(E) by
    counting incident edge endpoints in one pass (done in both adapters).
11. **SIGALRM per-op timeout works for pure-Python ops** (legacy bridges timed out
    cleanly at 90 s) but **cannot interrupt a long native/C call** — it fires only when
    control returns to Python. Keep ops Python-level where possible.
12. **The `--probe` path has no timeout** — an unguarded `TGraph.BetweennessCentrality`
    on the 26 832-node graph ran 5 min+ without finishing. The real `run_eval` wraps
    every op in the timeout; don't add heavy ops to probe.
13. **Heavy models need a separate build budget.** A 125/142 MB legacy build can exceed
    the per-op timeout; use `--build-timeout` (default 1800 s) so the model isn't
    skipped. Per-op `--timeout` stays short so slow ops cap quickly.
14. **Write reports incrementally.** A full matrix run is long; `run_eval` now persists
    each model's JSON + rewrites the rolling summary after every model so nothing is
    lost if a later heavy model hangs / OOMs / is killed.

## Environment

15. **Host has `python3`, not `python`** (the eval *container* has `python`). Use
    `python3 -m py_compile` for host-side syntax checks.
16. **Run the container as uid 1000** (matches the host user) so files written to the
    mounted `/results` are owned correctly, not by root.
17. **Build context = ifcpipeline repo root** (so the Dockerfile can COPY
    `topologicpy-worker/…`). `.dockerignore` excludes `shared/uploads` — fine, models
    are bind-mounted read-only at run time, not COPYed.
