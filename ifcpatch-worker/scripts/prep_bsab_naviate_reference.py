#!/usr/bin/env python3
"""
Parse Naviate BSAB 96 HUS keynote exports into mapping-ready reference files.

Inputs (place in custom_recipes/mappings/reference/):
  - BSAB 96 Byggdelar HUS.txt          → BIP.BSABe (byggdelstabell)
  - BSAB 96 Produktionsresultat HUS.txt → BIP.BSABwr / AMA (produktionsresultat)
  - BSAB 96 HUS.txt                     → optional combined (not required if split files exist)

Outputs (same folder):
  - bsab96_byggdelar.tsv
  - bsab96_produktionsresultat.tsv
  - bsab96_lookup.json
  - bsab96_bsabe_quick_reference.txt
  - din276_prefix_bsab_hints.tsv
  - nobel_bsabe_validation.txt (when nobel_a1_kostengruppe_bsabe.py exists)
  - din276_prefix_bsabwr_hints.tsv
  - nobel_bsabwr_validation.txt (when nobel_a1_kostengruppe_bsabwr.py exists)
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

WORKER_ROOT = Path(__file__).resolve().parent.parent
REF = WORKER_ROOT / "custom_recipes" / "mappings" / "reference"
MAPPINGS = WORKER_ROOT / "custom_recipes" / "mappings"

BYGGDELAR_FILE = REF / "BSAB 96 Byggdelar HUS.txt"
PROD_FILE = REF / "BSAB 96 Produktionsresultat HUS.txt"
HUS_FILE = REF / "BSAB 96 HUS.txt"
NOBEL_BSABE_MAPPING = MAPPINGS / "nobel_a1_kostengruppe_bsabe.py"
NOBEL_BSABWR_MAPPING = MAPPINGS / "nobel_a1_kostengruppe_bsabwr.py"

ENCODINGS = ("utf-8", "cp1252", "latin-1")

# Curated DIN 276 (3-digit) → BSABe hints for Nobel-style arch IFC (review per project).
DIN276_PREFIX_HINTS: list[tuple[str, str, str, str]] = [
    # prefix, suggested_bsabe, bsab_title_keyword, notes
    ("310", "15.S", "grund", "Baugrube/Erdbau → grundkonstruktioner"),
    ("320", "15.S", "grund", "Gründung → grundkonstruktioner"),
    ("330", "42.B", "yttervägg", "Außenwände → klimatskiljande yttervägg"),
    ("337", "42.B", "fasad", "Fassade → ytterklimatskärm / fasad"),
    ("340", "27.B", "stominnervägg", "Innenwände tragend"),
    ("342", "43.CB", "mellanvägg", "Innenwände nicht tragend → mellanväggar"),
    ("343", "27.D", "pelar", "Innenstützen"),
    ("351", "27.E", "balk", "Träger → balkstommar"),
    ("352", "27.F", "bjälklag", "Deckenöffnungen / decken"),
    ("360", "27.G", "tak", "Dach → yttertakstommar"),
    ("361", "27.G", "yttertakstommar", "Dachkonstruktion (structural) → 27.G"),
    ("363", "41.C", "ytterklimatskärm", "Dachbelag → 41.C"),
    ("364", "41.D", "innerklimatskärm", "Dachbekleidung / Abhang → 41.D"),
    ("370", "27.F", "bjälklag", "Infrastruktur — verify per object"),
    ("390", "45.C", "inredning", "Einbauten"),
    ("410", "51.B", "avlopp", "Sanitär — installationssystem"),
    ("420", "53.B", "värme", "Heizung"),
    ("430", "54.B", "ventilation", "RLT"),
    ("440", "63", "elkraft", "Elektro → Elkraftssystem (not 32.G tak)"),
    ("450", "65.B", "tele", "Schwachstrom"),
    ("460", "71.B", "hiss", "Aufzüge"),
    ("490", "63", "el", "Sonstige TGA — verify"),
]

# Curated DIN 276 (3-digit) → BSABwr (AMA produktionsresultat) hints.
DIN276_PREFIX_BSABWR_HINTS: list[tuple[str, str, str, str]] = [
    ("310", "BCS", "baugrube", "Erdbau / förarbeten"),
    ("320", "ESE", "grund", "Gründung → platsgjuten betong"),
    ("322", "ESE.182", "fundament", "Fundament / Bodenplatte"),
    ("330", "HS", "yttervägg", "Außenwände"),
    ("331", "HS", "yttervägg", "Tragende Außenwände"),
    ("332", "HS", "yttervägg", "Nichttragende Außenwände"),
    ("335", "HS", "fasad", "Fassade / Bekleidung außen"),
    ("340", "HS", "innervägg", "Innenwände"),
    ("342", "HS", "innervägg", "Innenwände nicht tragend"),
    ("343", "GS", "pelare", "Stützen"),
    ("351", "GS", "bjälklag", "Decken / Träger — verify TWP vs ERG"),
    ("352", "ESE.24", "bjälklag", "Deckenkonstruktion betong"),
    ("353", "M", "golvbeläggning", "Bodenbelag"),
    ("354", "NSF", "undertak", "Abhangdecken"),
    ("360", "GS", "tak", "Dachkonstruktion"),
    ("361", "GS", "yttertak", "Dachkonstruktion stom"),
    ("363", "JSE.151", "tätskikt", "Dachbelag / Abdichtung"),
    ("364", "NSF", "undertak", "Dachbekleidung innen"),
    ("410", "Q", "avlopp", "Sanitär — verify discipline"),
    ("440", "SHD.1", "solcell", "Elektro / PV"),
]

_BIP_BSABE_RE = re.compile(
    r"^(\d{1,2}(?:\.[A-Z][A-Z0-9]*)?(?:/\d+)?)$"
)


@dataclass(frozen=True, slots=True)
class BsabRow:
    code: str
    title: str
    parent: str
    table: str  # byggdel | produktionsresultat


def _read_lines(path: Path) -> list[str]:
    data = path.read_bytes()
    for enc in ENCODINGS:
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode("utf-8", errors="replace")
    return [ln.rstrip("\r\n") for ln in text.splitlines() if ln.strip()]


def _parse_table(lines: list[str], table: str) -> list[BsabRow]:
    rows: list[BsabRow] = []
    for line in lines:
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        code = parts[0].strip()
        title = parts[1].strip()
        parent = parts[2].strip() if len(parts) > 2 else ""
        if not code or not title:
            continue
        rows.append(BsabRow(code=code, title=title, parent=parent, table=table))
    return rows


def _build_index(rows: list[BsabRow]) -> dict[str, dict]:
    by_code = {r.code: r for r in rows}
    index: dict[str, dict] = {}

    def breadcrumb(code: str) -> str:
        parts: list[str] = []
        current = code
        seen: set[str] = set()
        while current and current not in seen:
            seen.add(current)
            row = by_code.get(current)
            if not row:
                break
            parts.append(f"{row.code} {row.title}")
            current = row.parent
        return " > ".join(reversed(parts))

    for row in rows:
        index[row.code] = {
            "code": row.code,
            "title": row.title,
            "parent": row.parent,
            "table": row.table,
            "breadcrumb": breadcrumb(row.code),
            "depth": row.code.count(".") + row.code.count("/"),
        }
    return index


def _is_bip_bsabe_code(code: str) -> bool:
    """True for numeric-byggdel codes used in BIP.BSABe (not AMA letter codes)."""
    return bool(_BIP_BSABE_RE.match(code))


def _write_tsv(path: Path, rows: list[BsabRow], index: dict[str, dict]) -> None:
    header = "code\ttitle_sv\tparent\tdepth\tbreadcrumb\ttable\n"
    lines = [header]
    for row in rows:
        meta = index[row.code]
        title = row.title.replace("\t", " ")
        crumb = meta["breadcrumb"].replace("\t", " ")
        lines.append(
            f"{row.code}\t{title}\t{row.parent}\t{meta['depth']}\t{crumb}\t{row.table}\n"
        )
    path.write_text("".join(lines), encoding="utf-8")


def _write_quick_reference(
    path: Path, byggdel_rows: list[BsabRow], index: dict[str, dict]
) -> None:
    """Human grep-friendly list of BIP-relevant BSABe codes (numeric byggdel)."""
    bip_rows = [r for r in byggdel_rows if _is_bip_bsabe_code(r.code)]
    # Group by main number (27, 32, 42, 43, 63, …)
    groups: dict[str, list[BsabRow]] = {}
    for row in bip_rows:
        main = row.code.split(".")[0].split("/")[0]
        groups.setdefault(main, []).append(row)

    lines = [
        "BSAB 96 Byggdelar (HUS) — BIP.BSABe quick reference",
        "=" * 72,
        f"Source: {BYGGDELAR_FILE.name} (Naviate Library SE)",
        f"Generated: {date.isoformat(date.today())}",
        f"Rows (BIP-style codes): {len(bip_rows)}",
        "",
        "Use for Kostengruppe → BSABe mapping. Produktionsresultat/AMA → BSABwr (separate tsv).",
        "",
    ]
    for main in sorted(groups, key=lambda x: (len(x), x)):
        lines.append(f"--- {main} ---")
        for row in groups[main]:
            lines.append(f"  {row.code:<16} {row.title}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_din_hints(path: Path, byggdel_index: dict[str, dict]) -> None:
    lines = [
        "din_prefix\tsuggested_bsabe\tbsab_title\tmatch_method\tnotes",
    ]
    for prefix, suggested, keyword, notes in DIN276_PREFIX_HINTS:
        title = ""
        method = "curated"
        if suggested in byggdel_index:
            title = byggdel_index[suggested]["title"]
        else:
            # prefix walk: 43.CB → 43.C → 43
            parts = suggested.split(".")
            for i in range(len(parts), 0, -1):
                cand = ".".join(parts[:i])
                if cand in byggdel_index:
                    title = byggdel_index[cand]["title"]
                    method = f"resolved_parent:{cand}"
                    break
        lines.append(
            f"{prefix}\t{suggested}\t{title}\t{method}\t{notes}"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def _extract_nobel_bsabe_codes(text: str) -> list[str]:
    codes: set[str] = set()
    codes.update(
        re.findall(
            r"KostengruppeMapping\(\s*\n?\s*\"[^\"]*\",\s*\n?\s*\"([^\"]+)\"",
            text,
        )
    )
    codes.update(re.findall(r'PREFIX_DEFAULTS[^}]+"(\d{1,2}(?:\.[A-Z][A-Z0-9]*)?)":', text, re.DOTALL))
    codes.update(
        re.findall(
            r'_PREFIX_IFC_CLASS_BSABE[^}]+:\s*"(\d{1,2}(?:\.[A-Z][A-Z0-9]*)?)"',
            text,
            re.DOTALL,
        )
    )
    return sorted(c for c in codes if c and c != "None")


def _write_din_bsabwr_hints(path: Path, prod_index: dict[str, dict]) -> None:
    lines = [
        "din_prefix\tsuggested_bsabwr\tama_title\tmatch_method\tnotes",
    ]
    for prefix, suggested, keyword, notes in DIN276_PREFIX_BSABWR_HINTS:
        title = ""
        method = "curated"
        if suggested in prod_index:
            title = prod_index[suggested]["title"]
        else:
            parent = suggested
            while parent:
                if parent in prod_index:
                    title = prod_index[parent]["title"]
                    method = f"resolved_parent:{parent}"
                    break
                if "." in parent:
                    parent = parent.rsplit(".", 1)[0]
                else:
                    break
        lines.append(f"{prefix}\t{suggested}\t{title}\t{method}\t{notes}")
    path.write_text("\n".join(lines), encoding="utf-8")


def _extract_nobel_bsabwr_codes(text: str) -> list[str]:
    codes: set[str] = set()
    codes.update(
        re.findall(
            r"KostengruppeBsabwrMapping\(\s*\n?\s*['\"][^'\"]*['\"],\s*\n?\s*['\"]([^'\"]+)['\"]",
            text,
        )
    )
    for _prefix, code in re.findall(
        r'PREFIX_DEFAULTS[^}]+"(\d{3})":\s*"([A-Z][A-Z0-9.-]*)"',
        text,
        re.DOTALL,
    ):
        codes.add(code)
    codes.update(
        re.findall(
            r'_PREFIX_IFC_CLASS_BSABWR[^}]+:\s*"([A-Z][A-Z0-9.-]*)"',
            text,
            re.DOTALL,
        )
    )
    return sorted(c for c in codes if c and c != "None")


def _validate_nobel_bsabe_codes(byggdel_index: dict[str, dict]) -> list[str]:
    if not NOBEL_BSABE_MAPPING.is_file():
        return ["Nobel BSABe mapping file not found — skip validation."]
    text = NOBEL_BSABE_MAPPING.read_text(encoding="utf-8")
    codes = _extract_nobel_bsabe_codes(text)

    lines = [
        "Nobel A1 BSABe validation against Naviate Byggdelar HUS",
        f"Generated: {date.isoformat(date.today())}",
        "",
    ]
    ok = 0
    for code in codes:
        if not code:
            continue
        if code in byggdel_index:
            lines.append(f"OK   {code:<12} {byggdel_index[code]['title']}")
            ok += 1
        else:
            parent = code
            resolved = None
            while parent:
                if parent in byggdel_index:
                    resolved = parent
                    break
                if "." in parent:
                    parent = parent.rsplit(".", 1)[0]
                elif "/" in parent:
                    parent = parent.split("/")[0]
                else:
                    break
            if resolved:
                lines.append(
                    f"WARN {code:<12} not exact; parent {resolved}: "
                    f"{byggdel_index[resolved]['title']}"
                )
            else:
                lines.append(f"FAIL {code:<12} not found in Naviate byggdelar")
    lines.extend(["", f"Summary: {ok}/{len(codes)} exact matches"])
    return lines


def _validate_nobel_bsabwr_codes(prod_index: dict[str, dict]) -> list[str]:
    if not NOBEL_BSABWR_MAPPING.is_file():
        return ["Nobel BSABwr mapping file not found — skip validation."]
    text = NOBEL_BSABWR_MAPPING.read_text(encoding="utf-8")
    codes = _extract_nobel_bsabwr_codes(text)

    lines = [
        "Nobel A1 BSABwr validation against Naviate Produktionsresultat HUS",
        f"Generated: {date.isoformat(date.today())}",
        "",
    ]
    ok = 0
    for code in codes:
        if not code:
            continue
        if code in prod_index:
            lines.append(f"OK   {code:<14} {prod_index[code]['title']}")
            ok += 1
            continue
        parent = code
        resolved = None
        while parent:
            if parent in prod_index:
                resolved = parent
                break
            if "." in parent:
                parent = parent.rsplit(".", 1)[0]
            else:
                break
        if resolved:
            lines.append(
                f"WARN {code:<14} not exact; parent {resolved}: "
                f"{prod_index[resolved]['title']}"
            )
        else:
            lines.append(f"FAIL {code:<14} not found in Naviate produktionsresultat")
    lines.extend(["", f"Summary: {ok}/{len(codes)} exact matches"])
    return lines


def _keyword_search(byggdel_rows: list[BsabRow], keyword: str, limit: int = 5) -> list[str]:
    kw = keyword.lower()
    hits = [r for r in byggdel_rows if kw in r.title.lower()]
    return [f"{r.code}\t{r.title}" for r in hits[:limit]]


def main() -> int:
    if not BYGGDELAR_FILE.is_file():
        print(f"Missing {BYGGDELAR_FILE}", file=sys.stderr)
        return 1
    if not PROD_FILE.is_file():
        print(f"Missing {PROD_FILE}", file=sys.stderr)
        return 1

    byggdel_rows = _parse_table(_read_lines(BYGGDELAR_FILE), "byggdel")
    prod_rows = _parse_table(_read_lines(PROD_FILE), "produktionsresultat")

    byggdel_index = _build_index(byggdel_rows)
    prod_index = _build_index(prod_rows)

    lookup = {
        "generated": date.isoformat(date.today()),
        "sources": {
            "byggdel": BYGGDELAR_FILE.name,
            "produktionsresultat": PROD_FILE.name,
        },
        "byggdel": byggdel_index,
        "produktionsresultat": prod_index,
    }

    _write_tsv(REF / "bsab96_byggdelar.tsv", byggdel_rows, byggdel_index)
    _write_tsv(REF / "bsab96_produktionsresultat.tsv", prod_rows, prod_index)
    (REF / "bsab96_lookup.json").write_text(
        json.dumps(lookup, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_quick_reference(REF / "bsab96_bsabe_quick_reference.txt", byggdel_rows, byggdel_index)
    _write_din_hints(REF / "din276_prefix_bsab_hints.tsv", byggdel_index)
    _write_din_bsabwr_hints(REF / "din276_prefix_bsabwr_hints.tsv", prod_index)

    validation = _validate_nobel_bsabe_codes(byggdel_index)
    (REF / "nobel_bsabe_validation.txt").write_text("\n".join(validation), encoding="utf-8")

    bsabwr_validation = _validate_nobel_bsabwr_codes(prod_index)
    (REF / "nobel_bsabwr_validation.txt").write_text(
        "\n".join(bsabwr_validation), encoding="utf-8"
    )

    print(f"Byggdelar: {len(byggdel_rows)} rows")
    print(f"Produktionsresultat: {len(prod_rows)} rows")
    print(f"Wrote outputs under {REF}")
    for name in (
        "bsab96_byggdelar.tsv",
        "bsab96_produktionsresultat.tsv",
        "bsab96_lookup.json",
        "bsab96_bsabe_quick_reference.txt",
        "din276_prefix_bsab_hints.tsv",
        "nobel_bsabe_validation.txt",
        "din276_prefix_bsabwr_hints.tsv",
        "nobel_bsabwr_validation.txt",
    ):
        p = REF / name
        print(f"  {name}: {p.stat().st_size:,} bytes")

    return 0


if __name__ == "__main__":
    sys.exit(main())
