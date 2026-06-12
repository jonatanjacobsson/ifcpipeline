"""Discipline routing for TopologicPy ingest scripts (single source of truth).

Complete_model / federated discipline exports use the same prefix rules as single-discipline
files (e.g. E1-600-MM-Complete_model.ifc → MepTopology). Multi-file bundles (space + door)
remain Graph Studio only until n8n accepts extra inputs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePath

ARCHITECTURE_SCRIPTS = ["ExtractSpaces", "SpatialContainment", "SpaceAdjacency"]
STRUCTURAL_SCRIPTS = ["StructuralConnectivity"]
MEP_SCRIPTS = ["MepTopology"]

_COMPLETE_MODEL_RE = re.compile(r"complete[\s_]model", re.IGNORECASE)
_MEP_PREFIXES = ("E1", "V1", "P1")


def _basename(filename: str) -> str:
    return PurePath(filename.replace("\\", "/")).name


def is_complete_model(filename: str) -> bool:
    """True when basename contains Complete_model / Complete model (case-insensitive)."""
    return _COMPLETE_MODEL_RE.search(_basename(filename)) is not None


@dataclass
class IngestRoute:
    scripts: list[str]
    skip_reason: str | None = None

    @property
    def branch(self) -> str:
        if self.scripts == ARCHITECTURE_SCRIPTS:
            return "architecture"
        if self.scripts == STRUCTURAL_SCRIPTS:
            return "structural"
        if self.scripts == MEP_SCRIPTS:
            return "mep"
        return "live_drop_only"


def resolve_route(filename: str) -> IngestRoute:
    """Resolve ingest scripts for an IFC filename (basename prefix rules only)."""
    basename = _basename(filename)

    if basename.startswith("A1"):
        return IngestRoute(scripts=list(ARCHITECTURE_SCRIPTS))

    if basename.startswith("S2") or basename.startswith("K1"):
        return IngestRoute(scripts=list(STRUCTURAL_SCRIPTS))

    if any(basename.startswith(prefix) for prefix in _MEP_PREFIXES):
        return IngestRoute(scripts=list(MEP_SCRIPTS))

    return IngestRoute(scripts=[])
