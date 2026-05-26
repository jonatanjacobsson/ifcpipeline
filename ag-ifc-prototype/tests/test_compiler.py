"""Unit tests for clash formalization stub."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ag_ifc.compiler import clash_to_ag2_stub, load_clash


def test_clash_stub_contains_parallel_goal():
    clash = load_clash(ROOT / "fixtures" / "clash_sample.json")
    stub = clash_to_ag2_stub(clash)
    assert "? para" in stub.ag2
    assert stub.mapping["a"] == "beam_start"


def test_clash_sample_json_valid():
    data = json.loads((ROOT / "fixtures" / "clash_sample.json").read_text())
    assert data["clash_id"] == "sample-001"
