import logging
import os
import ifcopenshell
import ifcopenshell.util.selector
import ifccsv
from shared.classes import IfcCsvRequest, IfcCsvImportRequest
from shared import object_storage as s3

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WORKER_NAME = "ifccsv-worker"


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
    output_key = s3.build_output_key(request.format, request.output_filename)
    suffix = os.path.splitext(request.filename)[1] or ".ifc"

    with s3.download_to_tempfile(input_key, suffix=suffix) as ifc_tmp:
        model = ifcopenshell.open(ifc_tmp)
        elements = (
            ifcopenshell.util.selector.filter_elements(model, request.query)
            if request.query else model.by_type("IfcProduct")
        )
        logger.info("Processing %d elements with %s", len(elements), request.attributes)

        out_suffix = os.path.splitext(request.output_filename)[1] or f".{request.format}"
        import tempfile
        fd, out_tmp = tempfile.mkstemp(suffix=out_suffix)
        os.close(fd)
        try:
            converter = ifccsv.IfcCsv()
            converter.export(model, elements, request.attributes)
            if request.format == "csv":
                converter.export_csv(out_tmp, delimiter=request.delimiter)
            elif request.format == "ods":
                converter.export_ods(out_tmp)
            elif request.format == "xlsx":
                converter.export_xlsx(out_tmp)
            else:
                raise ValueError(f"Unsupported format: {request.format}")
            audit = s3.upload_and_audit(
                out_tmp,
                key=output_key,
                operation="ifccsv",
                worker=WORKER_NAME,
                job_id=_current_job_id(),
                parents=[("input", input_key)],
                metadata={
                    "format": request.format,
                    "query": request.query,
                    "delimiter": request.delimiter,
                    "attribute_count": len(request.attributes or []),
                    "element_count": len(elements),
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

    model = ifcopenshell.open(file_path)
    elements = (
        ifcopenshell.util.selector.filter_elements(model, request.query)
        if request.query else model.by_type("IfcProduct")
    )
    logger.info("Processing %d elements with %s", len(elements), request.attributes)

    converter = ifccsv.IfcCsv()
    converter.export(model, elements, request.attributes)
    if request.format == "csv":
        converter.export_csv(output_path, delimiter=request.delimiter)
    elif request.format == "ods":
        converter.export_ods(output_path)
    elif request.format == "xlsx":
        converter.export_xlsx(output_path)
    else:
        raise ValueError(f"Unsupported format: {request.format}")

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
    import tempfile

    ifc_key = s3.build_upload_key(request.ifc_filename)
    # Treat csv_filename as a key under output/ (matches legacy data_input_dir=/output)
    csv_key = request.csv_filename.lstrip("/")
    if not csv_key.startswith("output/"):
        csv_key = f"output/{csv_key}"
    out_name = _derive_updated_name(request.ifc_filename, request.output_filename)
    out_key = s3.build_output_key("ifc_updated", out_name)

    with s3.download_to_tempfile(ifc_key, suffix=".ifc") as ifc_tmp, \
         s3.download_to_tempfile(csv_key, suffix=os.path.splitext(request.csv_filename)[1] or ".csv") as data_tmp:
        model = ifcopenshell.open(ifc_tmp)
        importer = ifccsv.IfcCsv()
        importer.Import(model, data_tmp)

        fd, out_tmp = tempfile.mkstemp(suffix=".ifc")
        os.close(fd)
        try:
            model.write(out_tmp)
            audit = s3.upload_and_audit(
                out_tmp,
                key=out_key,
                operation="ifccsv_import",
                worker=WORKER_NAME,
                job_id=_current_job_id(),
                parents=[("input", ifc_key), ("reference", csv_key)],
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

    model = ifcopenshell.open(ifc_path)
    importer = ifccsv.IfcCsv()
    importer.Import(model, data_path)
    model.write(output_ifc_path)

    return {
        "success": True,
        "message": "Data changes successfully imported to IFC model",
        "storage": "filesystem",
        "output_path": output_ifc_path,
    }
