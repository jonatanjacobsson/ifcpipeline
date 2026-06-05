"""
Example structure for ``nobel_a1_kostengruppe_bsabe.py`` (commit-safe template).

Copy to ``nobel_a1_kostengruppe_bsabe.py`` (gitignored) and fill ``KOSTENGRUPPE_REGISTRY``
from your project IFC inventory / ifccsv export.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from _property_mapping_utils import parse_kostengruppe


@dataclass(frozen=True, slots=True)
class KostengruppeMapping:
    raw: str
    bsabe: str | None
    din_prefix: str
    suffix: str | None = None
    contract_id_hint: str = ""
    in_dca_chain: bool = True


KOSTENGRUPPE_REGISTRY: tuple[KostengruppeMapping, ...] = (
    KostengruppeMapping(
        "342 Innenwände nicht tragend.ARC",
        "43.CB",
        din_prefix="342",
        suffix="ARC",
        contract_id_hint="DE306",
    ),
    KostengruppeMapping(
        "440 Elektro PV",
        "63",
        din_prefix="440",
        contract_id_hint="DE213",
    ),
)

KOSTENGRUPPE_TO_BSABE: dict[str, str | None] = {
    row.raw: row.bsabe for row in KOSTENGRUPPE_REGISTRY
}

KOSTENGRUPPE_DCA_CHAIN: frozenset[str] = frozenset(
    row.raw for row in KOSTENGRUPPE_REGISTRY if row.in_dca_chain
)

PREFIX_DEFAULTS: dict[str, str] = {
    "342": "43.CB",
    "440": "63",
}

_PREFIX_IFC_CLASS_BSABE: dict[tuple[str, str], str] = {}


def iter_kostengruppe_mappings(*, dca_chain_only: bool = False) -> Iterator[KostengruppeMapping]:
    for row in KOSTENGRUPPE_REGISTRY:
        if dca_chain_only and not row.in_dca_chain:
            continue
        yield row


def resolve_bsabe(raw: str, ifc_class: str | None = None) -> str | None:
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
