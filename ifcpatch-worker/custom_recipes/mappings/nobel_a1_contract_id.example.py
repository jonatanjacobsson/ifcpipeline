"""
Example structure for ``nobel_a1_contract_id.py`` (commit-safe template).

Copy to ``nobel_a1_contract_id.py`` (gitignored) or run:
  python3 scripts/generate_nobel_contract_id_from_baserow.py
"""

from __future__ import annotations

from typing import Any

DELENTREPRENADER: dict[str, dict[str, Any]] = {
    "DE100": {
        "delentreprenad": "DE100 -",
        "namn": "",
        "nummer": "100",
        "huvudgrupp": "1. STORA BYGGENTREPRENADER",
        "huvudgrupp_id": 3841,
        "grupp": "1",
        "modelleras_3d": False,
        "baserow_row_id": 1,
    },
    "DE213": {
        "delentreprenad": "DE213 - Solceller",
        "namn": "Solceller",
        "nummer": "213",
        "huvudgrupp": "2. INSTALLATIONSENTREPRENADER",
        "huvudgrupp_id": 3843,
        "grupp": "2",
        "modelleras_3d": True,
        "baserow_row_id": 0,
    },
}

VALID_DE_CODES = frozenset(DELENTREPRENADER.keys())


def get_delentreprenad(code: str) -> dict[str, Any] | None:
    if not code:
        return None
    return DELENTREPRENADER.get(code.strip())


def validate_contract_id(code: str) -> bool:
    if not code or not isinstance(code, str):
        return False
    return code.strip() in DELENTREPRENADER


_KG = 'BIP."BSABe/Kostengruppe"'


def _rule(selector: str, contract_id: str, *, require_not_duplicate: bool = True) -> dict:
    return {
        "selector": selector,
        "contract_id": contract_id,
        "require_not_duplicate": require_not_duplicate,
    }


CONTRACT_ID_RULES: list[dict] = [
    _rule("IfcWall", "DE100"),
]
