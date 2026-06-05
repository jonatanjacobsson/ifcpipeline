"""IFC → Fragments (.frag) worker for ifcpipeline.

Downloads a pinned IFC from object storage, calls the Node fragmenter
sidecar (``POST /convert`` with ``{ ifc_url }`` or raw bytes), and uploads
the resulting ``.frag`` to ``output/frag/<name>.frag`` with audit lineage.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from typing import Optional

import httpx
from shared import object_storage as s3
from shared.classes import FragmentsRequest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WORKER_NAME = "ifcfrag-worker"
FRAGMENTER_URL = os.environ.get("FRAGMENTER_URL", "http://fragmenter:4001").rstrip("/")
FRAGMENTER_TIMEOUT = float(os.environ.get("FRAGMENTER_TIMEOUT_SECONDS", "600"))


def _current_job_id() -> Optional[str]:
    try:
        from rq import get_current_job

        job = get_current_job()
        return job.id if job else None
    except Exception:
        return None


def _default_frag_name(input_filename: str) -> str:
    base = os.path.basename(s3.normalize_input_key(input_filename))
    stem, _ext = os.path.splitext(base)
    return f"{stem or 'model'}.frag"


def _call_fragmenter(*, ifc_url: str, filename: str, model_id: Optional[str] = None) -> bytes:
    payload = {"ifc_url": ifc_url, "filename": filename}
    if model_id:
        payload["id"] = model_id
    with httpx.Client(timeout=FRAGMENTER_TIMEOUT) as client:
        resp = client.post(
            f"{FRAGMENTER_URL}/convert",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"fragmenter HTTP {resp.status_code}: {(resp.text or '')[:512]}"
        )
    data = resp.content or b""
    if not data:
        raise RuntimeError("fragmenter returned empty payload")
    return data


def _call_fragmenter_bytes(ifc_bytes: bytes, *, filename: str) -> bytes:
    headers = {
        "Content-Type": "application/octet-stream",
        "Content-Length": str(len(ifc_bytes)),
        "x-filename": filename,
    }
    with httpx.Client(timeout=FRAGMENTER_TIMEOUT) as client:
        resp = client.post(
            f"{FRAGMENTER_URL}/convert",
            content=ifc_bytes,
            headers=headers,
        )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"fragmenter HTTP {resp.status_code}: {(resp.text or '')[:512]}"
        )
    data = resp.content or b""
    if not data:
        raise RuntimeError("fragmenter returned empty payload")
    return data


def run_ifcfrag(job_data: dict) -> dict:
    """Convert an IFC object in S3 to a sibling ``output/frag/*.frag`` derivative."""
    request = FragmentsRequest(**job_data)
    job_id = _current_job_id()
    logger.info(
        "ifcfrag job %s input=%s output=%s",
        job_id,
        request.input_filename,
        request.output_filename,
    )

    if not s3.is_enabled():
        raise RuntimeError("object storage is disabled; ifcfrag requires S3")

    input_key = s3.normalize_input_key(request.input_filename)
    input_pin = s3.pin_for(request, request.input_filename)
    out_name = request.output_filename or _default_frag_name(request.input_filename)
    output_key = s3.normalize_output_key(out_name, "frag")

    tmpdir = tempfile.mkdtemp(prefix="ifcfrag-")
    frag_path = os.path.join(tmpdir, os.path.basename(output_key) or "output.frag")
    input_basename = os.path.basename(input_key) or "input.ifc"

    try:
        ifc_url = s3.presigned_get_url(
            input_key,
            expires_in=int(os.environ.get("FRAGMENTER_IFC_URL_EXPIRY", "3600")),
            bucket=s3.bucket_name(),
        )
        if input_pin:
            # presigned_get_url doesn't take version_id — use Params manually
            client = s3.get_client()
            ifc_url = client.generate_presigned_url(
                ClientMethod="get_object",
                Params={
                    "Bucket": s3.bucket_name(),
                    "Key": input_key,
                    "VersionId": input_pin,
                },
                ExpiresIn=int(os.environ.get("FRAGMENTER_IFC_URL_EXPIRY", "3600")),
            )

        try:
            frag_bytes = _call_fragmenter(
                ifc_url=ifc_url,
                filename=input_basename,
                model_id=os.path.splitext(input_basename)[0],
            )
        except Exception as exc:
            logger.warning(
                "fragmenter ifc_url fetch failed (%s); falling back to streamed bytes",
                exc,
            )
            input_path = os.path.join(tmpdir, input_basename)
            s3.download_to_path(input_key, input_path, version_id=input_pin)
            with open(input_path, "rb") as fh:
                raw = fh.read()
            frag_bytes = _call_fragmenter_bytes(raw, filename=input_basename)

        with open(frag_path, "wb") as fh:
            fh.write(frag_bytes)

        parent_pins = (
            {input_key: input_pin} if input_pin else None
        )
        audit = s3.upload_and_audit(
            frag_path,
            key=output_key,
            operation="ifcfrag",
            worker=WORKER_NAME,
            job_id=job_id,
            parents=[("input", input_key)],
            parent_version_ids=parent_pins,
            metadata={
                "input_filename": request.input_filename,
                "input_key": input_key,
                "input_version_id": input_pin,
                "tool": "cde-fragmenter",
            },
            content_type="application/octet-stream",
            guid_role=None,
        )

        input_head = s3.head_metadata(input_key, version_id=input_pin)

        return {
            "success": True,
            "message": "Fragments generated successfully",
            "storage": "s3",
            "bucket": s3.bucket_name(),
            "input_key": input_key,
            "input_version_id": input_pin,
            "input_sha256": (input_head or {}).get("sha256"),
            "input_size_bytes": (input_head or {}).get("size_bytes"),
            "output_key": output_key,
            "output_path": f"s3://{s3.bucket_name()}/{output_key}",
            "frag_bytes": len(frag_bytes),
            "sha256": audit.get("sha256"),
            "size_bytes": audit.get("size_bytes"),
            "version_id": audit.get("version_id"),
            "audit_id": audit.get("audit_id"),
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
