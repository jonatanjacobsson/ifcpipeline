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


def host_wall_global_id(door) -> Optional[str]:
    """Resolve the GlobalId of the wall a door is hosted in, or ``None``.

    Walks the explicit IFC chain ``door.FillsVoids → IfcOpeningElement →
    (VoidsElements / HasOpenings) → RelatingBuildingElement`` and accepts only walls.
    Schema-tolerant on the inverse-attribute names that differ across IFC2X3 / IFC4.
    """
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
            if host and host.is_a() in WALL_TYPES:
                return host.GlobalId
    return None


class Ingester(_Base):
    SCRIPT_NAME = "WallHosting"
    DESCRIPTION = (
        "Extract door→wall hosting edges (hosted_by) from "
        "IfcRelFillsElement/IfcRelVoidsElement"
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
                wall_id = host_wall_global_id(door)
                if not wall_id:
                    unresolved += 1
                    continue
                pair = (door_id, wall_id)
                if pair in seen:
                    continue
                seen.add(pair)
                self._relationships.append(Relationship(
                    subject_global_id=door_id,
                    object_global_id=wall_id,
                    relationship_family="spatial",
                    relationship_type=HOSTED_BY_TYPE,
                    confidence=1.0,
                    source_kind="topologic_ingest_WallHosting",
                    evidence={
                        "rule": "ifc_fills_voids_host_wall",
                        "doorClass": door.is_a(),
                        "ifc": ifc_path.name,
                    },
                ))
                hosted += 1

        self._summary = {
            "doors_total": doors_total,
            "hosted_doors": hosted,
            "unresolved_doors": unresolved,
            "duration_ms": int((time.time() - t0) * 1000),
        }
