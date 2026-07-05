"""Shared TGraph adapter for the ingest scripts — supersedes legacy ``Graph``.

topologicpy 0.9.50 introduced the Python-native ``TGraph`` and, in the same
release, broke the legacy ``Graph`` API the ingest scripts were written against
(``Graph.ByIFCFile`` lost ``transferDictionaries``; ``StartVertex``/``EndVertex``
and ``CommunityDetection`` were removed; centrality/bridges/partitions now return
lists instead of graphs). The TGraph evaluation
(``topologicpy-worker/tgraph_eval/FINDINGS.md``) concluded we should adopt TGraph.

This module is the single place that knows the TGraph API. Every ingest script
builds and reads its graph through here, so future topologicpy changes are
contained to this file and scripts never hardcode dict-key names or index logic.

TGraph shapes (confirmed against 0.9.50):
  * ``TGraph.Vertices(g)`` -> list of dict records ``{"index", "dictionary": {...},
    "representation", "active"}``.
  * ``TGraph.Edges(g)``    -> records with integer ``"src"``/``"dst"`` indices.
  * ``TGraph.AdjacentVertices(g, idx, mode)`` -> list of vertex records.
  * centrality / partition functions return a list AND store the value in each
    vertex's dictionary under ``key``.

Performance notes (ingest_bench, 2026-07):
  * betweenness/closeness/bridges/cut_vertices run on python-igraph's C
    implementations (validated value-for-value against TGraph's pure-Python
    paths, which remain as fallback when igraph is unavailable).
  * vertex/edge record snapshots, gid maps, and the igraph twin are memoized
    per graph object (``TGraph.Vertices``/``Edges`` copy every record per call).
  * ``build_graph`` keeps an optional on-disk TGraph JSON cache so sibling
    ingest jobs on the same IFC file skip the parse + graph build.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from topologicpy.TGraph import TGraph

from ingest_scripts import perf_patches

perf_patches.apply()

try:
    import igraph as _igraph
except Exception:  # pragma: no cover — igraph ships in the worker image
    _igraph = None

# Normalized key lookup — scripts must NOT hardcode these. TGraph uses the IFC_*
# keys; the bare "GlobalId"/"IfcClass" variants are kept as fallbacks because some
# legacy scripts and older dictionaries used them.
_GID_KEYS = ("IFC_global_id", "GlobalId")
_TYPE_KEYS = ("IFC_type", "IfcClass")
_NAME_KEYS = ("IFC_name", "Name")


def _first(d: Dict[str, Any], keys) -> Any:
    for k in keys:
        v = d.get(k)
        if v:
            return v
    return None


class Node:
    """Lightweight view over a TGraph vertex record with normalized accessors."""

    __slots__ = ("record", "_d")

    def __init__(self, record: Dict[str, Any]):
        self.record = record
        self._d = record.get("dictionary") or {}

    @property
    def index(self) -> Optional[int]:
        return self.record.get("index")

    @property
    def gid(self) -> Optional[str]:
        return _first(self._d, _GID_KEYS)

    @property
    def ifc_type(self) -> str:
        return _first(self._d, _TYPE_KEYS) or ""

    @property
    def ifc_name(self) -> str:
        return _first(self._d, _NAME_KEYS) or ""

    @property
    def coords(self) -> Tuple[Any, Any, Any]:
        return (self._d.get("x"), self._d.get("y"), self._d.get("z"))

    def value(self, key: str, default: Any = None) -> Any:
        return self._d.get(key, default)


# --------------------------------------------------------------------------- #
# per-graph memo — record snapshots, gid maps, and the igraph twin are computed
# once per graph object. Keyed on (identity, TGraph._version) so structural
# mutation (AddVertex/AddEdge/RemoveEdge) invalidates the memo; in-place vertex
# dictionary writes do not (Node accessors only read identity keys, which never
# change after construction). TGraph defines __slots__ without __weakref__, so
# a small strong-ref LRU is used instead of a WeakKeyDictionary.
# --------------------------------------------------------------------------- #

_MEMO_MAX_GRAPHS = 4
_memo_store: "OrderedDict[int, Tuple[Any, Any, Dict[str, Any]]]" = OrderedDict()


def _memo(g) -> Dict[str, Any]:
    key = id(g)
    version = getattr(g, "_version", None)
    entry = _memo_store.get(key)
    if entry is not None and entry[0] is g and entry[1] == version:
        return entry[2]
    m: Dict[str, Any] = {}
    _memo_store[key] = (g, version, m)
    while len(_memo_store) > _MEMO_MAX_GRAPHS:
        _memo_store.popitem(last=False)
    return m


def _vertex_records(g) -> List[Dict[str, Any]]:
    m = _memo(g)
    recs = m.get("vrecs")
    if recs is None:
        recs = m["vrecs"] = TGraph.Vertices(g)
    return recs


def _node_list(g) -> List[Node]:
    m = _memo(g)
    nodes = m.get("nodes")
    if nodes is None:
        nodes = m["nodes"] = [Node(r) for r in _vertex_records(g)]
    return nodes


def _nodes_by_idx(g) -> Dict[Optional[int], Node]:
    m = _memo(g)
    by_idx = m.get("nodes_by_idx")
    if by_idx is None:
        by_idx = m["nodes_by_idx"] = {n.index: n for n in _node_list(g)}
    return by_idx


def _gid_maps(g) -> Tuple[Dict[int, Optional[str]], Dict[str, int]]:
    """(idx->gid, gid->first idx) over active vertices, built once per graph."""
    m = _memo(g)
    maps = m.get("gid_maps")
    if maps is None:
        i2g: Dict[int, Optional[str]] = {}
        g2i: Dict[str, int] = {}
        for r in _vertex_records(g):
            idx = r.get("index")
            if idx is None:
                continue
            gid = _first(r.get("dictionary") or {}, _GID_KEYS)
            i2g[idx] = gid
            if gid and gid not in g2i:
                g2i[gid] = idx
        maps = m["gid_maps"] = (i2g, g2i)
    return maps


# --------------------------------------------------------------------------- #
# construction (+ optional disk cache)
# --------------------------------------------------------------------------- #

# Cache format/version marker — bump when the serialized shape changes.
_CACHE_VERSION = "1"
_CACHE_SUFFIX = ".tgraph.json"


def _cache_max_bytes() -> int:
    # Default 1 GB: the host runs close to the disk-watcher's 20 GB free floor.
    try:
        return int(os.environ.get("INGEST_TGRAPH_CACHE_MAX_MB", "1024")) * 1024 ** 2
    except ValueError:
        return 1024 ** 3


def _cache_enabled() -> bool:
    return os.environ.get("INGEST_TGRAPH_CACHE", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _cache_dir() -> Path:
    return Path(os.environ.get("INGEST_TGRAPH_CACHE_DIR", "/tmp/tgraph_cache"))


def _topologicpy_version() -> str:
    try:
        import topologicpy
        v = getattr(topologicpy, "__version__", None)
        if v:
            return str(v)
    except Exception:
        pass
    try:
        from importlib.metadata import version
        return version("topologicpy")
    except Exception:
        return "unknown"


def _cache_key(ifc_path, tolerance: float) -> str:
    h = hashlib.sha256()
    with open(ifc_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    # build_graph pins importMode/dictionaryMode below; keyed anyway so a future
    # change can't serve stale graphs.
    h.update(
        f"|v{_CACHE_VERSION}|mode=topology|dict=basic|tol={float(tolerance)!r}"
        f"|topologicpy={_topologicpy_version()}".encode()
    )
    return h.hexdigest()


def _cache_load(path: Path):
    try:
        if not path.is_file():
            return None
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        g = TGraph.FromPython(data)
        if isinstance(g, TGraph):
            os.utime(path, None)  # refresh LRU rank
            return g
    except Exception:
        pass
    return None


def _cache_store(path: Path, g) -> None:
    tmp = None
    try:
        data = TGraph.ToPython(g)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, separators=(",", ":"))
        os.replace(tmp, path)  # atomic publish
        _cache_evict(path.parent)
    except Exception:
        try:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
        except Exception:
            pass


def _cache_evict(root: Path) -> None:
    try:
        # Orphaned tmp files from a SIGKILLed writer never get renamed or
        # unlinked — reap any older than an hour (a live writer holds its tmp
        # for seconds) so they can't accumulate outside the size cap.
        now = time.time()
        for p in root.glob(f"*{_CACHE_SUFFIX}.tmp.*"):
            try:
                if now - p.stat().st_mtime > 3600:
                    p.unlink(missing_ok=True)
            except OSError:
                continue
        entries = []
        for p in root.glob(f"*{_CACHE_SUFFIX}"):
            try:
                st = p.stat()
                entries.append((st.st_mtime, st.st_size, p))
            except OSError:
                continue
        total = sum(size for _, size, _ in entries)
        cap = _cache_max_bytes()
        for _, size, p in sorted(entries):  # oldest first
            if total <= cap:
                break
            try:
                p.unlink(missing_ok=True)
                total -= size
            except OSError:
                continue
    except Exception:
        pass


def build_graph(ifc_path, tolerance: float = 0.0001, silent: bool = True):
    """Build a TGraph from an IFC file (replaces ``Graph.ByIFCFile``).

    Maps both legacy call shapes (``transferDictionaries=True`` and ``tolerance=``)
    onto the unified 0.9.50 signature. ``dictionaryMode="basic"`` populates the
    IFC_* identity keys on every vertex.

    With ``INGEST_TGRAPH_CACHE=1`` (default) the built graph is persisted as
    TGraph JSON (``ToPython``/``FromPython`` round trip, validated fingerprint-
    identical) keyed by file sha256 + tolerance + modes + topologicpy version,
    so sibling ingest jobs on the same file skip the IFC parse + graph build.
    Any cache error silently falls back to a fresh build.
    """
    cache_path = None
    if _cache_enabled():
        try:
            cache_path = _cache_dir() / f"{_cache_key(ifc_path, tolerance)}{_CACHE_SUFFIX}"
            cached = _cache_load(cache_path)
            if cached is not None:
                return cached
        except Exception:
            cache_path = None
    g = TGraph.ByIFCFile(
        str(ifc_path),
        importMode="topology",
        dictionaryMode="basic",
        tolerance=tolerance,
        silent=silent,
    )
    if cache_path is not None and g is not None:
        _cache_store(cache_path, g)
    return g


# --------------------------------------------------------------------------- #
# vertices / edges
# --------------------------------------------------------------------------- #

def vertices(g) -> List[Node]:
    return list(_node_list(g))


def order(g) -> int:
    return TGraph.Order(g)


def size(g) -> int:
    return TGraph.Size(g)


def _idx2gid(g, verts: Optional[List[Dict[str, Any]]] = None) -> Dict[int, Optional[str]]:
    if verts is None:
        return _gid_maps(g)[0]
    out: Dict[int, Optional[str]] = {}
    for r in verts:
        idx = r.get("index")
        if idx is not None:
            out[idx] = _first(r.get("dictionary") or {}, _GID_KEYS)
    return out


def _gid2idx(g, verts: Optional[List[Dict[str, Any]]] = None) -> Dict[str, int]:
    if verts is None:
        return _gid_maps(g)[1]
    out: Dict[str, int] = {}
    for r in verts:
        gid = _first(r.get("dictionary") or {}, _GID_KEYS)
        idx = r.get("index")
        if gid and idx is not None and gid not in out:
            out[gid] = idx
    return out


def _edge_records(g) -> List[Dict[str, Any]]:
    m = _memo(g)
    recs = m.get("erecs")
    if recs is None:
        edges_ = TGraph.Edges(g)
        if edges_ and isinstance(edges_[0], dict) and "src" in edges_[0]:
            recs = edges_
        else:
            raw = getattr(g, "_edges", None) or []
            recs = [e for e in raw if isinstance(e, dict) and e.get("active", True)]
        m["erecs"] = recs
    return recs


def edges(g) -> List[Tuple[str, str]]:
    """Undirected edge list as (subject_gid, object_gid) pairs."""
    i2g = _idx2gid(g)
    out: List[Tuple[str, str]] = []
    for e in _edge_records(g):
        s = i2g.get(e.get("src"))
        t = i2g.get(e.get("dst"))
        if s and t:
            out.append((s, t))
    return out


def edge_nodes(g) -> List[Tuple[Node, Node]]:
    """Edge list as (source Node, target Node) pairs — when a script needs the
    endpoints' type/name, not just their gids (replaces Graph.StartVertex/EndVertex)."""
    by_idx = _nodes_by_idx(g)
    out: List[Tuple[Node, Node]] = []
    for e in _edge_records(g):
        s = by_idx.get(e.get("src"))
        t = by_idx.get(e.get("dst"))
        if s is not None and t is not None:
            out.append((s, t))
    return out


def adjacent(g, node, mode: str = "all") -> List[Node]:
    """Neighbours of a node (accepts a Node or an integer index)."""
    idx = node.index if isinstance(node, Node) else node
    if idx is None:
        return []
    return [Node(r) for r in (TGraph.AdjacentVertices(g, idx, mode=mode) or [])]


def adjacency_nodes(g) -> Dict[int, List[Node]]:
    """Undirected neighbour Nodes for every active vertex in one O(V+E) pass.

    Set-membership matches ``adjacent(g, idx, mode="all")`` (dedup'd neighbours,
    edges to inactive vertices ignored); neighbours are index-sorted. Use this
    instead of calling ``adjacent`` per vertex, which copies records per call.
    """
    by_idx = _nodes_by_idx(g)
    neigh: Dict[int, set] = {idx: set() for idx in by_idx if idx is not None}
    for e in _edge_records(g):
        s, t = e.get("src"), e.get("dst")
        if s in neigh and t in neigh:
            neigh[s].add(t)
            neigh[t].add(s)
    return {idx: [by_idx[i] for i in sorted(adj)] for idx, adj in neigh.items()}


# --------------------------------------------------------------------------- #
# igraph twin — C-backed analytics (validated against TGraph's pure-Python
# implementations: exact value/set parity on the Nobel A1/E1 models)
# --------------------------------------------------------------------------- #

def _igraph_data(g) -> Optional[Dict[str, Any]]:
    """Convert the TGraph edge list once into an undirected igraph.Graph.

    Local igraph vertex i corresponds to _vertex_records(g)[i]; ``eids[k]``
    maps igraph edge k back to its position in _edge_records(g). Self-loops
    and edges touching inactive vertices are skipped — the same filtering
    TGraph's centrality/bridge code applies.
    """
    if _igraph is None:
        return None
    m = _memo(g)
    data = m.get("ig")
    if data is None:
        idxs = [r.get("index") for r in _vertex_records(g)]
        pos = {vi: i for i, vi in enumerate(idxs)}
        pairs: List[Tuple[int, int]] = []
        eids: List[int] = []
        for k, e in enumerate(_edge_records(g)):
            s, t = e.get("src"), e.get("dst")
            if s == t or s not in pos or t not in pos:
                continue
            pairs.append((pos[s], pos[t]))
            eids.append(k)
        data = m["ig"] = {
            "graph": _igraph.Graph(n=len(idxs), edges=pairs, directed=False),
            "idxs": idxs,
            "eids": eids,
        }
    return data


def _gid_values(g, data: Dict[str, Any], vals: List[float]) -> Dict[str, float]:
    """Map per-local-vertex values to {gid: value} (last write wins on duplicate
    gids — matches reading the stored key back from TGraph.Vertices order)."""
    i2g = _gid_maps(g)[0]
    out: Dict[str, float] = {}
    for vi, val in zip(data["idxs"], vals):
        gid = i2g.get(vi)
        if gid:
            out[gid] = float(val)
    return out


# --------------------------------------------------------------------------- #
# metrics (gid-keyed)
# --------------------------------------------------------------------------- #

def degree_map(g) -> Dict[str, int]:
    """Degree per gid in O(E) (edge-incidence) — avoids the O(V*E) per-vertex trap."""
    i2g = _idx2gid(g)
    out: Dict[str, int] = {gid: 0 for gid in i2g.values() if gid}
    for e in _edge_records(g):
        s = i2g.get(e.get("src"))
        t = i2g.get(e.get("dst"))
        if s in out:
            out[s] += 1
        if t in out:
            out[t] += 1
    return out


def _centrality(g, fn, key: str, normalize: bool) -> Dict[str, float]:
    """Pure-Python TGraph fallback (used only when igraph is unavailable)."""
    fn(g, key=key, normalize=normalize, silent=True)
    out: Dict[str, float] = {}
    for r in TGraph.Vertices(g):
        d = r.get("dictionary") or {}
        gid = _first(d, _GID_KEYS)
        if gid and d.get(key) is not None:
            out[gid] = float(d[key])
    return out


def betweenness(g, normalize: bool = True) -> Dict[str, float]:
    data = _igraph_data(g)
    if data is None:
        return _centrality(g, TGraph.BetweennessCentrality, "betweenness_centrality", normalize)
    # TGraph.BetweennessCentrality defaults to nxCompatible=True, which IGNORES
    # ``normalize`` and applies the NetworkX scale 2/((n-1)(n-2)) to once-per-
    # pair Brandes counts (== igraph's betweenness), rounded to 6 decimals.
    n = len(data["idxs"])
    vals = data["graph"].betweenness(directed=False)
    if n <= 2:
        vals = [0.0] * n
    else:
        scale = 2.0 / float((n - 1) * (n - 2))
        vals = [float(v) * scale for v in vals]
    return _gid_values(g, data, [round(v, 6) for v in vals])


def closeness(g, normalize: bool = True) -> Dict[str, float]:
    data = _igraph_data(g)
    if data is None:
        return _centrality(g, TGraph.ClosenessCentrality, "closeness_centrality", normalize)
    # TGraph.ClosenessCentrality (nxCompatible) computes the Wasserman-Faust
    # closeness (s/(n-1))*(s/total) per vertex with s = reachable-1, then
    # min-max normalizes when ``normalize`` (eps = its tolerance, 0.0001),
    # rounding to 6 decimals. igraph's unnormalized closeness is 1/total over
    # the vertex's component; total is an exact integer hop sum, recovered
    # via round() so the float arithmetic matches TGraph's bit-for-bit.
    ig_g = data["graph"]
    n = len(data["idxs"])
    if n == 0:
        return {}
    if n == 1:
        vals = [0.0]
    else:
        comps = ig_g.connected_components(mode="weak")
        member = comps.membership
        sizes = [len(c) for c in comps]
        raw = ig_g.closeness(mode="all", normalized=False)
        vals = [0.0] * n
        for i in range(n):
            s = sizes[member[i]] - 1
            r = raw[i]
            if s > 0 and r == r and r > 0.0:  # r==r filters NaN (isolated vertex)
                total = round(1.0 / r)
                vals[i] = (float(s) / float(n - 1)) * (float(s) / float(total))
        if normalize:
            mn, mx = min(vals), max(vals)
            if abs(mx - mn) < 0.0001:
                vals = [0.0] * n
            else:
                vals = [(v - mn) / (mx - mn) for v in vals]
    return _gid_values(g, data, [round(v, 6) for v in vals])


# --------------------------------------------------------------------------- #
# partitions / community  (CommunityDetection/EdgeBetweenness/Fiedler replacements)
# --------------------------------------------------------------------------- #

def _louvain_labels(g) -> List[int]:
    """TGraph.CommunityPartition('louvain') delegates to igraph's
    community_multilevel, whose RNG is entropy-seeded per process — labels were
    NOT reproducible run to run (verified on the pristine adapter). Pin igraph's
    RNG to a fixed seed (17, TGraph's own documented default) around the call so
    partitions are deterministic; the default RNG is restored afterwards.
    """
    if _igraph is None:
        return TGraph.CommunityPartition(g, algorithm="louvain", silent=True)
    import random as _random
    _igraph.set_random_number_generator(_random.Random(17))
    try:
        return TGraph.CommunityPartition(g, algorithm="louvain", silent=True)
    finally:
        try:
            # igraph's documented default RNG is the Python random module;
            # None would instead install igraph's internal (unseedable) C PCG32.
            _igraph.set_random_number_generator(_random)
        except Exception:
            pass


def community(g, method: str = "community", num_partitions: int = 0) -> Dict[str, int]:
    """Partition the graph; returns {gid: label}.

    method: "community" (louvain), "edge_betweenness", or "fiedler".
    All three return a label list aligned to TGraph.Vertices(activeOnly) order.
    """
    if method == "edge_betweenness":
        labels = TGraph.BetweennessPartition(g, n=(num_partitions or 2), silent=True)
    elif method == "fiedler":
        labels = TGraph.FiedlerVectorPartition(g, silent=True)
    else:  # "community" / default
        labels = _louvain_labels(g)
    labels = labels or []
    out: Dict[str, int] = {}
    for node, label in zip(vertices(g), labels):
        if node.gid is not None:
            out[node.gid] = label
    return out


# --------------------------------------------------------------------------- #
# structure (recursion-guarded — TGraph's recursive DFS RecursionErrors on big graphs)
# --------------------------------------------------------------------------- #

def _with_deep_recursion(fn):
    """Run fn() under a raised recursion limit on a thread with a large stack.

    TGraph.Bridges/CutVertices use a recursive DFS that exceeds Python's ~1000
    limit on large decomposed graphs (observed at ~90k nodes). Bump the limit and
    give the thread a big stack so deep recursion doesn't crash.
    """
    box: Dict[str, Any] = {}
    err: Dict[str, BaseException] = {}

    def run():
        old = sys.getrecursionlimit()
        sys.setrecursionlimit(1_000_000)
        try:
            box["v"] = fn()
        except BaseException as exc:  # noqa: BLE001
            err["e"] = exc
        finally:
            sys.setrecursionlimit(old)

    try:
        threading.stack_size(512 * 1024 * 1024)  # 512 MB
    except (ValueError, RuntimeError):
        pass
    t = threading.Thread(target=run)
    t.start()
    t.join()
    if "e" in err:
        raise err["e"]
    return box.get("v")


def bridges(g) -> List[Tuple[str, str]]:
    data = _igraph_data(g)
    if data is not None:
        # igraph bridges == TGraph.Bridges' Tarjan set (parallel-edge pairs are
        # never bridges in either); emit in original edge-record orientation.
        erecs = _edge_records(g)
        eids = data["eids"]
        i2g = _gid_maps(g)[0]
        out: List[Tuple[str, str]] = []
        for ei in sorted(data["graph"].bridges()):
            e = erecs[eids[ei]]
            s = i2g.get(e.get("src"))
            t = i2g.get(e.get("dst"))
            if s and t:
                out.append((s, t))
        return out
    i2g = _idx2gid(g)
    recs = _with_deep_recursion(lambda: TGraph.Bridges(g)) or []
    out = []
    for e in recs:
        s = i2g.get(e.get("src") if isinstance(e, dict) else None)
        t = i2g.get(e.get("dst") if isinstance(e, dict) else None)
        if s and t:
            out.append((s, t))
    return out


def cut_vertices(g) -> List[Node]:
    """Articulation vertices as Nodes (so callers get gid + type + name)."""
    data = _igraph_data(g)
    if data is not None:
        idxs = data["idxs"]
        by_idx = _nodes_by_idx(g)
        out: List[Node] = []
        for vi in sorted(idxs[p] for p in data["graph"].articulation_points()):
            node = by_idx.get(vi)
            if node is not None and node.gid:
                out.append(node)
        return out
    recs = _with_deep_recursion(lambda: TGraph.CutVertices(g)) or []
    return [Node(r) for r in recs if isinstance(r, dict) and _first(r.get("dictionary") or {}, _GID_KEYS)]


# --------------------------------------------------------------------------- #
# pathfinding
# --------------------------------------------------------------------------- #

def shortest_path(g, src_gid: str, tgt_gid: str) -> List[str]:
    """Shortest path between two gids → ordered list of gids ([] if none)."""
    i2g, g2i = _gid_maps(g)
    si, ti = g2i.get(src_gid), g2i.get(tgt_gid)
    if si is None or ti is None:
        return []
    path = TGraph.ShortestPath(g, si, ti, mode="all")
    if not path:
        return []
    return [i2g.get(i) for i in path if i2g.get(i)]


# --------------------------------------------------------------------------- #
# probe — confirm runtime shapes (mirrors tgraph_eval --probe)
# --------------------------------------------------------------------------- #

def probe(ifc_path) -> None:
    g = build_graph(ifc_path)
    vs = vertices(g)
    print(f"order={order(g)} size={size(g)} | vertices={len(vs)}")
    if vs:
        n = vs[0]
        print(f"  Node[0]: gid={n.gid} type={n.ifc_type} name={n.ifc_name} idx={n.index}")
    es = edges(g)
    print(f"edges(gid pairs)={len(es)} sample={es[:2]}")
    if vs:
        adj = adjacent(g, vs[0])
        print(f"adjacent(v0)={len(adj)} sample_gids={[a.gid for a in adj[:3]]}")
    print(f"degree_map size={len(degree_map(g))}")
    comm = community(g)
    print(f"community labels={len(set(comm.values()))} over {len(comm)} nodes")
    print(f"bridges={len(bridges(g))} cut_vertices={len(cut_vertices(g))}")
    if len(vs) >= 2 and vs[0].gid and vs[-1].gid:
        sp = shortest_path(g, vs[0].gid, vs[-1].gid)
        print(f"shortest_path(v0,vN) hops={max(0, len(sp) - 1)}")
    print("PROBE_OK")
