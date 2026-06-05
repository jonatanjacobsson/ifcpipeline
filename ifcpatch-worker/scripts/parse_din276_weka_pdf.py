#!/usr/bin/env python3
"""
Parse WEKA/SIRADOS DIN 276:2018 overview PDF into reusable plain-text reference files.

Source (marketing PDF, not the full Beuth norm text):
  https://www.weka.de/bi/sirados/download/Download_DIN276.pdf

Outputs under custom_recipes/mappings/reference/:
  - din276_weka_sirados_2018_full.txt   — all 2018.12 codes from the comparison table
  - din276_weka_2008_to_2018_map.tsv    — rows where both 2008 and 2018 appear on one line

Requires: pdftotext (poppler-utils) for PDF extraction.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

WORKER_ROOT = Path(__file__).resolve().parent.parent
REF_DIR = WORKER_ROOT / "custom_recipes" / "mappings" / "reference"
PDF_URL = "https://www.weka.de/bi/sirados/download/Download_DIN276.pdf"

TABLE_HEADER = re.compile(r"^KG\s+Bezeichnung\s+KG\s+Bezeichnung\s*$")
# Real Impressum page — not the TOC line "Impressum .... 22"
TABLE_END = re.compile(r"^Impressum\s*$|^©\s*20\d{2}\s+by\s+WEKA", re.I)

# Dual-column row: 2008 code + title + 2018 code + title
DUAL_ROW = re.compile(
    r"^(\d{3})\s+(.+?)\s{2,}(\d{3})\s+(.+?)\s*$"
)
# 2018-only row (leading whitespace, no 2008 code at start)
NEW_ONLY_ROW = re.compile(r"^\s{10,}(\d{3})\s+(.+?)\s*$")
# Header / page noise (repeated on each PDF page)
NOISE = re.compile(
    r"^("
    r"KG\s+Bezeichnung|"
    r"DIN 276|"
    r"Übersicht|"
    r"Inhalt\s*\||"
    r"Impressum|"
    r"\f|"
    r"Kostengruppe\s+\d{3}\s"
    r")",
    re.I,
)
PAGE_FOOTER = re.compile(r"\|\s*\d{1,2}\s*$")


@dataclass
class CodeEntry:
    code: str
    title: str
    parent_hint: str = ""

    def level(self) -> int:
        if self.code.endswith("00") and len(self.code) == 3:
            return 1
        if self.code.endswith("0") and self.code[2] == "0":
            return 2
        return 3


@dataclass
class ParseState:
    entries_2018: dict[str, CodeEntry] = field(default_factory=dict)
    map_rows: list[tuple[str, str, str, str]] = field(default_factory=list)
    pending_2018: str | None = None
    pending_title_parts: list[str] = field(default_factory=list)


def _parent_hint(code: str) -> str:
    if len(code) != 3:
        return ""
    if code.endswith("00"):
        return ""
    if code[2] == "0":
        return code[0] + "00"
    return code[:2] + "0"


def _continuation_fragment(line: str) -> str:
    """First text column on a wrapped PDF line (ignore duplicated right column)."""
    parts = [p.strip() for p in re.split(r"\s{2,}", line.strip()) if p.strip()]
    return parts[0] if parts else ""


def _flush_pending(state: ParseState) -> None:
    if not state.pending_2018 or not state.pending_title_parts:
        state.pending_2018 = None
        state.pending_title_parts = []
        return
    title = "".join(state.pending_title_parts)
    title = re.sub(r"\s+", " ", title).strip()
    code = state.pending_2018
    if code and title:
        state.entries_2018[code] = CodeEntry(
            code=code, title=title, parent_hint=_parent_hint(code)
        )
    state.pending_2018 = None
    state.pending_title_parts = []


def _add_2018(state: ParseState, code: str, title: str) -> None:
    title = re.sub(r"\s+", " ", title).strip()
    if not code or not title:
        return
    state.entries_2018[code] = CodeEntry(
        code=code, title=title, parent_hint=_parent_hint(code)
    )


def parse_table_text(text: str) -> tuple[dict[str, CodeEntry], list[tuple[str, str, str, str]]]:
    state = ParseState()
    in_table = False

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not in_table:
            if TABLE_HEADER.match(line.strip()):
                in_table = True
            continue
        if TABLE_END.match(line.strip()):
            break
        stripped = line.strip()
        if not stripped or NOISE.match(stripped) or PAGE_FOOTER.search(line):
            continue

        dual = DUAL_ROW.match(line)
        if dual:
            _flush_pending(state)
            c08, t08, c18, t18 = dual.groups()
            t08 = t08.strip()
            t18 = t18.strip()
            state.map_rows.append((c08, t08, c18, t18))
            if t18.endswith("-"):
                state.pending_2018 = c18
                state.pending_title_parts = [t18.rstrip("-").strip()]
            else:
                _add_2018(state, c18, t18)
            continue

        new_only = NEW_ONLY_ROW.match(line)
        if new_only:
            _flush_pending(state)
            c18, t18 = new_only.groups()
            t18 = t18.strip()
            if t18.endswith("-"):
                state.pending_2018 = c18
                state.pending_title_parts = [t18.rstrip("-").strip()]
            else:
                _add_2018(state, c18, t18)
            continue

        # Wrapped 2018 title (hyphenated line break in PDF)
        if state.pending_2018 and not re.match(r"^\d{3}\s", stripped):
            frag = _continuation_fragment(line)
            if frag:
                state.pending_title_parts.append(frag)
            continue

        # 2008-only line — ignore for 2018 catalogue
        if re.match(r"^(\d{3})\s+", stripped) and not NEW_ONLY_ROW.match(line):
            _flush_pending(state)
            continue

    _flush_pending(state)
    return state.entries_2018, state.map_rows


def download_pdf(dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(
        PDF_URL,
        headers={"User-Agent": "ifcpipeline-reference-export/1.0"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        dest.write_bytes(resp.read())


def pdf_to_text(pdf_path: Path, txt_path: Path) -> None:
    subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), str(txt_path)],
        check=True,
        capture_output=True,
    )


def write_full_reference(
    entries: dict[str, CodeEntry],
    map_rows: list[tuple[str, str, str, str]],
    out_path: Path,
) -> None:
    by_level: dict[int, list[CodeEntry]] = {1: [], 2: [], 3: []}
    for e in sorted(entries.values(), key=lambda x: x.code):
        by_level[e.level()].append(e)

    lines: list[str] = [
        "DIN 276:2018-12 — Kostengruppen (WEKA/SIRADOS overview PDF)",
        "=" * 72,
        "",
        "SOURCE: https://www.weka.de/bi/sirados/download/Download_DIN276.pdf",
        f"PARSED: {date.today().isoformat()}",
        "TOOL:   ifcpatch-worker/scripts/parse_din276_weka_pdf.py",
        "",
        "NOTE: This is a marketing/overview extract (comparison table 2008→2018),",
        "      not the authoritative Beuth DIN 276 norm text. Use for mapping",
        "      discussions and IFC Kostengruppe prefix lookup (3-digit codes).",
        "",
        f"TOTAL 2018 codes in table: {len(entries)}",
        "",
        "FORMAT",
        "------",
        "  <code>\\t<level>\\t<parent>\\t<title>",
        "",
        "LEVELS: 1 = x00 (Hauptgruppe), 2 = xy0, 3 = xyz",
        "",
        "-" * 72,
        "ALL CODES (2018.12) — tab-separated",
        "-" * 72,
        "",
    ]

    for level in (1, 2, 3):
        lines.append(f"# Level {level}")
        for e in by_level[level]:
            lines.append(f"{e.code}\t{level}\t{e.parent_hint}\t{e.title}")
        lines.append("")

    lines.extend(
        [
            "-" * 72,
            "HUMAN-READABLE TREE (2018.12)",
            "-" * 72,
            "",
        ]
    )

    current_main = ""
    current_sub = ""
    for e in sorted(entries.values(), key=lambda x: x.code):
        lv = e.level()
        if lv == 1:
            current_main = e.code
            lines.append(f"{e.code}  {e.title}")
        elif lv == 2:
            current_sub = e.code
            lines.append(f"  {e.code}  {e.title}")
        else:
            lines.append(f"    {e.code}  {e.title}")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_map_tsv(
    map_rows: list[tuple[str, str, str, str]],
    out_path: Path,
) -> None:
    lines = [
        "# DIN 276 2008.12 → 2018.12 (dual-column rows from WEKA PDF only)",
        "# din276_2008\ttitle_2008\tdin276_2018\ttitle_2018",
    ]
    seen: set[tuple[str, str]] = set()
    for c08, t08, c18, t18 in map_rows:
        key = (c08, c18)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"{c08}\t{t08}\t{c18}\t{t18}")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse DIN 276 PDF into reference txt/tsv.")
    parser.add_argument(
        "--pdf",
        type=Path,
        help="Use local PDF (e.g. purchased DIN 276:2018-12); skip WEKA download.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Output full catalogue txt (default: din276_weka_sirados_2018_full.txt).",
    )
    parser.add_argument(
        "--map-out",
        type=Path,
        help="Output 2008→2018 map tsv (default: din276_weka_2008_to_2018_map.tsv).",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Reuse cached _source_Download_DIN276.pdf in reference/.",
    )
    args = parser.parse_args()

    pdf_path = args.pdf or (REF_DIR / "_source_Download_DIN276.pdf")
    raw_txt = REF_DIR / "_source_DIN276_pdftotext.txt"
    full_out = args.out or (REF_DIR / "din276_weka_sirados_2018_full.txt")
    map_out = args.map_out or (REF_DIR / "din276_weka_2008_to_2018_map.tsv")

    if args.pdf:
        if not pdf_path.is_file():
            print(f"PDF not found: {pdf_path}", file=sys.stderr)
            return 1
    elif args.skip_download and pdf_path.is_file():
        print(f"Using cached {pdf_path}")
    else:
        print(f"Downloading {PDF_URL} …")
        download_pdf(pdf_path)

    print(f"Extracting text → {raw_txt}")
    pdf_to_text(pdf_path, raw_txt)

    text = raw_txt.read_text(encoding="utf-8", errors="replace")
    entries, map_rows = parse_table_text(text)
    print(f"Parsed {len(entries)} DIN 276:2018 codes, {len(map_rows)} dual-column map rows")

    write_full_reference(entries, map_rows, full_out)
    write_map_tsv(map_rows, map_out)
    print(f"Wrote {full_out}")
    print(f"Wrote {map_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
