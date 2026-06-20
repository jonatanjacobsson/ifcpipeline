"""Discipline x size test-model matrix for the TGraph evaluation.

Models are mounted read-only into the eval container at fixed mount points by
the ``docker run`` command documented in README.md:

    -v <ifcpipeline>/shared/uploads      : /uploads        (ro)
    -v <repo>/test-output/temp           : /models_extra   (ro)
    -v <repo>/idswidget/shared/uploads/6 : /models_xl       (ro)

Each entry records the discipline, the approximate on-disk size, and a
``heavy`` flag. Heavy models (>100 MB) are only included when ``--heavy`` is
passed, because legacy ``Graph`` betweenness centrality on them can take many
minutes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class Model:
    key: str
    discipline: str
    path: str            # path *inside* the eval container
    size_mb: int         # approximate
    heavy: bool = False

    @property
    def label(self) -> str:
        return f"{self.key} ({self.discipline}, ~{self.size_mb}MB)"


# Ordered roughly small -> large so a run produces fast feedback first.
MATRIX: List[Model] = [
    Model("E1", "Electrical",    "/uploads/E1-600-MM-Complete_model.ifc", 18),
    Model("S2", "Structural",    "/models_extra/S2_2B_BIM_XXX_0001_00.ifc", 18),
    Model("M1", "Mechanical",    "/models_extra/M1_2b_BIM_XXX_5700_00.ifc", 29),
    Model("A1", "Architecture",  "/uploads/A--40_V00000.ifc", 50),
    Model("P1", "Plumbing",      "/uploads/P1_2b_BIM_XXX_5000_00.ifc", 125, heavy=True),
    Model("AX", "Architecture",  "/models_xl/A-40-V-1006300.ifc", 142, heavy=True),
]

SMOKE_KEY = "E1"


def by_key(key: str) -> Model:
    for m in MATRIX:
        if m.key == key:
            return m
    raise KeyError(f"Unknown model key: {key}")


def select(smoke: bool = False, heavy: bool = False, keys: List[str] | None = None) -> List[Model]:
    """Resolve the set of models to run from CLI flags."""
    if keys:
        return [by_key(k) for k in keys]
    if smoke:
        return [by_key(SMOKE_KEY)]
    if heavy:
        return list(MATRIX)
    return [m for m in MATRIX if not m.heavy]
