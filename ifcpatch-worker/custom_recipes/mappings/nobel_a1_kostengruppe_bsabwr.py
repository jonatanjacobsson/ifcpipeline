"""
Nobel Center A1 — DIN 276 ``BIP.BSABe/Kostengruppe`` → Swedish ``BIP.BSABwr``.

Source property: same as BSABe mapping — ``BIP.BSABe/Kostengruppe``.

BSABwr = BSAB 96 **produktionsresultat** (AMA HUS), paired with BIP TypeID via
``bip_typbeteckningar.json`` (BSABwr column). Byggdel codes live in ``BIP.BSABe``.

Auto-generated: 2026-05-26 by scripts/generate_nobel_bsabwr_from_bsabe.py
Do not edit by hand — re-run the generator after changing ``nobel_a1_kostengruppe_bsabe.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from _property_mapping_utils import parse_kostengruppe


@dataclass(frozen=True, slots=True)
class KostengruppeBsabwrMapping:
    """One audited Kostengruppe string → AMA produktionsresultat (BSABwr)."""

    raw: str
    bsabwr: str | None
    din_prefix: str
    suffix: str | None = None
    description_de: str = ""
    typbeteckning_hint: str = ""
    contract_id_hint: str = ""
    in_dca_chain: bool = True
    notes: str = ""


# fmt: off
KOSTENGRUPPE_REGISTRY: tuple[KostengruppeBsabwrMapping, ...] = (
    KostengruppeBsabwrMapping(
        '322 Bodenplatte.TWP',
        'ESE.182',
        din_prefix="322",
        suffix='TWP',
        description_de="Bodenplatte",
        typbeteckning_hint="FUxx Fundament",
        contract_id_hint="DE109",
        in_dca_chain=False,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '322 Fundament.TWP',
        'ESE.182',
        din_prefix="322",
        suffix='TWP',
        description_de="Fundament",
        typbeteckning_hint="FUxx Fundament",
        contract_id_hint="DE108",
        in_dca_chain=True,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '331 Außenwände tragend.TWP',
        'HS',
        din_prefix="331",
        suffix='TWP',
        description_de="Außenwände tragend",
        typbeteckning_hint="YVBxx Yttervägg bärande",
        contract_id_hint="DE119",
        in_dca_chain=False,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '332 Attika Rohbau / Brüstung außen.TWP',
        'HS',
        din_prefix="332",
        suffix='TWP',
        description_de="Attika Rohbau / Brüstung außen",
        typbeteckning_hint="YVxx Yttervägg icke bärande",
        contract_id_hint="DE119",
        in_dca_chain=True,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '332 Außenwände nicht tragend.ARC',
        'HS',
        din_prefix="332",
        suffix='ARC',
        description_de="Außenwände nicht tragend",
        typbeteckning_hint="YVxx",
        contract_id_hint="DE119",
        in_dca_chain=True,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '335 Attika Bekleidung.ARC',
        'HS',
        din_prefix="335",
        suffix='ARC',
        description_de="Attika Bekleidung",
        typbeteckning_hint="YVFxx Fasad",
        contract_id_hint="DE122",
        in_dca_chain=True,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '335 Außenwandbekleidung außen.ARC',
        'HS',
        din_prefix="335",
        suffix='ARC',
        description_de="Außenwandbekleidung außen",
        typbeteckning_hint="YVFxx",
        contract_id_hint="DE122",
        in_dca_chain=True,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '336 Außenwandbekleidung innen.ARC',
        'HS',
        din_prefix="336",
        suffix='ARC',
        description_de="Außenwandbekleidung innen",
        typbeteckning_hint="YVFxx",
        contract_id_hint="DE122",
        in_dca_chain=True,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '337 Fassade Streifen',
        'HS',
        din_prefix="337",
        suffix=None,
        description_de="Fassade Streifen",
        typbeteckning_hint="YVFxx",
        contract_id_hint="DE114",
        in_dca_chain=True,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '337 Fassade.ARC',
        'HS',
        din_prefix="337",
        suffix='ARC',
        description_de="Fassade",
        typbeteckning_hint="YVFxx",
        contract_id_hint="DE114",
        in_dca_chain=True,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '341 Innenwände tragend.TWP',
        'GS',
        din_prefix="341",
        suffix='TWP',
        description_de="Innenwände tragend",
        typbeteckning_hint="IVBxx Innervägg bärande",
        contract_id_hint="DE113",
        in_dca_chain=True,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '342 Innenwände nicht tragend.ARC',
        'HS',
        din_prefix="342",
        suffix='ARC',
        description_de="Innenwände nicht tragend",
        typbeteckning_hint="IVxx Innervägg",
        contract_id_hint="DE306",
        in_dca_chain=True,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '343 Innenstützen.TWP',
        'SFC.3',
        din_prefix="343",
        suffix='TWP',
        description_de="Innenstützen",
        typbeteckning_hint="Pxx Pelare",
        contract_id_hint="DE112",
        in_dca_chain=False,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '345 Innenwandbekleidung.ARC',
        'M',
        din_prefix="345",
        suffix='ARC',
        description_de="Innenwandbekleidung",
        typbeteckning_hint="BExx Beläggningar vägg",
        contract_id_hint="DE407",
        in_dca_chain=True,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '346 Elementierte Innenwandkonstruktionen/ Vorwand.ARC',
        'HS',
        din_prefix="346",
        suffix='ARC',
        description_de="Elementierte Innenwand / Vorwand",
        typbeteckning_hint="IVxx",
        contract_id_hint="DE307",
        in_dca_chain=True,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '346 Flexible walls',
        'HS',
        din_prefix="346",
        suffix=None,
        description_de="Flexible walls",
        typbeteckning_hint="IVxx",
        contract_id_hint="DE307",
        in_dca_chain=True,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '351 Decken.TWP',
        'ESE.24',
        din_prefix="351",
        suffix='TWP',
        description_de="Decken",
        typbeteckning_hint="BJLxx Bjälklag",
        contract_id_hint="DE109",
        in_dca_chain=False,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '351 Treppen / Rampe innen Unterkonstruktion.ERG',
        'NSK.1',
        din_prefix="351",
        suffix='ERG',
        description_de="Treppen/Rampe innen Unterkonstruktion",
        typbeteckning_hint="TRxx Trappa invändig",
        contract_id_hint="DE126",
        in_dca_chain=True,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '351 Treppen / Rampe innen.TWP',
        'NSK.1',
        din_prefix="351",
        suffix='TWP',
        description_de="Treppen/Rampe innen",
        typbeteckning_hint="TRxx",
        contract_id_hint="DE126",
        in_dca_chain=True,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '351 Träger Dachkonstruktion',
        'GS',
        din_prefix="351",
        suffix=None,
        description_de="Träger Dachkonstruktion",
        typbeteckning_hint="Bxx Balk",
        contract_id_hint="DE112",
        in_dca_chain=False,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '351 Träger bracing',
        'GS',
        din_prefix="351",
        suffix=None,
        description_de="Träger bracing",
        typbeteckning_hint="Bxx Balk",
        contract_id_hint="DE112",
        in_dca_chain=False,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '351 Träger.TWP',
        'GS',
        din_prefix="351",
        suffix='TWP',
        description_de="Träger",
        typbeteckning_hint="Bxx Balk",
        contract_id_hint="DE112",
        in_dca_chain=False,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '353 Belag Treppe / Rampe innen.ERG',
        'M',
        din_prefix="353",
        suffix='ERG',
        description_de="Belag Treppe/Rampe innen",
        typbeteckning_hint="GVBxx Golvbeläggning",
        contract_id_hint="DE403",
        in_dca_chain=True,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '353 Bodenbelag.ARC',
        'M',
        din_prefix="353",
        suffix='ARC',
        description_de="Bodenbelag",
        typbeteckning_hint="GVBxx",
        contract_id_hint="DE403",
        in_dca_chain=True,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '354 Deckenbekleidung/ Abhangdecken, Profile.ARC',
        'NSF',
        din_prefix="354",
        suffix='ARC',
        description_de="Deckenbekleidung / Abhangdecken Profile",
        typbeteckning_hint="UTxx Undertak",
        contract_id_hint="DE408",
        in_dca_chain=True,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '354 Deckenbekleidung/ Abhangdecken.ARC',
        'NSF',
        din_prefix="354",
        suffix='ARC',
        description_de="Deckenbekleidung / Abhangdecken",
        typbeteckning_hint="UTxx",
        contract_id_hint="DE408",
        in_dca_chain=True,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '359 Geländer innnen.ARC',
        'NSK.3112',
        din_prefix="359",
        suffix='ARC',
        description_de="Geländer innen",
        typbeteckning_hint="RTxx Trappräcke invändigt",
        contract_id_hint="DE515",
        in_dca_chain=True,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '361 Dachkonstruktion.TWP',
        'GS',
        din_prefix="361",
        suffix='TWP',
        description_de="Dachkonstruktion",
        typbeteckning_hint="TKYxx Yttertak — BSAB 27.G stom",
        contract_id_hint="DE123",
        in_dca_chain=True,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '361 Dächer.TWP',
        'GS',
        din_prefix="361",
        suffix='TWP',
        description_de="Dächer",
        typbeteckning_hint="TKYxx — BSAB 27.G stom",
        contract_id_hint="DE123",
        in_dca_chain=False,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '361 Treppen / Rampe außen.TWP',
        'NSK.1',
        din_prefix="361",
        suffix='TWP',
        description_de="Treppen/Rampe außen",
        typbeteckning_hint="YTRxx Trappa utvändig",
        contract_id_hint="DE125",
        in_dca_chain=True,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '363 Belag Treppe / Rampe außen.ERG',
        'NSK.1',
        din_prefix="363",
        suffix='ERG',
        description_de="Belag Treppe/Rampe außen",
        typbeteckning_hint="YTRxx",
        contract_id_hint="DE123",
        in_dca_chain=True,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '363 Dachbelag/ Dachdeckung/Attikaabdeckung.ARC',
        'JSE.151',
        din_prefix="363",
        suffix='ARC',
        description_de="Dachbelag / Dachdeckung",
        typbeteckning_hint="TKYxx — BSAB 41.C ytterklimatskärm tak",
        contract_id_hint="DE123",
        in_dca_chain=True,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '364 Dachbekleidung/ Abhangdecken.ARC',
        'NSF',
        din_prefix="364",
        suffix='ARC',
        description_de="Dachbekleidung / Abhangdecken",
        typbeteckning_hint="TKYxx — BSAB 41.D innerklimatskärm tak",
        contract_id_hint="DE123",
        in_dca_chain=True,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '369 Geländer außen.ARC',
        'NSK.3111',
        din_prefix="369",
        suffix='ARC',
        description_de="Geländer außen",
        typbeteckning_hint="YRTxx Trappräcke utvändigt",
        contract_id_hint="DE515",
        in_dca_chain=True,
        notes="",
    ),
    KostengruppeBsabwrMapping(
        '440 Elektro PV',
        'SHD.1',
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

KOSTENGRUPPE_TO_BSABWR: dict[str, str | None] = {
    row.raw: row.bsabwr for row in KOSTENGRUPPE_REGISTRY
}

KOSTENGRUPPE_DCA_CHAIN: frozenset[str] = frozenset(
    row.raw for row in KOSTENGRUPPE_REGISTRY if row.in_dca_chain
)

PREFIX_DEFAULTS: dict[str, str] = {
    "322": "ESE",
    "331": "HS",
    "332": "HS",
    "335": "HS",
    "336": "HS",
    "337": "HS",
    "341": "GS",
    "342": "HS",
    "343": "GS",
    "345": "M",
    "346": "HS",
    "351": "GS",
    "353": "M",
    "354": "NSF",
    "359": "NSK",
    "361": "GS",
    "363": "JSE",
    "364": "NSF",
    "369": "NSK",
    "440": "SHD.1",
}

_PREFIX_IFC_CLASS_BSABWR: dict[tuple[str, str], str] = {
    ("351", "IfcBeam"): "GS",
    ("351", "IfcColumn"): "GS",
    ("351", "IfcCovering"): "ESE.24",
    ("351", "IfcSlab"): "ESE.24",
    ("361", "IfcRamp"): "NSK.1",
    ("361", "IfcStair"): "NSK.1",
    ("361", "IfcStairFlight"): "NSK.1",
    ("361", "IfcWall"): "GS",
    ("363", "IfcRamp"): "NSK.1",
    ("363", "IfcStair"): "NSK.1",
    ("363", "IfcStairFlight"): "NSK.1",
}


def iter_kostengruppe_mappings(
    *, dca_chain_only: bool = False,
) -> Iterator[KostengruppeBsabwrMapping]:
    for row in KOSTENGRUPPE_REGISTRY:
        if dca_chain_only and not row.in_dca_chain:
            continue
        yield row


def resolve_bsabwr(raw: str, ifc_class: str | None = None) -> str | None:
    """Resolve ``BIP.BSABwr`` from ``BIP.BSABe/Kostengruppe`` (same rules as BSABe mapping)."""
    text = (raw or "").strip()
    if not text:
        return None
    if text in KOSTENGRUPPE_TO_BSABWR:
        return KOSTENGRUPPE_TO_BSABWR[text]
    parsed = parse_kostengruppe(text)
    prefix = parsed.get("prefix")
    if prefix and ifc_class:
        hinted = _PREFIX_IFC_CLASS_BSABWR.get((prefix, ifc_class))
        if hinted:
            return hinted
    if prefix and prefix in PREFIX_DEFAULTS:
        return PREFIX_DEFAULTS[prefix]
    return None


def mapping_audit_table(*, dca_chain_only: bool = False) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for m in iter_kostengruppe_mappings(dca_chain_only=dca_chain_only):
        rows.append(
            {
                "kostengruppe": m.raw,
                "din_prefix": m.din_prefix,
                "suffix": m.suffix or "",
                "bsabwr": m.bsabwr or "",
                "contract_id": m.contract_id_hint,
                "typbeteckning_hint": m.typbeteckning_hint,
                "in_dca_chain": "yes" if m.in_dca_chain else "no",
                "notes": m.notes,
            }
        )
    return rows
