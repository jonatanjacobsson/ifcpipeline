"""topologicpy 0.9.70+/0.9.80 regressions and the worker's compensations.

* KnowledgeGraphExport vocabulary (ingest_scripts/_kg_compat.py): literal repair,
  legacy typing/edges/IRIs, 0.9.65 Turtle writer.
* Vertex.Distance regression -> roomstamp distance_mode guard (tasks.py).
* CellComplex.ByCells / Topology.InternalVertex -> ingest_scripts/_topologic_guards.py
  and a static check that nothing calls them directly.

Pure tests run anywhere; kernel tests skip without topologicpy + topologic_core.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_WORKER = _REPO / "topologicpy-worker"
sys.path.insert(0, str(_WORKER))

from ingest_scripts import _kg_compat as kc  # noqa: E402


def _kernel_available() -> bool:
    try:
        import topologic_core  # noqa: F401
        import topologicpy  # noqa: F401
    except Exception:
        return False
    return True


needs_kernel = pytest.mark.skipif(not _kernel_available(), reason="topologicpy/topologic_core not installed")


# --------------------------------------------------------------------------- #
# static: regressed APIs only via the guards module
# --------------------------------------------------------------------------- #

_REGRESSED_CALL = re.compile(r"\b(CellComplex\.ByCells|\w+\.InternalVertex)\s*\(")


def test_regressed_apis_are_only_called_through_guards():
    offenders = []
    for path in sorted(_WORKER.rglob("*.py")):
        if path.name == "_topologic_guards.py":
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if _REGRESSED_CALL.search(code):
                offenders.append(f"{path.relative_to(_REPO)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "call CellComplex.ByCells / InternalVertex through "
        "ingest_scripts/_topologic_guards.py (0.9.80 regressions):\n" + "\n".join(offenders)
    )


# --------------------------------------------------------------------------- #
# _kg_compat: pure parts
# --------------------------------------------------------------------------- #

class _Lit:
    """Duck-typed stand-in for topologicpy.Ontology._RDFLiteral."""

    def __init__(self, lexical, datatype=None, language=None):
        self.lexical, self.datatype, self.language = lexical, datatype, language

    def __repr__(self):
        return f"_RDFLiteral(lexical={self.lexical!r}, datatype={self.datatype!r}, language={self.language!r})"


def test_literal_fix_turns_rdfliteral_objects_into_tokens():
    class FakeKG:
        @staticmethod
        def NormalizeTerm(value, *a, **k):
            return '"' + str(value) + '"'  # what 0.9.80 does: repr as a string literal

    kc.install_literal_fix(FakeKG)
    kc.install_literal_fix(FakeKG)  # idempotent
    xsd = "http://www.w3.org/2001/XMLSchema#"
    assert FakeKG.NormalizeTerm(_Lit("2O2Fr$t4X7Zf8NOew3FLOH")) == '"2O2Fr$t4X7Zf8NOew3FLOH"'
    assert FakeKG.NormalizeTerm(_Lit("66", xsd + "integer")) == '"66"^^xsd:integer'
    assert FakeKG.NormalizeTerm(_Lit('say "hi"', language="en")) == '"say \\"hi\\""@en'
    assert FakeKG.NormalizeTerm("plain") == '"plain"'


def test_corrupted_literals_are_counted():
    triples = [("inst:a", "top:ifcGUID", '"_RDFLiteral(lexical=\'x\', datatype=None, language=None)"'),
               ("inst:a", "top:index", '"1"^^xsd:integer')]
    assert kc.corrupted_literals(triples) == 1


@pytest.mark.parametrize("native,legacy", [
    ("inst:id_0MHtXm7N_Ya__mZbIob7RT", "inst:id_0MHtXm7N_Ya_mZbIob7RT"),  # "_$" in the GUID
    ("inst:node_id_9543", "inst:id_9543"),                               # GUID-less record
    ("inst:relationship_id_12", "inst:id_12"),
    ("inst:graph_graph", "inst:graph_graph"),
    ("top:connectsTo", "top:connectsTo"),
    ('"a__b"', '"a__b"'),
])
def test_remint_matches_065_iri_minting(native, legacy):
    assert kc._remint(native) == legacy


@pytest.mark.parametrize("ifc,cls,category", [
    ("IfcSpace", "top:Space", "space"),
    ("IfcSpaceType", "top:Space", "space"),
    ("IfcBuildingStorey", "top:Storey", "storey"),
    ("IfcProject", "top:Project", "project"),
    ("IfcPropertySet", "top:PropertySet", "metadata"),
    ("IfcElementQuantity", "top:Quantity", "metadata"),
    ("IfcRelAggregates", "top:Relationship", "graph"),
    ("IfcDoorPanelProperties", "top:PropertySet", "metadata"),
    ("IfcMaterialLayerSetUsage", "top:MaterialSet", "metadata"),
    ("IfcDistributionPort", "top:Port", "element"),
    ("IfcAnnotation", "top:Element", "element"),
    ("IfcWallType", "top:Element", "element"),
])
def test_legacy_class_and_category(ifc, cls, category):
    assert kc.legacy_class(ifc) == cls
    assert kc.legacy_category(cls) == category


def test_legacy_class_follows_ifc_supertypes():
    pytest.importorskip("ifcopenshell")
    assert kc.legacy_class("IfcFlowSegment") == "top:Equipment"
    assert kc.legacy_class("IfcPipeSegmentType") == "top:Equipment"
    assert kc.legacy_class("IfcElectricDistributionPoint") == "top:Equipment"  # IFC2X3


def test_legacy_edge_predicates_use_065_table():
    assert kc.legacy_edge_predicates({"IFC_type": "IfcRelConnectsPortToElement",
                                      "ontology_predicate": "top:connectsPortToElement"}) == \
        ("top:connectsPort", "top:hasConnectedPort")
    assert kc.legacy_edge_predicates({"IFC_type": "IfcRelSomethingNew"}) == ("top:connectsTo", None)


def test_turtle_header_and_formatting():
    ttl = kc.turtle(
        [("inst:b", "top:x", '"1"^^xsd:integer'), ("inst:a", "rdf:type", "http://example.org/C"),
         ("inst:a", "rdf:type", "http://example.org/C"), ("inst:a", "sh:path", "inst:b")],
        namespaces={"sh": "http://www.w3.org/ns/shacl#", "top": "http://w3id.org/topologicpy#"},
    )
    header = [ln for ln in ttl.splitlines() if ln.startswith("@prefix")]
    assert [h.split()[1] for h in header[:15]] == [p + ":" for p in sorted(kc.LEGACY_NAMESPACES)]
    assert header[15] == "@prefix sh: <http://www.w3.org/ns/shacl#> ."
    body = [ln for ln in ttl.splitlines() if ln and not ln.startswith("@")]
    assert body == [
        "inst:a rdf:type <http://example.org/C> .",
        "inst:a sh:path inst:b .",
        'inst:b top:x "1"^^xsd:integer .',
    ]


def test_restore_is_additive_and_noop_when_legacy_vocabulary_present():
    records = {
        "graph": {"source": "IFC"},
        "V": [{"index": 0, "dictionary": {"IFC_global_id": "S1", "IFC_type": "IfcBuildingStorey"}},
              {"index": 1, "dictionary": {"IFC_global_id": "R1", "IFC_type": "IfcSpace", "IFC_name": "Rum"}}],
        "E": [{"index": 0, "src": 0, "dst": 1, "dictionary": {
            "IFC_global_id": "A1", "IFC_type": "IfcRelAggregates", "relationship": "aggregates"}}],
    }
    legacy = kc.legacy_triples(records)
    assert ("inst:R1", "rdf:type", "bot:Space") in legacy
    assert ("inst:S1", "top:aggregates", "inst:R1") in legacy
    assert ("inst:R1", "top:isAggregatedBy", "inst:S1") in legacy
    assert ("inst:S1", "top:connectsTo", "inst:R1") in legacy

    # native already carries the legacy vocabulary (0.9.65): nothing to add
    out, stats = kc.restore(legacy, records)
    assert out == legacy and stats["legacy_triples_added"] == 0

    # native with a different label/type already set: never contradicted
    native = {("inst:R1", "rdf:type", "top:Room"), ("inst:R1", "rdfs:label", '"Rum"')}
    out, _ = kc.restore(native, records)
    assert ("inst:R1", "rdf:type", "top:Space") not in out
    assert ("inst:R1", "rdf:type", "bot:Space") not in out
    assert ("inst:S1", "rdf:type", "bot:Storey") in out


_MAPPED_TYPE = re.compile(r"^(bot|brick|prov):")


def _mapped_types(triples):
    return sorted(t for t in triples if t[1] == "rdf:type" and _MAPPED_TYPE.match(t[2]))


def test_legacy_typing_honours_include_bot_false():
    # 0.9.65 Ontology.Triples adds the BOTClassByClass rdf:type only with includeBOT;
    # the TGraph edge predicates (bot:containsElement, ...) are emitted either way.
    records = {
        "graph": {},
        "V": [{"index": 0, "dictionary": {"IFC_global_id": "P1", "IFC_type": "IfcProject"}},
              {"index": 1, "dictionary": {"IFC_global_id": "S1", "IFC_type": "IfcBuildingStorey"}},
              {"index": 2, "dictionary": {"IFC_global_id": "R1", "IFC_type": "IfcSpace"}},
              {"index": 3, "dictionary": {"IFC_global_id": "X1", "IFC_type": "IfcSensor"}},
              {"index": 4, "dictionary": {"IFC_global_id": "Q1", "IFC_type": "IfcBoiler",
                                          "ontology_class": "top:Equipment"}}],
        "E": [{"index": 0, "src": 1, "dst": 3, "dictionary": {
            "IFC_type": "IfcRelContainedInSpatialStructure"}}],
    }
    with_bot = kc.legacy_triples(records)
    assert {o for _s, _p, o in _mapped_types(with_bot)} == {
        "prov:Entity", "bot:Storey", "bot:Space", "brick:Point", "brick:Equipment"}

    no_bot = kc.legacy_triples(records, include_bot=False)
    assert _mapped_types(no_bot) == []
    assert no_bot == with_bot - set(_mapped_types(with_bot))
    assert ("inst:R1", "rdf:type", "top:Space") in no_bot
    assert ("inst:S1", "bot:containsElement", "inst:X1") in no_bot

    out, _ = kc.restore(set(), records, include_bot=False)
    assert _mapped_types(out) == []


# --------------------------------------------------------------------------- #
# _kg_compat: end to end on the installed topologicpy
# --------------------------------------------------------------------------- #

@needs_kernel
def test_kg_export_restores_types_edges_and_guids():
    import topologicpy
    from topologicpy.KnowledgeGraph import KnowledgeGraph
    from topologicpy.TGraph import TGraph

    storey, room_a, room_b, rel = ("2O2Fr$t4X7Zf8NOew3FLOH", "0BTBFw6f90Nfh9rP1dlXrb",
                                   "1kTvXnbbzCWw8lcMd1dR4o", "3Agw$ZqMnF4AFbKlxsyq7S")
    vd = [{"IFC_global_id": storey, "IFC_type": "IfcBuildingStorey", "IFC_name": "Plan 1", "IFC_id": 10},
          {"IFC_global_id": room_a, "IFC_type": "IfcSpace", "IFC_name": "Room 101", "IFC_id": 11},
          {"IFC_global_id": room_b, "IFC_type": "IfcSpace", "IFC_name": "Room 102", "IFC_id": 12}]
    ed = [{"IFC_global_id": rel, "IFC_type": "IfcRelAggregates", "relationship": "aggregates",
           "ontology_predicate": "top:aggregates", "inverse_predicate": "top:isAggregatedBy"}] * 2
    g = TGraph.ByMeshData([[0, 0, 0], [1, 0, 0], [2, 0, 0]], [[0, 1], [0, 2]],
                          vertexDictionaries=vd, edgeDictionaries=ed)

    kc.install_literal_fix(KnowledgeGraph)
    kg = KnowledgeGraph.ByTopology(g, includeBOT=True, silent=True, useRDFLib=False)
    records = {"graph": TGraph.Dictionary(g), "V": TGraph.Vertices(g), "E": TGraph.Edges(g)}
    kg, _stats = kc.apply(kg, records, KnowledgeGraph)
    triples = set(kg.Triples())

    s, a = "inst:id_2O2Fr_t4X7Zf8NOew3FLOH", "inst:id_0BTBFw6f90Nfh9rP1dlXrb"
    assert kc.corrupted_literals(triples) == 0
    assert (s, "top:ifcGUID", f'"{storey}"') in triples
    assert (s, "top:aggregates", a) in triples
    assert (a, "top:isAggregatedBy", s) in triples
    assert (s, "top:connectsTo", a) in triples
    assert any(p == "rdf:type" for (sub, p, _o) in triples if sub == a)
    if kc._version_at_least(topologicpy.__version__, (0, 9, 70)):
        assert (a, "rdf:type", "bot:Space") in triples
        assert (s, "rdf:type", "bot:Storey") in triples

    ttl = kc.kg_turtle(kg)
    assert all(f"@prefix {p}: " in ttl for p in kc.LEGACY_NAMESPACES)
    rdflib = pytest.importorskip("rdflib")
    parsed = rdflib.Graph().parse(data=ttl, format="turtle")
    assert len(parsed) == len(triples)

    # includeBOT=False: no mapped BOT/Brick/PROV typing on any topologicpy, the
    # top:* typing and the element edges stay.
    kg_nb = KnowledgeGraph.ByTopology(g, includeBOT=False, silent=True, useRDFLib=False)
    kg_nb, _stats = kc.apply(kg_nb, records, KnowledgeGraph, include_bot=False)
    nb = set(kg_nb.Triples())
    assert _mapped_types(nb) == []
    assert any(p == "rdf:type" and o.startswith("top:") for (sub, p, o) in nb if sub == a)
    assert (s, "top:aggregates", a) in nb
    assert (s, "top:ifcGUID", f'"{storey}"') in nb


# --------------------------------------------------------------------------- #
# _topologic_guards
# --------------------------------------------------------------------------- #

@needs_kernel
def test_internal_vertex_guard_returns_an_inside_point():
    from topologicpy.Cell import Cell

    from ingest_scripts import _topologic_guards as tg

    box = Cell.Prism(width=1, length=1, height=1)
    v = tg.internal_vertex(box)
    assert v is not None
    assert Cell.ContainmentStatus(box, v, tolerance=0.0001) == 0


@needs_kernel
def test_internal_vertex_guard_times_out(monkeypatch):
    from topologicpy.Cell import Cell
    from topologicpy.Topology import Topology

    from ingest_scripts import _topologic_guards as tg

    monkeypatch.setattr(Topology, "InternalVertex", staticmethod(lambda *a, **k: time.sleep(5)))
    t0 = time.perf_counter()
    assert tg.internal_vertex(Cell.Prism(), timeout_s=0.2) is None
    assert time.perf_counter() - t0 < 2


@needs_kernel
def test_cellcomplex_guard_never_returns_an_empty_complex(monkeypatch):
    from topologicpy.Cell import Cell
    from topologicpy.CellComplex import CellComplex
    from topologicpy.Cluster import Cluster
    from topologicpy.Topology import Topology
    from topologicpy.Vertex import Vertex

    from ingest_scripts import _topologic_guards as tg

    cells = [Cell.Prism(origin=Vertex.ByCoordinates(x, 0, 0), width=1, length=1, height=1) for x in (0, 1)]
    cc = tg.cellcomplex_by_cells(cells)
    assert cc is None or len(Topology.Cells(cc)) >= 1

    # the 0.9.80 failure mode: a non-empty input comes back with 0 cells
    empty = Cluster.ByTopologies([Vertex.ByCoordinates(0, 0, 0)])
    monkeypatch.setattr(CellComplex, "ByCells", staticmethod(lambda *a, **k: empty))
    assert tg.cellcomplex_by_cells(cells) is None
    assert tg.cellcomplex_by_cells([]) is None


# --------------------------------------------------------------------------- #
# roomstamp: Vertex.Distance guard
# --------------------------------------------------------------------------- #

def _tasks():
    pytest.importorskip("ifcopenshell")
    try:
        import tasks
    except Exception as exc:  # pragma: no cover - needs the worker's deps
        pytest.skip(f"tasks.py not importable here: {exc}")
    return tasks


def _tuning(tasks, distance_mode):
    t = tasks._default_job_tuning()
    t.distance_mode = distance_mode
    t.distance_mode_requested = t.distance_mode_note = None
    return t


@pytest.mark.parametrize("version,expected", [("0.9.65", "topologic"), ("0.9.68", "topologic"),
                                              ("0.9.70", "bbox"), ("0.9.80", "bbox"), ("garbage", "bbox")])
def test_topologic_distance_mode_is_downgraded_on_regressed_topologicpy(monkeypatch, version, expected):
    tasks = _tasks()
    topologicpy = pytest.importorskip("topologicpy")
    monkeypatch.setattr(topologicpy, "__version__", version)
    monkeypatch.setattr(tasks, "_ALLOW_REGRESSED_TOPOLOGIC_DISTANCE", False)
    t = tasks._guard_distance_mode(_tuning(tasks, "topologic"))
    assert t.distance_mode == expected
    if expected == "bbox":
        assert t.distance_mode_requested == "topologic" and "Vertex.Distance" in t.distance_mode_note
    assert tasks._guard_distance_mode(_tuning(tasks, "bbox")).distance_mode == "bbox"

    monkeypatch.setattr(tasks, "_ALLOW_REGRESSED_TOPOLOGIC_DISTANCE", True)
    assert tasks._guard_distance_mode(_tuning(tasks, "topologic")).distance_mode == "topologic"
