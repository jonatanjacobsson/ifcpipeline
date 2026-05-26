"""Fast global regression screening via AABB overlap in a local neighbourhood."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ag_ifc.ifc_geometry import (
    Aabb,
    IndexedElement,
    aabb_intersects,
    build_model_aabb_index,
    merge_indices,
    translate_aabb,
)


def _pair_key(a: str, b: str) -> str:
    return "|".join(sorted((a, b)))


@dataclass
class BboxOverlap:
    guid_a: str
    guid_b: str
    class_a: str
    class_b: str
    discipline_a: str
    discipline_b: str


@dataclass
class BboxRegressionReport:
    passed: bool
    moved_guid: str
    region_checked: list[float]
    baseline_overlap_count: int
    current_overlap_count: int
    new_overlaps: list[BboxOverlap] = field(default_factory=list)
    cleared_overlaps: int = 0
    elapsed_ms: float = 0.0
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "moved_guid": self.moved_guid,
            "region_checked": self.region_checked,
            "baseline_overlap_count": self.baseline_overlap_count,
            "current_overlap_count": self.current_overlap_count,
            "new_overlap_count": len(self.new_overlaps),
            "new_overlaps": [
                {
                    "guid_a": o.guid_a,
                    "guid_b": o.guid_b,
                    "class_a": o.class_a,
                    "class_b": o.class_b,
                }
                for o in self.new_overlaps[:50]
            ],
            "cleared_overlaps": self.cleared_overlaps,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "message": self.message,
        }


class BboxNeighbourhoodIndex:
    """Spatial index (grid bucket) for AABB overlap queries in a region."""

    def __init__(self, elements: list[IndexedElement], cell_m: float = 2.0):
        self.elements = elements
        self.cell_m = max(cell_m, 0.5)
        self._buckets: dict[tuple[int, int, int], list[int]] = {}
        for i, el in enumerate(elements):
            for cell in self._cells_for_aabb(el.aabb):
                self._buckets.setdefault(cell, []).append(i)

    def _cells_for_aabb(self, aabb: Aabb) -> set[tuple[int, int, int]]:
        cs = self.cell_m
        i0, j0, k0 = np.floor(aabb.min_corner / cs).astype(int)
        i1, j1, k1 = np.floor(aabb.max_corner / cs).astype(int)
        cells: set[tuple[int, int, int]] = set()
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                for k in range(k0, k1 + 1):
                    cells.add((i, j, k))
        return cells

    def query_aabb(self, region: Aabb) -> list[IndexedElement]:
        seen: set[int] = set()
        out: list[IndexedElement] = []
        for cell in self._cells_for_aabb(region):
            for idx in self._buckets.get(cell, []):
                if idx in seen:
                    continue
                seen.add(idx)
                out.append(self.elements[idx])
        return out

    def overlaps_for_element(
        self,
        element: IndexedElement,
        *,
        exclude_guids: set[str] | None = None,
        clearance_m: float = 0.0,
    ) -> list[BboxOverlap]:
        exclude = exclude_guids or set()
        probe = element.aabb.inflated(clearance_m)
        candidates = self.query_aabb(probe)
        hits: list[BboxOverlap] = []
        for other in candidates:
            if other.guid == element.guid or other.guid in exclude:
                continue
            if aabb_intersects(probe, other.aabb):
                hits.append(
                    BboxOverlap(
                        guid_a=element.guid,
                        guid_b=other.guid,
                        class_a=element.ifc_class,
                        class_b=other.ifc_class,
                        discipline_a=element.discipline,
                        discipline_b=other.discipline,
                    )
                )
        return hits


def build_federated_index(
    ifc_paths: list[str],
    *,
    extra_paths: list[str] | None = None,
    cell_m: float = 2.0,
) -> BboxNeighbourhoodIndex:
    parts = [build_model_aabb_index(p) for p in ifc_paths]
    if extra_paths:
        parts.extend(build_model_aabb_index(p) for p in extra_paths)
    return BboxNeighbourhoodIndex(merge_indices(parts), cell_m=cell_m)


def baseline_overlap_keys(
    index: BboxNeighbourhoodIndex,
    guid: str,
    *,
    clearance_m: float = 0.05,
) -> set[str]:
    el = next((e for e in index.elements if e.guid == guid), None)
    if el is None:
        return set()
    return {_pair_key(o.guid_a, o.guid_b) for o in index.overlaps_for_element(el, clearance_m=clearance_m)}


def check_bbox_regression(
    index: BboxNeighbourhoodIndex,
    moved_guid: str,
    translation: np.ndarray,
    baseline_keys: set[str],
    *,
    clearance_m: float = 0.05,
    allow_new: bool = False,
) -> BboxRegressionReport:
    import time

    start = time.perf_counter()
    el = next((e for e in index.elements if e.guid == moved_guid), None)
    if el is None:
        return BboxRegressionReport(
            passed=True,
            moved_guid=moved_guid,
            region_checked=[],
            baseline_overlap_count=len(baseline_keys),
            current_overlap_count=0,
            message="moved element not in bbox index",
            elapsed_ms=(time.perf_counter() - start) * 1000,
        )

    moved = IndexedElement(
        guid=el.guid,
        ifc_class=el.ifc_class,
        discipline=el.discipline,
        aabb=translate_aabb(el.aabb, translation),
        source_file=el.source_file,
    )
    current_hits = index.overlaps_for_element(moved, clearance_m=clearance_m)
    current_keys = {_pair_key(h.guid_a, h.guid_b) for h in current_hits}
    new_keys = current_keys - baseline_keys
    cleared = len(baseline_keys - current_keys)

    new_overlaps = [h for h in current_hits if _pair_key(h.guid_a, h.guid_b) in new_keys]
    passed = allow_new or len(new_keys) == 0
    msg = "bbox regression ok" if passed else f"{len(new_keys)} new AABB overlap(s) nearby"

    region = moved.aabb.inflated(clearance_m)
    return BboxRegressionReport(
        passed=passed,
        moved_guid=moved_guid,
        region_checked=[
            float(region.min_corner[0]),
            float(region.min_corner[1]),
            float(region.min_corner[2]),
            float(region.max_corner[0]),
            float(region.max_corner[1]),
            float(region.max_corner[2]),
        ],
        baseline_overlap_count=len(baseline_keys),
        current_overlap_count=len(current_keys),
        new_overlaps=new_overlaps,
        cleared_overlaps=cleared,
        elapsed_ms=(time.perf_counter() - start) * 1000,
        message=msg,
    )
