#!/usr/bin/env python3
"""
Generate gitignored ``mappings/nobel_a1_contract_id.py`` from Baserow table 1182.

Requires env (from ``../cde/.env`` or shell):
  CDE_BASEROW_API_BASE, CDE_BASEROW_API_KEY

Usage:
  python3 scripts/generate_nobel_contract_id_from_baserow.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

WORKER_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = WORKER_ROOT / "custom_recipes" / "mappings" / "nobel_a1_contract_id.py"
TABLE_ID = 1182


def _env(name: str, fallback: str = "") -> str:
    return (os.environ.get(name) or fallback).strip()


def _fetch_rows(api_base: str, token: str) -> list[dict[str, Any]]:
    base = api_base.rstrip("/")
    url = f"{base}/database/rows/table/{TABLE_ID}/?user_field_names=true&size=200"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Token {token}",
            "User-Agent": "ifcpipeline-baserow-client/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)
    return list(data.get("results") or [])


def _select_value(field: Any) -> str:
    if isinstance(field, dict):
        return str(field.get("value") or "").strip()
    if field is None:
        return ""
    return str(field).strip()


def _row_to_record(row: dict[str, Any]) -> dict[str, Any]:
    code = (
        _select_value(row.get("DE-nummer"))
        or (row.get("Delentreprenad") or "").split(" - ")[0].strip()
    )
    return {
        "code": code,
        "delentreprenad": str(row.get("Delentreprenad") or "").strip(),
        "namn": str(row.get("Namn") or "").strip(),
        "nummer": str(row.get("Nummer") or "").strip(),
        "huvudgrupp": _select_value(row.get("Huvudgrupp")),
        "huvudgrupp_id": (
            row.get("Huvudgrupp", {}).get("id")
            if isinstance(row.get("Huvudgrupp"), dict)
            else None
        ),
        "grupp": str(row.get("Grupp") or "").strip(),
        "modelleras_3d": bool(row.get("3D Modelleras")),
        "baserow_row_id": row.get("id"),
    }


def _py_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def main() -> int:
    api_base = _env("CDE_BASEROW_API_BASE") or _env("BASEROW_API_BASE")
    token = _env("CDE_BASEROW_API_KEY") or _env("BASEROW_API_TOKEN")
    if not api_base or not token:
        print("Set CDE_BASEROW_API_BASE and CDE_BASEROW_API_KEY", file=sys.stderr)
        return 1

    rows = _fetch_rows(api_base, token)
    records = [_row_to_record(r) for r in rows]
    records = [r for r in records if r["code"]]
    records.sort(key=lambda r: r["code"])

    # Preserve CONTRACT_ID_RULES from existing file if present.
    rules_block = ""
    existing = OUTPUT.read_text(encoding="utf-8") if OUTPUT.is_file() else ""
    match = re.search(
        r"(# First match wins.*\nCONTRACT_ID_RULES:.*?\n\])\n",
        existing,
        re.DOTALL,
    )
    if match:
        rules_block = match.group(1) + "\n"
    else:
        rules_block = "_CONTRACT_ID_RULES_PLACEHOLDER_\n"

    lines = [
        '"""',
        "Nobel Center — BIP.ContractID (local only, do not commit).",
        "",
        f"Generated from Baserow delentreprenader table {TABLE_ID}.",
        "Regenerate: python3 scripts/generate_nobel_contract_id_from_baserow.py",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Any",
        "",
        "# Baserow snapshot — metadata for validation / lookup (not published in git).",
        "DELENTREPRENADER: dict[str, dict[str, Any]] = {",
    ]
    for rec in records:
        lines.append(f'    {rec["code"]!r}: {{')
        for key, val in rec.items():
            if key == "code":
                continue
            lines.append(f"        {key!r}: {val!r},")
        lines.append("    },")
    lines.extend(
        [
            "}",
            "",
            "VALID_DE_CODES = frozenset(DELENTREPRENADER.keys())",
            "",
            "",
            "def get_delentreprenad(code: str) -> dict[str, Any] | None:",
            '    """Return Baserow metadata for a DE code, or None."""',
            "    if not code:",
            "        return None",
            "    return DELENTREPRENADER.get(code.strip())",
            "",
            "",
            "def validate_contract_id(code: str) -> bool:",
            '    """Return True when *code* is a known delentreprenad ID from Baserow."""',
            "    if not code or not isinstance(code, str):",
            "        return False",
            "    return code.strip() in DELENTREPRENADER",
            "",
            "",
            "_KG = 'BIP.\"BSABe/Kostengruppe\"'",
            "",
            "",
            "def _rule(selector: str, contract_id: str, *, require_not_duplicate: bool = True) -> dict:",
            "    return {",
            '        "selector": selector,',
            '        "contract_id": contract_id,',
            '        "require_not_duplicate": require_not_duplicate,',
            "    }",
            "",
            "",
        ]
    )
    if rules_block == "_CONTRACT_ID_RULES_PLACEHOLDER_\n":
        lines.append("# Add CONTRACT_ID_RULES manually after first generation.\n")
        lines.append("CONTRACT_ID_RULES: list[dict] = []\n")
    else:
        lines.append(rules_block)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(records)} delentreprenader to {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
