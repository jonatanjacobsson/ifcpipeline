import logging
import os
import json
import tempfile

from shared.classes import IfcTesterRequest
from shared.db_client import save_tester_result
from shared import audit_db
from shared import object_storage as s3
import ifcopenshell
import ifctester
from ifctester import reporter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WORKER_NAME = "ifctester-worker"


def _current_job_id():
    try:
        from rq import get_current_job
        job = get_current_job()
        return job.id if job else None
    except Exception:
        return None


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


def _tester_rows_from_report(report: dict):
    """Best-effort projection of an ifctester JSON report into
    `(ifc_guid, ids_rule, passed, reason)` tuples suitable for
    `audit_db.record_tester_results`.

    The report shape varies between ifctester versions, so this helper is
    defensive: it tolerates missing fields and never raises.

    `ids_rule` is a `spec_name|req_description` compound to keep the unique
    index (object_version_id, ifc_guid, ids_rule) tight without dropping
    rule context.
    """
    if not isinstance(report, dict):
        return
    specs = report.get("specifications") or []
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        spec_name = str(spec.get("name") or spec.get("description") or "spec").strip()
        for req in spec.get("requirements") or []:
            if not isinstance(req, dict):
                continue
            req_desc = str(req.get("description") or req.get("name") or "req").strip()
            rule = f"{spec_name}|{req_desc}"[:500]
            passing = _ids_collect_guids(req.get("passed_entities"))
            for guid in passing:
                yield (guid, rule, True, None)
            for entity in (req.get("failed_entities") or []):
                guid = _ids_guid(entity)
                if guid:
                    reason = entity.get("reason") if isinstance(entity, dict) else None
                    yield (guid, rule, False, (str(reason)[:500] if reason else None))


def _ids_collect_guids(entities):
    if not entities:
        return
    for e in entities:
        g = _ids_guid(e)
        if g:
            yield g


def _ids_guid(entity):
    if isinstance(entity, str) and len(entity) == 22:
        return entity
    if isinstance(entity, dict):
        return entity.get("GlobalId") or entity.get("global_id") or entity.get("guid")
    return None


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
    ifc_pin = s3.pin_for(request, request.ifc_filename)
    ids_pin = s3.pin_for(request, request.ids_filename)
    out_key = s3.build_output_key("ids", request.output_filename)
    out_suffix = os.path.splitext(request.output_filename)[1] or (
        ".json" if request.report_type == "json" else ".html"
    )

    with s3.download_to_tempfile(ifc_key, suffix=".ifc", version_id=ifc_pin) as ifc_tmp, \
         s3.download_to_tempfile(ids_key, suffix=".ids", version_id=ids_pin) as ids_tmp:
        fd, out_tmp = tempfile.mkstemp(suffix=out_suffix)
        os.close(fd)
        try:
            payload, test_results, passed, failed = _validate_and_report(
                ifc_tmp, ids_tmp, out_tmp, request.report_type
            )
            parent_pins = {}
            if ifc_pin:
                parent_pins[ifc_key] = ifc_pin
            if ids_pin:
                parent_pins[ids_key] = ids_pin
            audit = s3.upload_and_audit(
                out_tmp,
                key=out_key,
                operation="ifctester",
                worker=WORKER_NAME,
                job_id=_current_job_id(),
                parents=[("input", ifc_key), ("reference", ids_key)],
                parent_version_ids=parent_pins or None,
                metadata={
                    "report_type": request.report_type,
                    "pass_count": passed,
                    "fail_count": failed,
                    "total_specifications": passed + failed,
                },
                content_type="application/json" if request.report_type == "json" else "text/html",
                # Tester output doesn't index into object_guids; we write our
                # own richer rows into tester_results below.
                guid_role=None,
            )
            # Direct-write tester_results rows for the IFC input (anchored
            # on its audit row, not the report). The guid-level audit
            # survives even if the report JSON gets pruned.
            if audit.get("audit_id") and request.report_type == "json":
                try:
                    rows = list(_tester_rows_from_report(test_results))
                    if rows:
                        audit_db.record_tester_results(audit["audit_id"], rows)
                except Exception as e:
                    logger.warning("tester_results write failed: %s", e)
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
        "sha256": audit["sha256"],
        "size_bytes": audit["size_bytes"],
        "audit_id": audit["audit_id"],
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
