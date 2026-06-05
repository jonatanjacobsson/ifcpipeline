"""Unit tests for Nobel A1 property mapping tables and shared utils."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import ifcopenshell
import ifcopenshell.guid
import pytest

WORKER_ROOT = Path(__file__).resolve().parent.parent
CUSTOM = WORKER_ROOT / "custom_recipes"
if str(CUSTOM) not in sys.path:
    sys.path.insert(0, str(CUSTOM))

from _property_mapping_utils import is_not_duplicate_owned, set_pset_property  # noqa: E402

# Nobel mapping modules are gitignored — skip tests when not installed locally.
_NOBEL_CONTRACT = CUSTOM / "mappings" / "nobel_a1_contract_id.py"
_NOBEL_KOSTEN = CUSTOM / "mappings" / "nobel_a1_kostengruppe_bsabe.py"
_NOBEL_BSABWR = CUSTOM / "mappings" / "nobel_a1_kostengruppe_bsabwr.py"
pytestmark = pytest.mark.skipif(
    not _NOBEL_CONTRACT.is_file() or not _NOBEL_KOSTEN.is_file(),
    reason="Local Nobel mapping files missing (see custom_recipes/mappings/README.md)",
)

from mappings import nobel_a1_contract_id as contract_id  # noqa: E402
from mappings import nobel_a1_kostengruppe_bsabe as kg_bsabe  # noqa: E402

pytest.importorskip(
    "mappings.nobel_a1_kostengruppe_bsabwr",
    reason="Run scripts/generate_nobel_bsabwr_from_bsabe.py",
)
from mappings import nobel_a1_kostengruppe_bsabwr as kg_bsabwr  # noqa: E402

A1_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "A1_0001_trim.ifc"

# All exact A1 model strings (full 0001 inventory = 35).
A1_KOSTENGRUPPE_EXPECTED: dict[str, str | None] = dict(kg_bsabe.KOSTENGRUPPE_TO_BSABE)

# Subset after Remove BIP.Structural Parts (DCA Chain A) = 27.
A1_KOSTENGRUPPE_DCA: frozenset[str] = kg_bsabe.KOSTENGRUPPE_DCA_CHAIN


def test_kostengruppe_registry_has_35_entries():
    assert len(kg_bsabe.KOSTENGRUPPE_REGISTRY) == 35
    assert len(A1_KOSTENGRUPPE_EXPECTED) == 35


def test_kostengruppe_dca_chain_subset_has_27_entries():
    assert len(A1_KOSTENGRUPPE_DCA) == 27
    assert all(
        row.in_dca_chain
        for row in kg_bsabe.KOSTENGRUPPE_REGISTRY
        if row.raw in A1_KOSTENGRUPPE_DCA
    )


def test_every_registry_row_has_contract_id_hint():
    for row in kg_bsabe.KOSTENGRUPPE_REGISTRY:
        assert row.contract_id_hint
        assert contract_id.validate_contract_id(row.contract_id_hint)


def test_kostengruppe_to_contract_id_matches_registry():
    assert kg_bsabe.KOSTENGRUPPE_TO_CONTRACT_ID == {
        row.raw: row.contract_id_hint
        for row in kg_bsabe.KOSTENGRUPPE_REGISTRY
        if row.contract_id_hint
    }


@pytest.mark.parametrize("kostengruppe,expected", list(A1_KOSTENGRUPPE_EXPECTED.items()))
def test_resolve_bsabe_exact_a1_strings(kostengruppe: str, expected: str | None):
    assert kg_bsabe.resolve_bsabe(kostengruppe) == expected


def test_structural_only_kostengruppe_values_not_in_dca_chain():
    structural_only = {
        "322 Bodenplatte.TWP",
        "331 Außenwände tragend.TWP",
        "343 Innenstützen.TWP",
        "351 Decken.TWP",
        "351 Träger Dachkonstruktion",
        "351 Träger bracing",
        "351 Träger.TWP",
        "361 Dächer.TWP",
    }
    assert structural_only <= {row.raw for row in kg_bsabe.KOSTENGRUPPE_REGISTRY}
    assert structural_only.isdisjoint(A1_KOSTENGRUPPE_DCA)


def test_mapping_audit_table_covers_all_registry_rows():
    audit = kg_bsabe.mapping_audit_table()
    assert len(audit) == 35
    assert {r["kostengruppe"] for r in audit} == set(A1_KOSTENGRUPPE_EXPECTED.keys())


def test_valid_de_codes_contains_known_samples():
    for code in ("DE306", "DE114", "DE109", "DE122", "DE213"):
        assert code in contract_id.VALID_DE_CODES
    assert contract_id.validate_contract_id("DE306")
    assert not contract_id.validate_contract_id("DE999")
    assert len(contract_id.VALID_DE_CODES) == 166
    assert contract_id.VALID_DE_CODES == frozenset(contract_id.DELENTREPRENADER.keys())


def test_delentreprenad_baserow_metadata_de213():
    rec = contract_id.get_delentreprenad("DE213")
    assert rec is not None
    assert rec["namn"] == "Solceller"
    assert "INSTALLATIONS" in rec["huvudgrupp"]
    assert rec["modelleras_3d"] is True


def test_elektro_pv_maps_to_bsabe_63():
    assert kg_bsabe.resolve_bsabe("440 Elektro PV") == "63"


@pytest.mark.skipif(not _NOBEL_BSABWR.is_file(), reason="BSABwr mapping not generated")
def test_bsabwr_registry_matches_bsabe_row_count():
    assert len(kg_bsabwr.KOSTENGRUPPE_REGISTRY) == len(kg_bsabe.KOSTENGRUPPE_REGISTRY)
    assert set(kg_bsabwr.KOSTENGRUPPE_TO_BSABWR) == set(kg_bsabe.KOSTENGRUPPE_TO_BSABE)


def test_elektro_pv_maps_to_bsabwr_shd():
    assert kg_bsabwr.resolve_bsabwr("440 Elektro PV") == "SHD.1"


def test_resolve_bsabwr_roof_covering_jse():
    assert (
        kg_bsabwr.resolve_bsabwr("363 Dachbelag/ Dachdeckung/Attikaabdeckung.ARC")
        == "JSE.151"
    )


def test_resolve_bsabwr_prefix_disambiguation_slab():
    assert kg_bsabwr.resolve_bsabwr("351 Unknown.TWP", "IfcSlab") == "ESE.24"


def test_resolve_bsabe_prefix_disambiguation_by_ifc_class():
    assert (
        kg_bsabe.resolve_bsabe("361 Something else.TWP", "IfcStair")
        == "45.BE"
    )
    assert (
        kg_bsabe.resolve_bsabe("361 Something else.TWP", "IfcWall")
        == "27.G"
    )
    assert (
        kg_bsabe.resolve_bsabe("351 Unknown label.TWP", "IfcBeam")
        == "27.E"
    )


def _element_with_duplicate_owned(value: str | None):
    ifc_file = ifcopenshell.file(schema="IFC4")
    wall = ifc_file.create_entity(
        "IfcWall", GlobalId=ifcopenshell.guid.new(), Name="Wall"
    )
    if value is not None:
        set_pset_property(
            ifc_file,
            wall,
            "BIP-PROCESS",
            "DuplicateOwnedBy",
            value,
            data_type="IfcLabel",
        )
    return ifc_file, wall


@pytest.mark.parametrize(
    "duplicate_value,expected_not_duplicate",
    [
        (None, True),
        ("", True),
        ("undefined", True),
        ("Undefined", True),
        ("#123=IfcWall", False),
        ("owner-guid", False),
    ],
)
def test_is_not_duplicate_owned(duplicate_value, expected_not_duplicate):
    _, wall = _element_with_duplicate_owned(duplicate_value)
    assert is_not_duplicate_owned(wall) is expected_not_duplicate


@pytest.mark.skipif(
    not A1_FIXTURE.is_file(),
    reason="trimmed A1 fixture not present; mapping unit tests cover logic",
)
def test_a1_trim_fixture_kostengruppe_values_map():
    import ifcopenshell.util.element

    ifc_file = ifcopenshell.open(str(A1_FIXTURE))
    seen: set[str] = set()
    unmapped: list[str] = []
    for element in ifc_file.by_type("IfcElement"):
        if not is_not_duplicate_owned(element):
            continue
        psets = ifcopenshell.util.element.get_psets(element)
        raw = (psets.get("BIP") or {}).get("BSABe/Kostengruppe")
        if not raw:
            continue
        text = str(raw).strip()
        if text in seen:
            continue
        seen.add(text)
        resolved = kg_bsabe.resolve_bsabe(text, element.is_a())
        if resolved is None and text not in A1_KOSTENGRUPPE_EXPECTED:
            unmapped.append(text)
    assert not unmapped, f"unmapped Kostengruppe values in fixture: {unmapped[:10]}"
