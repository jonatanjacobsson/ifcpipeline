"""Backward-compatible RDF vocabulary for KnowledgeGraphExport (topologicpy >= 0.9.70).

topologicpy 0.9.70 rewrote ``KnowledgeGraph``/``Ontology``. On the published
0.9.80 wheel ``KnowledgeGraph.ByTopology(TGraph)`` regresses in three ways that
make the Turtle artifact incompatible with what 0.9.52-0.9.65 produced (and
what ADR-015 documents):

1. **Corrupted literals.** ``Ontology.GraphTriples`` emits ``_RDFLiteral``
   objects that ``KnowledgeGraph.NormalizeTerm`` does not recognise, so every
   literal is serialised as its Python repr -- e.g. ``top:ifcGUID
   "_RDFLiteral(lexical='00XC..', datatype=None, language=None)"``. GUIDs are
   therefore unusable (54,181 of 62,064 triples on A1).
2. **No classes, no element<->element edges.** The canonical vocabulary is
   loaded from ``topologicpy/ontology/topologicpy.ttl``, which the wheel does
   not ship. With an empty vocabulary ``Ontology.CanonicalClass`` and
   ``PropertyQName`` reject every ``top:`` term, so no ``rdf:type``
   (``top:Space``/``bot:Space``/``bot:Storey``/...) and no ``top:connectsTo``/
   ``top:aggregates``/``top:hasPropertySet``/... triples are emitted.
3. **Vocabulary drift.** IFC data properties moved from ``top:ifcType``/
   ``top:ifcStepId``/``top:ifcStepKey``/``top:ifcName``/``top:category`` to
   ``dict:*`` mirrors; IRIs keep repeated underscores (``a_$b`` ->
   ``a__b``, 0.9.65 minted ``a_b``) and GUID-less records get a role prefix
   (``inst:node_id_9543``, 0.9.65 ``inst:id_9543``); the TGraph's port
   relationships changed predicate (``connectsPortToElement``); the TTL header
   only declares the prefixes in use (5 instead of 15).

This module restores the 0.9.65 vocabulary *additively* from the TGraph
records (whose content -- IFC keys, indices, coordinates, src/dst, relationship
class -- is unchanged). Every restoration is "only if missing", so on a
topologicpy that already emits the legacy vocabulary (0.9.65) it is a no-op,
and the 0.9.80 ``dict:*`` mirrors are kept. ``turtle()`` writes the 0.9.65
flat, sorted Turtle with all 15 prefixes declared.

Measured against 0.9.65 (A1 0003 and E1 600, 2026-09-25): every 0.9.65 triple is
present except two deliberately not re-created kinds -- the per-node
``top:generatedByMethod "TGraph.ByMeshData"`` provenance literal (0.9.80's TGraph
no longer records it) and ``rdfs:label`` = vertex *index* (0.9.80 labels nodes
with their IFC Name; the index is still ``top:index``, the name also
``top:ifcName``). With ``reason=True`` 0.9.80 infers nothing (no ontology
axioms in the wheel), see KnowledgeGraphExport.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

Triple = Tuple[str, str, str]

# The 15 prefixes 0.9.52-0.9.65 always declared (header order is sorted).
LEGACY_NAMESPACES: Dict[str, str] = {
    "bot": "https://w3id.org/bot#",
    "brick": "https://brickschema.org/schema/Brick#",
    "dcterms": "http://purl.org/dc/terms/",
    "dict": "http://w3id.org/topologicpy/dictionary#",
    "geo": "http://www.opengis.net/ont/geosparql#",
    "ifc": "https://standards.buildingsmart.org/IFC/DEV/IFC4/ADD2_TC1/OWL#",
    "inst": "http://w3id.org/topologicpy/instance#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "prov": "http://www.w3.org/ns/prov#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "top": "http://w3id.org/topologicpy#",
    "vann": "http://purl.org/vocab/vann/",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
}

# IFC entity -> TopologicPy class, as 0.9.65 assigned it (TGraph ontology_class).
_IFC_TO_TOP: Dict[str, str] = {
    "IfcProject": "top:Project", "IfcSite": "top:Site", "IfcBuilding": "top:Building",
    "IfcBuildingStorey": "top:Storey", "IfcSpace": "top:Space", "IfcSpaceType": "top:Space",
    "IfcZone": "top:Zone", "IfcSystem": "top:System", "IfcDistributionPort": "top:Port",
    "IfcElementQuantity": "top:Quantity", "IfcPropertySet": "top:PropertySet",
    "IfcMaterial": "top:Material", "IfcClassificationReference": "top:ClassificationReference",
    "IfcRelSpaceBoundary": "top:Interface", "IfcRelSpaceBoundary1stLevel": "top:Interface",
    "IfcRelSpaceBoundary2ndLevel": "top:Interface",
    "IfcWall": "top:Wall", "IfcWallStandardCase": "top:Wall", "IfcCurtainWall": "top:CurtainWall",
    "IfcDoor": "top:Door", "IfcWindow": "top:Window", "IfcSlab": "top:Slab", "IfcRoof": "top:Roof",
    "IfcColumn": "top:Column", "IfcBeam": "top:Beam", "IfcMember": "top:Member",
    "IfcStair": "top:Stair", "IfcStairFlight": "top:Stair", "IfcRailing": "top:Railing",
    "IfcOpeningElement": "top:Opening", "IfcFurnishingElement": "top:Furniture",
    "IfcFurniture": "top:Furniture", "IfcSensor": "top:Sensor",
}
# Supertypes (IFC2X3/IFC4) that 0.9.65 mapped to top:Equipment.
_EQUIPMENT_SUPERTYPES = (
    "IfcDistributionElement", "IfcDistributionElementType",
    "IfcDistributionFlowElement", "IfcDistributionFlowElementType",
    "IfcDistributionControlElement", "IfcDistributionControlElementType",
)

# top: class -> BOT/Brick/PROV class (0.9.65 and 0.9.80 agree on this table).
_TOP_TO_BOT: Dict[str, str] = {
    "top:Building": "bot:Building", "top:Element": "bot:Element",
    "top:Equipment": "brick:Equipment", "top:Interface": "bot:Interface",
    "top:Project": "prov:Entity", "top:Sensor": "brick:Point",
    "top:Site": "bot:Site", "top:Space": "bot:Space", "top:Storey": "bot:Storey",
    "top:Zone": "bot:Zone", "top:Room": "bot:Space",
    "top:Wall": "bot:Element", "top:CurtainWall": "bot:Element", "top:Door": "bot:Element",
    "top:Window": "bot:Element", "top:Slab": "bot:Element", "top:Roof": "bot:Element",
    "top:Column": "bot:Element", "top:Beam": "bot:Element", "top:Member": "bot:Element",
    "top:Stair": "bot:Element", "top:Railing": "bot:Element", "top:Opening": "bot:Element",
    "top:Furniture": "bot:Element",
}

# IFC relationship -> (predicate, inverse) exactly as 0.9.65's TGraph.ByIFCFile
# assigned them. 0.9.80 changed the port ones (connectsPortToElement / a
# symmetric connectsPort); the legacy edges are restored from this table.
_LEGACY_REL_PREDICATES: Dict[str, Tuple[str, Optional[str]]] = {
    "IFCRELCONTAINEDINSPATIALSTRUCTURE": ("bot:containsElement", "bot:hasElement"),
    "IFCRELAGGREGATES": ("top:aggregates", "top:isAggregatedBy"),
    "IFCRELNESTS": ("brick:hasPart", "brick:isPartOf"),
    "IFCRELASSIGNSTOGROUP": ("brick:hasPart", "brick:isPartOf"),
    "IFCRELDEFINESBYPROPERTIES": ("top:hasPropertySet", "top:isPropertySetOf"),
    "IFCRELDEFINESBYTYPE": ("top:hasIFCType", "top:isIFCTypeOf"),
    "IFCRELASSOCIATESMATERIAL": ("top:hasMaterial", "top:isMaterialOf"),
    "IFCRELASSOCIATESCLASSIFICATION": ("top:hasClassification", "top:isClassificationOf"),
    "IFCRELASSOCIATESDOCUMENT": ("top:hasDocument", "top:isDocumentOf"),
    "IFCRELASSOCIATESAPPROVAL": ("top:hasApproval", "top:isApprovalOf"),
    "IFCRELASSOCIATESCONSTRAINT": ("top:hasConstraint", "top:isConstraintOf"),
    "IFCRELVOIDSELEMENT": ("top:hasOpening", "top:isOpeningIn"),
    "IFCRELFILLSELEMENT": ("top:fillsOpening", "top:isFilledBy"),
    "IFCRELSPACEBOUNDARY": ("bot:adjacentElement", "bot:interfaceOf"),
    "IFCRELSPACEBOUNDARY1STLEVEL": ("bot:adjacentElement", "bot:interfaceOf"),
    "IFCRELSPACEBOUNDARY2NDLEVEL": ("bot:adjacentElement", "bot:interfaceOf"),
    "IFCRELCONNECTSPORTS": ("top:connectsPort", "top:isConnectedPortOf"),
    "IFCRELCONNECTSPORTTOELEMENT": ("top:connectsPort", "top:hasConnectedPort"),
    "IFCRELCONNECTSELEMENTS": ("top:connectsTo", "top:isConnectedTo"),
    "IFCRELCONNECTSPATHELEMENTS": ("brick:feeds", "brick:isFedBy"),
    "IFCRELSERVICESBUILDINGS": ("top:servesBuilding", "top:isServedBy"),
}


def legacy_edge_predicates(edge_dictionary: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    key = str(edge_dictionary.get("IFC_type") or edge_dictionary.get("ifc_relationship") or "").upper()
    if key in _LEGACY_REL_PREDICATES:
        return _LEGACY_REL_PREDICATES[key]
    if key.startswith("IFCREL"):
        return "top:connectsTo", None
    return (edge_dictionary.get("ontology_predicate") or edge_dictionary.get("ontologyPredicate"),
            edge_dictionary.get("inverse_predicate") or edge_dictionary.get("inversePredicate"))


_METADATA_CLASSES = {"top:PropertySet", "top:Quantity", "top:Material", "top:MaterialSet",
                     "top:ClassificationReference"}

_LITERAL_REPR = "_RDFLiteral("


# --------------------------------------------------------------------------- #
# 1. literal repair (at the source)
# --------------------------------------------------------------------------- #

def install_literal_fix(KG) -> None:
    """Teach ``KnowledgeGraph.NormalizeTerm`` about ``Ontology._RDFLiteral``.

    Duck-typed on ``lexical``/``datatype``/``language`` so it survives the class
    being renamed; a no-op for values that are not such objects. Idempotent.
    """
    if getattr(KG, "_kg_compat_literal_fix", False):
        return
    orig = KG.NormalizeTerm

    def normalize(value, *args, **kwargs):
        lexical = getattr(value, "lexical", None)
        if lexical is not None and hasattr(value, "datatype") and hasattr(value, "language"):
            return _literal_token(str(lexical), getattr(value, "datatype", None),
                                  getattr(value, "language", None))
        return orig(value, *args, **kwargs)

    KG.NormalizeTerm = staticmethod(normalize)
    KG._kg_compat_literal_fix = True


def _escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))


def _qname(uri: Optional[str]) -> Optional[str]:
    if not uri:
        return None
    for prefix, ns in LEGACY_NAMESPACES.items():
        if uri.startswith(ns) and len(uri) > len(ns):
            return f"{prefix}:{uri[len(ns):]}"
    return f"<{uri}>" if "://" in uri else uri


def _literal_token(lexical: str, datatype: Optional[str] = None, language: Optional[str] = None) -> str:
    tok = '"' + _escape(lexical) + '"'
    if language:
        return tok + "@" + str(language).strip()
    if datatype:
        return tok + "^^" + _qname(str(datatype))
    return tok


def _lit(value: Any) -> str:
    """0.9.65 ``Ontology._literal`` for python values."""
    if isinstance(value, bool):
        return '"' + str(value).lower() + '"^^xsd:boolean'
    if isinstance(value, int):
        return '"' + str(value) + '"^^xsd:integer'
    if isinstance(value, float):
        return '"' + repr(value) + '"^^xsd:double'
    return '"' + _escape("" if value is None else str(value)) + '"'


def _version_at_least(version: Any, minimum: Tuple[int, ...]) -> bool:
    try:
        parts = tuple(int(p) for p in str(version).split("+")[0].split(".")[:3])
    except ValueError:
        return True
    return parts >= minimum


def corrupted_literals(triples: Iterable[Triple]) -> int:
    return sum(1 for _, _, o in triples if _LITERAL_REPR in o)


# --------------------------------------------------------------------------- #
# 2. identity (0.9.65 IRI minting)
# --------------------------------------------------------------------------- #

def _safe_local_name(value: Any) -> str:
    s = re.sub(r"[^A-Za-z0-9_\-]+", "_", str(value).strip())
    s = re.sub(r"_+", "_", s).strip("_") or "unnamed"
    return "id_" + s if s[0].isdigit() else s


_ROLE_INDEX_IRI = re.compile(r"^inst:(?:node|relationship|edge)_(id_\d+)$")


def _remint(token: str) -> str:
    """Re-mint a native ``inst:`` IRI the way 0.9.65 did.

    0.9.80 does not collapse repeated underscores (GUID ``..._$..`` ->
    ``..__..``; 0.9.65 minted ``.._..``) and prefixes index-only IRIs of
    GUID-less records with their role (``inst:node_id_9543``; 0.9.65
    ``inst:id_9543``).
    """
    if not token.startswith("inst:"):
        return token
    m = _ROLE_INDEX_IRI.match(token)
    if m:
        return "inst:" + m.group(1)
    if "__" in token:
        return "inst:" + re.sub(r"_+", "_", token[5:])
    return token


_ID_KEYS = ("uuid", "UUID", "id", "ID", "ifc_guid", "ifcGUID", "IFC_global_id", "GlobalId")


def _identity(record: Dict[str, Any], role: str, fallback: Any) -> str:
    d = record.get("dictionary") or {}
    for key in ("uri", "URI"):
        v = d.get(key)
        if isinstance(v, str) and v.strip():
            return "inst:" + _safe_local_name(v)
    for key in _ID_KEYS:
        v = d.get(key)
        if v not in (None, ""):
            return "inst:" + _safe_local_name(v)
    # 0.9.65 minted index-only IRIs without a role prefix (inst:id_9543);
    # 0.9.80 prefixes the role (inst:node_id_9543).
    return "inst:" + _safe_local_name(record.get("index", fallback))


# --------------------------------------------------------------------------- #
# 3. legacy vocabulary
# --------------------------------------------------------------------------- #

def _supertypes(ifc_class: str) -> List[str]:
    try:
        import ifcopenshell.ifcopenshell_wrapper as w
    except Exception:  # pragma: no cover - ifcopenshell ships in the worker
        return []
    for schema in ("IFC4", "IFC2X3", "IFC4X3_ADD2"):
        try:
            decl = w.schema_by_name(schema).declaration_by_name(ifc_class)
        except Exception:
            continue
        out = []
        while decl is not None:
            out.append(decl.name())
            try:
                decl = decl.supertype()
            except Exception:
                break
        return out
    return []


@lru_cache(maxsize=4096)
def legacy_class(ifc_class: Optional[str]) -> str:
    """TopologicPy class 0.9.65's TGraph assigned to an IFC entity."""
    if not ifc_class:
        return "top:Element"
    cls = str(ifc_class)
    if cls in _IFC_TO_TOP:
        return _IFC_TO_TOP[cls]
    up = cls.upper()
    if up.startswith("IFCREL"):
        return "top:Relationship"
    if up.endswith("PROPERTIES") or up.endswith("PROPERTYSET"):
        return "top:PropertySet"
    if up.startswith("IFCMATERIAL"):
        return "top:MaterialSet"
    for sup in _supertypes(cls)[1:]:
        if sup in _IFC_TO_TOP:
            return _IFC_TO_TOP[sup]
        if sup in _EQUIPMENT_SUPERTYPES:
            return "top:Equipment"
    if cls in _EQUIPMENT_SUPERTYPES:
        return "top:Equipment"
    return "top:Element"


def legacy_category(top_class: str) -> str:
    local = top_class.split(":", 1)[-1]
    if local in {"Graph", "Relationship", "Node"} or local.endswith("Graph"):
        return "graph"
    if local in {"Space", "Room", "Zone"}:
        return "space"
    if local in {"Building", "Site", "Storey", "Project"}:
        return local.lower()
    if top_class in _METADATA_CLASSES:
        return "metadata"
    return "element"


def _is_qname(token: Any) -> bool:
    if not isinstance(token, str) or ":" not in token:
        return False
    prefix, local = token.split(":", 1)
    return bool(local) and prefix in LEGACY_NAMESPACES and prefix != "dict"


def legacy_triples(graph_records: Dict[str, Any]) -> Set[Triple]:
    """The 0.9.65 vocabulary for a TGraph, from its records alone.

    ``graph_records`` = {"graph": graph dictionary, "V": vertex records,
    "E": edge records} (``TGraph.Vertices``/``TGraph.Edges``).
    """
    out: Set[Triple] = set()
    add = out.add
    gd = graph_records.get("graph") or {}
    gs = "inst:graph_graph"
    add((gs, "rdf:type", "top:Graph"))
    add((gs, "top:category", _lit("graph")))
    if gd.get("source") not in (None, ""):
        add((gs, "top:source", _lit(gd["source"])))

    subj: Dict[Any, str] = {}
    for i, v in enumerate(graph_records.get("V") or []):
        s = _identity(v, "node", i)
        subj[v.get("index", i)] = s
        d = v.get("dictionary") or {}
        cls = d.get("ontology_class") or legacy_class(d.get("IFC_type"))
        add((gs, "top:hasNode", s))
        add((s, "rdf:type", cls))
        bot = _TOP_TO_BOT.get(cls)
        if bot:
            add((s, "rdf:type", bot))
        # 0.9.65: "equipment" iff IFC.BrickClassByIFCClass found a Brick class
        # (TGraph keeps brick_class in both versions), else by class.
        add((s, "top:category", _lit("equipment" if d.get("brick_class") else legacy_category(cls))))
        for key, pred in (("IFC_type", "top:ifcType"), ("IFC_id", "top:ifcStepId"),
                          ("IFC_key", "top:ifcStepKey"), ("IFC_name", "top:ifcName"),
                          ("IFC_global_id", "top:ifcGUID")):
            if d.get(key) not in (None, ""):
                add((s, pred, _lit(d[key])))
        if d.get("IFC_global_id") and d.get("IFC_type"):
            # 0.9.65 only had ifc_class where KnowledgeGraphExport stamped a GUID.
            add((s, "top:ifcClass", _lit(d["IFC_type"])))

    for i, e in enumerate(graph_records.get("E") or []):
        es = _identity(e, "edge", i)
        d = e.get("dictionary") or {}
        add((gs, "top:hasRelationship", es))
        add((es, "rdf:type", "top:Relationship"))
        add((es, "top:category", _lit("relationship")))
        rel = d.get("relationship")
        if rel not in (None, ""):
            add((es, "top:relationship", _lit(rel)))
            add((es, "rdfs:label", _lit(rel)))
        for key, pred in (("src", "top:srcId"), ("dst", "top:dstId")):
            val = e.get(key, d.get(key))
            if val is not None:
                add((es, pred, _lit(val)))
        s, o = subj.get(e.get("src")), subj.get(e.get("dst"))
        if s is None or o is None:
            continue
        add((es, "top:startsAt", s))
        add((es, "top:endsAt", o))
        add((s, "top:connectsTo", o))
        pred, inv = legacy_edge_predicates(d)
        if _is_qname(pred):
            add((s, pred, o))
        if _is_qname(inv):
            add((o, inv, s))
    return out


# Legacy predicates restored per subject only when the subject has none yet, so
# values an up-to-date topologicpy already emits are never contradicted.
_PER_SUBJECT_ONCE = {"rdf:type", "top:category", "rdfs:label", "top:ifcType", "top:ifcStepId",
                     "top:ifcStepKey", "top:ifcName", "top:ifcGUID", "top:ifcClass",
                     "top:relationship", "top:srcId", "top:dstId", "top:source"}


def restore(native: Iterable[Triple], graph_records: Dict[str, Any]) -> Tuple[Set[Triple], Dict[str, int]]:
    """Native triples (re-minted) plus the missing legacy vocabulary.

    Returns ``(triples, stats)``. Additive only: a legacy triple is skipped when
    the native output already types the subject (``rdf:type top:*``) or already
    carries that single-valued predicate for it.
    """
    base: Set[Triple] = {(_remint(s), p, _remint(o)) for s, p, o in native}
    have = {(s, p) for s, p, _ in base}
    typed = {s for s, p, o in base if p == "rdf:type" and o.startswith("top:")}
    added: Set[Triple] = set()
    for t in legacy_triples(graph_records):
        s, p, _o = t
        if t in base:
            continue
        if p == "rdf:type":
            if s in typed:
                continue
        elif p in _PER_SUBJECT_ONCE and (s, p) in have:
            continue
        added.add(t)
    return base | added, {"legacy_triples_added": len(added),
                          "corrupted_literals": corrupted_literals(base)}


def turtle(triples: Iterable[Triple], namespaces: Optional[Dict[str, str]] = None) -> str:
    """Turtle exactly as 0.9.65's ``KnowledgeGraph.TurtleString`` wrote it.

    Header: the 15 legacy prefixes (sorted), then any other prefix of
    ``namespaces`` in its own order (a reasoned graph carries rdflib's extra
    bindings, e.g. ``csvw``/``dc``/``sh``). Body: statements sorted by their
    raw tokens, each term formatted like 0.9.65's ``_turtle_from_triples``
    (bare ``http(s)://`` IRIs are bracketed, unknown-prefix terms become
    literals / ``dict:`` predicates), duplicate lines dropped.
    """
    ns: Dict[str, str] = {p: LEGACY_NAMESPACES[p] for p in sorted(LEGACY_NAMESPACES)}
    for k, v in (namespaces or {}).items():
        ns.setdefault(str(k), str(v))

    def fmt(term: Any, predicate: bool = False) -> Optional[str]:
        if term is None:
            return None
        if not isinstance(term, str):
            return _lit(term)
        if term.startswith('"'):
            return term
        text = term.strip()
        if not text:
            return None
        if ":" in text:
            prefix, local = text.split(":", 1)
            if local and prefix in ns:
                return text
        if text.startswith("_:") or (text.startswith("<") and text.endswith(">")):
            return text
        if text.startswith(("http://", "https://")):
            return "<" + text + ">"
        return "dict:" + _safe_local_name(text) if predicate else _lit(text)

    lines = [f"@prefix {p}: <{u}> ." for p, u in ns.items()]
    lines.append("")
    seen: Set[str] = set()
    for s_, p_, o_ in sorted(triples):
        fs, fp, fo = fmt(s_), fmt(p_, predicate=True), fmt(o_)
        if fs is None or fp is None or fo is None:
            continue
        line = f"{fs} {fp} {fo} ."
        if line not in seen:
            seen.add(line)
            lines.append(line)
    return "\n".join(lines) + "\n"


def kg_turtle(kg: Any) -> str:
    """:func:`turtle` for a KnowledgeGraph, declaring the graph's own namespaces."""
    return turtle(kg.Triples(sort=False), namespaces=getattr(kg, "_namespaces", None))


def apply(kg: Any, graph_records: Dict[str, Any], KG: Any) -> Tuple[Any, Dict[str, int]]:
    """Return ``(kg', stats)``: ``kg`` with the legacy vocabulary restored.

    ``kg`` is returned unchanged when nothing is missing (topologicpy 0.9.65).
    Raises RuntimeError if literals are still corrupted (i.e. the upstream
    literal type changed shape and :func:`install_literal_fix` no longer
    matches it) -- exporting GUIDs as Python reprs must never pass silently.
    """
    native = list(kg.Triples(sort=False))
    triples, stats = restore(native, graph_records)
    if stats["corrupted_literals"]:
        raise RuntimeError(
            "KnowledgeGraph produced %d corrupted literals (%s...); the "
            "_kg_compat literal fix no longer matches this topologicpy"
            % (stats["corrupted_literals"], _LITERAL_REPR)
        )
    if triples == set(native):
        return kg, stats
    rebuilt = KG.ByTriples(sorted(triples), namespaces=dict(LEGACY_NAMESPACES), useRDFLib=False, silent=True)
    stats["triples"] = len(rebuilt)
    return rebuilt, stats
