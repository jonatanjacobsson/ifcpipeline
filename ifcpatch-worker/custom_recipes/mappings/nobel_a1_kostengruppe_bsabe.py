"""
Nobel Center A1 — DIN 276 ``BIP.BSABe/Kostengruppe`` → Swedish ``BIP.BSABe``.

Source property: ``BIP.BSABe/Kostengruppe`` (ArchiCAD / German DIN 276 cost group label).

Inventory (2026-05, MinIO ``uploads/A1_2b_BIM_XXX_*``):
- **35** distinct values on ``0001_00`` (full architectural + structural)
- **27** values on ``0001_00`` after structural strip / on ``0002_00`` (DCA Chain A target)
- **0** on ``0003_00`` (rooms)

BSABe codes follow
https://storage.googleapis.com/storage.infopack.io/bim-alliance/bipkoder-data/latest/typbeteckningar.json
(e.g. FU→15.ST, YVB→27.C, IVB→27.B, YVF→42.B, TR→45.CB, TKY→27.G/41.C).

ContractID hints align with ``mappings.nobel_a1_contract_id`` (Baserow delentreprenader 1182).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from _property_mapping_utils import parse_kostengruppe


@dataclass(frozen=True, slots=True)
class KostengruppeMapping:
    """One audited Kostengruppe string from the A1 IFC models."""

    raw: str
    bsabe: str | None
    din_prefix: str
    suffix: str | None = None
    description_de: str = ""
    typbeteckning_hint: str = ""
    contract_id_hint: str = ""
    in_dca_chain: bool = True
    notes: str = ""


# fmt: off
# Each row: exact IFC string → BSABe + metadata for audit / ContractID cross-reference.
# ``in_dca_chain``: True when value appears on models after Remove BIP.Structural Parts (27-set).
KOSTENGRUPPE_REGISTRY: tuple[KostengruppeMapping, ...] = (
    KostengruppeMapping(
        "322 Bodenplatte.TWP",
        "15.ST",
        din_prefix="322",
        suffix="TWP",
        description_de="Bodenplatte",
        typbeteckning_hint="FUxx Fundament",
        contract_id_hint="DE109",
        in_dca_chain=False,
    ),
    KostengruppeMapping(
        "322 Fundament.TWP",
        "15.ST",
        din_prefix="322",
        suffix="TWP",
        description_de="Fundament",
        typbeteckning_hint="FUxx Fundament",
        contract_id_hint="DE108",
        in_dca_chain=True,
    ),
    KostengruppeMapping(
        "331 Außenwände tragend.TWP",
        "27.C",
        din_prefix="331",
        suffix="TWP",
        description_de="Außenwände tragend",
        typbeteckning_hint="YVBxx Yttervägg bärande",
        contract_id_hint="DE119",
        in_dca_chain=False,
    ),
    KostengruppeMapping(
        "332 Attika Rohbau / Brüstung außen.TWP",
        "42.A",
        din_prefix="332",
        suffix="TWP",
        description_de="Attika Rohbau / Brüstung außen",
        typbeteckning_hint="YVxx Yttervägg icke bärande",
        contract_id_hint="DE119",
        in_dca_chain=True,
    ),
    KostengruppeMapping(
        "332 Außenwände nicht tragend.ARC",
        "42.A",
        din_prefix="332",
        suffix="ARC",
        description_de="Außenwände nicht tragend",
        typbeteckning_hint="YVxx",
        contract_id_hint="DE119",
        in_dca_chain=True,
    ),
    KostengruppeMapping(
        "335 Attika Bekleidung.ARC",
        "42.B",
        din_prefix="335",
        suffix="ARC",
        description_de="Attika Bekleidung",
        typbeteckning_hint="YVFxx Fasad",
        contract_id_hint="DE122",
        in_dca_chain=True,
    ),
    KostengruppeMapping(
        "335 Außenwandbekleidung außen.ARC",
        "42.B",
        din_prefix="335",
        suffix="ARC",
        description_de="Außenwandbekleidung außen",
        typbeteckning_hint="YVFxx",
        contract_id_hint="DE122",
        in_dca_chain=True,
    ),
    KostengruppeMapping(
        "336 Außenwandbekleidung innen.ARC",
        "42.B",
        din_prefix="336",
        suffix="ARC",
        description_de="Außenwandbekleidung innen",
        typbeteckning_hint="YVFxx",
        contract_id_hint="DE122",
        in_dca_chain=True,
    ),
    KostengruppeMapping(
        "337 Fassade Streifen",
        "42.B",
        din_prefix="337",
        suffix=None,
        description_de="Fassade Streifen",
        typbeteckning_hint="YVFxx",
        contract_id_hint="DE114",
        in_dca_chain=True,
    ),
    KostengruppeMapping(
        "337 Fassade.ARC",
        "42.B",
        din_prefix="337",
        suffix="ARC",
        description_de="Fassade",
        typbeteckning_hint="YVFxx",
        contract_id_hint="DE114",
        in_dca_chain=True,
    ),
    KostengruppeMapping(
        "341 Innenwände tragend.TWP",
        "27.B",
        din_prefix="341",
        suffix="TWP",
        description_de="Innenwände tragend",
        typbeteckning_hint="IVBxx Innervägg bärande",
        contract_id_hint="DE113",
        in_dca_chain=True,
    ),
    KostengruppeMapping(
        "342 Innenwände nicht tragend.ARC",
        "43.CB",
        din_prefix="342",
        suffix="ARC",
        description_de="Innenwände nicht tragend",
        typbeteckning_hint="IVxx Innervägg",
        contract_id_hint="DE306",
        in_dca_chain=True,
    ),
    KostengruppeMapping(
        "343 Innenstützen.TWP",
        "27.D",
        din_prefix="343",
        suffix="TWP",
        description_de="Innenstützen",
        typbeteckning_hint="Pxx Pelare",
        contract_id_hint="DE112",
        in_dca_chain=False,
    ),
    KostengruppeMapping(
        "345 Innenwandbekleidung.ARC",
        "44.C",
        din_prefix="345",
        suffix="ARC",
        description_de="Innenwandbekleidung",
        typbeteckning_hint="BExx Beläggningar vägg",
        contract_id_hint="DE407",
        in_dca_chain=True,
    ),
    KostengruppeMapping(
        "346 Elementierte Innenwandkonstruktionen/ Vorwand.ARC",
        "43.CB",
        din_prefix="346",
        suffix="ARC",
        description_de="Elementierte Innenwand / Vorwand",
        typbeteckning_hint="IVxx",
        contract_id_hint="DE307",
        in_dca_chain=True,
    ),
    KostengruppeMapping(
        "346 Flexible walls",
        "43.CB",
        din_prefix="346",
        suffix=None,
        description_de="Flexible walls",
        typbeteckning_hint="IVxx",
        contract_id_hint="DE307",
        in_dca_chain=True,
    ),
    KostengruppeMapping(
        "351 Decken.TWP",
        "27.F",
        din_prefix="351",
        suffix="TWP",
        description_de="Decken",
        typbeteckning_hint="BJLxx Bjälklag",
        contract_id_hint="DE109",
        in_dca_chain=False,
    ),
    KostengruppeMapping(
        "351 Treppen / Rampe innen Unterkonstruktion.ERG",
        "45.CB",
        din_prefix="351",
        suffix="ERG",
        description_de="Treppen/Rampe innen Unterkonstruktion",
        typbeteckning_hint="TRxx Trappa invändig",
        contract_id_hint="DE126",
        in_dca_chain=True,
    ),
    KostengruppeMapping(
        "351 Treppen / Rampe innen.TWP",
        "45.CB",
        din_prefix="351",
        suffix="TWP",
        description_de="Treppen/Rampe innen",
        typbeteckning_hint="TRxx",
        contract_id_hint="DE126",
        in_dca_chain=True,
    ),
    KostengruppeMapping(
        "351 Träger Dachkonstruktion",
        "27.E",
        din_prefix="351",
        suffix=None,
        description_de="Träger Dachkonstruktion",
        typbeteckning_hint="Bxx Balk",
        contract_id_hint="DE112",
        in_dca_chain=False,
    ),
    KostengruppeMapping(
        "351 Träger bracing",
        "27.E",
        din_prefix="351",
        suffix=None,
        description_de="Träger bracing",
        typbeteckning_hint="Bxx Balk",
        contract_id_hint="DE112",
        in_dca_chain=False,
    ),
    KostengruppeMapping(
        "351 Träger.TWP",
        "27.E",
        din_prefix="351",
        suffix="TWP",
        description_de="Träger",
        typbeteckning_hint="Bxx Balk",
        contract_id_hint="DE112",
        in_dca_chain=False,
    ),
    KostengruppeMapping(
        "353 Belag Treppe / Rampe innen.ERG",
        "44.BB",
        din_prefix="353",
        suffix="ERG",
        description_de="Belag Treppe/Rampe innen",
        typbeteckning_hint="GVBxx Golvbeläggning",
        contract_id_hint="DE403",
        in_dca_chain=True,
    ),
    KostengruppeMapping(
        "353 Bodenbelag.ARC",
        "44.BB",
        din_prefix="353",
        suffix="ARC",
        description_de="Bodenbelag",
        typbeteckning_hint="GVBxx",
        contract_id_hint="DE403",
        in_dca_chain=True,
    ),
    KostengruppeMapping(
        "354 Deckenbekleidung/ Abhangdecken, Profile.ARC",
        "43.E",
        din_prefix="354",
        suffix="ARC",
        description_de="Deckenbekleidung / Abhangdecken Profile",
        typbeteckning_hint="UTxx Undertak",
        contract_id_hint="DE408",
        in_dca_chain=True,
    ),
    KostengruppeMapping(
        "354 Deckenbekleidung/ Abhangdecken.ARC",
        "43.E",
        din_prefix="354",
        suffix="ARC",
        description_de="Deckenbekleidung / Abhangdecken",
        typbeteckning_hint="UTxx",
        contract_id_hint="DE408",
        in_dca_chain=True,
    ),
    KostengruppeMapping(
        "359 Geländer innnen.ARC",
        "45.C",
        din_prefix="359",
        suffix="ARC",
        description_de="Geländer innen",
        typbeteckning_hint="RTxx Trappräcke invändigt",
        contract_id_hint="DE515",
        in_dca_chain=True,
    ),
    KostengruppeMapping(
        "361 Dachkonstruktion.TWP",
        "27.G",
        din_prefix="361",
        suffix="TWP",
        description_de="Dachkonstruktion",
        typbeteckning_hint="TKYxx Yttertak — BSAB 27.G stom",
        contract_id_hint="DE123",
        in_dca_chain=True,
    ),
    KostengruppeMapping(
        "361 Dächer.TWP",
        "27.G",
        din_prefix="361",
        suffix="TWP",
        description_de="Dächer",
        typbeteckning_hint="TKYxx — BSAB 27.G stom",
        contract_id_hint="DE123",
        in_dca_chain=False,
    ),
    KostengruppeMapping(
        "361 Treppen / Rampe außen.TWP",
        "45.BE",
        din_prefix="361",
        suffix="TWP",
        description_de="Treppen/Rampe außen",
        typbeteckning_hint="YTRxx Trappa utvändig",
        contract_id_hint="DE125",
        in_dca_chain=True,
    ),
    KostengruppeMapping(
        "363 Belag Treppe / Rampe außen.ERG",
        "45.BE",
        din_prefix="363",
        suffix="ERG",
        description_de="Belag Treppe/Rampe außen",
        typbeteckning_hint="YTRxx",
        contract_id_hint="DE123",
        in_dca_chain=True,
    ),
    KostengruppeMapping(
        "363 Dachbelag/ Dachdeckung/Attikaabdeckung.ARC",
        "41.C",
        din_prefix="363",
        suffix="ARC",
        description_de="Dachbelag / Dachdeckung",
        typbeteckning_hint="TKYxx — BSAB 41.C ytterklimatskärm tak",
        contract_id_hint="DE123",
        in_dca_chain=True,
    ),
    KostengruppeMapping(
        "364 Dachbekleidung/ Abhangdecken.ARC",
        "41.D",
        din_prefix="364",
        suffix="ARC",
        description_de="Dachbekleidung / Abhangdecken",
        typbeteckning_hint="TKYxx — BSAB 41.D innerklimatskärm tak",
        contract_id_hint="DE123",
        in_dca_chain=True,
    ),
    KostengruppeMapping(
        "369 Geländer außen.ARC",
        "45.B",
        din_prefix="369",
        suffix="ARC",
        description_de="Geländer außen",
        typbeteckning_hint="YRTxx Trappräcke utvändigt",
        contract_id_hint="DE515",
        in_dca_chain=True,
    ),
    KostengruppeMapping(
        "440 Elektro PV",
        "63",
        din_prefix="440",
        suffix=None,
        description_de="Elektro PV / Starkstrom (DIN 276 KG 440)",
        typbeteckning_hint="El/GC1xx Solcellspanel; BSAB 96 elkraft (huvudgrupp 6)",
        contract_id_hint="DE213",
        in_dca_chain=True,
        notes="DIN 276:440 = elektrische Anlagen → BSABe 63 (Elkraftssystem).",
    ),
)
# fmt: on

KOSTENGRUPPE_TO_BSABE: dict[str, str | None] = {
    row.raw: row.bsabe for row in KOSTENGRUPPE_REGISTRY
}

# DCA Chain A subset (27 values) — used by workflow after structural removal.
KOSTENGRUPPE_DCA_CHAIN: frozenset[str] = frozenset(
    row.raw for row in KOSTENGRUPPE_REGISTRY if row.in_dca_chain
)

# Exact string → suggested ContractID (mirrors nobel_a1_contract_id rules).
KOSTENGRUPPE_TO_CONTRACT_ID: dict[str, str] = {
    row.raw: row.contract_id_hint
    for row in KOSTENGRUPPE_REGISTRY
    if row.contract_id_hint
}

# Fallback when an unknown variant shares a DIN prefix with the A1 model.
PREFIX_DEFAULTS: dict[str, str] = {
    "322": "15.ST",
    "331": "27.C",
    "332": "42.A",
    "335": "42.B",
    "336": "42.B",
    "337": "42.B",
    "341": "27.B",
    "342": "43.CB",
    "343": "27.D",
    "345": "44.C",
    "346": "43.CB",
    "351": "45.CB",
    "353": "44.BB",
    "354": "43.E",
    "359": "45.C",
    "361": "27.G",
    "363": "41.C",
    "364": "41.D",
    "369": "45.B",
}

# Disambiguate prefixes that map to more than one BSABe in the A1 model.
_PREFIX_IFC_CLASS_BSABE: dict[tuple[str, str], str] = {
    ("351", "IfcBeam"): "27.E",
    ("351", "IfcColumn"): "27.D",
    ("351", "IfcSlab"): "27.F",
    ("351", "IfcCovering"): "27.F",
    ("361", "IfcStair"): "45.BE",
    ("361", "IfcRamp"): "45.BE",
    ("361", "IfcStairFlight"): "45.BE",
    ("363", "IfcStair"): "45.BE",
    ("363", "IfcRamp"): "45.BE",
    ("363", "IfcStairFlight"): "45.BE",
}


def iter_kostengruppe_mappings(
    *, dca_chain_only: bool = False,
) -> Iterator[KostengruppeMapping]:
    """Yield audited mapping rows (optionally only the 27-value DCA Chain A set)."""
    for row in KOSTENGRUPPE_REGISTRY:
        if dca_chain_only and not row.in_dca_chain:
            continue
        yield row


def resolve_bsabe(raw: str, ifc_class: str | None = None) -> str | None:
    """
    Resolve a BSABe typbeteckning from a raw ``BSABe/Kostengruppe`` value.

    1. Exact match in ``KOSTENGRUPPE_TO_BSABE`` (may be ``None`` for unmapped codes).
    2. ``(prefix, ifc_class)`` hint for ambiguous DIN prefixes.
    3. ``PREFIX_DEFAULTS`` by 3-digit DIN prefix.
    """
    text = (raw or "").strip()
    if not text:
        return None

    if text in KOSTENGRUPPE_TO_BSABE:
        return KOSTENGRUPPE_TO_BSABE[text]

    parsed = parse_kostengruppe(text)
    prefix = parsed.get("prefix")
    if prefix and ifc_class:
        hinted = _PREFIX_IFC_CLASS_BSABE.get((prefix, ifc_class))
        if hinted:
            return hinted

    if prefix and prefix in PREFIX_DEFAULTS:
        return PREFIX_DEFAULTS[prefix]

    return None


def mapping_audit_table(*, dca_chain_only: bool = False) -> list[dict[str, str]]:
    """Flat dict rows for CSV export / documentation (all accounted mappings)."""
    rows: list[dict[str, str]] = []
    for m in iter_kostengruppe_mappings(dca_chain_only=dca_chain_only):
        rows.append(
            {
                "kostengruppe": m.raw,
                "din_prefix": m.din_prefix,
                "suffix": m.suffix or "",
                "bsabe": m.bsabe or "",
                "contract_id": m.contract_id_hint,
                "typbeteckning_hint": m.typbeteckning_hint,
                "in_dca_chain": "yes" if m.in_dca_chain else "no",
                "notes": m.notes,
            }
        )
    return rows
