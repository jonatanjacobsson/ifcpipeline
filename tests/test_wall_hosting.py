"""Unit tests for the WallHosting ingest script (door→wall ``hosted_by`` edge)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "topologicpy-worker"))

import ingest_scripts.WallHosting as wh
from ingest_scripts.WallHosting import Ingester, host_element_of, host_wall_global_id


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


def _door_hosted_in(door_gid, host_gid, host_cls):
    opening = _Opening([_VoidRel(_Wall(host_gid, cls=host_cls))])
    return _Door(door_gid, [_FillRel(opening)])


def test_non_wall_host_resolves_but_wall_compat_is_none():
    # the SBUF case: 14 doors are voided into an IfcCovering, not a wall
    door = _door_hosted_in("D3", "C1", "IfcCovering")
    assert host_wall_global_id(door) is None              # wall-only compat: None
    assert host_element_of(door) == ("C1", "IfcCovering")  # broadened: resolves the actual host


def test_host_element_prefers_wall_over_non_wall():
    op = _Opening([_VoidRel(_Wall("C1", cls="IfcCovering")), _VoidRel(_Wall("W1", cls="IfcWall"))])
    assert host_element_of(_Door("D", [_FillRel(op)])) == ("W1", "IfcWall")


# --- Ingester ---------------------------------------------------------------


def test_ingester_emits_hosted_by_incl_non_wall(monkeypatch):
    # D1 hosted in a wall, D2 in a covering (the SBUF gap), D3 unhosted
    doors = [_hosted_door("D1", "W1"), _door_hosted_in("D2", "C2", "IfcCovering"), _Door("D3", [])]
    monkeypatch.setattr(wh.ifcopenshell, "open", lambda _p: object())
    monkeypatch.setattr(wh, "safe_by_type", lambda _ifc, _q: doors)

    ing = Ingester([Path("model.ifc")], logging.getLogger("test"))
    ing.extract()
    rels = ing.get_relationships()

    assert len(rels) == 2
    by_door = {r["subject_global_id"]: r for r in rels}
    assert by_door["D1"]["object_global_id"] == "W1"
    assert by_door["D1"]["evidence"]["isWall"] is True
    assert by_door["D2"]["object_global_id"] == "C2"
    assert by_door["D2"]["evidence"]["hostClass"] == "IfcCovering"
    assert by_door["D2"]["evidence"]["isWall"] is False
    assert all(r["relationship_type"] == "hosted_by" for r in rels)
    assert all(r["confidence"] == 1.0 for r in rels)

    summary = ing.get_summary()
    assert summary["doors_total"] == 3
    assert summary["hosted_doors"] == 2
    assert summary["hosted_in_wall"] == 1
    assert summary["hosted_in_non_wall"] == 1
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
