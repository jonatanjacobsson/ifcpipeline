"""
Shared helpers for custom property-mapping recipes (Kostengruppe → BSABe, ContractID, …).

Not an IfcPatch recipe (no ``Patcher`` at module level) — skipped by recipe discovery
because of the leading underscore, same as ``_magaid_shell_repair.py``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import ifcopenshell
import ifcopenshell.api.pset
import ifcopenshell.guid
import ifcopenshell.util.element

_EI = ifcopenshell.entity_instance

_KOSTENGRUPPE_SUFFIX_RE = re.compile(r"\.(ARC|TWP|ERG)$", re.IGNORECASE)
_KOSTENGRUPPE_PREFIX_RE = re.compile(r"^(\d{3})\b")
_MAPPING_MODULE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_BLANK_ARG_VALUES = frozenset({"", "none", "null", "undefined"})


def is_blank_argument(value: Any) -> bool:
    """True when an n8n ``argumentValues`` slot was left empty."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in _BLANK_ARG_VALUES
    return False


def normalize_mapping_module(value: Any, default: str, *, logger: logging.Logger | None = None) -> str:
    """
    Resolve ``mapping_module`` from recipe args.

    n8n often passes ``""`` or whitespace for unused optional slots; treat those as
    *omit* so the recipe default (e.g. ``nobel_a1_kostengruppe_bsabe``) applies.
    """
    if is_blank_argument(value):
        if logger and value is not None and str(value).strip():
            logger.warning(
                "Ignoring blank mapping_module %r; using default %r",
                value,
                default,
            )
        return default
    name = str(value).strip()
    if not _MAPPING_MODULE_RE.match(name):
        raise ValueError(
            f"Invalid mapping_module {value!r}; expected identifier like {default!r}"
        )
    return name


def normalize_bool_argument(value: Any, default: bool) -> bool:
    """Parse overwrite/dry_run flags; blank strings keep the recipe default."""
    if is_blank_argument(value):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "t", "on")
    return bool(value)

_SUPPORTED_DATA_TYPES: dict[str, type] = {
    "IfcText": str,
    "IfcLabel": str,
    "IfcIdentifier": str,
    "IfcInteger": int,
    "IfcReal": float,
    "IfcBoolean": bool,
}


def parse_property_path(prop_path: str) -> tuple[str | None, str | None]:
    """
    Split ``PsetName.PropertyName`` (e.g. ``BIP.BSABe``) into pset and property.

    Returns ``(None, None)`` when the path is empty or has no dot separator.
    """
    if not prop_path or not isinstance(prop_path, str):
        return None, None
    path = prop_path.strip()
    if "." not in path:
        return None, None
    pset_name, prop_name = path.split(".", 1)
    pset_name = pset_name.strip()
    prop_name = prop_name.strip()
    if not pset_name or not prop_name:
        return None, None
    return pset_name, prop_name


def get_pset_property(element: Any, pset: str, prop: str) -> str | None:
    """Read a single pset property as a string, or ``None`` if missing or empty."""
    if element is None or not pset or not prop:
        return None
    try:
        psets = ifcopenshell.util.element.get_psets(
            element, should_inherit=True
        )
    except Exception:
        return None
    if pset not in psets or prop not in psets[pset]:
        return None
    raw = psets[pset][prop]
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() == "undefined":
        return None
    return text


def is_not_duplicate_owned(element: Any) -> bool:
    """
    True when ``BIP-PROCESS.DuplicateOwnedBy`` is unset (``None``, empty, or ``undefined``).

    Elements with a real duplicate owner reference should be skipped by mapping recipes.
    """
    value = get_pset_property(element, "BIP-PROCESS", "DuplicateOwnedBy")
    if value is None:
        return True
    return value.strip().lower() == "undefined"


def parse_kostengruppe(value: str | None) -> dict[str, str | None]:
    """
    Parse a DIN 276 Kostengruppe string from ``BIP.BSABe/Kostengruppe``.

    Returns ``{"prefix": "342", "suffix": "ARC"|"TWP"|"ERG"|None, "raw": "<original>"}``.
    """
    raw = (value or "").strip()
    result: dict[str, str | None] = {"prefix": None, "suffix": None, "raw": raw}
    if not raw:
        return result
    prefix_match = _KOSTENGRUPPE_PREFIX_RE.match(raw)
    if prefix_match:
        result["prefix"] = prefix_match.group(1)
    suffix_match = _KOSTENGRUPPE_SUFFIX_RE.search(raw)
    if suffix_match:
        result["suffix"] = suffix_match.group(1).upper()
    return result


def _safe_is_a(inst: Any, class_name: str) -> bool:
    if inst is None or not isinstance(inst, _EI):
        return False
    try:
        return inst.is_a(class_name)
    except (AttributeError, TypeError, RuntimeError, SystemError):
        return False


def _convert_value(value: Any, data_type: str) -> Any:
    converter = _SUPPORTED_DATA_TYPES.get(data_type, str)
    if data_type == "IfcBoolean":
        if isinstance(value, bool):
            return value
        return str(value).lower() in ("true", "1", "yes")
    return converter(value)


def _get_or_create_owner_history(file: ifcopenshell.file) -> Any:
    owner_histories = file.by_type("IfcOwnerHistory")
    if owner_histories:
        return owner_histories[0]
    person = file.create_entity("IfcPerson", None, None, None)
    org = file.create_entity("IfcOrganization", None, "Unknown")
    person_org = file.create_entity("IfcPersonAndOrganization", person, org)
    app = file.create_entity(
        "IfcApplication", org, "Unknown", "Unknown", "Unknown"
    )
    return file.create_entity(
        "IfcOwnerHistory", person_org, app, None, None, None, None, None, 0
    )


def _relating_property_definitions(related_props: Any) -> list[Any]:
    if related_props is None:
        return []
    if isinstance(related_props, _EI):
        return [related_props]
    if isinstance(related_props, (list, tuple)):
        return [x for x in related_props if isinstance(x, _EI)]
    return []


def _find_property_set(file: ifcopenshell.file, element: Any, pset_name: str):
    rels = None
    try:
        raw = getattr(element, "IsDefinedBy", None)
        if raw:
            rels = list(raw)
    except (TypeError, AttributeError):
        rels = None

    if rels:
        for rel in rels:
            if not isinstance(rel, _EI) or not _safe_is_a(rel, "IfcRelDefinesByProperties"):
                continue
            try:
                related_props = getattr(rel, "RelatingPropertyDefinition", None)
            except Exception:
                continue
            for cand in _relating_property_definitions(related_props):
                if _safe_is_a(cand, "IfcPropertySet") and cand.Name == pset_name:
                    return cand, rel

    try:
        eid = element.id()
    except Exception:
        return None, None
    for rel in file.by_type("IfcRelDefinesByProperties"):
        if not _safe_is_a(rel, "IfcRelDefinesByProperties"):
            continue
        try:
            objs = getattr(rel, "RelatedObjects", None) or []
        except Exception:
            continue
        candidates = [objs] if isinstance(objs, _EI) else list(objs or [])
        if not any(isinstance(o, _EI) and o.id() == eid for o in candidates):
            continue
        try:
            related_props = getattr(rel, "RelatingPropertyDefinition", None)
        except Exception:
            continue
        for cand in _relating_property_definitions(related_props):
            if _safe_is_a(cand, "IfcPropertySet") and cand.Name == pset_name:
                return cand, rel
    return None, None


def _find_property_in_set(property_set: Any, property_name: str):
    if not isinstance(property_set, _EI):
        return None
    try:
        props = getattr(property_set, "HasProperties", None) or []
    except (TypeError, AttributeError):
        return None
    for prop in props:
        if (
            isinstance(prop, _EI)
            and _safe_is_a(prop, "IfcPropertySingleValue")
            and prop.Name == property_name
        ):
            return prop
    return None


def _create_property_value(
    file: ifcopenshell.file, property_name: str, data_type: str, value: Any
):
    typed_value = file.create_entity(data_type, _convert_value(value, data_type))
    return file.create_entity(
        "IfcPropertySingleValue", property_name, None, typed_value, None
    )


def _update_property_value(property_entity: Any, file: ifcopenshell.file, data_type: str, value: Any):
    property_entity.NominalValue = file.create_entity(
        data_type, _convert_value(value, data_type)
    )


def set_pset_property(
    file: ifcopenshell.file,
    element: Any,
    pset: str,
    prop: str,
    value: Any,
    data_type: str = "IfcLabel",
    overwrite: bool = True,
) -> bool:
    """
    Create or update ``IfcPropertySingleValue`` on ``element`` (merge pset, do not replace).

    When ``overwrite`` is False, leaves an existing non-empty, non-``undefined`` value unchanged.
    """
    if data_type not in _SUPPORTED_DATA_TYPES:
        raise ValueError(f"Unsupported data_type '{data_type}'")

    if not overwrite:
        existing = get_pset_property(element, pset, prop)
        if existing is not None:
            low = existing.strip().lower()
            if existing.strip() and low != "undefined":
                return False

    try:
        property_set, _rel = _find_property_set(file, element, pset)
        converted = _convert_value(value, data_type)

        if property_set:
            existing_property = _find_property_in_set(property_set, prop)
            if existing_property:
                _update_property_value(existing_property, file, data_type, converted)
            else:
                prop_value = _create_property_value(file, prop, data_type, converted)
                props = list(property_set.HasProperties or [])
                props.append(prop_value)
                property_set.HasProperties = props
        else:
            owner_history = _get_or_create_owner_history(file)
            prop_value = _create_property_value(file, prop, data_type, converted)
            property_set = file.create_entity(
                "IfcPropertySet",
                ifcopenshell.guid.new(),
                owner_history,
                pset,
                None,
                [prop_value],
            )
            file.create_entity(
                "IfcRelDefinesByProperties",
                ifcopenshell.guid.new(),
                owner_history,
                None,
                None,
                [element],
                property_set,
            )
        return True
    except Exception:
        # Fallback to high-level API when manual entity wiring fails
        try:
            pset_entity = ifcopenshell.api.pset.add_pset(file, product=element, name=pset)
            ifcopenshell.api.pset.edit_pset(
                file, pset=pset_entity, properties={prop: value}
            )
            return True
        except Exception:
            return False


@dataclass
class PatchStats:
    """Counters for property-mapping patch runs."""

    matched: int = 0
    written: int = 0
    skipped: int = 0
    unmapped: int = 0
    errors: int = 0
    extra: dict[str, int] = field(default_factory=dict)

    def log_summary(self, logger: logging.Logger) -> None:
        logger.info(
            "Patch summary: matched=%s written=%s skipped=%s unmapped=%s errors=%s",
            self.matched,
            self.written,
            self.skipped,
            self.unmapped,
            self.errors,
        )
        for key, count in sorted(self.extra.items()):
            if count:
                logger.info("  %s: %s", key, count)
