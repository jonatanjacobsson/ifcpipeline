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
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from topologicpy.TGraph import TGraph

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
# construction
# --------------------------------------------------------------------------- #

def build_graph(ifc_path, tolerance: float = 0.0001, silent: bool = True):
    """Build a TGraph from an IFC file (replaces ``Graph.ByIFCFile``).

    Maps both legacy call shapes (``transferDictionaries=True`` and ``tolerance=``)
    onto the unified 0.9.50 signature. ``dictionaryMode="basic"`` populates the
    IFC_* identity keys on every vertex.
    """
    return TGraph.ByIFCFile(
        str(ifc_path),
        importMode="topology",
        dictionaryMode="basic",
        tolerance=tolerance,
        silent=silent,
    )


# --------------------------------------------------------------------------- #
# vertices / edges
# --------------------------------------------------------------------------- #

def vertices(g) -> List[Node]:
    return [Node(r) for r in TGraph.Vertices(g)]


def order(g) -> int:
    return TGraph.Order(g)


def size(g) -> int:
    return TGraph.Size(g)


def _idx2gid(g, verts: Optional[List[Dict[str, Any]]] = None) -> Dict[int, Optional[str]]:
    verts = verts if verts is not None else TGraph.Vertices(g)
    out: Dict[int, Optional[str]] = {}
    for r in verts:
        idx = r.get("index")
        if idx is not None:
            out[idx] = _first(r.get("dictionary") or {}, _GID_KEYS)
    return out


def _gid2idx(g, verts: Optional[List[Dict[str, Any]]] = None) -> Dict[str, int]:
    verts = verts if verts is not None else TGraph.Vertices(g)
    out: Dict[str, int] = {}
    for r in verts:
        gid = _first(r.get("dictionary") or {}, _GID_KEYS)
        idx = r.get("index")
        if gid and idx is not None and gid not in out:
            out[gid] = idx
    return out


def _edge_records(g) -> List[Dict[str, Any]]:
    edges = TGraph.Edges(g)
    if edges and isinstance(edges[0], dict) and "src" in edges[0]:
        return edges
    raw = getattr(g, "_edges", None) or []
    return [e for e in raw if isinstance(e, dict) and e.get("active", True)]


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
    by_idx = {n.index: n for n in vertices(g)}
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
    fn(g, key=key, normalize=normalize, silent=True)
    out: Dict[str, float] = {}
    for r in TGraph.Vertices(g):
        d = r.get("dictionary") or {}
        gid = _first(d, _GID_KEYS)
        if gid and d.get(key) is not None:
            out[gid] = float(d[key])
    return out


def betweenness(g, normalize: bool = True) -> Dict[str, float]:
    return _centrality(g, TGraph.BetweennessCentrality, "betweenness_centrality", normalize)


def closeness(g, normalize: bool = True) -> Dict[str, float]:
    return _centrality(g, TGraph.ClosenessCentrality, "closeness_centrality", normalize)


# --------------------------------------------------------------------------- #
# partitions / community  (CommunityDetection/EdgeBetweenness/Fiedler replacements)
# --------------------------------------------------------------------------- #

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
        labels = TGraph.CommunityPartition(g, algorithm="louvain", silent=True)
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
    i2g = _idx2gid(g)
    recs = _with_deep_recursion(lambda: TGraph.Bridges(g)) or []
    out: List[Tuple[str, str]] = []
    for e in recs:
        s = i2g.get(e.get("src") if isinstance(e, dict) else None)
        t = i2g.get(e.get("dst") if isinstance(e, dict) else None)
        if s and t:
            out.append((s, t))
    return out


def cut_vertices(g) -> List[Node]:
    """Articulation vertices as Nodes (so callers get gid + type + name)."""
    recs = _with_deep_recursion(lambda: TGraph.CutVertices(g)) or []
    return [Node(r) for r in recs if isinstance(r, dict) and _first(r.get("dictionary") or {}, _GID_KEYS)]


# --------------------------------------------------------------------------- #
# pathfinding
# --------------------------------------------------------------------------- #

def shortest_path(g, src_gid: str, tgt_gid: str) -> List[str]:
    """Shortest path between two gids → ordered list of gids ([] if none)."""
    verts = TGraph.Vertices(g)
    g2i = _gid2idx(g, verts)
    i2g = _idx2gid(g, verts)
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
