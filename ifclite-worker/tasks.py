import json
import logging
import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, Tuple

from shared.classes import IfcTesterRequest
from shared.db_client import save_tester_result
from shared import object_storage as s3

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WORKER_NAME = "ifclite-worker"
_IFC_LITE_BIN = os.environ.get("IFC_LITE_CLI", "ifc-lite")


def _current_job_id():
    try:
        from rq import get_current_job
        job = get_current_job()
        return job.id if job else None
    except Exception:
        return None


def _summary_spec_counts(summary: Dict[str, Any]) -> Tuple[int, int]:
    """Map IFClite `summarize()` output to (passed_specs, failed_specs)."""
    if not summary:
        return 0, 0
    passed = summary.get("passedSpecifications")
    failed = summary.get("failedSpecifications")
    total = summary.get("totalSpecifications")
    if passed is None and total is not None and failed is not None:
        passed = total - failed
    if failed is None and total is not None and passed is not None:
        failed = total - passed
    return int(passed or 0), int(failed or 0)


def _check_cli_available() -> None:
    if os.path.isfile(_IFC_LITE_BIN):
        if not os.access(_IFC_LITE_BIN, os.X_OK):
            raise RuntimeError(f"IFClite CLI is not executable: {_IFC_LITE_BIN}")
    elif shutil.which(_IFC_LITE_BIN) is None:
        raise RuntimeError(
            f"IFClite CLI not found (expected '{_IFC_LITE_BIN}' on PATH). "
            "Install @ifc-lite/cli in the worker image."
        )


def _run_cli(ifc_path: str, ids_path: str, output_path: str) -> Tuple[Dict[str, Any], int]:
    _check_cli_available()

    cmd = [_IFC_LITE_BIN, "ids", ifc_path, ids_path, "--json"]
    logger.info("Running: %s", " ".join(cmd))
    proc = subprocess.run(
        cmd, capture_output=True, text=True, check=False, cwd="/",
    )
    if proc.stderr:
        logger.info("ifc-lite stderr: %s", proc.stderr[:8000])

    stdout = (proc.stdout or "").strip()
    if not stdout:
        raise RuntimeError(
            f"ifc-lite produced no stdout (exit {proc.returncode}). stderr: {proc.stderr!r}"
        )
    if proc.returncode != 0:
        logger.warning("ifc-lite exited with code %s", proc.returncode)

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"ifc-lite output is not valid JSON: {e}. First 500 chars: {stdout[:500]!r}"
        ) from e

    summary = payload.get("summary") or {}
    report = payload.get("report")
    out_obj: Dict[str, Any] = {
        "engine": "ifc-lite",
        "summary": summary,
        "report": report,
        "cli_exit_code": proc.returncode,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(out_obj, f, indent=2, ensure_ascii=False)

    return out_obj, proc.returncode


def run_ifclite_ids_validation(job_data: dict) -> dict:
    """Validate IFC against IDS using the IFClite CLI (`ifc-lite ids ... --json`)."""
    try:
        request = IfcTesterRequest(**job_data)
        logger.info(
            "Processing ifclite IDS job: ifc=%s ids=%s (object_storage=%s)",
            request.ifc_filename, request.ids_filename, s3.is_enabled(),
        )
        if request.report_type != "json":
            raise ValueError(
                "ifclite IDS worker only supports report_type='json' (IFClite CLI emits JSON reports)"
            )
        if s3.is_enabled():
            return _run_s3(request)
        return _run_filesystem(request)
    except Exception:
        logger.exception("Error during ifclite IDS validation")
        raise


def _run_s3(request: IfcTesterRequest) -> dict:
    ifc_key = s3.build_upload_key(request.ifc_filename)
    ids_key = s3.build_upload_key(request.ids_filename)
    out_key = s3.build_output_key("ids-ifclite", request.output_filename)

    with s3.download_to_tempfile(ifc_key, suffix=".ifc") as ifc_tmp, \
         s3.download_to_tempfile(ids_key, suffix=".ids") as ids_tmp:
        fd, out_tmp = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            out_obj, exit_code = _run_cli(ifc_tmp, ids_tmp, out_tmp)
            summary = out_obj.get("summary") or {}
            passed, failed = _summary_spec_counts(summary)

            audit = s3.upload_and_audit(
                out_tmp,
                key=out_key,
                operation="ifclite",
                worker=WORKER_NAME,
                job_id=_current_job_id(),
                parents=[("input", ifc_key), ("reference", ids_key)],
                metadata={
                    "engine": "ifc-lite",
                    "pass_count": passed,
                    "fail_count": failed,
                    "total_specifications": summary.get("totalSpecifications"),
                    "cli_exit_code": exit_code,
                },
                content_type="application/json",
            )
        finally:
            try:
                os.remove(out_tmp)
            except FileNotFoundError:
                pass

    if failed > 0 and exit_code == 0:
        logger.warning(
            "ifc-lite exited 0 but summary shows failed specifications=%s",
            failed,
        )

    result = {
        "success": True,
        "storage": "s3",
        "bucket": s3.bucket_name(),
        "output_key": out_key,
        "output_path": f"s3://{s3.bucket_name()}/{out_key}",
        "validation_passed": failed == 0,
        "total_specifications": summary.get("totalSpecifications"),
        "passed_specifications": passed,
        "failed_specifications": failed,
        "report": json.dumps(out_obj, ensure_ascii=False),
        "cli_exit_code": exit_code,
        "sha256": audit["sha256"],
        "size_bytes": audit["size_bytes"],
        "audit_id": audit["audit_id"],
    }

    db_id = save_tester_result(
        ifc_filename=request.ifc_filename,
        ids_filename=request.ids_filename,
        output_filename=result["output_path"],
        test_results=out_obj,
        pass_count=passed,
        fail_count=failed,
    )
    if db_id:
        result["db_id"] = db_id

    logger.info("IFClite IDS ok → s3://%s/%s", s3.bucket_name(), out_key)
    return result


def _run_filesystem(request: IfcTesterRequest) -> dict:
    models_dir = "/uploads"
    ids_dir = "/uploads"
    output_dir = "/output/ids-ifclite"
    ifc_path = os.path.join(models_dir, request.ifc_filename)
    ids_path = os.path.join(ids_dir, request.ids_filename)
    output_path = os.path.join(output_dir, request.output_filename)

    if not os.path.exists(ifc_path):
        raise FileNotFoundError(f"IFC file {request.ifc_filename} not found")
    if not os.path.exists(ids_path):
        raise FileNotFoundError(f"IDS file {request.ids_filename} not found")
    os.makedirs(output_dir, exist_ok=True)

    out_obj, exit_code = _run_cli(ifc_path, ids_path, output_path)
    summary = out_obj.get("summary") or {}
    passed, failed = _summary_spec_counts(summary)

    if failed > 0 and exit_code == 0:
        logger.warning(
            "ifc-lite exited 0 but summary shows failed specifications=%s",
            failed,
        )

    result = {
        "success": True,
        "storage": "filesystem",
        "output_path": output_path,
        "validation_passed": failed == 0,
        "total_specifications": summary.get("totalSpecifications"),
        "passed_specifications": passed,
        "failed_specifications": failed,
        "report": json.dumps(out_obj, ensure_ascii=False),
        "cli_exit_code": exit_code,
    }

    db_id = save_tester_result(
        ifc_filename=request.ifc_filename,
        ids_filename=request.ids_filename,
        output_filename=output_path,
        test_results=out_obj,
        pass_count=passed,
        fail_count=failed,
    )
    if db_id:
        result["db_id"] = db_id

    logger.info("IFClite IDS ok → %s", output_path)
    return result
