"""
Shared logic for MagiCAD shell repair: TessellateElements → OrientFacetedBrepShells.

Used by ``scripts/repair_full_magiad_ifc.py`` and the ``MagiadTessellateAndOrient`` custom recipe.
Not an IfcPatch recipe (no ``Patcher`` at module level) — skipped by recipe discovery.
"""

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING

import ifcpatch
import ifcopenshell

if TYPE_CHECKING:
    import ifcopenshell

DEFAULT_IFC_ELEMENT_BATCH_SIZE = 50
# Orient runs ``create_shape`` per product; batches of ~50 can still SIGSEGV in the kernel on some models.
IFC_ELEMENT_ORIENT_BATCH_CAP = 10
# Worker / subprocess retries may set ``IFC_MAGIAD_ORIENT_BATCH_CAP`` / ``IFC_MAGIAD_ELEMENT_BATCH``.
COORD_DECIMALS_DEFAULT = 6


def effective_orient_batch_cap() -> int:
    """Orient batch cap: env ``IFC_MAGIAD_ORIENT_BATCH_CAP`` overrides default (min 1)."""
    raw = os.environ.get("IFC_MAGIAD_ORIENT_BATCH_CAP", "").strip()
    if not raw:
        return IFC_ELEMENT_ORIENT_BATCH_CAP
    try:
        return max(1, int(float(raw)))
    except ValueError:
        return IFC_ELEMENT_ORIENT_BATCH_CAP


def effective_ifc_element_batch_size(requested: int) -> int:
    """Tessellation batch size: env ``IFC_MAGIAD_ELEMENT_BATCH`` overrides recipe value when set."""
    raw = os.environ.get("IFC_MAGIAD_ELEMENT_BATCH", "").strip()
    if not raw:
        return max(1, int(requested))
    try:
        return max(1, int(float(raw)))
    except ValueError:
        return max(1, int(requested))

# IFC2×3 (e.g. MagiCAD V--57): all concrete ``IfcDistributionElement`` subclasses used in practice.
# ``IfcFlowController`` is required — models use it directly (not only ``IfcElectricDistributionPoint``).
# Silencers etc. are usually ``IfcFlowSegment`` + ``IfcDuctSilencerType``, not ``IfcDuctSilencer`` (IFC4+ class).
IFC2X3_MEP_DISTRIBUTION_TYPES: tuple[str, ...] = (
    "IfcFlowController",
    "IfcFlowFitting",
    "IfcFlowSegment",
    "IfcFlowTerminal",
    "IfcFlowMovingDevice",
    "IfcFlowTreatmentDevice",
    "IfcEnergyConversionDevice",
    "IfcDistributionChamberElement",
    "IfcDistributionControlElement",
    "IfcFlowStorageDevice",
    "IfcElectricDistributionPoint",
)

# Non-distribution products often modeled with MEP (supports, proxies, light steel).
# IfcCovering is intentionally omitted — it pulls in broad architectural finishes; add via ``types`` if needed.
MEP_ANCILLARY_TYPES: tuple[str, ...] = (
    "IfcDiscreteAccessory",
    "IfcBuildingElementProxy",
    "IfcPlate",
    "IfcMember",
)


def distribution_element_leaf_type_names(schema_name: str) -> tuple[str, ...]:
    """Concrete leaf classes under ``IfcDistributionElement`` for ``schema_name`` (IFC4, IFC4X3, …)."""
    try:
        schema = ifcopenshell.schema_by_name(schema_name)
    except Exception:
        schema = ifcopenshell.schema_by_name("IFC4")
    root = schema.declaration_by_name("IfcDistributionElement")
    leaves: list[str] = []

    def walk(decl) -> None:
        subs = list(decl.subtypes())
        if not subs:
            leaves.append(decl.name())
            return
        for sub in subs:
            walk(sub)

    walk(root)
    return tuple(sorted(leaves))


def mep_preset_base_types(schema_name: str) -> tuple[str, ...]:
    """Bundled MEP preset for ``schema_name``: IFC2×3 fixed list; else all distribution leaf types + ancillary."""
    if schema_name == "IFC2X3":
        core: tuple[str, ...] = IFC2X3_MEP_DISTRIBUTION_TYPES
    else:
        core = distribution_element_leaf_type_names(schema_name)
    seen: set[str] = set()
    out: list[str] = []
    for t in core + MEP_ANCILLARY_TYPES:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return tuple(out)


def collapse_to_ifc_element_if_present(
    type_list: list[str],
    log: logging.Logger | None = None,
) -> list[str]:
    """
    If ``IfcElement`` appears anywhere in ``type_list``, return ``[\"IfcElement\"]`` only.

    ``IfcElement`` is a supertype of the MEP preset and other building classes; keeping it
    alongside explicit types produced a 10k+ product selector and kernel instability.
    The batched IfcElement path in ``run_tessellate_and_orient`` is the safe route.
    """
    if not type_list or "IfcElement" not in type_list:
        return type_list
    logger = log or logging.getLogger(__name__)
    if len(type_list) > 1:
        logger.info(
            "Collapsing type list to IfcElement only (batched kernel-safe path); had %d type(s): %s",
            len(type_list),
            ", ".join(type_list),
        )
    return ["IfcElement"]


def merge_mep_preset_with_extras(schema_name: str, extra_types: list[str]) -> list[str]:
    """MEP preset for ``schema_name`` plus any *additional* ``extra_types`` (deduped, preset order first)."""
    seen: set[str] = set()
    out: list[str] = []
    for t in mep_preset_base_types(schema_name):
        if t not in seen:
            seen.add(t)
            out.append(t)
    for t in extra_types:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return collapse_to_ifc_element_if_present(out, None)


def filter_type_list_to_schema(
    f: "ifcopenshell.file",
    type_list: list[str],
    log: logging.Logger,
) -> list[str]:
    """Drop class names that do not exist in the open file’s IFC schema (typos / IFC4-only on IFC2×3)."""
    try:
        schema = ifcopenshell.schema_by_name(f.schema)
    except Exception:
        schema = ifcopenshell.schema_by_name("IFC4")
    out: list[str] = []
    skipped: list[str] = []
    for t in type_list:
        try:
            schema.declaration_by_name(t)
            out.append(t)
        except Exception:
            skipped.append(t)
    if skipped:
        log.warning(
            "Skipping IFC class name(s) not in schema %s: %s",
            f.schema,
            ", ".join(skipped),
        )
    return out


def _normalize_class_token(token: str) -> str:
    t = token.strip()
    if t.lower() == "ifcelement":
        return "IfcElement"
    return t


def parse_types_arg(types_str: str) -> list[str]:
    raw = (types_str or "").strip()
    if not raw:
        return ["IfcFlowFitting"]
    parts = [_normalize_class_token(p) for p in re.split(r"[\s,]+", raw) if p.strip()]
    if not parts:
        return ["IfcFlowFitting"]
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return collapse_to_ifc_element_if_present(out, None)


def types_to_selector(type_names: list[str]) -> str:
    return ", ".join(type_names)


def is_whole_ifc_element_scope(type_list: list[str]) -> bool:
    """True when the scope is the full batched IfcElement pass (IfcElement subsumes other types)."""
    return "IfcElement" in type_list


def _ifc_element_instances_in_order(f: "ifcopenshell.file", log: logging.Logger) -> list:
    raw = sorted(f.by_type("IfcElement"), key=lambda e: e.id())
    out: list = []
    skipped_site = 0
    for e in raw:
        if e.is_a("IfcSite"):
            skipped_site += 1
            continue
        out.append(e)
    if skipped_site:
        log.info("Excluded %d IfcSite instance(s) from IfcElement scope", skipped_site)
    return out


def parse_bool(s: str | bool) -> bool:
    if isinstance(s, bool):
        return s
    return str(s).strip().lower() in ("1", "true", "yes", "on")


def parse_int(s: str | int, default: int) -> int:
    try:
        return int(float(str(s).strip()))
    except Exception:
        return default


def run_tessellate_and_orient(
    f: "ifcopenshell.file",
    log: logging.Logger,
    input_path: str,
    *,
    type_list: list[str],
    ifc_element_batch_size: int = DEFAULT_IFC_ELEMENT_BATCH_SIZE,
    coord_decimals: int = COORD_DECIMALS_DEFAULT,
) -> None:
    """Mutates ``f`` in place: tessellation then ``OrientFacetedBrepShells``."""
    type_list = filter_type_list_to_schema(f, type_list, log)
    type_list = collapse_to_ifc_element_if_present(type_list, log)
    # Local import so OrientFacetedBrepShells stays a normal custom recipe module
    from OrientFacetedBrepShells import Patcher as OrientPatcher

    selector = types_to_selector(type_list)
    batch_size = effective_ifc_element_batch_size(ifc_element_batch_size)
    # Ordered IfcElement list for batched tessellation + orient (same order for both passes).
    ife_elements: list | None = None

    if is_whole_ifc_element_scope(type_list):
        ife_elements = _ifc_element_instances_in_order(f, log)
        n = len(ife_elements)
        batches = (n + batch_size - 1) // batch_size if n else 0
        log.info(
            "TessellateElements (IfcElement, %d instance(s) in %d batch(es) of up to %d, ordered by entity id)",
            n,
            batches,
            batch_size,
        )
        model = f
        done = 0
        for b in range(batches):
            chunk = ife_elements[b * batch_size : (b + 1) * batch_size]
            gids: list[str] = []
            for elem in chunk:
                gid = getattr(elem, "GlobalId", None)
                if not gid:
                    log.warning("Skipping element #%s: no GlobalId", elem.id())
                    continue
                gids.append(gid)
            if not gids:
                continue
            query = ", ".join(gids)
            done += len(gids)
            log.info(
                "TessellateElements batch [%d/%d]: %d element(s), %s … %s",
                b + 1,
                batches,
                len(gids),
                gids[0],
                gids[-1],
            )
            model = ifcpatch.execute(
                {
                    "input": input_path,
                    "file": model,
                    "recipe": "TessellateElements",
                    "arguments": [query, False],
                }
            )
        log.info("TessellateElements IfcElement scope: processed %d instance(s)", done)
    elif len(type_list) == 1:
        log.info("TessellateElements selector: %s", selector)
        model = ifcpatch.execute(
            {
                "input": input_path,
                "file": f,
                "recipe": "TessellateElements",
                "arguments": [selector, False],
            }
        )
    else:
        log.info("TessellateElements (sequential by type, %d class(es)): %s", len(type_list), selector)
        model = f
        for i, t in enumerate(type_list, start=1):
            log.info("TessellateElements [%d/%d]: %s", i, len(type_list), t)
            model = ifcpatch.execute(
                {
                    "input": input_path,
                    "file": model,
                    "recipe": "TessellateElements",
                    "arguments": [t, False],
                }
            )

    # Full-model IfcElement: orient in the same GlobalId batches. A single selector="IfcElement"
    # pass walks ~10k products and can SIGSEGV in the geometry kernel; batching matches tessellation.
    if ife_elements is not None:
        # Tessellation can use larger batches; orientation is heavier (geom + mesh volume per product).
        orient_bs = max(1, min(batch_size, effective_orient_batch_cap()))
        if orient_bs < batch_size:
            log.info(
                "OrientFacetedBrepShells: capping IfcElement orient batch size %d → %d (tessellation batch was %d)",
                batch_size,
                orient_bs,
                batch_size,
            )
        n_or = len(ife_elements)
        obatches = (n_or + orient_bs - 1) // orient_bs if n_or else 0
        log.info(
            "OrientFacetedBrepShells (IfcElement, %d instance(s) in %d batch(es) of up to %d)",
            n_or,
            obatches,
            orient_bs,
        )
        for b in range(obatches):
            chunk = ife_elements[b * orient_bs : (b + 1) * orient_bs]
            gids: list[str] = []
            for elem in chunk:
                gid = getattr(elem, "GlobalId", None)
                if gid:
                    gids.append(gid)
            if not gids:
                continue
            q = ", ".join(gids)
            log.info(
                "OrientFacetedBrepShells batch [%d/%d]: %d element(s), %s … %s",
                b + 1,
                obatches,
                len(gids),
                gids[0],
                gids[-1],
            )
            OrientPatcher(model, log, q, coord_decimals=coord_decimals).patch()
    else:
        log.info("OrientFacetedBrepShells (selector=%s)...", selector)
        OrientPatcher(model, log, selector, coord_decimals=coord_decimals).patch()
