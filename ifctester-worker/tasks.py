import logging
import os
import json
import tempfile

from shared.classes import IfcTesterRequest
from shared.db_client import save_tester_result
from shared import object_storage as s3
import ifcopenshell
import ifctester
from ifctester import reporter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_ifctester_validation(job_data: dict) -> dict:
    try:
        request = IfcTesterRequest(**job_data)
        logger.info(
            "Processing ifctester job: ifc=%s ids=%s (object_storage=%s)",
            request.ifc_filename, request.ids_filename, s3.is_enabled(),
        )
        if s3.is_enabled():
            return _run_s3(request)
        return _run_filesystem(request)
    except Exception:
        logger.exception("Error during ifctester validation")
        raise


def _validate_and_report(ifc_path: str, ids_path: str, output_path: str, report_type: str):
    my_ids = ifctester.ids.open(ids_path)
    my_ifc = ifcopenshell.open(ifc_path)
    my_ids.validate(my_ifc)

    total = len(my_ids.specifications)
    passed = sum(1 for spec in my_ids.specifications if spec.status)
    failed = total - passed

    if report_type == "json":
        json_reporter = reporter.Json(my_ids)
        json_reporter.report()
        json_reporter.to_file(output_path)
        test_results = json.loads(json_reporter.to_string())
        payload = {
            "success": True,
            "total_specifications": total,
            "passed_specifications": passed,
            "failed_specifications": failed,
            "report": json_reporter.to_string(),
        }
    elif report_type == "html":
        html_reporter = reporter.Html(my_ids)
        html_reporter.report()
        html_reporter.to_file(output_path)
        test_results = {
            "specifications": [
                {
                    "id": i,
                    "name": getattr(spec, "name", f"Spec {i}"),
                    "status": spec.status,
                    "description": getattr(spec, "description", ""),
                }
                for i, spec in enumerate(my_ids.specifications)
            ]
        }
        payload = {
            "success": True,
            "report": html_reporter.to_string(),
        }
    else:
        raise ValueError(f"Unsupported report_type: {report_type}")

    return payload, test_results, passed, failed


def _run_s3(request: IfcTesterRequest) -> dict:
    ifc_key = s3.build_upload_key(request.ifc_filename)
    ids_key = s3.build_upload_key(request.ids_filename)
    out_key = s3.build_output_key("ids", request.output_filename)
    out_suffix = os.path.splitext(request.output_filename)[1] or (
        ".json" if request.report_type == "json" else ".html"
    )

    with s3.download_to_tempfile(ifc_key, suffix=".ifc") as ifc_tmp, \
         s3.download_to_tempfile(ids_key, suffix=".ids") as ids_tmp:
        fd, out_tmp = tempfile.mkstemp(suffix=out_suffix)
        os.close(fd)
        try:
            payload, test_results, passed, failed = _validate_and_report(
                ifc_tmp, ids_tmp, out_tmp, request.report_type
            )
            s3.upload_from_path(out_tmp, out_key)
        finally:
            try:
                os.remove(out_tmp)
            except FileNotFoundError:
                pass

    payload.update({
        "storage": "s3",
        "bucket": s3.bucket_name(),
        "output_key": out_key,
        "output_path": f"s3://{s3.bucket_name()}/{out_key}",
    })

    db_id = save_tester_result(
        ifc_filename=request.ifc_filename,
        ids_filename=request.ids_filename,
        output_filename=payload["output_path"],
        test_results=test_results,
        pass_count=passed,
        fail_count=failed,
    )
    if db_id:
        payload["db_id"] = db_id

    logger.info("IfcTester ok → s3://%s/%s", s3.bucket_name(), out_key)
    return payload


def _run_filesystem(request: IfcTesterRequest) -> dict:
    models_dir = "/uploads"
    ids_dir = "/uploads"
    output_dir = "/output/ids"
    ifc_path = os.path.join(models_dir, request.ifc_filename)
    ids_path = os.path.join(ids_dir, request.ids_filename)
    output_path = os.path.join(output_dir, request.output_filename)

    if not os.path.exists(ifc_path):
        raise FileNotFoundError(f"IFC file {request.ifc_filename} not found")
    if not os.path.exists(ids_path):
        raise FileNotFoundError(f"IDS file {request.ids_filename} not found")
    os.makedirs(output_dir, exist_ok=True)

    payload, test_results, passed, failed = _validate_and_report(
        ifc_path, ids_path, output_path, request.report_type
    )
    payload.update({
        "storage": "filesystem",
        "output_path": output_path,
    })

    db_id = save_tester_result(
        ifc_filename=request.ifc_filename,
        ids_filename=request.ids_filename,
        output_filename=output_path,
        test_results=test_results,
        pass_count=passed,
        fail_count=failed,
    )
    if db_id:
        payload["db_id"] = db_id

    logger.info("IfcTester ok → %s", output_path)
    return payload
