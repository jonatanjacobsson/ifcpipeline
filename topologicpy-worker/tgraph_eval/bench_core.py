"""Benchmark engine: legacy ``Graph`` vs new ``TGraph`` vs NetworkX.

Three measurement axes, all keyed on the IFC GlobalId so the two topologicpy
graph engines can be compared element-for-element:

  * fidelity  — do both engines build the *same* building graph?
                (vertex/edge count parity, Jaccard of vertex & edge sets)
  * accuracy  — do graph algorithms agree numerically?
                (centrality value deltas + rank correlation vs NetworkX oracle)
  * speed     — wall-clock per operation + TGraph/Graph speedup, peak RSS.

Every operation is wrapped in a wall-clock timeout (SIGALRM) and a try/except,
so a slow or unsupported op degrades to a recorded status instead of aborting
the whole suite. This matters on the heavy models where legacy betweenness can
run for minutes.

API-shift notes (legacy Graph -> TGraph), encapsulated by the adapters below:
  * Graph.ByIFCFile(path, transferDictionaries=True)
        -> TGraph.ByIFCFile(path, importMode="topology", dictionaryMode="basic")
  * Graph.Vertices(g) -> topologic Vertex objects (read dict via Topology.Dictionary)
        TGraph.Vertices(g) -> list of dict records {"index", "dictionary": {...}}
  * Graph.Edges + Graph.StartVertex/EndVertex
        TGraph is index based: edge records carry "src"/"dst" vertex indices
  * Graph.BetweennessCentrality(g, key=) returns a *graph* (value in vertex dict)
        TGraph.BetweennessCentrality(g, key=) returns a *list* AND stores in dict
  * Graph.CommunityDetection -> TGraph.CommunityPartition (louvain, needs igraph)
  * Graph.Bridges/CutVertices(g, key=) return graphs
        TGraph.Bridges/CutVertices(g) return lists of records (no key arg)
"""

from __future__ import annotations

import math
import resource
import signal
import statistics
import time
import traceback
from contextlib import contextmanager
from typing import Any, Callable, Dict, List, Optional, Tuple

from topologicpy.Graph import Graph
from topologicpy.Topology import Topology
from topologicpy.Dictionary import Dictionary
from topologicpy.Edge import Edge
from topologicpy.TGraph import TGraph

try:
    import networkx as nx
    HAS_NX = True
except Exception:  # pragma: no cover
    HAS_NX = False

GID_KEY = "IFC_global_id"


# --------------------------------------------------------------------------- #
# timing / timeout primitives
# --------------------------------------------------------------------------- #

class OpTimeout(Exception):
    pass


@contextmanager
def time_limit(seconds: Optional[float]):
    """Raise OpTimeout if the block runs longer than `seconds` (0/None = no limit).

    Uses SIGALRM; the eval runs single-threaded in the main thread so this is safe.
    """
    if not seconds or seconds <= 0:
        yield
        return

    def _handler(signum, frame):
        raise OpTimeout()

    old = signal.signal(signal.SIGALRM, _handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def peak_rss_mb() -> float:
    # ru_maxrss is in KB on Linux.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def measure(fn: Callable[[], Any], repeats: int = 1, timeout: Optional[float] = None) -> Dict[str, Any]:
    """Run `fn` up to `repeats` times under a timeout; return timing + result + status."""
    times: List[float] = []
    result = None
    status = "ok"
    err = None
    for _ in range(max(1, repeats)):
        t0 = time.perf_counter()
        try:
            with time_limit(timeout):
                result = fn()
        except OpTimeout:
            status = "timeout"
            times.append(float(timeout))
            break
        except Exception as exc:  # noqa: BLE001
            status = "error"
            err = f"{type(exc).__name__}: {exc}"
            break
        times.append(time.perf_counter() - t0)
    out: Dict[str, Any] = {"status": status}
    if times:
        out["median_s"] = round(statistics.median(times), 6)
        out["min_s"] = round(min(times), 6)
        out["runs"] = len(times)
    if err:
        out["error"] = err
    return out, result


# --------------------------------------------------------------------------- #
# small stats helpers (numpy-free so the report never depends on heavy libs)
# --------------------------------------------------------------------------- #

def _pearson(a: List[float], b: List[float]) -> Optional[float]:
    n = len(a)
    if n < 2:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va == 0 or vb == 0:
        return 1.0 if va == vb else 0.0
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    return round(cov / math.sqrt(va * vb), 6)


def _ranks(vals: List[float]) -> List[float]:
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _spearman(a: List[float], b: List[float]) -> Optional[float]:
    if len(a) < 2:
        return None
    return _pearson(_ranks(a), _ranks(b))


def _topk_overlap(a: Dict[str, float], b: Dict[str, float], k: int = 10) -> Optional[float]:
    if not a or not b:
        return None
    ta = {x for x, _ in sorted(a.items(), key=lambda kv: kv[1], reverse=True)[:k]}
    tb = {x for x, _ in sorted(b.items(), key=lambda kv: kv[1], reverse=True)[:k]}
    if not ta:
        return None
    return round(len(ta & tb) / len(ta), 4)


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    u = len(a | b)
    return round(len(a & b) / u, 6) if u else 1.0


def compare_value_maps(name_a: str, a: Dict[str, float], name_b: str, b: Dict[str, float]) -> Dict[str, Any]:
    """Numerical agreement between two {gid: value} maps over their common keys."""
    common = sorted(set(a) & set(b))
    out: Dict[str, Any] = {"keys_a": len(a), "keys_b": len(b), "common": len(common)}
    if not common:
        return out
    va = [float(a[k] or 0.0) for k in common]
    vb = [float(b[k] or 0.0) for k in common]
    diffs = [abs(va[i] - vb[i]) for i in range(len(common))]
    out["max_abs_diff"] = round(max(diffs), 8)
    out["mean_abs_diff"] = round(sum(diffs) / len(diffs), 8)
    out["pearson"] = _pearson(va, vb)
    out["spearman"] = _spearman(va, vb)
    out["topk_overlap"] = _topk_overlap(a, b, 10)
    return out


def _ekey(a: str, b: str) -> Tuple[str, str]:
    return (a, b) if a <= b else (b, a)


# --------------------------------------------------------------------------- #
# legacy Graph adapters
# --------------------------------------------------------------------------- #

class LegacyAdapter:
    name = "Graph"

    @staticmethod
    def build(path: str):
        # NOTE: 0.9.50 dropped the old `transferDictionaries=True` kwarg and
        # unified Graph.ByIFCFile onto the same signature as TGraph.ByIFCFile
        # (importMode / dictionaryMode). Identical import params for both engines
        # = fairest comparison. The live ingest scripts (0.9.43) still call the
        # old signature and must be updated when the worker upgrades.
        return Graph.ByIFCFile(path, importMode="topology", dictionaryMode="basic", silent=True)

    @staticmethod
    def vertex_gid_map(g) -> Dict[str, Any]:
        """gid -> topologic vertex (first wins)."""
        out: Dict[str, Any] = {}
        for v in Graph.Vertices(g):
            d = Topology.Dictionary(v)
            gid = Dictionary.ValueAtKey(d, GID_KEY) if d else None
            if gid and gid not in out:
                out[gid] = v
        return out

    @staticmethod
    def vertex_set(g) -> set:
        return set(LegacyAdapter.vertex_gid_map(g).keys())

    @staticmethod
    def _edge_endpoints_gid(e) -> Tuple[Optional[str], Optional[str]]:
        # 0.9.50 removed Graph.StartVertex/EndVertex; edges are topologic Edges
        # whose endpoints come from Edge.StartVertex/EndVertex.
        sv = Edge.StartVertex(e)
        ev = Edge.EndVertex(e)
        sd = Topology.Dictionary(sv) if sv else None
        ed = Topology.Dictionary(ev) if ev else None
        s = Dictionary.ValueAtKey(sd, GID_KEY) if sd else None
        t = Dictionary.ValueAtKey(ed, GID_KEY) if ed else None
        return s, t

    @staticmethod
    def edge_set(g) -> set:
        edges = set()
        for e in Graph.Edges(g):
            s, t = LegacyAdapter._edge_endpoints_gid(e)
            if s and t:
                edges.add(_ekey(s, t))
        return edges

    @staticmethod
    def order(g) -> int:
        return len(Graph.Vertices(g))

    @staticmethod
    def size(g) -> int:
        return len(Graph.Edges(g))

    @staticmethod
    def centrality(g, kind: str) -> Dict[str, float]:
        # 0.9.50: returns a list AND stores values into vertex dicts in place
        # (nxCompatible=True by default). Read them back gid-keyed from the graph.
        key = {"betweenness": "betweenness_centrality", "closeness": "closeness_centrality"}[kind]
        fn = {"betweenness": Graph.BetweennessCentrality, "closeness": Graph.ClosenessCentrality}[kind]
        vals = fn(g, key=key, silent=True)
        out: Dict[str, float] = {}
        verts = Graph.Vertices(g)
        for i, v in enumerate(verts):
            d = Topology.Dictionary(v)
            if not d:
                continue
            gid = Dictionary.ValueAtKey(d, GID_KEY)
            val = Dictionary.ValueAtKey(d, key)
            if val is None and isinstance(vals, list) and i < len(vals):
                val = vals[i]
            if gid is not None and val is not None:
                out[gid] = float(val)
        return out

    @staticmethod
    def degree_map(g) -> Dict[str, int]:
        # O(E): count incident edge endpoints once, instead of O(V*E) per-vertex
        # Graph.VertexDegree calls.
        out: Dict[str, int] = {}
        for v in Graph.Vertices(g):
            d = Topology.Dictionary(v)
            gid = Dictionary.ValueAtKey(d, GID_KEY) if d else None
            if gid is not None:
                out.setdefault(gid, 0)
        for e in Graph.Edges(g):
            s, t = LegacyAdapter._edge_endpoints_gid(e)
            if s in out:
                out[s] += 1
            if t in out:
                out[t] += 1
        return out

    @staticmethod
    def bridge_set(g) -> set:
        # 0.9.50: Graph.Bridges returns a *list* of bridge edges (was a flagged graph).
        edges = set()
        for e in Graph.Bridges(g, silent=True):
            s, t = LegacyAdapter._edge_endpoints_gid(e)
            if s and t:
                edges.add(_ekey(s, t))
        return edges

    @staticmethod
    def cut_vertex_set(g) -> set:
        # 0.9.50: Graph.CutVertices returns a *list* of articulation vertices.
        out = set()
        for v in Graph.CutVertices(g, silent=True):
            d = Topology.Dictionary(v)
            if not d:
                continue
            gid = Dictionary.ValueAtKey(d, GID_KEY)
            if gid is not None:
                out.add(gid)
        return out

    @staticmethod
    def num_communities(g) -> int:
        # 0.9.50: CommunityDetection removed; CommunityPartition returns a label list.
        labels = Graph.CommunityPartition(g, silent=True)
        return len(set(labels)) if labels else 0

    @staticmethod
    def shortest_path_hops(g, gid_v: Dict[str, Any], src: str, tgt: str) -> Optional[int]:
        a, b = gid_v.get(src), gid_v.get(tgt)
        if a is None or b is None:
            return None
        # edgeKey="" -> unweighted (hop count), comparable to the NetworkX/TGraph BFS.
        path = Graph.ShortestPath(g, a, b, edgeKey="", directed=False)
        if path is None:
            return None
        try:
            return len(Topology.Edges(path))
        except Exception:
            return None


# --------------------------------------------------------------------------- #
# TGraph adapters
# --------------------------------------------------------------------------- #

class TGraphAdapter:
    name = "TGraph"

    @staticmethod
    def build(path: str):
        return TGraph.ByIFCFile(path, importMode="topology", dictionaryMode="basic", silent=True)

    @staticmethod
    def _vertices(g) -> List[dict]:
        return TGraph.Vertices(g)

    @staticmethod
    def idx2gid(g, records: Optional[List[dict]] = None) -> Dict[int, Any]:
        records = records if records is not None else TGraph.Vertices(g)
        out: Dict[int, Any] = {}
        for r in records:
            idx = r.get("index")
            gid = (r.get("dictionary") or {}).get(GID_KEY)
            if idx is not None:
                out[idx] = gid
        return out

    @staticmethod
    def gid2idx(g, records: Optional[List[dict]] = None) -> Dict[Any, int]:
        records = records if records is not None else TGraph.Vertices(g)
        out: Dict[Any, int] = {}
        for r in records:
            gid = (r.get("dictionary") or {}).get(GID_KEY)
            idx = r.get("index")
            if gid is not None and gid not in out and idx is not None:
                out[gid] = idx
        return out

    @staticmethod
    def vertex_set(g) -> set:
        return {(r.get("dictionary") or {}).get(GID_KEY) for r in TGraph.Vertices(g)} - {None}

    @staticmethod
    def _edge_records(g) -> List[dict]:
        edges = TGraph.Edges(g)
        if edges and isinstance(edges[0], dict) and "src" in edges[0]:
            return edges
        # Fallback: private active-edge records (same structure Bridges() uses).
        raw = getattr(g, "_edges", None) or []
        return [e for e in raw if isinstance(e, dict) and e.get("active", True)]

    @staticmethod
    def edge_set(g) -> set:
        idx2gid = TGraphAdapter.idx2gid(g)
        edges = set()
        for e in TGraphAdapter._edge_records(g):
            s = idx2gid.get(e.get("src"))
            t = idx2gid.get(e.get("dst"))
            if s and t:
                edges.add(_ekey(s, t))
        return edges

    @staticmethod
    def order(g) -> int:
        return TGraph.Order(g)

    @staticmethod
    def size(g) -> int:
        return TGraph.Size(g)

    @staticmethod
    def centrality(g, kind: str) -> Dict[str, float]:
        key = {"betweenness": "betweenness_centrality", "closeness": "closeness_centrality"}[kind]
        fn = {"betweenness": TGraph.BetweennessCentrality, "closeness": TGraph.ClosenessCentrality}[kind]
        # nxCompatible=True (default) -> NetworkX-style normalization; stores into vertex dicts.
        fn(g, key=key, silent=True)
        out: Dict[str, float] = {}
        for r in TGraph.Vertices(g):
            d = r.get("dictionary") or {}
            gid = d.get(GID_KEY)
            if gid is not None and key in d and d[key] is not None:
                out[gid] = float(d[key])
        return out

    @staticmethod
    def degree_map(g) -> Dict[str, int]:
        # O(E) to match the legacy adapter: count incident edge endpoints.
        idx2gid = TGraphAdapter.idx2gid(g)
        out: Dict[str, int] = {gid: 0 for gid in idx2gid.values() if gid is not None}
        for e in TGraphAdapter._edge_records(g):
            s = idx2gid.get(e.get("src"))
            t = idx2gid.get(e.get("dst"))
            if s in out:
                out[s] += 1
            if t in out:
                out[t] += 1
        return out

    @staticmethod
    def bridge_set(g) -> set:
        idx2gid = TGraphAdapter.idx2gid(g)
        edges = set()
        for e in TGraph.Bridges(g):
            s = idx2gid.get(e.get("src") if isinstance(e, dict) else None)
            t = idx2gid.get(e.get("dst") if isinstance(e, dict) else None)
            if s and t:
                edges.add(_ekey(s, t))
        return edges

    @staticmethod
    def cut_vertex_set(g) -> set:
        out = set()
        for r in TGraph.CutVertices(g):
            gid = (r.get("dictionary") or {}).get(GID_KEY) if isinstance(r, dict) else None
            if gid is not None:
                out.add(gid)
        return out

    @staticmethod
    def num_communities(g, algorithm: str = "louvain") -> int:
        labels = TGraph.CommunityPartition(g, algorithm=algorithm, silent=True)
        return len(set(labels)) if labels else 0

    @staticmethod
    def shortest_path_hops(g, gid2idx: Dict[Any, int], src: str, tgt: str) -> Optional[int]:
        a, b = gid2idx.get(src), gid2idx.get(tgt)
        if a is None or b is None:
            return None
        path = TGraph.ShortestPath(g, a, b, mode="all")
        if not path:
            return None
        return len(path) - 1


# --------------------------------------------------------------------------- #
# NetworkX oracle (independent ground truth)
# --------------------------------------------------------------------------- #

class NxOracle:
    name = "NetworkX"

    @staticmethod
    def from_tgraph(g):
        if not HAS_NX:
            return None
        return TGraph.NetworkXGraph(g, nodeIDKey=GID_KEY)

    @staticmethod
    def centrality(nxg, kind: str) -> Dict[str, float]:
        if kind == "betweenness":
            return nx.betweenness_centrality(nxg, normalized=True)
        return nx.closeness_centrality(nxg)

    @staticmethod
    def endpoints(nxg) -> Optional[Tuple[Any, Any, int]]:
        """Pick a deterministic, definitely-connected (src,tgt) pair: the farthest
        node from the highest-degree node, by BFS."""
        if nxg.number_of_nodes() == 0:
            return None
        src = max(nxg.nodes, key=lambda n: nxg.degree(n))
        lengths = nx.single_source_shortest_path_length(nxg, src)
        if len(lengths) < 2:
            return None
        tgt = max(lengths, key=lengths.get)
        return src, tgt, lengths[tgt]


# --------------------------------------------------------------------------- #
# per-model driver
# --------------------------------------------------------------------------- #

DEFAULT_OPS = [
    "vertices", "edges", "degree", "betweenness", "closeness",
    "shortest_path", "bridges", "cut_vertices", "community", "adjacent",
]


def _secs(entry: Dict[str, Any]) -> str:
    if entry.get("status") == "ok":
        return f"{entry.get('median_s')}s"
    return entry.get("status", "?")


def _speedup(legacy: Dict[str, Any], tgraph: Dict[str, Any]) -> Optional[float]:
    if legacy.get("status") == "ok" and tgraph.get("status") == "ok":
        ls, ts = legacy.get("median_s"), tgraph.get("median_s")
        if ls and ts and ts > 0:
            return round(ls / ts, 2)
    return None


def run_model(model, ops: List[str], repeats: int = 1, timeout: Optional[float] = 600.0,
              build_timeout: Optional[float] = 1800.0, log=print) -> Dict[str, Any]:
    """Build both graphs once, then benchmark each op. Returns a structured report.

    `build_timeout` bounds the (potentially very slow) ByIFCFile construction
    separately from the per-op `timeout`, so a heavy model that takes >timeout to
    build is not silently skipped.
    """
    rep: Dict[str, Any] = {
        "model": {"key": model.key, "discipline": model.discipline,
                  "path": model.path, "size_mb": model.size_mb},
        "topologicpy_version": _tpy_version(),
        "construct": {},
        "fidelity": {},
        "ops": {},
        "errors": [],
        "rss_mb": None,
    }

    # ---- construction (bounded by build_timeout, not the per-op timeout) ----
    log(f"[{model.key}] building legacy Graph ...")
    lt, g = measure(lambda: LegacyAdapter.build(model.path), repeats=1, timeout=build_timeout)
    log(f"[{model.key}] building TGraph ...")
    tt, tg = measure(lambda: TGraphAdapter.build(model.path), repeats=1, timeout=build_timeout)
    rep["construct"] = {
        "legacy": lt, "tgraph": tt, "speedup": _speedup(lt, tt),
    }
    if g is None or lt["status"] != "ok":
        rep["errors"].append(f"legacy build failed: {lt}")
    if tg is None or tt["status"] != "ok":
        rep["errors"].append(f"tgraph build failed: {tt}")
    if g is None or tg is None:
        rep["rss_mb"] = round(peak_rss_mb(), 1)
        return rep

    # ---- fidelity: do both engines describe the same building? ----
    try:
        lv, tv = LegacyAdapter.vertex_set(g), TGraphAdapter.vertex_set(tg)
        le, te = LegacyAdapter.edge_set(g), TGraphAdapter.edge_set(tg)
        rep["fidelity"] = {
            "legacy_order": LegacyAdapter.order(g), "tgraph_order": TGraphAdapter.order(tg),
            "legacy_size": LegacyAdapter.size(g), "tgraph_size": TGraphAdapter.size(tg),
            "legacy_gid_vertices": len(lv), "tgraph_gid_vertices": len(tv),
            "vertex_jaccard": jaccard(lv, tv),
            "edge_jaccard": jaccard(le, te),
            "vertices_only_legacy": len(lv - tv),
            "vertices_only_tgraph": len(tv - lv),
            "edges_only_legacy": len(le - te),
            "edges_only_tgraph": len(te - le),
        }
        f = rep["fidelity"]
        log(f"[{model.key}] construct: Graph {_secs(lt)} / TGraph {_secs(tt)} (speedup {rep['construct']['speedup']})")
        log(f"[{model.key}] fidelity: |V| Graph={f['legacy_order']} TGraph={f['tgraph_order']} "
            f"| |E| Graph={f['legacy_size']} TGraph={f['tgraph_size']} "
            f"| vtx Jaccard={f['vertex_jaccard']} edge Jaccard={f['edge_jaccard']}")
    except Exception as exc:  # noqa: BLE001
        rep["errors"].append(f"fidelity: {type(exc).__name__}: {exc}")

    # Pre-compute shared lookups once.
    legacy_gid_v = _safe(lambda: LegacyAdapter.vertex_gid_map(g), {})
    tg_gid2idx = _safe(lambda: TGraphAdapter.gid2idx(tg), {})
    nxg = _safe(lambda: NxOracle.from_tgraph(tg), None)

    def do(op: str, legacy_fn, tgraph_fn):
        entry: Dict[str, Any] = {}
        log(f"  [{model.key}] {op}: legacy ...")
        lr, lres = measure(legacy_fn, repeats=repeats, timeout=timeout)
        log(f"  [{model.key}] {op}: legacy {_secs(lr)}; tgraph ...")
        tr, tres = measure(tgraph_fn, repeats=repeats, timeout=timeout)
        log(f"  [{model.key}] {op}: tgraph {_secs(tr)}")
        entry["legacy"] = lr
        entry["tgraph"] = tr
        entry["speedup"] = _speedup(lr, tr)
        rep["ops"][op] = entry
        return lres, tres

    for op in ops:
        log(f"[{model.key}] op: {op} ...")
        try:
            if op == "vertices":
                do("vertices", lambda: Graph.Vertices(g), lambda: TGraph.Vertices(tg))

            elif op == "edges":
                do("edges", lambda: Graph.Edges(g), lambda: TGraph.Edges(tg))

            elif op == "degree":
                lres, tres = do("degree", lambda: LegacyAdapter.degree_map(g),
                                lambda: TGraphAdapter.degree_map(tg))
                if isinstance(lres, dict) and isinstance(tres, dict):
                    rep["ops"]["degree"]["accuracy"] = compare_value_maps("Graph", lres, "TGraph", tres)

            elif op in ("betweenness", "closeness"):
                lres, tres = do(op, lambda k=op: LegacyAdapter.centrality(g, k),
                                lambda k=op: TGraphAdapter.centrality(tg, k))
                acc: Dict[str, Any] = {}
                if isinstance(lres, dict) and isinstance(tres, dict):
                    acc["graph_vs_tgraph"] = compare_value_maps("Graph", lres, "TGraph", tres)
                if nxg is not None and HAS_NX:
                    nr, nres = measure(lambda k=op: NxOracle.centrality(nxg, k),
                                       repeats=1, timeout=timeout)
                    rep["ops"][op]["networkx"] = nr
                    if isinstance(nres, dict):
                        if isinstance(tres, dict):
                            acc["tgraph_vs_networkx"] = compare_value_maps("TGraph", tres, "NetworkX", nres)
                        if isinstance(lres, dict):
                            acc["graph_vs_networkx"] = compare_value_maps("Graph", lres, "NetworkX", nres)
                rep["ops"][op]["accuracy"] = acc

            elif op == "shortest_path":
                ep = NxOracle.endpoints(nxg) if (nxg is not None and HAS_NX) else None
                if ep is None:
                    rep["ops"]["shortest_path"] = {"status": "skipped", "reason": "no nx endpoints"}
                else:
                    src, tgt, nx_hops = ep
                    lres, tres = do("shortest_path",
                                    lambda: LegacyAdapter.shortest_path_hops(g, legacy_gid_v, src, tgt),
                                    lambda: TGraphAdapter.shortest_path_hops(tg, tg_gid2idx, src, tgt))
                    rep["ops"]["shortest_path"]["accuracy"] = {
                        "endpoints": [str(src), str(tgt)],
                        "networkx_hops": nx_hops,
                        "legacy_hops": lres,
                        "tgraph_hops": tres,
                        "legacy_matches_nx": lres == nx_hops,
                        "tgraph_matches_nx": tres == nx_hops,
                    }

            elif op == "bridges":
                lres, tres = do("bridges", lambda: LegacyAdapter.bridge_set(g),
                                lambda: TGraphAdapter.bridge_set(tg))
                if isinstance(lres, set) and isinstance(tres, set):
                    rep["ops"]["bridges"]["accuracy"] = {
                        "legacy_count": len(lres), "tgraph_count": len(tres),
                        "jaccard": jaccard(lres, tres),
                    }

            elif op == "cut_vertices":
                lres, tres = do("cut_vertices", lambda: LegacyAdapter.cut_vertex_set(g),
                                lambda: TGraphAdapter.cut_vertex_set(tg))
                if isinstance(lres, set) and isinstance(tres, set):
                    rep["ops"]["cut_vertices"]["accuracy"] = {
                        "legacy_count": len(lres), "tgraph_count": len(tres),
                        "jaccard": jaccard(lres, tres),
                    }

            elif op == "community":
                lres, tres = do("community", lambda: LegacyAdapter.num_communities(g),
                                lambda: TGraphAdapter.num_communities(tg))
                rep["ops"]["community"]["accuracy"] = {
                    "legacy_communities": lres, "tgraph_communities": tres,
                    "note": "label-permutation invariant; counts only (not 1:1 comparable)",
                }

            elif op == "adjacent":
                # Sample the first ~200 vertices for a representative neighbour-lookup time.
                lverts = Graph.Vertices(g)[:200]
                tidx = list(TGraphAdapter.idx2gid(tg).keys())[:200]
                do("adjacent",
                   lambda: [Graph.AdjacentVertices(g, v) for v in lverts],
                   lambda: [TGraph.AdjacentVertices(tg, i) for i in tidx])
        except Exception as exc:  # noqa: BLE001
            rep["errors"].append(f"op {op}: {type(exc).__name__}: {exc}")
            rep["ops"].setdefault(op, {})["status"] = "error"
            rep["ops"][op]["error"] = traceback.format_exc(limit=2)

    rep["rss_mb"] = round(peak_rss_mb(), 1)
    return rep


def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default


def _tpy_version() -> str:
    try:
        from topologicpy import version as _v
        return getattr(_v, "__version__", str(_v))
    except Exception:
        try:
            import importlib.metadata as md
            return md.version("topologicpy")
        except Exception:
            return "unknown"
