#!/usr/bin/env python3
"""
Generate ``mappings/nobel_a1_kostengruppe_bsabwr.py`` from the BSABe mapping + BIP typbeteckningar.

BSABwr = AMA produktionsresultat (Naviate), from ``bip_typbeteckningar_reference.txt``.

Usage:
  python3 scripts/generate_nobel_bsabwr_from_bsabe.py
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

WORKER_ROOT = Path(__file__).resolve().parent.parent
MAPPINGS = WORKER_ROOT / "custom_recipes" / "mappings"
BSABE_MOD = MAPPINGS / "nobel_a1_kostengruppe_bsabe.py"
OUT_MOD = MAPPINGS / "nobel_a1_kostengruppe_bsabwr.py"
TYPD_REF = (
    WORKER_ROOT
    / "custom_recipes/mappings/reference/bip_typbeteckningar_reference.txt"
)

# Per-row overrides where typ hint → BSABwr is wrong for the German label.
RAW_BSABWR_OVERRIDES: dict[str, str] = {
    "322 Bodenplatte.TWP": "ESE.182",
    "322 Fundament.TWP": "ESE.182",
    "351 Decken.TWP": "ESE.24",
    "363 Dachbelag/ Dachdeckung/Attikaabdeckung.ARC": "JSE.151",
    "363 Belag Treppe / Rampe außen.ERG": "NSK.1",
    "364 Dachbekleidung/ Abhangdecken.ARC": "NSF",
    "364 Dachbekleidung/ Abhangdecken, Profile.ARC": "NSF",
}

PREFIX_DEFAULTS_BSABWR: dict[str, str] = {
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
    ("351", "IfcSlab"): "ESE.24",
    ("351", "IfcCovering"): "ESE.24",
    ("361", "IfcStair"): "NSK.1",
    ("361", "IfcRamp"): "NSK.1",
    ("361", "IfcStairFlight"): "NSK.1",
    ("361", "IfcWall"): "GS",
    ("363", "IfcStair"): "NSK.1",
    ("363", "IfcRamp"): "NSK.1",
    ("363", "IfcStairFlight"): "NSK.1",
}


def _load_typeid_to_bsabwr() -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not TYPD_REF.is_file():
        return mapping
    for line in TYPD_REF.read_text(encoding="utf-8").splitlines():
        if line.startswith("Discipline") or not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 6:
            continue
        typeid = parts[2].strip()
        bsabwr = parts[5].strip()
        if typeid and bsabwr and "xx" in typeid:
            mapping[typeid] = bsabwr
    return mapping


def _typ_prefix_from_hint(hint: str) -> str | None:
    if not hint:
        return None
    head = hint.split(";")[0].strip()
    if "GC1" in head:
        return "GC1xx"
    m = re.search(r"\b([A-Z][A-Za-z0-9]*xx)\b", head)
    return m.group(1) if m else None


def _import_bsabe_registry():
    sys.path.insert(0, str(WORKER_ROOT / "custom_recipes"))
    from mappings import nobel_a1_kostengruppe_bsabe as mod  # noqa: WPS433

    return mod


def main() -> int:
    if not BSABE_MOD.is_file():
        print(f"Missing {BSABE_MOD}", file=sys.stderr)
        return 1

    typeid_map = _load_typeid_to_bsabwr()
    bsabe = _import_bsabe_registry()
    rows: list[tuple[str, str | None, dict]] = []

    for entry in bsabe.KOSTENGRUPPE_REGISTRY:
        raw = entry.raw
        if raw in RAW_BSABWR_OVERRIDES:
            code = RAW_BSABWR_OVERRIDES[raw]
        else:
            prefix = _typ_prefix_from_hint(entry.typbeteckning_hint)
            code = typeid_map.get(prefix or "", None) if prefix else None
        rows.append(
            (
                raw,
                code,
                {
                    "din_prefix": entry.din_prefix,
                    "suffix": repr(entry.suffix),
                    "description_de": entry.description_de.replace('"', '\\"'),
                    "typbeteckning_hint": entry.typbeteckning_hint.replace('"', '\\"'),
                    "contract_id_hint": entry.contract_id_hint,
                    "in_dca_chain": entry.in_dca_chain,
                    "notes": entry.notes.replace('"', '\\"'),
                },
            )
        )

    registry_lines: list[str] = []
    for raw, code, meta in rows:
        code_repr = repr(code)
        registry_lines.append(
            f'    KostengruppeBsabwrMapping(\n'
            f'        {raw!r},\n'
            f'        {code_repr},\n'
            f'        din_prefix="{meta["din_prefix"]}",\n'
            f'        suffix={meta["suffix"]},\n'
            f'        description_de="{meta["description_de"]}",\n'
            f'        typbeteckning_hint="{meta["typbeteckning_hint"]}",\n'
            f'        contract_id_hint="{meta["contract_id_hint"]}",\n'
            f'        in_dca_chain={meta["in_dca_chain"]},\n'
            f'        notes="{meta["notes"]}",\n'
            f'    ),'
        )

    prefix_lines = [f'    "{k}": "{v}",' for k, v in sorted(PREFIX_DEFAULTS_BSABWR.items())]
    ifc_lines = [
        f'    ("{k[0]}", "{k[1]}"): "{v}",'
        for k, v in sorted(_PREFIX_IFC_CLASS_BSABWR.items())
    ]

    body = f'''"""
Nobel Center A1 — DIN 276 ``BIP.BSABe/Kostengruppe`` → Swedish ``BIP.BSABwr``.

Source property: same as BSABe mapping — ``BIP.BSABe/Kostengruppe``.

BSABwr = BSAB 96 **produktionsresultat** (AMA HUS), paired with BIP TypeID via
``bip_typbeteckningar.json`` (BSABwr column). Byggdel codes live in ``BIP.BSABe``.

Auto-generated: {date.today().isoformat()} by scripts/generate_nobel_bsabwr_from_bsabe.py
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
{chr(10).join(registry_lines)}
)
# fmt: on

KOSTENGRUPPE_TO_BSABWR: dict[str, str | None] = {{
    row.raw: row.bsabwr for row in KOSTENGRUPPE_REGISTRY
}}

KOSTENGRUPPE_DCA_CHAIN: frozenset[str] = frozenset(
    row.raw for row in KOSTENGRUPPE_REGISTRY if row.in_dca_chain
)

PREFIX_DEFAULTS: dict[str, str] = {{
{chr(10).join(prefix_lines)}
}}

_PREFIX_IFC_CLASS_BSABWR: dict[tuple[str, str], str] = {{
{chr(10).join(ifc_lines)}
}}


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
            {{
                "kostengruppe": m.raw,
                "din_prefix": m.din_prefix,
                "suffix": m.suffix or "",
                "bsabwr": m.bsabwr or "",
                "contract_id": m.contract_id_hint,
                "typbeteckning_hint": m.typbeteckning_hint,
                "in_dca_chain": "yes" if m.in_dca_chain else "no",
                "notes": m.notes,
            }}
        )
    return rows
'''

    OUT_MOD.write_text(body, encoding="utf-8")
    mapped = sum(1 for _, c, _ in rows if c)
    print(f"Wrote {OUT_MOD} ({len(rows)} rows, {mapped} with BSABwr)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
