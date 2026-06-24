"""Federated cross-model spatial relationships — geometry-derived, replayable recipe.

The federated graph's reason for being: relationships that exist in **no single IFC** because
discipline models are siloed (IFC `IfcRel*` are intra-file). When models are projected into one
revision keyed by `(projectId, revisionId, globalId)`, geometry reveals cross-discipline relations:

  * ``penetrates``      — an MEP run passes through a building element (duct/pipe through wall/slab)
  * ``intersects``      — generic solid overlap between two disciplines ("crosses")
  * ``sits_in``         — an element's body falls inside an IfcSpace
  * ``mounted_on``      — an element rests on / is fixed against a host surface (equipment on wall/slab)
  * ``within_clearance``— two cross-discipline elements are closer than a clearance threshold

These are **assumed** candidate evidence (ADR-006): geometry-derived, source-kind
``deterministic_geometry``, replayable (same revisions + recipe + engine ⇒ same edge set), and
review-only until confirmed. Sibling to ``WallHosting`` (the explicit, intra-file ``hosted_by``
instance) — together they are the federated relationship family.

v1 is an **AABB broad-phase** classifier (axis-aligned bounding boxes from IfcOpenShell world
geometry) + class-aware rules — deterministic and cheap. Precise TopologicPy Boolean narrow-phase
is a refinement; the ``assumed`` state already reflects that these need confirmation.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import ifcopenshell
import ifcopenshell.geom

from ingest_scripts import Ingester as _Base, Relationship, safe_by_type

# --- class taxonomy ---------------------------------------------------------
BUILDING_HOSTS = {
    "IfcWall", "IfcWallStandardCase", "IfcWallElementedCase", "IfcSlab", "IfcRoof",
    "IfcColumn", "IfcBeam", "IfcMember", "IfcPlate", "IfcCovering", "IfcCurtainWall",
}
MEP_RUNS = {
    "IfcPipeSegment", "IfcDuctSegment", "IfcCableCarrierSegment", "IfcCableSegment",
    "IfcFlowSegment", "IfcPipeFitting", "IfcDuctFitting", "IfcFlowFitting", "IfcCableCarrierFitting",
}
MEP_EQUIP = {
    "IfcFlowTerminal", "IfcEnergyConversionDevice", "IfcDistributionControlElement",
    "IfcFlowController", "IfcFlowMovingDevice", "IfcSanitaryTerminal", "IfcLightFixture",
    "IfcAirTerminal", "IfcElectricAppliance", "IfcOutlet", "IfcDistributionElement",
}
SPACES = {"IfcSpace", "IfcSpatialZone"}

# coarse discipline tag from class — cross-discipline is the federated value we want
_DISCIPLINE = [
    (MEP_RUNS | MEP_EQUIP, "mep"),
    ({"IfcColumn", "IfcBeam", "IfcMember", "IfcPlate", "IfcFooting", "IfcPile"}, "structural"),
    (BUILDING_HOSTS, "architectural"),
    (SPACES, "spatial"),
]


def discipline(ifc_class: str) -> str:
    for classes, disc in _DISCIPLINE:
        if ifc_class in classes:
            return disc
    return "other"


# --- geometry helpers (AABB + centroid in world coords) ---------------------
def _settings():
    s = ifcopenshell.geom.settings()
    try:
        s.set(s.USE_WORLD_COORDS, True)
    except Exception:
        pass
    return s


def aabb_of(element, settings) -> Optional[Tuple[float, ...]]:
    """World-coord axis-aligned bounding box (minx,miny,minz,maxx,maxy,maxz) or None."""
    try:
        shape = ifcopenshell.geom.create_shape(settings, element)
        verts = shape.geometry.verts
        if not verts:
            return None
        xs, ys, zs = verts[0::3], verts[1::3], verts[2::3]
        return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))
    except Exception:
        return None


def _overlap_1d(amin, amax, bmin, bmax) -> float:
    return min(amax, bmax) - max(amin, bmin)  # >0 overlap, <0 gap


def aabb_relation(a: Tuple[float, ...], b: Tuple[float, ...]) -> Dict[str, Any]:
    """Per-axis overlap (negative = gap) + derived overlap box / gap metrics."""
    ox = _overlap_1d(a[0], a[3], b[0], b[3])
    oy = _overlap_1d(a[1], a[4], b[1], b[4])
    oz = _overlap_1d(a[2], a[5], b[2], b[5])
    overlaps = ox > 0 and oy > 0 and oz > 0
    gap = 0.0 if overlaps else max(0.0, max(-ox, -oy, -oz))
    return {"ox": ox, "oy": oy, "oz": oz, "overlaps": overlaps, "gap": gap,
            "overlap_min_dim": min(ox, oy, oz) if overlaps else 0.0}


def _thinnest(a: Tuple[float, ...]) -> float:
    return min(a[3] - a[0], a[4] - a[1], a[5] - a[2])


# --- classification (pure, unit-testable with fake dicts) -------------------
def classify_pair(src: Dict[str, Any], tgt: Dict[str, Any], *, clearance: float = 0.05) -> Optional[Dict[str, Any]]:
    """Classify the geometric relation src→tgt. Returns {type, confidence, evidence} or None.

    src/tgt are dicts: {gid, ifc_class, discipline, aabb, centroid}. One predicate per pair
    (highest-priority match). Only cross-discipline pairs are emitted (the federated value)."""
    if src["discipline"] == tgt["discipline"]:
        return None
    sc, tc = src["ifc_class"], tgt["ifc_class"]

    # sits_in: any element whose centroid falls inside a space's AABB
    if tc in SPACES:
        cx, cy, cz = src["centroid"]
        ta = tgt["aabb"]
        if ta[0] <= cx <= ta[3] and ta[1] <= cy <= ta[4] and ta[2] <= cz <= ta[5]:
            return {"type": "sits_in", "confidence": 0.8,
                    "evidence": {"method": "aabb_centroid_in_space"}}
        return None
    if sc in SPACES:
        return None  # space as subject handled from the element side

    rel = aabb_relation(src["aabb"], tgt["aabb"])
    sa, ta = src["aabb"], tgt["aabb"]
    tol = max(clearance, 0.05)

    # penetrates: an MEP run overlapping a building host (passes through it)
    if rel["overlaps"] and sc in MEP_RUNS and tc in BUILDING_HOSTS:
        return {"type": "penetrates", "confidence": 0.8,
                "evidence": {"method": "aabb_overlap", "penetration_m": round(rel["overlap_min_dim"], 4),
                             "host_thickness_m": round(_thinnest(ta), 4)}}

    # mounted_on: element resting ON TOP of a host surface (z-contact within tol, xy overlap).
    # (Wall-face mounting and embedded fixings are a narrow-phase refinement.)
    if tc in BUILDING_HOSTS and sc not in BUILDING_HOSTS:
        if abs(sa[2] - ta[5]) <= tol and rel["ox"] > 0 and rel["oy"] > 0:
            return {"type": "mounted_on", "confidence": 0.75,
                    "evidence": {"method": "aabb_contact", "contact": "on_top"}}

    # generic cross-discipline solid overlap ("crosses")
    if rel["overlaps"]:
        return {"type": "intersects", "confidence": 0.7,
                "evidence": {"method": "aabb_overlap", "overlap_min_dim_m": round(rel["overlap_min_dim"], 4)}}

    # within_clearance: cross-discipline near-miss
    if 0 < rel["gap"] <= clearance:
        return {"type": "within_clearance", "confidence": 0.6,
                "evidence": {"method": "aabb_gap", "gap_m": round(rel["gap"], 4)}}
    return None


class Ingester(_Base):
    SCRIPT_NAME = "FederatedRelationships"
    DESCRIPTION = "Derive cross-discipline spatial relationships (penetrates/intersects/sits_in/mounted_on) across federated models"

    def __init__(self, ifc_files: List[Path], log: logging.Logger, clearance: float = 0.05, grid_m: float = 3.0):
        """Geometry-derived cross-model relationships from federated IFC models.

        :param clearance: max gap (m) for a within_clearance relation.
        :param grid_m: broad-phase grid cell size (m) for the target spatial index.
        """
        super().__init__(ifc_files, log)
        self.clearance = float(clearance)
        self.grid_m = float(grid_m)

    def _collect(self, ifc, settings) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for e in safe_by_type(ifc, "IfcProduct"):
            gid = getattr(e, "GlobalId", None)
            cls = e.is_a()
            if not gid or cls in {"IfcOpeningElement", "IfcOpeningStandardCase"}:
                continue
            a = aabb_of(e, settings)
            if a is None:
                continue
            out.append({"gid": gid, "ifc_class": cls, "discipline": discipline(cls),
                        "aabb": a, "centroid": ((a[0]+a[3])/2, (a[1]+a[4])/2, (a[2]+a[5])/2)})
        return out

    def extract(self) -> None:
        t0 = time.time()
        settings = _settings()
        elems: List[Dict[str, Any]] = []
        for ifc_path in self.ifc_files:
            self.log.info("federated_rel: geometry from %s", ifc_path.name)
            elems.extend(self._collect(ifcopenshell.open(str(ifc_path)), settings))

        # targets = hosts + spaces (what others relate TO); grid-index them for broad-phase
        targets = [e for e in elems if e["ifc_class"] in BUILDING_HOSTS or e["ifc_class"] in SPACES]
        grid: Dict[Tuple[int, int], List[int]] = {}
        g = self.grid_m
        for idx, t in enumerate(targets):
            a = t["aabb"]
            for cx in range(int(a[0] // g), int(a[3] // g) + 1):
                for cy in range(int(a[1] // g), int(a[4] // g) + 1):
                    grid.setdefault((cx, cy), []).append(idx)

        seen: set = set()
        emitted = 0
        by_type: Dict[str, int] = {}
        for src in sorted(elems, key=lambda e: e["gid"]):
            a = src["aabb"]
            cand: set = set()
            for cx in range(int(a[0] // g), int(a[3] // g) + 1):
                for cy in range(int(a[1] // g), int(a[4] // g) + 1):
                    cand.update(grid.get((cx, cy), ()))
            for ti in sorted(cand):
                tgt = targets[ti]
                if tgt["gid"] == src["gid"]:
                    continue
                res = classify_pair(src, tgt, clearance=self.clearance)
                if not res:
                    continue
                key = (src["gid"], tgt["gid"], res["type"])
                if key in seen:
                    continue
                seen.add(key)
                self._relationships.append(Relationship(
                    subject_global_id=src["gid"],
                    object_global_id=tgt["gid"],
                    relationship_family="spatial",
                    relationship_type=res["type"],
                    confidence=res["confidence"],
                    source_kind="topologic_ingest_FederatedRelationships",
                    evidence={**res["evidence"], "subjectClass": src["ifc_class"],
                              "objectClass": tgt["ifc_class"],
                              "subjectDiscipline": src["discipline"],
                              "objectDiscipline": tgt["discipline"], "state": "assumed"},
                ))
                by_type[res["type"]] = by_type.get(res["type"], 0) + 1
                emitted += 1

        self._summary = {"elements_with_geometry": len(elems), "targets": len(targets),
                         "relationships": emitted, "by_type": by_type,
                         "duration_ms": int((time.time() - t0) * 1000)}
