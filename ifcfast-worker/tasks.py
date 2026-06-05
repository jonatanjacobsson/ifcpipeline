import logging
import os
import shutil
import tempfile

from shared.classes import IfcFastRequest
from shared import object_storage as s3
from shared.ifcfast_ops import (
    ENGINE,
    content_type_for_format,
    extension_for_format,
    run_operation,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WORKER_NAME = "ifcfast-worker"
OUTPUT_FORMAT = "csv"

HEAVY_OPERATIONS = frozenset(
    {"mesh_qto", "point_cloud", "meshes_summary", "extract_all", "diff"}
)


def _current_job_id():
    try:
        from rq import get_current_job

        job = get_current_job()
        return job.id if job else None
    except Exception:
        return None


def _operation_kwargs(request: IfcFastRequest) -> dict:
    return {
        "operation": request.operation,
        "output_filename": request.output_filename,
        "output_format": request.output_format,
        "delimiter": request.delimiter,
        "query": request.query,
        "attributes": request.attributes,
        "include_global_id": request.include_global_id,
        "layer": request.layer,
        "layers": request.layers,
        "output_prefix": request.output_prefix,
        "traverse": request.traverse,
        "guid": request.guid,
        "filter_entity": request.filter_entity,
        "filter_mode": request.filter_mode,
        "filter_storey_guid": request.filter_storey_guid,
        "preview_table": request.preview_table,
        "preview_n": request.preview_n,
        "diff_sample": request.diff_sample,
        "sample_guids": request.sample_guids,
        "point_cloud_per_m2": request.point_cloud_per_m2,
        "point_cloud_seed": request.point_cloud_seed,
        "mesh_unit": request.mesh_unit,
        "entity_type": request.entity_type,
    }


def run_ifcfast_export(job_data: dict) -> dict:
    """Run any ``ifcfast`` operation (RQ entrypoint — name kept for compatibility)."""
    try:
        request = IfcFastRequest(**job_data)
        logger.info(
            "ifcfast %s for %s (object_storage=%s)",
            request.operation,
            request.filename,
            s3.is_enabled(),
        )
        if s3.is_enabled():
            return _run_s3(request)
        return _run_filesystem(request)
    except FileNotFoundError:
        logger.exception("Input file missing")
        raise
    except Exception:
        logger.exception("ifcfast operation failed")
        raise


def _run_s3(request: IfcFastRequest) -> dict:
    input_key = s3.build_upload_key(request.filename)
    input_pin = s3.pin_for(request, request.filename)
    suffix = os.path.splitext(request.filename)[1] or ".ifc"

    other_tmp = None
    other_ctx = None
    if request.operation == "diff" and request.other_filename:
        other_key = s3.build_upload_key(request.other_filename)
        other_pin = s3.pin_for(request, request.other_filename)
        other_ctx = s3.download_to_tempfile(
            other_key,
            suffix=os.path.splitext(request.other_filename)[1] or ".ifc",
            version_id=other_pin,
        )
        other_tmp = other_ctx.__enter__()

    try:
        with s3.download_to_tempfile(input_key, suffix=suffix, version_id=input_pin) as ifc_tmp:
            work_dir = tempfile.mkdtemp(prefix="ifcfast-")
            try:
                result = run_operation(
                    ifc_tmp,
                    work_dir,
                    other_ifc_path=other_tmp,
                    **_operation_kwargs(request),
                )
                return _upload_artifacts(request, result, input_key, input_pin)
            finally:
                shutil.rmtree(work_dir, ignore_errors=True)
    finally:
        if other_ctx is not None:
            other_ctx.__exit__(None, None, None)


def _run_filesystem(request: IfcFastRequest) -> dict:
    file_path = os.path.join("/uploads", request.filename)
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Input IFC file {request.filename} not found")

    other_path = None
    if request.operation == "diff" and request.other_filename:
        other_path = os.path.join("/uploads", request.other_filename)
        if not os.path.exists(other_path):
            raise FileNotFoundError(f"Other IFC {request.other_filename} not found")

    work_dir = tempfile.mkdtemp(prefix="ifcfast-")
    try:
        result = run_operation(
            file_path,
            work_dir,
            other_ifc_path=other_path,
            **_operation_kwargs(request),
        )
        inline = result["inline"]
        primary = result["artifacts"][0]["local_path"]
        out_dir = "/output/ifcfast"
        os.makedirs(out_dir, exist_ok=True)
        uploaded = []
        for art in result["artifacts"]:
            dest = os.path.join(out_dir, art["filename"])
            shutil.copy2(art["local_path"], dest)
            uploaded.append({**art, "output_path": dest})
        inline["artifacts"] = [
            {k: v for k, v in a.items() if k not in ("local_path",)} for a in uploaded
        ]
        inline["storage"] = "filesystem"
        inline["output_path"] = uploaded[0]["output_path"]
        inline["engine"] = ENGINE
        return inline
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _upload_artifacts(
    request: IfcFastRequest,
    result: dict,
    input_key: str,
    input_pin: str | None,
) -> dict:
    inline = result["inline"]
    uploaded_meta = []
    audits = []
    parent_pins = {input_key: input_pin} if input_pin else None

    for art in result["artifacts"]:
        local_path = art["local_path"]
        fmt = art.get("format") or request.output_format or "csv"
        subdir = "ifcfast"
        out_name = art["filename"]
        output_key = s3.build_output_key(subdir, out_name)
        audit = s3.upload_and_audit(
            local_path,
            key=output_key,
            operation=f"ifcfast_{request.operation}",
            worker=WORKER_NAME,
            job_id=_current_job_id(),
            parents=[("input", input_key)],
            parent_version_ids=parent_pins,
            metadata={
                "engine": ENGINE,
                "operation": request.operation,
                "role": art.get("role"),
                "rows": art.get("rows"),
            },
            content_type=content_type_for_format(
                fmt if fmt in ("csv", "json", "parquet") else "csv"
            ),
        )
        audits.append(audit)
        uploaded_meta.append(
            {
                "role": art.get("role"),
                "filename": out_name,
                "output_key": output_key,
                "output_path": f"s3://{s3.bucket_name()}/{output_key}",
                "format": fmt,
                "rows": art.get("rows"),
                "sha256": audit["sha256"],
                "size_bytes": audit["size_bytes"],
            }
        )

    primary = uploaded_meta[0]
    inline.update(
        {
            "storage": "s3",
            "bucket": s3.bucket_name(),
            "output_key": primary["output_key"],
            "output_path": primary["output_path"],
            "sha256": primary["sha256"],
            "size_bytes": primary["size_bytes"],
            "audit_id": audits[0].get("audit_id") if audits else None,
            "artifacts": uploaded_meta,
            "engine": ENGINE,
        }
    )
    return inline
