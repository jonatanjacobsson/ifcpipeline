#!/usr/bin/env python3
"""
Extract DIN 276 KG 300 (Baukonstruktionen) reference bundles for ifcpipeline.

1. Slice WEKA 2018 full catalogue → din276_kg300_weka_2018.txt
2. Pull OpenConstructionERP partials (config, UI template, BIM maps, golden CWICR codes)
   → din276_ocerp_partials_kg300.tsv, din276_ocerp_bim_revit_kg300.tsv,
     din276_ocerp_cwicr_kg300_prefixes.txt

Source repo (clone locally first):
  https://github.com/datadrivenconstruction/OpenConstructionERP

Usage:
  git clone --depth 1 https://github.com/datadrivenconstruction/OpenConstructionERP /tmp/OpenConstructionERP
  python3 scripts/extract_din276_ocerp_kg300.py --ocerp /tmp/OpenConstructionERP
"""

from __future__ import annotations

import argparse
import ast
import os
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

WORKER_ROOT = Path(__file__).resolve().parent.parent
REF_DIR = WORKER_ROOT / "custom_recipes" / "mappings" / "reference"
WEKA_FULL = REF_DIR / "din276_weka_sirados_2018_full.txt"

OCERP_DEFAULT = Path(os.environ.get("OCERP_ROOT", "/tmp/OpenConstructionERP"))

PATHS = {
    "dach_pack": "backend/app/modules/dach_pack/config.py",
    "de_template": "frontend/src/modules/de-din276-exchange/deTemplate.ts",
    "classification_mapper": "backend/app/modules/cad/classification_mapper.py",
    "golden_set": "backend/tests/eval/golden_set.yaml",
    "seed_demo": "backend/app/scripts/seed_demo.py",
}


def _kg300_code(code: str) -> bool:
    c = code.strip().split(".")[0]
    return len(c) == 3 and c.isdigit() and c.startswith("3")


def _norm_code(code: str) -> str:
    return code.strip().split(".")[0][:3]


def slice_weka_kg300() -> tuple[list[str], list[str]]:
    """Return (tab_rows, tree_lines) for KG 300–399 only."""
    if not WEKA_FULL.is_file():
        raise SystemExit(f"Missing {WEKA_FULL}; run parse_din276_weka_pdf.py first")
    tab_rows: list[str] = ["code\tlevel\tparent\ttitle"]
    tree_lines: list[str] = []
    in_tab = False
    in_tree = False
    for raw in WEKA_FULL.read_text(encoding="utf-8").splitlines():
        if "ALL CODES (2018.12)" in raw:
            in_tab = True
            continue
        if raw.startswith("HUMAN-READABLE TREE"):
            in_tab = False
            in_tree = True
            continue
        if in_tab:
            parts = raw.split("\t")
            if len(parts) >= 4 and len(parts[0]) == 3 and parts[0].isdigit() and _kg300_code(parts[0]):
                tab_rows.append(raw)
        elif in_tree:
            if re.match(r"^400\s{2,}", raw):
                break
            m = re.match(r"^(\s*)(\d{3})\s{2,}(.+)$", raw)
            if m and m.group(2).startswith("3"):
                tree_lines.append(raw)
    return tab_rows, tree_lines


def _parse_dach_pack_300(config_path: Path) -> list[dict[str, str]]:
    """Regex extract KG 300 subtree (PACK_CONFIG uses Decimal() — not literal_eval-safe)."""
    text = config_path.read_text(encoding="utf-8")
    m = re.search(
        r'"kg":\s*"300".*?"title":\s*"([^"]+)".*?"children":\s*\[(.*?)\]\s*,\s*\}\s*,\s*\{\s*"kg":\s*"400"',
        text,
        re.DOTALL,
    )
    if not m:
        return []
    l1_title = m.group(1)
    children_block = m.group(2)
    rows: list[dict[str, str]] = [
        {
            "source": "ocerp_dach_pack",
            "kind": "l1",
            "code": "300",
            "parent": "",
            "key": "",
            "title": l1_title,
            "notes": "backend/app/modules/dach_pack/config.py",
        }
    ]
    for cm in re.finditer(r'"kg":\s*"(\d{3})".*?"title":\s*"([^"]+)"', children_block):
        rows.append(
            {
                "source": "ocerp_dach_pack",
                "kind": "l2",
                "code": cm.group(1),
                "parent": "300",
                "key": "",
                "title": cm.group(2),
                "notes": "L2 only; no L3 in OCERP config",
            }
        )
    return rows


def _parse_de_template(ts_path: Path) -> list[dict[str, str]]:
    text = ts_path.read_text(encoding="utf-8")
    block = re.search(
        r"DE_TRADE_SECTIONS.*?=\s*\[(.*?)\];",
        text,
        re.DOTALL,
    )
    if not block:
        return []
    rows: list[dict[str, str]] = []
    for m in re.finditer(
        r"\{\s*code:\s*['\"](\d+)['\"],\s*label:\s*['\"]([^'\"]+)['\"]\s*\}",
        block.group(1),
    ):
        code, label = m.group(1), m.group(2)
        if _kg300_code(code):
            parent = "" if code.endswith("00") else code[0] + "00"
            rows.append(
                {
                    "source": "ocerp_de_template",
                    "kind": "ui_section",
                    "code": code,
                    "parent": parent,
                    "key": "",
                    "title": label,
                    "notes": "frontend de-din276-exchange/deTemplate.ts",
                }
            )
    return rows


def _dict_from_ann_or_assign(node: ast.AST) -> dict | None:
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        val = node.value
        if isinstance(val, ast.Dict):
            return {
                ast.literal_eval(k): ast.literal_eval(v)
                for k, v in zip(val.keys, val.values, strict=False)
            }
    return None


def _parse_classification_mapper(py_path: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    text = py_path.read_text(encoding="utf-8")
    mod = ast.parse(text)
    revit: dict[str, str] = {}
    material: dict[tuple[str, str], str] = {}
    for node in mod.body:
        name = None
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    name = t.id
                    break
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
        if name not in ("REVIT_TO_DIN276", "MATERIAL_AWARE_DIN276"):
            continue
        val = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
        if not isinstance(val, ast.Dict):
            continue
        data = {
            ast.literal_eval(k): ast.literal_eval(v)
            for k, v in zip(val.keys, val.values, strict=False)
        }
        if name == "REVIT_TO_DIN276":
            revit = data
        else:
            material = data
    coarse_rows: list[dict[str, str]] = []
    for category, code in sorted(revit.items()):
        if _kg300_code(code):
            coarse_rows.append(
                {
                    "source": "ocerp_revit_coarse",
                    "revit_category": category,
                    "material_key": "",
                    "din276_code": code,
                    "notes": "classification_mapper.REVIT_TO_DIN276",
                }
            )
    fine_rows: list[dict[str, str]] = []
    for (category, mat), code in sorted(material.items()):
        if code.startswith("3"):
            fine_rows.append(
                {
                    "source": "ocerp_revit_material",
                    "revit_category": category,
                    "material_key": mat,
                    "din276_code": code,
                    "notes": "classification_mapper.MATERIAL_AWARE_DIN276",
                }
            )
    return coarse_rows, fine_rows


def _parse_golden_cwicr(yaml_path: Path) -> list[str]:
    text = yaml_path.read_text(encoding="utf-8")
    codes: set[str] = set()
    for m in re.finditer(r'"(3[\d.]+)"', text):
        raw = m.group(1)
        if not raw.startswith("3"):
            continue
        codes.add(raw)
        parts = raw.split(".")
        if len(parts) >= 2:
            codes.add(f"{parts[0]}.{parts[1]}")
        if parts:
            codes.add(parts[0])
    return sorted(codes, key=lambda c: (len(c.split(".")), c))


def _parse_seed_demo(py_path: Path) -> list[dict[str, str]]:
    text = py_path.read_text(encoding="utf-8")
    rows: list[dict[str, str]] = []
    for m in re.finditer(
        r'"classification":\s*\{\s*"din276":\s*"(\d{3})"\s*\}',
        text,
    ):
        code = m.group(1)
        if _kg300_code(code):
            rows.append(
                {
                    "source": "ocerp_seed_demo",
                    "kind": "boq_example",
                    "code": code,
                    "parent": "",
                    "key": "",
                    "title": "",
                    "notes": "seed_demo.py BOQ line example",
                }
            )
    return rows


def write_weka_slice(tab_rows: list[str], tree_lines: list[str]) -> None:
    out = REF_DIR / "din276_kg300_weka_2018.txt"
    n_codes = len(tab_rows) - 1
    preamble = [
        "DIN 276:2018-12 — KG 300 Bauwerk Baukonstruktionen (WEKA/SIRADOS extract)",
        "=" * 72,
        f"Generated: {date.today().isoformat()}",
        f"Source: {WEKA_FULL.name} (filter code 300–399)",
        f"Codes: {n_codes}",
        "",
        "FORMAT: code\\tlevel\\tparent\\ttitle",
        "",
        "--- tab-separated ---",
        "",
    ]
    body = preamble + tab_rows
    if tree_lines:
        body += [
            "",
            "--- tree (2018.12) ---",
            "",
        ] + tree_lines
    out.write_text("\n".join(body) + "\n", encoding="utf-8")
    print(f"Wrote {out} ({n_codes} codes)")


def write_partials_tsv(rows: list[dict[str, str]]) -> None:
    out = REF_DIR / "din276_ocerp_partials_kg300.tsv"
    cols = ["source", "kind", "code", "parent", "key", "title", "notes"]
    lines = ["\t".join(cols)]
    seen: set[tuple[str, ...]] = set()
    for r in sorted(rows, key=lambda x: (x["source"], x.get("code", ""), x.get("key", ""))):
        key = tuple(r.get(c, "") for c in cols)
        if key in seen:
            continue
        seen.add(key)
        lines.append("\t".join(r.get(c, "") for c in cols))
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out} ({len(lines) - 1} rows)")


def write_bim_tsv(coarse: list[dict[str, str]], fine: list[dict[str, str]]) -> None:
    out = REF_DIR / "din276_ocerp_bim_revit_kg300.tsv"
    cols = ["source", "revit_category", "material_key", "din276_code", "notes"]
    lines = ["\t".join(cols)]
    for r in coarse + fine:
        lines.append("\t".join(r.get(c, "") for c in cols))
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out} ({len(lines) - 1} rows)")


def write_cwicr_prefixes(codes: list[str]) -> None:
    out = REF_DIR / "din276_ocerp_cwicr_kg300_prefixes.txt"
    body = [
        "OpenConstructionERP golden_set.yaml — CWICR position code prefixes (KG 300)",
        "=" * 72,
        f"Generated: {date.today().isoformat()}",
        "Repo: https://github.com/datadrivenconstruction/OpenConstructionERP",
        "File: backend/tests/eval/golden_set.yaml",
        "",
        "Hierarchical form KG.LL.PPP used by OCERP matcher (not official DIN 276 alone).",
        "",
    ]
    by_kg: dict[str, list[str]] = defaultdict(list)
    for c in codes:
        kg = c.split(".")[0]
        if kg.startswith("3"):
            by_kg[kg].append(c)
    for kg in sorted(by_kg):
        body.append(f"## KG {kg}")
        for c in by_kg[kg]:
            body.append(f"  {c}")
        body.append("")
    out.write_text("\n".join(body), encoding="utf-8")
    print(f"Wrote {out} ({len(codes)} unique prefixes/codes)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--ocerp",
        type=Path,
        default=OCERP_DEFAULT,
        help=f"OpenConstructionERP clone root (default: {OCERP_DEFAULT})",
    )
    args = ap.parse_args()
    root: Path = args.ocerp
    if not root.is_dir():
        raise SystemExit(
            f"OCERP root not found: {root}\n"
            "Clone: git clone --depth 1 "
            "https://github.com/datadrivenconstruction/OpenConstructionERP "
            f"{root}"
        )

    tab_rows, tree_lines = slice_weka_kg300()
    write_weka_slice(tab_rows, tree_lines)

    partial_rows: list[dict[str, str]] = []
    for name, rel in (
        ("dach_pack", PATHS["dach_pack"]),
        ("de_template", PATHS["de_template"]),
        ("seed_demo", PATHS["seed_demo"]),
    ):
        p = root / rel
        if not p.is_file():
            print(f"skip missing {rel}")
            continue
        if name == "dach_pack":
            partial_rows.extend(_parse_dach_pack_300(p))
        elif name == "de_template":
            partial_rows.extend(_parse_de_template(p))
        elif name == "seed_demo":
            partial_rows.extend(_parse_seed_demo(p))

    mapper = root / PATHS["classification_mapper"]
    if mapper.is_file():
        coarse, fine = _parse_classification_mapper(mapper)
        write_bim_tsv(coarse, fine)
    else:
        print(f"skip missing {PATHS['classification_mapper']}")

    write_partials_tsv(partial_rows)

    golden = root / PATHS["golden_set"]
    if golden.is_file():
        write_cwicr_prefixes(_parse_golden_cwicr(golden))
    else:
        print(f"skip missing {PATHS['golden_set']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
