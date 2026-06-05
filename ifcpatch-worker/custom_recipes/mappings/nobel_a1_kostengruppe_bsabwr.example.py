"""
Example structure for ``nobel_a1_kostengruppe_bsabwr.py`` (commit-safe template).

Regenerate the full table from BSABe + typbeteckningar:

  python3 scripts/generate_nobel_bsabwr_from_bsabe.py
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from _property_mapping_utils import parse_kostengruppe


@dataclass(frozen=True, slots=True)
class KostengruppeBsabwrMapping:
    raw: str
    bsabwr: str | None
    din_prefix: str
    suffix: str | None = None
    contract_id_hint: str = ""
    in_dca_chain: bool = True


KOSTENGRUPPE_REGISTRY: tuple[KostengruppeBsabwrMapping, ...] = (
    KostengruppeBsabwrMapping(
        "342 Innenwände nicht tragend.ARC",
        "HS",
        din_prefix="342",
        suffix="ARC",
        contract_id_hint="DE306",
    ),
    KostengruppeBsabwrMapping(
        "440 Elektro PV",
        "SHD.1",
        din_prefix="440",
        contract_id_hint="DE213",
    ),
)

KOSTENGRUPPE_TO_BSABWR: dict[str, str | None] = {
    row.raw: row.bsabwr for row in KOSTENGRUPPE_REGISTRY
}

PREFIX_DEFAULTS: dict[str, str] = {
    "342": "HS",
    "440": "SHD.1",
}

_PREFIX_IFC_CLASS_BSABWR: dict[tuple[str, str], str] = {}


def iter_kostengruppe_mappings(*, dca_chain_only: bool = False) -> Iterator[KostengruppeBsabwrMapping]:
    for row in KOSTENGRUPPE_REGISTRY:
        if dca_chain_only and not row.in_dca_chain:
            continue
        yield row


def resolve_bsabwr(raw: str, ifc_class: str | None = None) -> str | None:
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
