import logging
import os
import shutil
import tempfile
from multiprocessing import get_context
from queue import Empty
from typing import Any, Optional

from shared.classes import IfcQtoRequest
from shared import object_storage as s3

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WORKER_NAME = "ifc5d-worker"


def _execute_qto_core(input_file_path: str, output_file_path: str) -> dict[str, int]:
    """Open IFC, quantify, edit QTOs, and write output."""
    import ifcopenshell
    from ifc5d import qto

    ifc_file = ifcopenshell.open(input_file_path)
    elements = set(ifc_file.by_type("IfcProduct"))
    qto_rule = qto.rules.get("IFC4QtoBaseQuantities")
    if not qto_rule:
        raise ValueError("Required QTO rule not found.")
    qto_results = qto.quantify(ifc_file, elements, qto_rule)
    qto.edit_qtos(ifc_file, qto_results)
    ifc_file.write(output_file_path)
    if not os.path.exists(output_file_path):
        raise RuntimeError("Output IFC file was not created successfully.")
    return {
        "element_count": len(elements),
        "qto_result_count": len(qto_results),
    }


def _isolated_qto_worker(result_queue, payload: dict) -> None:
    """Top-level spawn entry: isolate ifcopenshell/ifc5d from the rq work-horse."""
    import logging as _logging

    _logging.basicConfig(level=_logging.INFO)
    wlog = _logging.getLogger("ifc5d.isolated_qto")
    try:
        meta = _execute_qto_core(payload["input_file_path"], payload["output_file_path"])
        wlog.info(
            "QTO complete: %d elements, %d results",
            meta["element_count"],
            meta["qto_result_count"],
        )
        result_queue.put(("ok", meta))
    except Exception as e:
        import traceback as _tb

        tb_str = _tb.format_exc()
        try:
            result_queue.put(("err", f"{type(e).__name__}: {e}\n{tb_str}"))
        except Exception:
            pass
        wlog.exception("Isolated QTO worker failed")
        raise


def _run_in_spawn_isolation(
    worker_target,
    payload: dict,
    *,
    label: str = "default",
    operation: str = "ifc5d",
    result_timeout: int = 3600,
) -> dict[str, Any]:
    """Run ifcopenshell work in a spawn subprocess."""
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


def _run_qto_in_spawn_isolation(input_file_path: str, output_file_path: str) -> dict[str, int]:
    payload = {
        "input_file_path": input_file_path,
        "output_file_path": output_file_path,
    }
    return _run_in_spawn_isolation(
        _isolated_qto_worker,
        payload,
        label="qto",
        operation="ifc5d qto",
    )


def _current_job_id():
    try:
        from rq import get_current_job
        job = get_current_job()
        return job.id if job else None
    except Exception:
        return None


def run_qto_calculation(job_data: dict) -> dict:
    """Calculate quantities for elements in an IFC file and write a new IFC."""
    try:
        request = IfcQtoRequest(**job_data)
        logger.info("Starting QTO calculation job for input: %s", request.input_file)

        s3_ctx = None
        if s3.is_enabled():
            tmpdir = tempfile.mkdtemp(prefix="ifc5d-")
            input_key = s3.normalize_input_key(request.input_file)
            if request.output_file:
                output_key = s3.normalize_output_key(request.output_file, "qto")
            else:
                base, ext = os.path.splitext(os.path.basename(request.input_file))
                output_key = f"output/qto/{base}_qto{ext}"
            input_file_path = os.path.join(tmpdir, os.path.basename(input_key) or "input.ifc")
            output_file_path = os.path.join(tmpdir, os.path.basename(output_key))
            input_pin = s3.pin_for(request, request.input_file)
            s3.download_to_path(input_key, input_file_path, version_id=input_pin)
            s3_ctx = {
                "tmpdir": tmpdir,
                "output_key": output_key,
                "input_key": input_key,
                "input_pin": input_pin,
            }
            logger.info(
                "[s3] staged ifc5d input, output → s3://%s/%s",
                s3.bucket_name(),
                output_key,
            )
        else:
            models_dir = "/uploads"
            output_dir = "/output/qto"
            input_file_path = os.path.join(models_dir, request.input_file)
            if request.output_file:
                output_file_path = os.path.join(output_dir, request.output_file)
            else:
                base, ext = os.path.splitext(request.input_file)
                default_output_name = f"{base}_qto{ext}"
                output_file_path = os.path.join(output_dir, default_output_name)
            os.makedirs(os.path.dirname(output_file_path), exist_ok=True)
            if not os.path.exists(input_file_path):
                logger.error("Input IFC file not found: %s", input_file_path)
                raise FileNotFoundError(f"Input IFC file {request.input_file} not found")
            logger.info("Input file found: %s", input_file_path)

        logger.info("Running QTO in spawn-isolated subprocess")
        qto_meta = _run_qto_in_spawn_isolation(input_file_path, output_file_path)
        logger.info(
            "QTO calculation completed: %d elements, %d results",
            qto_meta["element_count"],
            qto_meta["qto_result_count"],
        )

        result = {
            "success": True,
            "message": f"Quantities calculated and inserted. Results saved to {output_file_path}",
            "output_path": output_file_path,
        }
        if s3_ctx is not None:
            try:
                audit = s3.upload_and_audit(
                    output_file_path,
                    key=s3_ctx["output_key"],
                    operation="ifc5d",
                    worker=WORKER_NAME,
                    job_id=_current_job_id(),
                    parents=[("input", s3_ctx["input_key"])],
                    parent_version_ids=(
                        {s3_ctx["input_key"]: s3_ctx["input_pin"]}
                        if s3_ctx.get("input_pin")
                        else None
                    ),
                    metadata={
                        "rule": "IFC4QtoBaseQuantities",
                        "element_count": qto_meta["element_count"],
                        "qto_result_count": qto_meta["qto_result_count"],
                    },
                    content_type="application/x-step",
                )
                result.update({
                    "storage": "s3",
                    "bucket": s3.bucket_name(),
                    "output_key": s3_ctx["output_key"],
                    "output_path": f"s3://{s3.bucket_name()}/{s3_ctx['output_key']}",
                    "sha256": audit["sha256"],
                    "size_bytes": audit["size_bytes"],
                    "audit_id": audit["audit_id"],
                })
            finally:
                shutil.rmtree(s3_ctx["tmpdir"], ignore_errors=True)
        return result

    except FileNotFoundError:
        logger.exception("File not found error during QTO calculation")
        raise
    except Exception:
        logger.exception("Error during QTO calculation")
        raise
