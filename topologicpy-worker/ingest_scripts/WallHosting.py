"""Extract door→wall hosting edges (``hosted_by``) — a replayable geometry-derived edge.

A door is hosted in the wall it fills:
``IfcDoor —IfcRelFillsElement→ IfcOpeningElement —IfcRelVoidsElement→ IfcWall``.

ADR-007 *permanently* excludes those geometry-moving ``IfcRel*`` from the graph-authoring
registry, so the hosting link is never authored directly. This script recovers it as a
**replayable ingest recipe** (ADR-006 *"geometry-derived relationships as replayable
recipes"*): the host wall is read from the explicit IFC relationships — no geometry, no
heuristics — so re-running reproduces the identical ``hosted_by`` edge set, and the same
script re-applied to a new revision backfills the edge without touching geometry.

Emits one ``hosted_by`` relationship per hosted door (subject=door, object=wall) in the
``spatial`` family with ``source_kind=topologic_ingest_WallHosting``. Because the link is
read straight from ``IfcRelFillsElement``/``IfcRelVoidsElement`` (not inferred), confidence
is ``1.0``. The emitted set is deterministic: doors are processed in sorted ``GlobalId``
order and each ``(door, wall)`` pair is emitted at most once.

Downstream, the CDE projection turns ``hosted_by`` into a Neo4j ``HOSTED_BY`` edge
``(:Element door)-[:HOSTED_BY]->(:Element wall)`` (``_neo4j_rel_label``), which the graph
door-selector can traverse to match a door by its host wall (``selector.hostWall``).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import List, Optional, Set, Tuple

import ifcopenshell

from ingest_scripts import (
    Ingester as _Base,
    Relationship,
    safe_by_type,
)

# IfcWall and its standard/elemented-case subtypes carry the void a door fills.
WALL_TYPES = {"IfcWall", "IfcWallStandardCase", "IfcWallElementedCase"}
HOSTED_BY_TYPE = "hosted_by"  # IfcDoor --> IfcWall (the wall that hosts the door)


def host_element_of(door) -> Optional[Tuple[str, str]]:
    """Resolve ``(globalId, ifcClass)`` of the element whose opening the door fills, or None.

    Walks the explicit IFC chain ``door.FillsVoids → IfcOpeningElement →
    (VoidsElements / HasOpenings) → RelatingBuildingElement``. **Prefers a wall** when the
    opening is voided into one; otherwise returns the actual host (e.g. an ``IfcCovering`` an
    opening was cut into, a curtain wall, a slab). Every door with an explicit void host
    therefore resolves — ``WALL_TYPES`` is only a *preference*, not a filter (a too-narrow
    filter dropped doors hosted in non-wall elements). Schema-tolerant on inverse-attribute
    names across IFC2X3 / IFC4. Returns None only when the door has no void host at all.
    """
    fallback: Optional[Tuple[str, str]] = None
    for rel in getattr(door, "FillsVoids", None) or []:
        opening = getattr(rel, "RelatedOpeningElement", None) or getattr(
            rel, "RelatingOpeningElement", None
        )
        if not opening:
            continue
        void_rels = (
            getattr(opening, "VoidsElements", None)
            or getattr(opening, "HasOpenings", None)
            or []
        )
        for vrel in void_rels:
            host = getattr(vrel, "RelatingBuildingElement", None) or getattr(
                vrel, "RelatedBuildingElement", None
            )
            if not host:
                continue
            if host.is_a() in WALL_TYPES:
                return host.GlobalId, host.is_a()
            if fallback is None:
                fallback = (host.GlobalId, host.is_a())
    return fallback


def host_wall_global_id(door) -> Optional[str]:
    """Backward-compat: the host GlobalId only when it is a wall (else None)."""
    res = host_element_of(door)
    return res[0] if res and res[1] in WALL_TYPES else None


class Ingester(_Base):
    SCRIPT_NAME = "WallHosting"
    DESCRIPTION = (
        "Extract door→host hosting edges (hosted_by, prefers wall) from "
        "IfcRelFillsElement/IfcRelVoidsElement; host class recorded in evidence"
    )

    def __init__(
        self,
        ifc_files: List[Path],
        log: logging.Logger,
        door_query: str = "IfcDoor",
    ):
        """Extract door→wall hosting relationships from IFC models.

        Reads the explicit IfcRelFillsElement/IfcRelVoidsElement chain that records which
        wall hosts each door and emits a ``hosted_by`` edge (door → wall). No geometry is
        computed; the link is deterministic and replayable.

        :param door_query: IFC class queried for hosted elements (default IfcDoor; subtypes included).
        """
        super().__init__(ifc_files, log)
        self.door_query = door_query

    def extract(self) -> None:
        t0 = time.time()
        seen: Set[Tuple[str, str]] = set()
        doors_total = 0
        hosted = 0
        hosted_in_wall = 0
        hosted_in_non_wall = 0
        unresolved = 0

        for ifc_path in self.ifc_files:
            self.log.info("wall_hosting: processing %s", ifc_path.name)
            ifc = ifcopenshell.open(str(ifc_path))
            doors = safe_by_type(ifc, self.door_query)
            for door in sorted(doors, key=lambda d: getattr(d, "GlobalId", "") or ""):
                door_id = getattr(door, "GlobalId", None)
                if not door_id:
                    continue
                doors_total += 1
                res = host_element_of(door)
                if not res:
                    unresolved += 1
                    continue
                host_id, host_class = res
                pair = (door_id, host_id)
                if pair in seen:
                    continue
                seen.add(pair)
                is_wall = host_class in WALL_TYPES
                self._relationships.append(Relationship(
                    subject_global_id=door_id,
                    object_global_id=host_id,
                    relationship_family="spatial",
                    relationship_type=HOSTED_BY_TYPE,
                    confidence=1.0,
                    source_kind="topologic_ingest_WallHosting",
                    evidence={
                        "rule": "ifc_fills_voids_host",
                        "doorClass": door.is_a(),
                        "hostClass": host_class,
                        "isWall": is_wall,
                        "ifc": ifc_path.name,
                    },
                ))
                hosted += 1
                hosted_in_wall += int(is_wall)
                hosted_in_non_wall += int(not is_wall)

        self._summary = {
            "doors_total": doors_total,
            "hosted_doors": hosted,
            "hosted_in_wall": hosted_in_wall,
            "hosted_in_non_wall": hosted_in_non_wall,
            "unresolved_doors": unresolved,
            "duration_ms": int((time.time() - t0) * 1000),
        }
