"""ifcpipeline bindings for the PyPI ``ifcfast`` library (all data-layer operations)."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import pandas as pd

from shared.ifcfast_export import (  # noqa: F401 — re-export
    export_products_csv,
    filter_products_df,
)

logger = logging.getLogger(__name__)

ENGINE = "ifcfast-native"

# Tables documented in ifcfast ``Model.schemas``.
DATA_LAYERS = (
    "products",
    "storeys",
    "spaces",
    "type_objects",
    "contained_in",
    "aggregates",
    "storey_building",
    "voids",
    "psets",
    "quantities",
    "materials",
    "classifications",
    "drift",
    "segments",
)

TRAVERSE_OPS = frozenset(
    {
        "parent",
        "children",
        "ancestors",
        "descendants",
        "storey_of",
        "building_of",
        "products_in",
    }
)

OUTPUT_FORMATS = frozenset({"csv", "json", "parquet"})


def _import_ifcfast():
    try:
        import ifcfast  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "ifcfast package is not installed. Add 'ifcfast>=0.4.18' to "
            "ifcfast-worker/requirements.txt and rebuild the image."
        ) from exc
    return ifcfast


def _rows_to_records(rows: Sequence[Any]) -> List[dict]:
    out: List[dict] = []
    for row in rows:
        if is_dataclass(row):
            out.append(asdict(row))
        elif hasattr(row, "_asdict"):
            out.append(row._asdict())
        elif isinstance(row, dict):
            out.append(row)
        else:
            out.append({"value": row})
    return out


def layer_dataframe(model, layer: str) -> pd.DataFrame:
    """Resolve a schema layer name to a pandas DataFrame."""
    layer = layer.strip()
    if layer == "products":
        return model.products_df
    if layer == "storeys":
        return pd.DataFrame(_rows_to_records(model.storeys))
    if layer == "spaces":
        return model.spaces_df
    if layer == "type_objects":
        return model.type_objects_df
    if layer not in DATA_LAYERS:
        raise ValueError(
            f"Unknown layer {layer!r}. Choose from: {', '.join(DATA_LAYERS)}"
        )
    return getattr(model, layer)


def write_table(
    df: pd.DataFrame,
    path: str,
    *,
    fmt: str,
    delimiter: str = ",",
) -> None:
    fmt = (fmt or "csv").lower()
    if fmt not in OUTPUT_FORMATS:
        raise ValueError(f"Unsupported output_format {fmt!r}")
    if fmt == "csv":
        df.to_csv(path, index=False, sep=delimiter)
    elif fmt == "json":
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(df.to_dict(orient="records"), fh, indent=2, default=str)
    else:
        df.to_parquet(path, index=False)


def write_json(obj: Any, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, default=str)


def content_type_for_format(fmt: str) -> str:
    fmt = (fmt or "csv").lower()
    return {
        "csv": "text/csv",
        "json": "application/json",
        "parquet": "application/vnd.apache.parquet",
    }.get(fmt, "application/octet-stream")


def extension_for_format(fmt: str) -> str:
    fmt = (fmt or "csv").lower()
    return {"csv": ".csv", "json": ".json", "parquet": ".parquet"}.get(fmt, ".bin")


def open_model(ifc_path: str):
    ifcfast = _import_ifcfast()
    logger.info("ifcfast.open: %s", ifc_path)
    return ifcfast.open(ifc_path)


def run_operation(
    ifc_path: str,
    work_dir: str,
    *,
    operation: str,
    output_filename: Optional[str] = None,
    output_format: str = "csv",
    delimiter: str = ",",
    query: str = "IfcProduct",
    attributes: Optional[Sequence[str]] = None,
    include_global_id: bool = True,
    layer: Optional[str] = None,
    layers: Optional[Sequence[str]] = None,
    output_prefix: Optional[str] = None,
    traverse: Optional[str] = None,
    guid: Optional[str] = None,
    filter_entity: Optional[str] = None,
    filter_mode: Optional[str] = None,
    filter_storey_guid: Optional[str] = None,
    preview_table: Optional[str] = None,
    preview_n: int = 5,
    other_ifc_path: Optional[str] = None,
    diff_sample: int = 5,
    sample_guids: int = 3,
    point_cloud_per_m2: float = 1000.0,
    point_cloud_seed: int = 42,
    mesh_unit: str = "m",
    entity_type: Optional[str] = None,
) -> dict:
    """Run one ifcfast operation; write artifact(s) under work_dir."""
    op = (operation or "export_products").strip().lower()
    fmt = (output_format or "csv").lower()
    os.makedirs(work_dir, exist_ok=True)

    model = open_model(ifc_path)
    parse_seconds = getattr(model, "parse_seconds", None)
    artifacts: List[dict] = []
    inline: dict = {"operation": op, "engine": ENGINE, "parse_seconds": parse_seconds}

    def _artifact_path(name: str) -> str:
        return os.path.join(work_dir, name)

    def _add_artifact(
        path: str,
        *,
        role: str,
        rows: Optional[int] = None,
        extra: Optional[dict] = None,
    ) -> str:
        artifacts.append(
            {
                "role": role,
                "local_path": path,
                "filename": os.path.basename(path),
                "format": fmt if path.endswith(extension_for_format(fmt)) else None,
                "rows": rows,
                **(extra or {}),
            }
        )
        return path

    if op in ("export_products", "export", "export_to_csv"):
        from shared.ifcfast_export import resolve_export_columns

        df = filter_products_df(model.products_df, query)
        fmt_used = "csv" if fmt == "csv" else fmt
        ext = extension_for_format(fmt_used)
        out_name = output_filename or f"products{ext}"
        out_path = _artifact_path(out_name)
        if fmt_used == "csv":
            columns = resolve_export_columns(
                attributes or ["Name", "Description"],
                include_global_id=include_global_id,
                available=set(df.columns),
            )
            out = df[[src for _, src in columns]].copy()
            out.columns = [label for label, _ in columns]
            out.to_csv(out_path, index=False, sep=delimiter)
        else:
            write_table(df, out_path, fmt=fmt_used, delimiter=delimiter)
        row_count = len(df)
        _add_artifact(
            out_path, role="export_products", rows=row_count, extra={"format": fmt_used}
        )
        inline.update({"element_count": row_count, "query": query})

    elif op == "export_layer":
        if not layer:
            raise ValueError("export_layer requires layer= (e.g. psets, quantities)")
        df = layer_dataframe(model, layer)
        ext = extension_for_format(fmt)
        out_name = output_filename or f"{layer}{ext}"
        out_path = _artifact_path(out_name)
        write_table(df, out_path, fmt=fmt, delimiter=delimiter)
        _add_artifact(out_path, role="layer", rows=len(df), extra={"layer": layer})
        inline.update({"layer": layer, "row_count": len(df)})

    elif op == "extract_all":
        target_layers = list(layers) if layers else list(DATA_LAYERS)
        prefix = output_prefix or "ifcfast"
        for lay in target_layers:
            df = layer_dataframe(model, lay)
            ext = extension_for_format(fmt)
            name = f"{prefix}_{lay}{ext}"
            path = _artifact_path(name)
            write_table(df, path, fmt=fmt, delimiter=delimiter)
            _add_artifact(path, role="layer", rows=len(df), extra={"layer": lay})
        inline.update({"layers": target_layers, "layer_count": len(target_layers)})

    elif op == "summary":
        payload = model.summary()
        out_name = output_filename or "summary.json"
        out_path = _artifact_path(out_name)
        write_json(payload, out_path)
        _add_artifact(out_path, role="summary")
        inline["summary"] = payload

    elif op == "schemas":
        payload = model.schemas
        out_name = output_filename or "schemas.json"
        out_path = _artifact_path(out_name)
        write_json(payload, out_path)
        _add_artifact(out_path, role="schemas")
        inline["schemas"] = payload

    elif op == "traverse":
        if not traverse or traverse not in TRAVERSE_OPS:
            raise ValueError(f"traverse requires traverse= one of {sorted(TRAVERSE_OPS)}")
        if not guid:
            raise ValueError("traverse requires guid=")
        fn = getattr(model, traverse)
        result = fn(guid)
        payload = {"traverse": traverse, "guid": guid, "result": result}
        out_name = output_filename or f"traverse_{traverse}.json"
        out_path = _artifact_path(out_name)
        write_json(payload, out_path)
        _add_artifact(out_path, role="traverse")
        inline.update(payload)

    elif op == "types":
        payload = model.types()
        out_name = output_filename or "types.json"
        out_path = _artifact_path(out_name)
        write_json(payload, out_path)
        _add_artifact(out_path, role="types")
        inline["types"] = payload

    elif op == "type_bank":
        payload = model.type_bank(sample_guids=sample_guids)
        out_name = output_filename or "type_bank.json"
        out_path = _artifact_path(out_name)
        write_json(payload, out_path)
        _add_artifact(out_path, role="type_bank")
        inline["type_bank"] = payload

    elif op == "type_summary":
        payload = model.type_summary(sample_guids=sample_guids)
        out_name = output_filename or "type_summary.json"
        out_path = _artifact_path(out_name)
        write_json(payload, out_path)
        _add_artifact(out_path, role="type_summary")
        inline["type_summary"] = payload

    elif op == "preview":
        if not preview_table:
            raise ValueError("preview requires preview_table=")
        payload = model.preview(preview_table, n=preview_n)
        out_name = output_filename or f"preview_{preview_table}.json"
        out_path = _artifact_path(out_name)
        write_json(payload, out_path)
        _add_artifact(out_path, role="preview", extra={"table": preview_table})
        inline.update({"table": preview_table, "preview": payload})

    elif op == "diff":
        if not other_ifc_path:
            raise ValueError("diff requires other_filename (second IFC in uploads/)")
        other_model = open_model(other_ifc_path)
        payload = model.diff(other_model, sample=diff_sample)
        out_name = output_filename or "diff.json"
        out_path = _artifact_path(out_name)
        write_json(payload, out_path)
        _add_artifact(out_path, role="diff")
        inline["diff"] = payload

    elif op == "filter_products":
        kwargs: Dict[str, Any] = {}
        if filter_entity:
            kwargs["entity"] = filter_entity
        if filter_mode:
            kwargs["mode"] = filter_mode
        if filter_storey_guid:
            kwargs["storey_guid"] = filter_storey_guid
        rows = model.filter(**kwargs)
        df = pd.DataFrame(_rows_to_records(rows))
        ext = extension_for_format(fmt)
        out_name = output_filename or f"filter_products{ext}"
        out_path = _artifact_path(out_name)
        write_table(df, out_path, fmt=fmt, delimiter=delimiter)
        _add_artifact(out_path, role="filter_products", rows=len(df))
        inline.update({"filter": kwargs, "row_count": len(df)})

    elif op == "by_type":
        if not entity_type:
            raise ValueError("by_type requires entity_type= (e.g. IfcWall)")
        rows = model.by_type(entity_type)
        df = pd.DataFrame(_rows_to_records(rows))
        ext = extension_for_format(fmt)
        out_name = output_filename or f"by_type_{entity_type}{ext}"
        out_path = _artifact_path(out_name)
        write_table(df, out_path, fmt=fmt, delimiter=delimiter)
        _add_artifact(out_path, role="by_type", rows=len(df), extra={"entity_type": entity_type})
        inline.update({"entity_type": entity_type, "row_count": len(df)})

    elif op == "mesh_qto":
        products_df, surfaces_df = model.mesh_qto()
        ext = extension_for_format(fmt)
        p_path = _artifact_path(output_filename or f"mesh_qto_products{ext}")
        write_table(products_df, p_path, fmt=fmt, delimiter=delimiter)
        _add_artifact(p_path, role="mesh_qto_products", rows=len(products_df))
        s_name = (output_filename or "mesh_qto_products").replace(
            extension_for_format(fmt), f"_surfaces{ext}"
        )
        if output_filename and "_products" in output_filename:
            s_name = output_filename.replace("_products", "_surfaces")
        s_path = _artifact_path(s_name if output_filename else f"mesh_qto_surfaces{ext}")
        write_table(surfaces_df, s_path, fmt=fmt, delimiter=delimiter)
        _add_artifact(s_path, role="mesh_qto_surfaces", rows=len(surfaces_df))
        inline.update(
            {"mesh_qto_products": len(products_df), "mesh_qto_surfaces": len(surfaces_df)}
        )

    elif op == "point_cloud":
        df = model.point_cloud(
            per_m2=point_cloud_per_m2, seed=point_cloud_seed, unit=mesh_unit
        )
        ext = extension_for_format(fmt if fmt != "csv" else "parquet")
        out_name = output_filename or f"point_cloud{ext}"
        out_path = _artifact_path(out_name)
        write_table(df, out_path, fmt=ext, delimiter=delimiter)
        _add_artifact(out_path, role="point_cloud", rows=len(df))
        inline.update({"point_cloud_rows": len(df)})

    elif op == "meshes_summary":
        summary_rows = []
        mesh_list = model.meshes(unit=mesh_unit)
        shift = getattr(mesh_list, "global_shift", [0, 0, 0])
        for mesh in mesh_list:
            summary_rows.append(
                {
                    "guid": mesh.guid,
                    "entity": mesh.entity,
                    "vertex_count": int(len(mesh.vertices)),
                    "face_count": int(len(mesh.faces)),
                }
            )
        df = pd.DataFrame(summary_rows)
        ext = extension_for_format(fmt)
        out_name = output_filename or f"meshes_summary{ext}"
        out_path = _artifact_path(out_name)
        write_table(df, out_path, fmt=fmt, delimiter=delimiter)
        _add_artifact(
            out_path,
            role="meshes_summary",
            rows=len(df),
            extra={"global_shift": shift},
        )
        inline.update({"meshes_count": len(df), "global_shift": shift})

    else:
        raise ValueError(
            f"Unknown ifcfast operation {op!r}. "
            f"Supported: export_products, export_layer, extract_all, summary, schemas, "
            f"traverse, types, type_bank, type_summary, preview, diff, filter_products, "
            f"by_type, mesh_qto, point_cloud, meshes_summary"
        )

    if not artifacts:
        raise RuntimeError(f"Operation {op} produced no artifacts")

    primary = artifacts[0]
    inline.update(
        {
            "success": True,
            "message": f"ifcfast {op} completed",
            "artifacts": [
                {k: v for k, v in a.items() if k != "local_path"} for a in artifacts
            ],
            "output_path": primary["local_path"],
            "primary_artifact": primary["role"],
        }
    )
    return {"inline": inline, "artifacts": artifacts}
