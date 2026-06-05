#!/usr/bin/env python3
"""
Export plain-text reference catalogues for Kostengruppe → BSABe mapping work.

Sources (see reference/README.md):
  - BIP typbeteckningar: public JSON from BIM Alliance / Infopack
  - DIN 276:2018-12 structure summary compiled from public overviews (not the full norm text)
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import date
from pathlib import Path

WORKER_ROOT = Path(__file__).resolve().parent.parent
REF_DIR = WORKER_ROOT / "custom_recipes" / "mappings" / "reference"

TYPDATA_URL = (
    "https://storage.googleapis.com/storage.infopack.io/"
    "bim-alliance/bipkoder-data/latest/typbeteckningar.json"
)

# DIN 276:2018-12 — 1st/2nd level labels from public summaries (Beuth norm is copyrighted).
# Do not treat this as a complete or authoritative substitute for DIN 276.
DIN276_STRUCTURE: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "100",
        "Grundstück",
        [
            ("110", "Grundstückswert"),
            ("120", "Grundstücksnebenkosten"),
            ("130", "Rechte Dritter"),
        ],
    ),
    (
        "200",
        "Vorbereitende Maßnahmen",
        [
            ("210", "Herrichten"),
            ("220", "Öffentliche Erschließung"),
            ("230", "Nicht öffentliche Erschließung"),
            ("240", "Ausgleichsmaßnahmen und -abgaben"),
            ("250", "Sicherungsmaßnahmen"),
            ("260", "Unvorhergesehene Maßnahmen"),
            ("270", "Baustelleneinrichtung"),
            ("280", "Baustellenversorgung und -entsorgung"),
            ("290", "Sonstige Maßnahmen für vorbereitende Maßnahmen"),
        ],
    ),
    (
        "300",
        "Bauwerk – Baukonstruktionen",
        [
            ("310", "Baugrube, Erdbau"),
            ("320", "Gründung, Unterbau"),
            ("330", "Außenwände / vertikale Baukonstruktionen, außen"),
            ("340", "Innenwände / vertikale Baukonstruktionen, innen"),
            ("350", "Decken / horizontale Baukonstruktionen"),
            ("360", "Dächer"),
            ("370", "Infrastrukturanlagen"),
            ("380", "Baukonstruktive Einbauten"),
            ("390", "Sonstige Maßnahmen für Baukonstruktionen"),
        ],
    ),
    (
        "400",
        "Bauwerk – Technische Anlagen",
        [
            ("410", "Abwasser-, Wasser-, Gasanlagen"),
            ("420", "Wärmeversorgungsanlagen"),
            ("430", "Raumlufttechnische Anlagen"),
            ("440", "Elektrische Anlagen"),
            ("450", "Kommunikations-, sicherheits- und informationstechnische Anlagen"),
            ("460", "Förderanlagen"),
            ("470", "Nutzungsspezifische und verfahrenstechnische Anlagen"),
            ("480", "Gebäude- und Anlagenautomation"),
            ("490", "Sonstige Maßnahmen für technische Anlagen"),
        ],
    ),
    (
        "500",
        "Außenanlagen und Freiflächen",
        [
            ("510", "Gelände"),
            ("520", "Befestigte Flächen"),
            ("530", "Baukonstruktionen in Außenanlagen"),
            ("540", "Technische Anlagen in Außenanlagen"),
            ("550", "Einbauten in Außenanlagen"),
            ("560", "Vegetationsflächen"),
            ("570", "Wasserflächen"),
            ("580", "Besondere Maßnahmen in Außenanlagen"),
            ("590", "Sonstige Maßnahmen für Außenanlagen"),
        ],
    ),
    (
        "600",
        "Ausstattung und Kunstwerke",
        [
            ("610", "Ausstattung"),
            ("620", "Künstlerische Ausstattung"),
        ],
    ),
    (
        "700",
        "Baunebenkosten",
        [
            ("710", "Bauherrenaufgaben"),
            ("720", "Vorbereitung der Objektplanung"),
            ("730", "Objektplanung"),
            ("740", "Allgemeine Baunebenkosten"),
            ("750", "Sonstige Baunebenkosten"),
        ],
    ),
    (
        "800",
        "Finanzierung",
        [
            ("810", "Finanzierungsnebenkosten"),
            ("820", "Zinsen"),
        ],
    ),
]

# Selected 3rd-level examples used in arch IFC (Nobel-style labels often map here).
DIN276_EXAMPLES_3RD: list[tuple[str, str]] = [
    ("342", "Nichttragende Innenwände"),
    ("343", "Innenstützen"),
    ("337", "Fassade (often mapped under 330/332 in norm; check project convention)"),
    ("351", "Deckenkonstruktionen / Träger"),
    ("361", "Dachkonstruktionen"),
    ("440", "Elektrische Anlagen (e.g. PV, building electrical)"),
]


def fetch_typbeteckningar() -> dict:
    with urllib.request.urlopen(TYPDATA_URL, timeout=120) as resp:
        return json.load(resp)


def write_din276_reference(path: Path) -> None:
    lines = [
        "DIN 276:2018-12 — Kostengruppen (reference summary)",
        "=" * 72,
        "",
        "IMPORTANT: The authoritative DIN 276 text is copyrighted (Beuth Verlag).",
        "This file is a compact 1st/2nd-level overview for mapping discussions only.",
        "Purchase DIN 276:2018-12 for complete 3rd-level titles and rules.",
        "",
        f"Generated: {date.isoformat(date.today())}",
        "Public structure sources: industry summaries of DIN 276:2018-12 (KG 100–800).",
        "",
        "FORMAT:  <code>  <title>",
        "",
    ]
    for kg, title, subs in DIN276_STRUCTURE:
        lines.append(f"{kg}  {title}")
        for code, sub_title in subs:
            lines.append(f"  {code}  {sub_title}")
        lines.append("")
    lines.append("-" * 72)
    lines.append("Common 3-digit prefixes in German arch IFC (project-specific suffixes):")
    lines.append("")
    for code, title in DIN276_EXAMPLES_3RD:
        lines.append(f"  {code}  {title}")
    lines.append("")
    lines.extend(
        [
            "Nobel A1 uses full strings on BIP.BSABe/Kostengruppe, e.g.:",
            '  "342 Innenwände nicht tragend.ARC"',
            '  "440 Elektro PV"',
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_bip_reference(data: dict, path: Path) -> None:
    rows: list[tuple[str, str, str, str, str]] = []
    for discipline, categories in data.items():
        if not isinstance(categories, dict):
            continue
        for category, types in categories.items():
            if not isinstance(types, dict):
                continue
            for type_id, meta in types.items():
                if not isinstance(meta, dict):
                    continue
                rows.append(
                    (
                        discipline,
                        category,
                        type_id,
                        str(meta.get("underkategori", "") or ""),
                        str(meta.get("BSABe", "") or ""),
                        str(meta.get("BSABwr", "") or ""),
                    )
                )
    rows.sort(key=lambda r: (r[0], r[2]))

    with_bsabe = [r for r in rows if r[4].strip()]
    lines = [
        "BIP typbeteckningar — BSAB cross-reference (from typbeteckningar.json)",
        "=" * 72,
        "",
        f"Source URL: {TYPDATA_URL}",
        f"Generated: {date.isoformat(date.today())}",
        f"Total TypeIDs: {len(rows)}",
        f"TypeIDs with non-empty BSABe: {len(with_bsabe)}",
        "",
        "Columns (tab-separated):",
        "  Discipline | Category | TypeID | underkategori | BSABe | BSABwr",
        "",
        "NOTE: Many El (VS, etc.) typbeteckningar have empty BSABe; use BSABwr/TypeID.",
        "Official BSAB 96 byggdelstabell: https://bsab.byggtjanst.se/ (license for export).",
        "",
        "--- ALL TYPES ---",
        "",
    ]
    for r in rows:
        lines.append("\t".join(r))

    path.write_text("\n".join(lines), encoding="utf-8")

    # Smaller file: only rows with BSABe (most useful for Kostengruppe mapping)
    bsabe_path = path.with_name("bip_typbeteckningar_bsabe_only.txt")
    bsabe_lines = [
        "BIP typbeteckningar — entries with BSABe set",
        "=" * 72,
        f"Source: {TYPDATA_URL}",
        f"Generated: {date.isoformat(date.today())}",
        f"Count: {len(with_bsabe)}",
        "",
        "Discipline\tCategory\tTypeID\tunderkategori\tBSABe\tBSABwr",
        "",
    ]
    for r in with_bsabe:
        bsabe_lines.append("\t".join(r))
    bsabe_path.write_text("\n".join(bsabe_lines), encoding="utf-8")


def main() -> int:
    REF_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Writing references to {REF_DIR}")

    din_path = REF_DIR / "din276_kostengruppen_2018_reference.txt"
    write_din276_reference(din_path)
    print(f"  {din_path.name} ({din_path.stat().st_size} bytes)")

    print(f"Fetching {TYPDATA_URL} ...")
    data = fetch_typbeteckningar()
    bip_path = REF_DIR / "bip_typbeteckningar_reference.txt"
    write_bip_reference(data, bip_path)
    print(f"  {bip_path.name} ({bip_path.stat().st_size} bytes)")
    bsabe_only = REF_DIR / "bip_typbeteckningar_bsabe_only.txt"
    print(f"  {bsabe_only.name} ({bsabe_only.stat().st_size} bytes)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
