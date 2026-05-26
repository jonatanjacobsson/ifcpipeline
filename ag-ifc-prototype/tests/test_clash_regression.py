"""Regression snapshot and BCF export tests."""

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ag_ifc.bcf_export import ValidatedFix, export_validated_fixes_bcf
from ag_ifc.clash_regression import ClashSnapshot, clash_stable_id, compare_regression


def test_stable_id_symmetric():
    c = {"a_global_id": "A", "b_global_id": "B"}
    assert clash_stable_id(c) == clash_stable_id({"a_global_id": "B", "b_global_id": "A"})


def test_regression_detects_new_global_clash():
    base = ClashSnapshot(
        stable_ids={"A|B"},
        clash_key_by_stable={"A|B": "k1"},
        count=1,
        clashes={"k1": {"a_global_id": "A", "b_global_id": "B"}},
    )
    current = ClashSnapshot(
        stable_ids={"A|B", "C|D"},
        clash_key_by_stable={"A|B": "k1", "C|D": "k2"},
        count=2,
        clashes={},
    )
    report = compare_regression(base, current)
    assert not report.passed
    assert "C|D" in report.new_global_clashes


def test_bcf_export_writes_file(tmp_path):
    fix = ValidatedFix(
        case_id="t1",
        clash_key="k",
        stable_id="A|B",
        a_global_id="A",
        b_global_id="B",
        a_ifc_class="IfcDuctSegment",
        b_ifc_class="IfcBeam",
        moved_guid="A",
        moved_class="IfcDuctSegment",
        position=[1.0, 2.0, 3.0],
        translation=[0.0, 0.2, 0.0],
        route_waypoints=[[1, 2, 3], [1, 2.2, 3]],
        attempts=2,
        ag_proven=True,
        regression_passed=True,
    )
    out = tmp_path / "fixes.bcf"
    export_validated_fixes_bcf([fix], out)
    assert out.is_file() and out.stat().st_size > 500
