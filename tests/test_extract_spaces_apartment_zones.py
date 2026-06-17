"""Unit tests for ExtractSpaces apartment zone derivation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from ingest_scripts.ExtractSpaces import (
    apartment_aggregate_guids,
    apartment_id_from_space,
    group_member_space_guids,
    is_apartment_room_space,
)


@dataclass
class _FakeSpace:
    GlobalId: str
    Name: str = ""
    LongName: str = ""
    _psets: Optional[Dict[str, Dict[str, Any]]] = None

    def is_a(self, name: str) -> bool:
        return name == "IfcSpace"


def _patch_psets(monkeypatch, mapping):
    def _get_psets(space, psets_only=True):
        return mapping.get(space.GlobalId, {})

    monkeypatch.setattr(
        "ingest_scripts.ExtractSpaces.ifcopenshell.util.element.get_psets",
        _get_psets,
    )


def test_apartment_id_from_bip_property(monkeypatch):
    space = _FakeSpace("g1", Name="2-1103-2")
    _patch_psets(monkeypatch, {"g1": {"BIP": {"Appartment": "2-1103"}}})
    assert apartment_id_from_space(space) == "2-1103"


def test_apartment_id_from_room_name_when_bip_missing(monkeypatch):
    space = _FakeSpace("g1", Name="2-1104-1")
    _patch_psets(monkeypatch, {"g1": {"BIP": {}}})
    assert apartment_id_from_space(space) == "2-1104"


def test_apartment_id_from_aggregate_name(monkeypatch):
    space = _FakeSpace("g1", Name="2-1105", LongName="2 ROK")
    _patch_psets(monkeypatch, {"g1": {}})
    assert apartment_id_from_space(space) == "2-1105"


def test_apartment_aggregate_guids_index():
    spaces = [
        _FakeSpace("agg1", Name="2-1103", LongName="2 ROK"),
        _FakeSpace("room1", Name="2-1103-2", LongName="Kök"),
        _FakeSpace("agg2", Name="2-1104", LongName="3 ROK"),
    ]
    assert apartment_aggregate_guids(spaces) == {
        "2-1103": "agg1",
        "2-1104": "agg2",
    }


def test_is_apartment_room_space():
    assert is_apartment_room_space(_FakeSpace("r", Name="2-1103-2")) is True
    assert is_apartment_room_space(_FakeSpace("a", Name="2-1103", LongName="2 ROK")) is False


@dataclass
class _FakeRel:
    RelatedObjects: list

    def is_a(self, name: str) -> bool:
        return name == "IfcRelAssignsToGroup"


class _FakeGroup:
    def __init__(self, members):
        self.IsGroupedBy = [_FakeRel(members)]


def test_group_member_space_guids_deduplicates():
    members = [
        _FakeSpace("s1", Name="a"),
        _FakeSpace("s1", Name="a-dup"),
        _FakeSpace("s2", Name="b"),
    ]
    assert group_member_space_guids(_FakeGroup(members)) == ["s1", "s2"]
