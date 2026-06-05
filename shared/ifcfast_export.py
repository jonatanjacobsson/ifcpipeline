"""IFC → CSV via the native ``ifcfast`` library (Rust STEP tokenizer).

This is NOT a thin ifcopenshell/ifccsv wrapper. The PyPI package ``ifcfast``
builds a tier-1 ``products_df`` without ``ifcopenshell.open()`` on the hot path.

See https://pypi.org/project/ifcfast/ and ``ifcfast-worker/README.md``.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Sequence

logger = logging.getLogger(__name__)

# User-facing attribute names → products_df columns (tier-1 index).
_PRODUCT_COLUMN_MAP = {
    "GlobalId": "guid",
    "globalid": "guid",
    "guid": "guid",
    "Name": "name",
    "name": "name",
    "Description": "object_type",
    "description": "object_type",
    "ObjectType": "object_type",
    "object_type": "object_type",
    "Tag": "tag",
    "tag": "tag",
    "Entity": "entity",
    "entity": "entity",
    "PredefinedType": "predefined_type",
    "predefined_type": "predefined_type",
    "Storey": "storey_name",
    "storey_name": "storey_name",
    "TypeName": "type_name",
    "type_name": "type_name",
}


def _import_ifcfast():
    try:
        import ifcfast  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "ifcfast package is not installed. Add 'ifcfast>=0.4.18' to "
            "ifcfast-worker/requirements.txt and rebuild the image."
        ) from exc
    return ifcfast


def filter_products_df(df, query: Optional[str]):
    """Filter tier-1 products by entity type (not full IOS entity walk)."""
    if not query or query in ("IfcProduct", ""):
        return df
    if query == "IfcElement":
        # Tier-1 table is products only; exclude spatial structure rows.
        spatial = {
            "IfcProject",
            "IfcSite",
            "IfcBuilding",
            "IfcBuildingStorey",
            "IfcSpace",
        }
        return df[~df["entity"].isin(spatial)]
    return df[df["entity"] == query]


def resolve_export_columns(
    attributes: Sequence[str],
    *,
    include_global_id: bool,
    available: set[str],
) -> List[tuple[str, str]]:
    """Return (header_label, products_df_column) pairs."""
    attrs = list(attributes or ["Name", "Description"])
    if include_global_id and "GlobalId" not in attrs and "guid" not in attrs:
        attrs.insert(0, "GlobalId")

    columns: List[tuple[str, str]] = []
    for attr in attrs:
        src = _PRODUCT_COLUMN_MAP.get(attr) or _PRODUCT_COLUMN_MAP.get(attr.lower()) or attr
        if src not in available:
            raise ValueError(
                f"Attribute {attr!r} is not available in ifcfast products_df "
                f"(columns: {sorted(available)}). Use /ifccsv for arbitrary "
                "ifcopenshell selector paths or request psets via a follow-up extract."
            )
        columns.append((attr, src))
    return columns


def export_products_csv(
    ifc_path: str,
    output_path: str,
    *,
    query: Optional[str] = "IfcProduct",
    attributes: Optional[Sequence[str]] = None,
    delimiter: str = ",",
    include_global_id: bool = True,
    mmap: bool = True,  # noqa: ARG001 — native parser always mmap-based
) -> int:
    """Export tier-1 products to CSV using ``ifcfast.open()`` + ``products_df``."""
    ifcfast = _import_ifcfast()
    logger.info("ifcfast native open: %s", ifc_path)
    model = ifcfast.open(ifc_path)
    df = filter_products_df(model.products_df, query)
    columns = resolve_export_columns(
        attributes or ["Name", "Description"],
        include_global_id=include_global_id,
        available=set(df.columns),
    )
    out = df[[src for _, src in columns]].copy()
    out.columns = [label for label, _ in columns]
    out.to_csv(output_path, index=False, sep=delimiter)
    return len(out)
