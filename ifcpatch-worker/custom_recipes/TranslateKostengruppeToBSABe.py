"""
TranslateKostengruppeToBSABe Recipe

Maps ``BIP.BSABe/Kostengruppe`` (DIN 276 German cost-group labels) to ``BIP.BSABe``
using the in-code Nobel A1 mapping table (bipkoder typbeteckningar).

Recipe Name: TranslateKostengruppeToBSABe
Author: ifcpipeline (2026-05)

Positional arguments (n8n IfcPatch node order):
    mapping_module: Python module under ``custom_recipes/mappings/`` (default:
                    ``nobel_a1_kostengruppe_bsabe``).
    overwrite:      When ``false``, skip elements whose ``BIP.BSABe`` already has a
                    non-empty, non-``undefined`` value (default: ``true``).
    dry_run:        When ``true``, resolve and count but do not write properties
                    (default: ``false``).

Example::

    patcher = Patcher(ifc_file, logger)
    patcher.patch()
    output = patcher.get_output()
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from typing import Callable, Optional

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.element

logger = logging.getLogger(__name__)

PSET_BIP = "BIP"
PROP_KOSTENGRUPPE = "BSABe/Kostengruppe"
PROP_BSABE = "BSABe"
DATA_TYPE = "IfcLabel"

_DEFAULT_MAPPING_MODULE = "nobel_a1_kostengruppe_bsabe"

try:
    from _property_mapping_utils import (  # noqa: F401
        PatchStats,
        get_pset_property,
        normalize_bool_argument,
        normalize_mapping_module,
        set_pset_property,
    )

    _HAS_MAPPING_UTILS = True
except ImportError:  # pragma: no cover - exercised when utils not yet deployed
    _HAS_MAPPING_UTILS = False

    @dataclass
    class PatchStats:
        matched: int = 0
        written: int = 0
        skipped: int = 0
        unmapped: int = 0
        errors: int = 0

        def log_summary(self, log: logging.Logger) -> None:
            log.info(
                "TranslateKostengruppeToBSABe: matched=%s written=%s skipped=%s "
                "unmapped=%s errors=%s",
                self.matched,
                self.written,
                self.skipped,
                self.unmapped,
                self.errors,
            )

    def get_pset_property(element, pset_name: str, prop_name: str) -> Optional[str]:
        try:
            psets = ifcopenshell.util.element.get_psets(element)
        except Exception:
            return None
        if pset_name not in psets or prop_name not in psets[pset_name]:
            return None
        raw = psets[pset_name][prop_name]
        if raw is None:
            return None
        text = str(raw).strip()
        if not text or text.lower() == "undefined":
            return None
        return text

    def set_pset_property(
        file: ifcopenshell.file,
        element,
        pset_name: str,
        prop_name: str,
        value: str,
        data_type: str = DATA_TYPE,
        overwrite: bool = True,
    ) -> bool:
        try:
            psets = ifcopenshell.util.element.get_psets(element, psets_only=True)
            pset = psets.get(pset_name)
            if pset is None:
                pset = ifcopenshell.api.run(
                    "pset.add_pset",
                    file,
                    product=element,
                    name=pset_name,
                )
            ifcopenshell.api.run(
                "pset.edit_pset",
                file,
                pset=pset,
                properties={prop_name: value},
            )
            return True
        except Exception:
            return False


def _is_meaningful_property_value(value: Optional[str]) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    return bool(text) and text.lower() != "undefined"


def _load_resolve_bsabe(module_name: str) -> Callable[[str, Optional[str]], Optional[str]]:
    mod = importlib.import_module(f"mappings.{module_name}")
    resolve = getattr(mod, "resolve_bsabe", None)
    if resolve is None:
        raise AttributeError(
            f"mappings.{module_name} has no resolve_bsabe(); "
            "expected nobel_a1_kostengruppe_bsabe mapping module"
        )
    return resolve


class Patcher:
    """
    Translate ``BIP.BSABe/Kostengruppe`` → ``BIP.BSABe`` for all ``IfcElement`` instances.

    Parameters:
        file: IFC model to patch
        logger: Logger instance
        mapping_module: ``mappings`` subpackage module name (default:
            ``nobel_a1_kostengruppe_bsabe``)
        overwrite: Replace existing ``BIP.BSABe`` when true (default: true)
        dry_run: Count matches without writing (default: false)
    """

    def __init__(
        self,
        file: ifcopenshell.file,
        logger: logging.Logger,
        mapping_module: str = _DEFAULT_MAPPING_MODULE,
        overwrite: str = "true",
        dry_run: str = "false",
    ):
        self.file = file
        self.logger = logger
        if _HAS_MAPPING_UTILS:
            self.mapping_module = normalize_mapping_module(
                mapping_module, _DEFAULT_MAPPING_MODULE, logger=logger
            )
            self.overwrite = normalize_bool_argument(overwrite, True)
            self.dry_run = normalize_bool_argument(dry_run, False)
        else:
            self.mapping_module = (
                mapping_module or _DEFAULT_MAPPING_MODULE
            ).strip() or _DEFAULT_MAPPING_MODULE
            self.overwrite = str(overwrite).strip().lower() in ("true", "1", "yes", "t")
            self.dry_run = str(dry_run).strip().lower() in ("true", "1", "yes", "t")
        self.stats = PatchStats()
        self._would_write = 0

        try:
            self._resolve_bsabe = _load_resolve_bsabe(self.mapping_module)
        except Exception as exc:
            self.logger.error(
                "Failed to load mapping module mappings.%s: %s",
                self.mapping_module,
                exc,
            )
            raise

        self.logger.info(
            "TranslateKostengruppeToBSABe: mapping=%s overwrite=%s dry_run=%s "
            "utils=%s",
            self.mapping_module,
            self.overwrite,
            self.dry_run,
            "shared" if _HAS_MAPPING_UTILS else "inline-fallback",
        )

    def patch(self) -> None:
        """Map Kostengruppe values to BSABe on every IfcElement."""
        self.logger.info("TranslateKostengruppeToBSABe: starting patch")

        elements = self.file.by_type("IfcElement")
        self.logger.info("Found %s IfcElement instance(s)", len(elements))

        for idx, element in enumerate(elements):
            if len(elements) > 500 and (idx + 1) % 500 == 0:
                self.logger.info("Processing element %s/%s", idx + 1, len(elements))

            try:
                self._process_element(element)
            except Exception as exc:
                self.stats.errors += 1
                guid = getattr(element, "GlobalId", "?")
                self.logger.warning(
                    "Failed on %s (%s): %s",
                    element.is_a(),
                    guid,
                    exc,
                )

        self.stats.log_summary(self.logger)
        if self.dry_run and self._would_write:
            self.logger.info(
                "Dry run: would have written BSABe on %s element(s)",
                self._would_write,
            )

        self.logger.info("TranslateKostengruppeToBSABe: patch complete")

    def _process_element(self, element) -> None:
        raw = get_pset_property(element, PSET_BIP, PROP_KOSTENGRUPPE)
        if not _is_meaningful_property_value(raw):
            self.stats.skipped += 1
            return

        self.stats.matched += 1
        bsabe = self._resolve_bsabe(raw, element.is_a())
        if bsabe is None:
            self.stats.unmapped += 1
            self.logger.debug(
                "Unmapped Kostengruppe %r on %s (%s)",
                raw,
                element.is_a(),
                getattr(element, "GlobalId", "?"),
            )
            return

        if not self.overwrite:
            existing = get_pset_property(element, PSET_BIP, PROP_BSABE)
            if _is_meaningful_property_value(existing):
                self.stats.skipped += 1
                return

        if self.dry_run:
            self._would_write += 1
            return

        ok = set_pset_property(
            self.file,
            element,
            PSET_BIP,
            PROP_BSABE,
            bsabe,
            data_type=DATA_TYPE,
            overwrite=self.overwrite,
        )
        if ok:
            self.stats.written += 1
        else:
            self.stats.errors += 1

    def get_output(self) -> ifcopenshell.file:
        """Return the patched IFC file."""
        return self.file
