"""report_detail=simple stamps only room identity into the target pset."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from shared.classes import TopologicpyRequest

_WORKER = Path(__file__).resolve().parents[1] / "topologicpy-worker"
if str(_WORKER) not in sys.path:
    sys.path.insert(0, str(_WORKER))

tasks = pytest.importorskip("tasks")


def _space(name="1234", long_name="Kontor"):
    return tasks.SpaceCandidate(
        source_file="A_spaces.ifc",
        global_id="0Ab1cD2eF3gH4iJ5kL6mN7",
        name=name,
        long_name=long_name,
        storey="Plan 1",
        zones=[{"name": "Zon A", "global_id": "1Zz"}],
        bbox=tasks.BBox(0.0, 0.0, 0.0, 1.0, 1.0, 1.0),
    )


def _resolution(space):
    return tasks.MatchResolution(
        space=space,
        method="overlap_majority",
        status="Contained",
        confidence=0.93,
        candidate_count=1,
    )


class _FakePsetCache:
    def __init__(self):
        self.pset = object()

    def get_or_create(self, element):
        return self.pset


def _stamp(monkeypatch, **kwargs):
    """Run _stamp_element against a stubbed ifcopenshell.api and return the props."""
    import ifcopenshell.api

    captured = {}

    def fake_run(name, model, **params):
        captured["name"] = name
        captured["properties"] = params.get("properties")
        return None

    monkeypatch.setattr(ifcopenshell.api, "run", fake_run)

    space = kwargs.pop("space", None) or _space()
    tasks._stamp_element(
        object(),
        object(),
        space,
        _resolution(space),
        "Pset_IfcPipelineRoomStamp",
        "topologicpy",
        pset_cache=_FakePsetCache(),
        **kwargs,
    )
    assert captured["name"] == "pset.edit_pset"
    return captured["properties"]


def test_request_defaults_to_summary():
    request = TopologicpyRequest(
        spatial_files=["A_spaces.ifc"], element_files=["A.ifc"]
    )
    # summary is the backward-compatible default: it stamps the full SpatialMatch*
    # pset that graph ingestion (ExtractRoomStamp) depends on. simple is opt-in.
    assert request.report_detail == "summary"
    assert request.space_number_attribute == "Name"
    assert request.space_name_attribute == "LongName"


def test_report_detail_rejects_unknown_level():
    with pytest.raises(ValueError):
        TopologicpyRequest(
            spatial_files=["A_spaces.ifc"],
            element_files=["A.ifc"],
            report_detail="verbose",
        )


def test_simple_properties_map_name_to_number_and_long_name_to_long_name():
    props = tasks._simple_stamp_properties(_space(), "Name", "LongName")
    assert props == {"SpaceNumber": "1234", "SpaceLongName": "Kontor"}


def test_simple_properties_honour_attribute_overrides():
    props = tasks._simple_stamp_properties(_space(), "LongName", "Name")
    assert props == {"SpaceNumber": "Kontor", "SpaceLongName": "1234"}


def test_simple_properties_coerce_missing_attributes_to_empty_string():
    props = tasks._simple_stamp_properties(_space(name=None, long_name=None), "Name", "LongName")
    assert props == {"SpaceNumber": "", "SpaceLongName": ""}


def test_simple_stamp_writes_only_two_properties(monkeypatch):
    props = _stamp(monkeypatch, report_detail="simple")
    # SpaceLongName (not SpaceName) so the key never collides with the summary
    # pset, where SpaceName means the IfcSpace Name attribute.
    assert set(props) == {"SpaceNumber", "SpaceLongName"}
    assert props["SpaceNumber"] == "1234"
    assert props["SpaceLongName"] == "Kontor"


def test_summary_stamp_keeps_diagnostic_properties(monkeypatch):
    props = _stamp(monkeypatch, report_detail="summary")
    assert "SpatialMatchStatus" in props
    assert props["SpaceGlobalId"] == "0Ab1cD2eF3gH4iJ5kL6mN7"
    # summary keeps the legacy meaning: SpaceName is the IfcSpace Name attribute
    assert props["SpaceName"] == "1234"
    assert props["SpaceLongName"] == "Kontor"
    assert "SpaceNumber" not in props


def test_full_stamp_matches_summary_stamp(monkeypatch):
    assert _stamp(monkeypatch, report_detail="full") == _stamp(
        monkeypatch, report_detail="summary"
    )
