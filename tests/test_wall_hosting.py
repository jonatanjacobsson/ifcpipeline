"""Unit tests for the WallHosting ingest script (door→wall ``hosted_by`` edge)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "topologicpy-worker"))

import ingest_scripts.WallHosting as wh
from ingest_scripts.WallHosting import Ingester, host_wall_global_id


# --- fake IFC objects (mirror test_egress_door_linking style) ---------------


class _Wall:
    def __init__(self, gid, cls="IfcWall"):
        self.GlobalId = gid
        self._c = cls

    def is_a(self):
        return self._c


class _VoidRel:
    def __init__(self, wall):
        self.RelatingBuildingElement = wall


class _Opening:
    def __init__(self, voids):
        self.VoidsElements = voids


class _FillRel:
    def __init__(self, opening):
        self.RelatedOpeningElement = opening


class _Door:
    def __init__(self, gid, fills, cls="IfcDoor"):
        self.GlobalId = gid
        self.FillsVoids = fills
        self._c = cls

    def is_a(self):
        return self._c


def _hosted_door(door_gid="D1", wall_gid="W1"):
    wall = _Wall(wall_gid)
    opening = _Opening([_VoidRel(wall)])
    return _Door(door_gid, [_FillRel(opening)])


# --- host_wall_global_id ----------------------------------------------------


def test_host_wall_global_id_resolves_wall():
    assert host_wall_global_id(_hosted_door("D1", "W9")) == "W9"


def test_host_wall_global_id_none_when_unhosted():
    assert host_wall_global_id(_Door("D2", [])) is None


def test_host_wall_global_id_ignores_non_wall_host():
    opening = _Opening([_VoidRel(_Wall("S1", cls="IfcSlab"))])
    assert host_wall_global_id(_Door("D3", [_FillRel(opening)])) is None


# --- Ingester ---------------------------------------------------------------


def test_ingester_emits_hosted_by(monkeypatch):
    doors = [_hosted_door("D1", "W1"), _hosted_door("D2", "W2"), _Door("D3", [])]
    monkeypatch.setattr(wh.ifcopenshell, "open", lambda _p: object())
    monkeypatch.setattr(wh, "safe_by_type", lambda _ifc, _q: doors)

    ing = Ingester([Path("model.ifc")], logging.getLogger("test"))
    ing.extract()
    rels = ing.get_relationships()

    assert len(rels) == 2
    by_door = {r["subject_global_id"]: r for r in rels}
    assert by_door["D1"]["object_global_id"] == "W1"
    assert by_door["D2"]["object_global_id"] == "W2"
    assert all(r["relationship_type"] == "hosted_by" for r in rels)
    assert all(r["relationship_family"] == "spatial" for r in rels)
    assert all(r["confidence"] == 1.0 for r in rels)
    assert all(r["source_kind"] == "topologic_ingest_WallHosting" for r in rels)

    summary = ing.get_summary()
    assert summary["doors_total"] == 3
    assert summary["hosted_doors"] == 2
    assert summary["unresolved_doors"] == 1


def test_ingester_dedupes_pairs(monkeypatch):
    """The same (door, wall) pair is emitted once — replay/idempotency parity."""
    monkeypatch.setattr(wh.ifcopenshell, "open", lambda _p: object())
    monkeypatch.setattr(
        wh, "safe_by_type", lambda _ifc, _q: [_hosted_door("D1", "W1"), _hosted_door("D1", "W1")]
    )
    ing = Ingester([Path("m.ifc")], logging.getLogger("test"))
    ing.extract()
    assert len(ing.get_relationships()) == 1
