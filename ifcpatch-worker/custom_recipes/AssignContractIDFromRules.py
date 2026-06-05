"""
AssignContractIDFromRules Recipe

Assign BIP.ContractID from a code-defined mapping module (selector rules + DE codes).
Rules are applied in order; the first matching rule wins per element.

Recipe Name: AssignContractIDFromRules
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.element
import ifcopenshell.util.selector

logger = logging.getLogger(__name__)

PSET_BIP = "BIP"
PROP_CONTRACT_ID = "ContractID"
PSET_PROCESS = "BIP-PROCESS"
PROP_DUPLICATE_OWNED = "DuplicateOwnedBy"

_EMPTY_VALUES = frozenset({None, "", "undefined"})


_DEFAULT_MAPPING_MODULE = "nobel_a1_contract_id"

try:
    from _property_mapping_utils import (  # type: ignore[import-not-found]
        PatchStats,
        get_pset_property,
        is_not_duplicate_owned,
        normalize_bool_argument,
        normalize_mapping_module,
        set_pset_property,
    )

    _HAS_MAPPING_UTILS = True
except ImportError:
    _HAS_MAPPING_UTILS = False
    # Minimal fallbacks until _property_mapping_utils.py lands — keep in sync with that module.
    @dataclass
    class PatchStats:
        matched: int = 0
        written: int = 0
        skipped: int = 0
        unmapped: int = 0
        errors: int = 0
        extra: dict = field(default_factory=dict)

        def log_summary(self, log: logging.Logger, prefix: str = "") -> None:
            label = f"{prefix}: " if prefix else ""
            log.info(
                f"{label}matched={self.matched} written={self.written} "
                f"skipped={self.skipped} unmapped={self.unmapped} errors={self.errors}"
            )

    def get_pset_property(element, pset: str, prop: str) -> str | None:
        try:
            psets = ifcopenshell.util.element.get_psets(element)
        except Exception:
            return None
        value = (psets.get(pset) or {}).get(prop)
        if value in _EMPTY_VALUES:
            return None
        return str(value)

    def is_not_duplicate_owned(element) -> bool:
        dup = get_pset_property(element, PSET_PROCESS, PROP_DUPLICATE_OWNED)
        return dup in _EMPTY_VALUES

    def set_pset_property(
        file: ifcopenshell.file,
        element,
        pset: str,
        prop: str,
        value: str,
        *,
        data_type: str = "IfcLabel",
        overwrite: bool = True,
    ) -> bool:
        try:
            ifcopenshell.api.run(
                "pset.edit_pset",
                file,
                pset=pset,
                properties={prop: file.create_entity(data_type, value)},
                product=element,
            )
            return True
        except Exception:
            return False


def _contract_id_is_empty(element) -> bool:
    current = get_pset_property(element, PSET_BIP, PROP_CONTRACT_ID)
    return current in _EMPTY_VALUES


class Patcher:
    """
    Assign BIP.ContractID using ordered selector rules from a mapping module.

    Parameters:
        file: IFC model to patch
        logger: Logger instance
        mapping_module: Python module name under ``mappings`` (default ``nobel_a1_contract_id``)
        overwrite: When false, skip elements that already have a non-empty ContractID
    """

    def __init__(
        self,
        file: ifcopenshell.file,
        logger: logging.Logger,
        mapping_module: str = _DEFAULT_MAPPING_MODULE,
        overwrite: str = "false",
    ):
        self.file = file
        self.logger = logger
        if _HAS_MAPPING_UTILS:
            self.overwrite = normalize_bool_argument(overwrite, False)
            module_name = normalize_mapping_module(
                mapping_module, _DEFAULT_MAPPING_MODULE, logger=logger
            )
        else:
            self.overwrite = str(overwrite).strip().lower() in ("true", "1", "yes", "on")
            module_name = (
                mapping_module or _DEFAULT_MAPPING_MODULE
            ).strip() or _DEFAULT_MAPPING_MODULE
        self.stats = PatchStats()
        self.stats.extra = {
            "rules_total": 0,
            "rules_applied": 0,
            "by_rule": {},
        }

        try:
            self.mapping = importlib.import_module(f"mappings.{module_name}")
        except ImportError as exc:
            raise ValueError(f"Cannot load mappings.{module_name}: {exc}") from exc

        if not hasattr(self.mapping, "CONTRACT_ID_RULES"):
            raise ValueError(f"mappings.{module_name} has no CONTRACT_ID_RULES")
        if not hasattr(self.mapping, "validate_contract_id"):
            raise ValueError(f"mappings.{module_name} has no validate_contract_id")

        self.rules = list(self.mapping.CONTRACT_ID_RULES)
        self.stats.extra["rules_total"] = len(self.rules)
        self.logger.info(
            f"AssignContractIDFromRules: module=mappings.{module_name}, "
            f"rules={len(self.rules)}, overwrite={self.overwrite}"
        )

    def _filter_elements(self, selector: str) -> set:
        try:
            return ifcopenshell.util.selector.filter_elements(self.file, selector)
        except Exception as exc:
            self.logger.warning(f"Selector failed for '{selector}': {exc}")
            self.stats.errors += 1
            return set()

    def _should_write(self, element) -> bool:
        if self.overwrite:
            return True
        return _contract_id_is_empty(element)

    def patch(self) -> None:
        assigned_guids: set[str] = set()

        for idx, rule in enumerate(self.rules):
            selector = rule.get("selector", "")
            contract_id = rule.get("contract_id", "")
            require_not_dup = rule.get("require_not_duplicate", True)

            if not selector or not contract_id:
                self.logger.warning(f"Rule {idx}: missing selector or contract_id, skipping")
                continue
            if not self.mapping.validate_contract_id(contract_id):
                self.logger.warning(f"Rule {idx}: invalid contract_id '{contract_id}', skipping")
                self.stats.errors += 1
                continue

            matched = self._filter_elements(selector)
            rule_matched = 0
            rule_written = 0
            rule_skipped = 0

            for element in matched:
                guid = getattr(element, "GlobalId", None)
                if guid and guid in assigned_guids:
                    continue

                if require_not_dup and not is_not_duplicate_owned(element):
                    rule_skipped += 1
                    self.stats.skipped += 1
                    continue

                rule_matched += 1
                self.stats.matched += 1

                if not self._should_write(element):
                    rule_skipped += 1
                    self.stats.skipped += 1
                    if guid:
                        assigned_guids.add(guid)
                    continue

                if set_pset_property(
                    self.file,
                    element,
                    PSET_BIP,
                    PROP_CONTRACT_ID,
                    contract_id,
                    data_type="IfcLabel",
                    overwrite=True,
                ):
                    rule_written += 1
                    self.stats.written += 1
                    if guid:
                        assigned_guids.add(guid)
                else:
                    self.stats.errors += 1
                    self.logger.warning(
                        f"Failed to set ContractID={contract_id} on "
                        f"{element.is_a()} ({guid})"
                    )

            if rule_matched or rule_written:
                self.stats.extra["rules_applied"] += 1
            self.stats.extra["by_rule"][selector] = {
                "contract_id": contract_id,
                "selector_matches": len(matched),
                "newly_matched": rule_matched,
                "written": rule_written,
                "skipped": rule_skipped,
            }
            self.logger.info(
                f"Rule {idx + 1}/{len(self.rules)} '{selector}' -> {contract_id}: "
                f"selector={len(matched)} newly_matched={rule_matched} written={rule_written}"
            )

        self.stats.log_summary(self.logger)
        self.logger.info(
            "AssignContractIDFromRules: rules_applied=%s",
            self.stats.extra.get("rules_applied"),
        )

    def get_output(self) -> ifcopenshell.file:
        return self.file
