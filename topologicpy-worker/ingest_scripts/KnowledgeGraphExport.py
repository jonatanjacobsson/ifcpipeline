"""Knowledge Graph (RDF) export — opt-in semantic projection from the TGraph.

TopologicPy 0.9.52 added a ``KnowledgeGraph`` module that turns a TGraph into RDF
triples linked to BOT (Building Topology Ontology), Brick, IFC-OWL, GeoSPARQL and
PROV. This script is the worker-side entry point: it builds the same TGraph the LPG
scripts use (via :mod:`ingest_scripts.topograph`), converts it to a KnowledgeGraph,
and emits a Turtle (``.ttl``) artifact alongside the standard relationships JSON.

Design — RDF is a *derived projection*, not a backend (the LPG/Neo4j graph stays the
system of record):

  * ``reason``  — run RDFS + BOT inference; report how many triples were materialized.
  * ``merge``   — when several models are passed, union them into one semantic graph
                  (cross-model federation by shared identity/vocabulary, not geometry).
  * ``materialize`` — when reasoning, emit the *inferred element->element* edges back
                  into the relationships JSON so cde projects them into the LPG with
                  ``source_kind="topologic_reason_rdfs"`` (review-only, ``state=assumed``).
                  RDFS *type* assertions are node-level (not element<->element edges) so
                  cde's relationship ingest would skip them — they are counted in the
                  summary instead of emitted.

GlobalId identity: TopologicPy's ``Ontology._uri_for_topology`` mints an instance IRI
from the dictionary key ``ifc_guid`` first. The topograph adapter populates
``IFC_global_id``; :meth:`_set_guid_keys` copies it to ``ifc_guid`` so every instance
gets a GlobalId-unique IRI — without this, instances collapse by class label and a
merge over-deduplicates.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ingest_scripts import Ingester as _Base, Relationship
from ingest_scripts import topograph

# Predicates that are schema/annotation, never instance-to-instance edges.
_NON_EDGE_PRED = re.compile(
    r"(22-rdf-syntax-ns#type|rdf-schema#|2002/07/owl#|/skos/|/dc/|/dcterms/|vann#|#IFC_)",
    re.IGNORECASE,
)
_GUID_PRED = re.compile(r"(ifc_guid|IFC_global_id|globalId|#guid$)", re.IGNORECASE)


def _localname(uri: str) -> str:
    s = str(uri)
    for sep in ("#", "/"):
        if sep in s:
            s = s.rsplit(sep, 1)[-1]
    return s


class Ingester(_Base):
    SCRIPT_NAME = "KnowledgeGraphExport"
    DESCRIPTION = (
        "Export an RDF/Turtle KnowledgeGraph (BOT/Brick/IFC-OWL) from the TGraph; "
        "optional RDFS+BOT reasoning and multi-model semantic merge (0.9.52)."
    )

    def __init__(
        self,
        ifc_files: List[Path],
        log: logging.Logger,
        reason: bool = False,
        merge: bool = True,
        include_bot: bool = True,
        materialize: bool = True,
        fmt: str = "turtle",
    ):
        """Convert each model's TGraph into a KnowledgeGraph (RDF) and emit Turtle.

        :param reason: run RDFS + BOT inference and report materialized triples.
        :param merge: when multiple input files, union them into one semantic graph.
        :param include_bot: link instances to Building Topology Ontology concepts.
        :param materialize: emit inferred element->element edges into the LPG pipeline.
        :param fmt: RDF serialization format (currently "turtle").
        """
        super().__init__(ifc_files, log)
        self.reason = bool(reason)
        self.merge = bool(merge)
        self.include_bot = bool(include_bot)
        self.materialize = bool(materialize)
        self.fmt = str(fmt or "turtle")
        self._artifacts: List[Tuple[str, Any, str]] = []

    # --- helpers -------------------------------------------------------------
    def _set_guid_keys(self, g) -> int:
        """Copy ``IFC_global_id`` -> ``ifc_guid`` on every vertex so Ontology mints
        GlobalId-unique IRIs. Returns the number of vertices stamped."""
        n = 0
        try:
            from topologicpy.TGraph import TGraph
            verts = TGraph.Vertices(g)
        except Exception:
            self.log.warning("kg_export: could not enumerate vertices for guid keying", exc_info=True)
            return 0
        for rec in verts:
            d = rec.get("dictionary") if isinstance(rec, dict) else None
            if not isinstance(d, dict):
                continue
            gid = d.get("IFC_global_id") or d.get("GlobalId")
            if gid and not d.get("ifc_guid"):
                d["ifc_guid"] = gid
                if d.get("IFC_name") and not d.get("label"):
                    d["label"] = d["IFC_name"]
                if d.get("IFC_type") and not d.get("ifc_class"):
                    d["ifc_class"] = d["IFC_type"]
                n += 1
        return n

    @staticmethod
    def _triple_count(ttl: str) -> Optional[int]:
        try:
            import rdflib
            rg = rdflib.Graph()
            rg.parse(data=ttl, format="turtle")
            return len(rg)
        except Exception:
            return None

    @staticmethod
    def _prefixes(ttl: str) -> List[str]:
        out = []
        for line in ttl.splitlines():
            line = line.strip()
            if line.startswith("@prefix"):
                m = re.match(r"@prefix\s+([A-Za-z0-9_-]+):", line)
                if m:
                    out.append(m.group(1))
            elif not line.startswith("@") and line:
                break
        return out

    def _iri_to_guid(self, ttl: str) -> Dict[str, str]:
        """Map each subject IRI to its GlobalId by reading the guid property triples."""
        mapping: Dict[str, str] = {}
        try:
            import rdflib
            rg = rdflib.Graph()
            rg.parse(data=ttl, format="turtle")
            for s, p, o in rg:
                if _GUID_PRED.search(str(p)) and isinstance(o, rdflib.Literal):
                    mapping[str(s)] = str(o)
        except Exception:
            self.log.warning("kg_export: iri->guid mapping failed", exc_info=True)
        return mapping

    def _materialize_edges(self, ttl: str, source_kind: str) -> int:
        """Emit inferred element->element edges (both endpoints map to a GlobalId)."""
        guid = self._iri_to_guid(ttl)
        if not guid:
            return 0
        emitted = 0
        seen: set = set()
        try:
            import rdflib
            rg = rdflib.Graph()
            rg.parse(data=ttl, format="turtle")
            for s, p, o in rg:
                ps = str(p)
                if _NON_EDGE_PRED.search(ps) or _GUID_PRED.search(ps):
                    continue
                ss, os_ = str(s), str(o)
                sg, og = guid.get(ss), guid.get(os_)
                if not sg or not og or sg == og:
                    continue
                rtype = _localname(ps) or "related"
                key = (sg, og, rtype)
                if key in seen:
                    continue
                seen.add(key)
                self._relationships.append(Relationship(
                    subject_global_id=sg,
                    object_global_id=og,
                    relationship_family="semantic",
                    relationship_type=rtype,
                    confidence=1.0,
                    source_kind=source_kind,
                    evidence={"predicate": ps, "profile": "rdfs",
                              "bot": self.include_bot, "state": "assumed"},
                ))
                emitted += 1
        except Exception:
            self.log.warning("kg_export: edge materialization failed", exc_info=True)
        return emitted

    # --- main ----------------------------------------------------------------
    def extract(self) -> None:
        t0 = time.time()
        try:
            from topologicpy.KnowledgeGraph import KnowledgeGraph
        except Exception as exc:  # pragma: no cover - depends on topologicpy>=0.9.52
            raise RuntimeError(
                "KnowledgeGraphExport requires topologicpy>=0.9.52 (KnowledgeGraph module): %r" % exc
            )
        try:
            import rdflib  # noqa: F401
        except Exception as exc:
            raise RuntimeError("KnowledgeGraphExport requires rdflib (pip install rdflib): %r" % exc)

        per_file: List[Dict[str, Any]] = []
        kgs: List[Any] = []
        ttls: List[Tuple[str, str]] = []  # (stem, ttl)

        for ifc_path in self.ifc_files:
            stem = Path(ifc_path).stem
            self.log.info("kg_export: building TGraph for %s", stem)
            g = topograph.build_graph(ifc_path)
            stamped = self._set_guid_keys(g)
            kg = KnowledgeGraph.ByTopology(g, includeBOT=self.include_bot, silent=True)

            base_ttl = kg.TurtleString()
            base_n = self._triple_count(base_ttl)
            entry: Dict[str, Any] = {
                "file": Path(ifc_path).name,
                "vertices": topograph.order(g),
                "guid_keyed_vertices": stamped,
                "base_triples": base_n,
            }

            final_kg = kg
            ttl = base_ttl
            if self.reason:
                final_kg = kg.Infer(profile="rdfs", includeBOT=self.include_bot,
                                    includeOntologyAxioms=True)
                ttl = final_kg.TurtleString()
                inf_n = self._triple_count(ttl)
                entry["inferred_triples"] = inf_n
                entry["inferred_delta"] = (None if (inf_n is None or base_n is None)
                                          else inf_n - base_n)

            entry["prefixes"] = self._prefixes(ttl)
            per_file.append(entry)
            kgs.append(final_kg)
            ttls.append((stem, ttl))

        # Artifact(s): one merged graph, or one per file.
        merged_triples = None
        if self.merge and len(kgs) > 1:
            merged = KnowledgeGraph.MergeGraphs(kgs)
            mttl = merged.TurtleString()
            merged_triples = self._triple_count(mttl)
            name = "merged%s.kg.ttl" % (".reasoned" if self.reason else "")
            self._artifacts.append((name, mttl, "text/turtle"))
            if self.materialize and self.reason:
                self._materialize_edges(mttl, "topologic_reason_rdfs")
        else:
            for stem, ttl in ttls:
                safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem) or "model"
                name = "%s%s.kg.ttl" % (safe, ".reasoned" if self.reason else "")
                self._artifacts.append((name, ttl, "text/turtle"))
            if self.materialize and self.reason:
                for _stem, ttl in ttls:
                    self._materialize_edges(ttl, "topologic_reason_rdfs")

        self._summary = {
            "reason": self.reason,
            "merge": self.merge,
            "include_bot": self.include_bot,
            "materialize": self.materialize,
            "format": self.fmt,
            "files": per_file,
            "merged_triples": merged_triples,
            "artifacts": [a[0] for a in self._artifacts],
            "materialized_element_edges": len(self._relationships),
            "duration_ms": int((time.time() - t0) * 1000),
        }

    def get_artifacts(self) -> List[Tuple[str, Any, str]]:
        return self._artifacts
