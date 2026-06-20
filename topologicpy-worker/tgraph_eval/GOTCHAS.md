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

7. **The TGraph-vs-Graph fidelity gap is DISCIPLINE-DEPENDENT — and Structural is the
   ONLY drop-in.** Measured: Structural Jaccard 1.0 (identical); Electrical 0.25 (4×),
   Mechanical 0.17 (6×), Architecture 0.11 (9×). A tempting "MEP-vs-non-MEP" rule (true
   after 3 models) was **refuted by Architecture** (not MEP, yet decomposes most). The
   real driver is element decomposition (voids/openings, space boundaries, ports, nested
   assemblies) — structural single-solids have none, so they map 1:1. Lesson: never
   generalize the fidelity pattern from a subset of disciplines; check each.
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

## Memory measurement

18. **The per-model `rss_mb` in a matrix run is NOT per-engine and NOT per-model.**
    `resource.getrusage().ru_maxrss` is the **process** peak and monotonic, so in a
    single-process multi-model run each model's value is the *cumulative* peak so far,
    and within a model it includes **both graphs + the NetworkX copy + numpy/scipy
    baseline**. It cannot separate TGraph from Graph RAM. To compare engine memory, use
    `--mem-probe graph|tgraph` (build ONE engine in a fresh process; reports peak −
    baseline = that graph's footprint, and approx bytes/vertex). Run one container per
    engine. _Design expectation: TGraph (Python dict records) is lighter per node than
    legacy Graph (OCCT/Topologic C++ shape wrappers), but builds 4–6× more nodes on MEP,
    so absolute footprint is discipline-dependent — measure, don't assume._

## Scalability limits found on large graphs

19. **TGraph `Bridges` / `CutVertices` error on large graphs** (A1 Architecture,
    89 756 nodes → `tgraph error`, almost certainly `RecursionError`: both use a recursive
    DFS, Python's default limit is ~1000). They worked on E1 (26 832 nodes). So TGraph's
    articulation/bridge finders are **not safe on big decomposed graphs** without raising
    the recursion limit or an iterative rewrite. Confirm the exact exception in the
    model's JSON `errors[]`.
20. **Legacy ops overrun the per-op SIGALRM timeout** when stuck in native/C calls:
    A1 legacy `cut_vertices` ran **255 s** and `community` **395 s** despite a 180 s cap.
    The wall-clock budget is best-effort, not a hard kill (see #11). For a hard cap you'd
    need a subprocess-per-op (too costly here — each build is minutes).

## Resource / stewardship

21. **The full heavy matrix is expensive on a shared host.** Legacy `ByIFCFile` is the
    long pole (E1 18 MB → 125 s; M1 29 MB → 569 s; A1 50 MB → 444 s — roughly superlinear),
    and each heavy op adds a 180 s timeout. On a 12-core box shared with the live stacks,
    a 4-CPU run pushes load to ~100%. The 4 light/medium disciplines already establish the
    full fidelity+speed pattern; the 125/142 MB models mostly *confirm* it. Weigh the ~2 h
    of saturated host time against the marginal insight.

## Environment

15. **Host has `python3`, not `python`** (the eval *container* has `python`). Use
    `python3 -m py_compile` for host-side syntax checks.
16. **Run the container as uid 1000** (matches the host user) so files written to the
    mounted `/results` are owned correctly, not by root.
17. **Build context = ifcpipeline repo root** (so the Dockerfile can COPY
    `topologicpy-worker/…`). `.dockerignore` excludes `shared/uploads` — fine, models
    are bind-mounted read-only at run time, not COPYed.
