import logging
import os
import tempfile
from multiprocessing import get_context
from queue import Empty
from typing import Any, Optional

from shared.classes import IfcCsvRequest, IfcCsvImportRequest
from shared import object_storage as s3

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WORKER_NAME = "ifccsv-worker"


def _ifc_csv_request_to_dict(request: IfcCsvRequest) -> dict:
    if hasattr(request, "model_dump"):
        return request.model_dump()
    return request.dict()


def _run_ifccsv_export_to_path(
    model, elements, request: IfcCsvRequest, output_path: str
) -> None:
    """Single ifccsv export call (writes csv/ods/xlsx via format=)."""
    import ifccsv

    attrs = list(request.attributes or [])
    include_gid = request.include_global_id
    if include_gid and "GlobalId" in attrs:
        include_gid = False
    fmt = (request.format or "csv").lower()
    if fmt not in ("csv", "ods", "xlsx"):
        raise ValueError(f"Unsupported format: {request.format}")
    converter = ifccsv.IfcCsv()
    headers = request.headers if request.headers else None
    converter.export(
        model,
        elements,
        attrs,
        headers=headers,
        output=output_path,
        format=fmt,
        delimiter=request.delimiter,
        null=request.null_value,
        groups=request.groups,
        sort=request.sort,
        summaries=request.summaries,
        formatting=request.formatting,
        include_global_id=include_gid,
    )


def _isolated_export_worker(result_queue, payload: dict) -> None:
    """Top-level spawn entry: open IFC, filter, export — isolates ifcopenshell
    SIGSEGVs and lark corruption from the rq work-horse."""
    import logging as _logging

    import ifcopenshell
    import ifcopenshell.util.selector

    _logging.basicConfig(level=_logging.INFO)
    wlog = _logging.getLogger("ifccsv.isolated_export")
    try:
        req = IfcCsvRequest(**payload["request"])
        model = ifcopenshell.open(payload["ifc_path"])
        elements = (
            ifcopenshell.util.selector.filter_elements(model, req.query)
            if req.query
            else model.by_type("IfcElement")
        )
        element_count = len(elements)
        wlog.info("Processing %d elements with %s", element_count, req.attributes)
        _run_ifccsv_export_to_path(model, elements, req, payload["output_path"])
        result_queue.put(("ok", {"element_count": element_count}))
    except Exception as e:
        import traceback as _tb

        tb_str = _tb.format_exc()
        try:
            result_queue.put(("err", f"{type(e).__name__}: {e}\n{tb_str}"))
        except Exception:
            pass
        wlog.exception("Isolated export worker failed")
        raise


def _isolated_import_worker(result_queue, payload: dict) -> None:
    """Top-level spawn entry: import tabular data into IFC and write output."""
    import logging as _logging

    import ifcopenshell
    import ifccsv

    _logging.basicConfig(level=_logging.INFO)
    wlog = _logging.getLogger("ifccsv.isolated_import")
    try:
        model = ifcopenshell.open(payload["ifc_path"])
        importer = ifccsv.IfcCsv()
        importer.Import(model, payload["data_path"])
        model.write(payload["output_path"])
        result_queue.put(("ok", {}))
    except Exception as e:
        import traceback as _tb

        tb_str = _tb.format_exc()
        try:
            result_queue.put(("err", f"{type(e).__name__}: {e}\n{tb_str}"))
        except Exception:
            pass
        wlog.exception("Isolated import worker failed")
        raise


def _run_in_spawn_isolation(
    worker_target,
    payload: dict,
    *,
    label: str = "default",
    operation: str = "ifccsv",
    result_timeout: int = 600,
) -> dict:
    """Run ifcopenshell work in a spawn subprocess.

    A SIGSEGV inside ``_ifcopenshell_wrapper`` kills only the child; the rq
    work-horse survives and converts the non-zero exit into RuntimeError for
    n8n retry.
    """
    ctx = get_context("spawn")
    q = ctx.Queue(maxsize=1)
    proc = ctx.Process(target=worker_target, args=(q, payload))
    proc.start()
    proc.join()
    if proc.exitcode == 0:
        try:
            status, data = q.get(timeout=result_timeout)
        except Empty as e:
            raise RuntimeError(
                f"{operation} child exited 0 but sent no result"
            ) from e
        if status == "ok":
            return data
        raise RuntimeError(f"{operation} isolated worker: {data}")
    child_err: Optional[str] = None
    try:
        status, data = q.get_nowait()
        if status == "err":
            child_err = data
    except Empty:
        pass
    raise RuntimeError(
        f"{operation} crashed in isolated subprocess "
        f"(label={label!r}, exit={proc.exitcode}"
        + (f", child={child_err!r}" if child_err else "")
        + ")"
    )


def _run_export_in_spawn_isolation(
    request: IfcCsvRequest, ifc_path: str, output_path: str
) -> dict[str, Any]:
    payload = {
        "request": _ifc_csv_request_to_dict(request),
        "ifc_path": ifc_path,
        "output_path": output_path,
    }
    return _run_in_spawn_isolation(
        _isolated_export_worker,
        payload,
        label="export",
        operation="ifccsv export",
    )


def _run_import_in_spawn_isolation(
    ifc_path: str, data_path: str, output_path: str
) -> dict[str, Any]:
    payload = {
        "ifc_path": ifc_path,
        "data_path": data_path,
        "output_path": output_path,
    }
    return _run_in_spawn_isolation(
        _isolated_import_worker,
        payload,
        label="import",
        operation="ifccsv import",
    )


def _prepare_export_output_path(request: IfcCsvRequest) -> str:
    out_suffix = os.path.splitext(request.output_filename)[1] or f".{request.format}"
    fd, out_tmp = tempfile.mkstemp(suffix=out_suffix)
    os.close(fd)
    # mkstemp leaves a zero-byte file. ifccsv.export_xlsx() treats any existing
    # path as a workbook to append to and calls openpyxl.load_workbook → BadZipFile.
    try:
        os.unlink(out_tmp)
    except OSError:
        pass
    return out_tmp


def _current_job_id():
    try:
        from rq import get_current_job
        job = get_current_job()
        return job.id if job else None
    except Exception:
        return None


def run_ifc_to_csv_conversion(job_data: dict) -> dict:
    """Convert IFC → CSV/XLSX/ODS.

    With object storage enabled, the input IFC is pulled from the bucket at
    `uploads/<filename>` and the result is pushed to `output/<format>/<output_filename>`.
    The legacy filesystem layout is preserved otherwise.
    """
    try:
        request = IfcCsvRequest(**job_data)
        logger.info(
            "Starting IFC→%s conversion for %s (object_storage=%s)",
            request.format.upper(), request.filename, s3.is_enabled(),
        )

        if s3.is_enabled():
            return _run_export_s3(request)
        return _run_export_filesystem(request)

    except FileNotFoundError:
        logger.exception("Input file missing")
        raise
    except Exception:
        logger.exception("Error during IFC→CSV conversion")
        raise


def _run_export_s3(request: IfcCsvRequest) -> dict:
    input_key = s3.build_upload_key(request.filename)
    input_pin = s3.pin_for(request, request.filename)
    output_key = s3.build_output_key(request.format, request.output_filename)
    suffix = os.path.splitext(request.filename)[1] or ".ifc"

    with s3.download_to_tempfile(input_key, suffix=suffix, version_id=input_pin) as ifc_tmp:
        out_tmp = _prepare_export_output_path(request)
        try:
            export_meta = _run_export_in_spawn_isolation(request, ifc_tmp, out_tmp)
            element_count = export_meta["element_count"]
            logger.info("Exported %d elements with %s", element_count, request.attributes)
            audit = s3.upload_and_audit(
                out_tmp,
                key=output_key,
                operation="ifccsv",
                worker=WORKER_NAME,
                job_id=_current_job_id(),
                parents=[("input", input_key)],
                parent_version_ids={input_key: input_pin} if input_pin else None,
                metadata={
                    "format": request.format,
                    "query": request.query,
                    "delimiter": request.delimiter,
                    "attribute_count": len(request.attributes or []),
                    "element_count": element_count,
                    "has_groups": bool(request.groups),
                },
                content_type=_csv_content_type(request.format),
            )
        finally:
            try:
                os.remove(out_tmp)
            except FileNotFoundError:
                pass

    return {
        "success": True,
        "message": f"Successfully converted to {request.format.upper()}",
        "storage": "s3",
        "bucket": s3.bucket_name(),
        "output_key": output_key,
        "output_path": f"s3://{s3.bucket_name()}/{output_key}",
        "sha256": audit["sha256"],
        "size_bytes": audit["size_bytes"],
        "audit_id": audit["audit_id"],
    }


def _csv_content_type(fmt: str) -> str:
    return {
        "csv": "text/csv",
        "ods": "application/vnd.oasis.opendocument.spreadsheet",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(fmt, "application/octet-stream")


def _run_export_filesystem(request: IfcCsvRequest) -> dict:
    models_dir = "/uploads"
    output_dir = f"/output/{request.format}"
    file_path = os.path.join(models_dir, request.filename)
    output_path = os.path.join(output_dir, request.output_filename)

    os.makedirs(output_dir, exist_ok=True)
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Input IFC file {request.filename} not found")

    export_meta = _run_export_in_spawn_isolation(request, file_path, output_path)
    logger.info(
        "Exported %d elements with %s",
        export_meta["element_count"],
        request.attributes,
    )

    return {
        "success": True,
        "message": f"Successfully converted to {request.format.upper()}",
        "storage": "filesystem",
        "output_path": output_path,
    }


def run_csv_to_ifc_import(job_data: dict) -> dict:
    """Import CSV/XLSX/ODS changes back into an IFC.

    S3 mode: IFC pulled from `uploads/<ifc_filename>`, data from
    `output/<csv_filename>` (same legacy layout, just re-keyed), result pushed
    to `output/ifc_updated/<output_filename or derived>`.
    """
    try:
        request = IfcCsvImportRequest(**job_data)
        logger.info(
            "Starting import from %s into %s (object_storage=%s)",
            request.csv_filename, request.ifc_filename, s3.is_enabled(),
        )

        if s3.is_enabled():
            return _run_import_s3(request)
        return _run_import_filesystem(request)

    except FileNotFoundError:
        logger.exception("Input file missing")
        raise
    except Exception:
        logger.exception("Error importing CSV→IFC")
        raise


def _derive_updated_name(ifc_filename: str, output_filename: str | None) -> str:
    if output_filename:
        return output_filename
    base, ext = os.path.splitext(ifc_filename)
    return f"{base}_updated{ext}"


def _run_import_s3(request: IfcCsvImportRequest) -> dict:
    ifc_key = s3.build_upload_key(request.ifc_filename)
    ifc_pin = s3.pin_for(request, request.ifc_filename)
    # Treat csv_filename as a key under output/ (matches legacy data_input_dir=/output)
    csv_key = request.csv_filename.lstrip("/")
    if not csv_key.startswith("output/"):
        csv_key = f"output/{csv_key}"
    csv_pin = s3.pin_for(request, request.csv_filename)
    out_name = _derive_updated_name(request.ifc_filename, request.output_filename)
    out_key = s3.build_output_key("ifc_updated", out_name)

    with s3.download_to_tempfile(ifc_key, suffix=".ifc", version_id=ifc_pin) as ifc_tmp, \
         s3.download_to_tempfile(
             csv_key,
             suffix=os.path.splitext(request.csv_filename)[1] or ".csv",
             version_id=csv_pin,
         ) as data_tmp:
        fd, out_tmp = tempfile.mkstemp(suffix=".ifc")
        os.close(fd)
        try:
            _run_import_in_spawn_isolation(ifc_tmp, data_tmp, out_tmp)
            parent_pins = {}
            if ifc_pin:
                parent_pins[ifc_key] = ifc_pin
            if csv_pin:
                parent_pins[csv_key] = csv_pin
            audit = s3.upload_and_audit(
                out_tmp,
                key=out_key,
                operation="ifccsv_import",
                worker=WORKER_NAME,
                job_id=_current_job_id(),
                parents=[("input", ifc_key), ("reference", csv_key)],
                parent_version_ids=parent_pins or None,
                metadata={
                    "ifc_filename": request.ifc_filename,
                    "csv_filename": request.csv_filename,
                },
                content_type="application/x-step",
            )
        finally:
            try:
                os.remove(out_tmp)
            except FileNotFoundError:
                pass

    return {
        "success": True,
        "message": "Data changes successfully imported to IFC model",
        "storage": "s3",
        "bucket": s3.bucket_name(),
        "output_key": out_key,
        "output_path": f"s3://{s3.bucket_name()}/{out_key}",
        "sha256": audit["sha256"],
        "size_bytes": audit["size_bytes"],
        "audit_id": audit["audit_id"],
    }


def _run_import_filesystem(request: IfcCsvImportRequest) -> dict:
    models_dir = "/uploads"
    data_input_dir = "/output"
    ifc_output_dir = "/output/ifc_updated"

    ifc_path = os.path.join(models_dir, request.ifc_filename)
    data_path = os.path.join(data_input_dir, request.csv_filename)
    out_name = _derive_updated_name(request.ifc_filename, request.output_filename)
    output_ifc_path = os.path.join(ifc_output_dir, out_name)

    os.makedirs(os.path.dirname(output_ifc_path), exist_ok=True)
    if not os.path.exists(ifc_path):
        raise FileNotFoundError(f"Input IFC file {request.ifc_filename} not found")
    if not os.path.exists(data_path):
        raise FileNotFoundError(f"Input data file {request.csv_filename} not found")

    _run_import_in_spawn_isolation(ifc_path, data_path, output_ifc_path)

    return {
        "success": True,
        "message": "Data changes successfully imported to IFC model",
        "storage": "filesystem",
        "output_path": output_ifc_path,
    }
