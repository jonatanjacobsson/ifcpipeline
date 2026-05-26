"""Fast bbox neighbourhood regression."""

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ag_ifc.bbox_regression import BboxNeighbourhoodIndex
from ag_ifc.ifc_geometry import Aabb, IndexedElement


def test_new_overlap_detected():
    a = IndexedElement("g1", "IfcDuctSegment", "mep", Aabb(np.array([0, 0, 0]), np.array([1, 1, 1])), "f")
    b = IndexedElement("g2", "IfcBeam", "structural", Aabb(np.array([2, 0, 0]), np.array([3, 1, 1])), "f")
    idx = BboxNeighbourhoodIndex([a, b], cell_m=1.0)
    base = set()
    from ag_ifc.bbox_regression import check_bbox_regression

    r = check_bbox_regression(idx, "g1", np.array([1.5, 0, 0]), base, clearance_m=0.0)
    assert not r.passed
    assert len(r.new_overlaps) >= 1
