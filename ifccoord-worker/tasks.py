import logging
import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional, Tuple
from rq import get_current_job
from shared.classes import IfcCoordRequest
from shared import object_storage as s3

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WORKER_NAME = "ifccoord-worker"

# Map produced artifact extensions to sensible S3 content types. Patched IFCs
# are STEP, BCF is a zip container, proposals/manifest are JSON.
_ARTIFACT_CONTENT_TYPES = {
    ".ifc": "application/x-step",
    ".ifczip": "application/x-step",
    ".bcf": "application/octet-stream",
    ".bcfzip": "application/octet-stream",
    ".json": "application/json",
}


def _current_job_id() -> Optional[str]:
    try:
        job = get_current_job()
        return job.id if job else None
    except Exception:
        return None


def _content_type_for(path: str) -> str:
    return _ARTIFACT_CONTENT_TYPES.get(
        os.path.splitext(path)[1].lower(), "application/octet-stream"
    )


def resolve_ifc_path(filename: str) -> str:
    """Resolve an IFC file path safely under the mounted /uploads folder.

    Used only in filesystem mode (USE_OBJECT_STORAGE!=true). In S3 mode inputs
    are staged into a temp dir by :func:`_stage_input_from_s3` instead.
    """
    if not filename:
        return ""
    # If the exact path exists, use it
    if os.path.exists(filename):
        return filename
    # Strip common prefixes from api-gateway
    if filename.startswith("/app/uploads/"):
        filename = filename[13:]
    elif filename.startswith("uploads/"):
        filename = filename[8:]
    elif filename.startswith("/uploads/"):
        filename = filename[9:]

    return os.path.join("/uploads", filename)


def _stage_input_from_s3(
    ref: str, dest_dir: str, label: str, version_id: Optional[str] = None
) -> Tuple[str, str]:
    """Download an input IFC referenced by ``ref`` (filename, ``uploads/...``
    key, or ``s3://`` URI) into ``dest_dir`` and return ``(s3_key, local_path)``.

    Honors a pinned ``version_id`` so a coordination job always consumes the
    exact bytes the caller intended.
    """
    key = s3.normalize_input_key(ref)
    if not s3.object_exists(key, version_id=version_id):
        raise FileNotFoundError(
            f"{label} not found in object storage: s3://{s3.bucket_name()}/{key}"
            + (f"?versionId={version_id}" if version_id else "")
        )
    local_path = os.path.join(dest_dir, os.path.basename(key) or f"{label}.ifc")
    s3.download_to_path(key, local_path, version_id=version_id)
    logger.info("[s3] staged %s → %s (key=%s)", label, local_path, key)
    return key, local_path


def _resolve_policy(request: IfcCoordRequest, scratch_dir: str, policy_cls):
    """Resolve the coordination policy for this job.

    Precedence: inline policy → absolute local path → image-bundled
    ``/app/scenarios/coord`` → mounted ``/uploads`` → S3 object. Returns a
    ``(policy, policy_path)`` tuple where exactly one (or neither) is set, to be
    forwarded to ``run_coordination``.
    """
    if request.policy_inline:
        return policy_cls.from_dict(request.policy_inline), None
    if not request.policy_path:
        return None, None

    pp = request.policy_path

    # Absolute path already on disk.
    if os.path.isabs(pp) and os.path.exists(pp):
        return None, pp

    # Bundled scenario policy shipped inside the image.
    scenarios_path = os.path.join("/app", "scenarios", "coord", pp)
    if os.path.exists(scenarios_path):
        return None, scenarios_path

    # Locally mounted uploads (filesystem mode).
    uploads_path = os.path.join("/uploads", pp)
    if os.path.exists(uploads_path):
        return None, uploads_path

    # Object storage.
    if s3.is_enabled():
        key = s3.normalize_input_key(pp)
        if s3.object_exists(key):
            local_policy = os.path.join(
                scratch_dir, os.path.basename(key) or "policy.json"
            )
            s3.download_to_path(key, local_policy)
            logger.info("[s3] staged policy → %s (key=%s)", local_policy, key)
            return None, local_policy

    # Fallback: let run_coordination surface a clear missing-file error.
    return None, uploads_path


def _output_base_prefix(request: IfcCoordRequest, case_id: str) -> str:
    """S3 key prefix under which all artifacts for this job are written."""
    subdir = (request.output_subdir or case_id).strip("/")
    return f"output/coord/{subdir}"


def _upload_artifacts_to_s3(
    result,
    base_dir: str,
    input_keys: List[str],
    parent_version_ids: Optional[Dict[str, str]],
    job_id: Optional[str],
) -> Dict[str, Any]:
    """Upload every produced coordination artifact to S3 and return a dict of
    ``<prefix>_key`` / ``<prefix>_path`` / ``<prefix>_audit_id`` entries.

    Patched IFCs get ``guid_role="patched"`` so they feed the GUID index;
    BCF/JSON artifacts skip GUID extraction (``guid_role=None``).
    """
    out: Dict[str, Any] = {}
    parents = [("input", k) for k in input_keys if k]

    # (result attribute, result-key prefix, guid_role)
    specs = [
        ("patched_ifc_a", "output", "patched"),
        ("patched_ifc_b", "patched_b", "patched"),
        ("bcf_path", "bcf", None),
        ("report_json_path", "proposals_json", None),
        ("manifest_path", "manifest", None),
        ("fixed_only_ifc_a", "fixed_only", "patched"),
    ]

    for attr, prefix, guid_role in specs:
        local = getattr(result, attr, None)
        if not local or not os.path.isfile(local):
            continue
        key = f"{base_dir}/{os.path.basename(local)}"
        audit = s3.upload_and_audit(
            local,
            key=key,
            operation="ifccoord",
            worker=WORKER_NAME,
            job_id=job_id,
            parents=parents,
            parent_version_ids=parent_version_ids or None,
            guid_role=guid_role,
            metadata={
                "artifact": prefix,
                "case_id": result.case_id,
                "original_filename": os.path.basename(local),
            },
            content_type=_content_type_for(local),
        )
        out[f"{prefix}_key"] = key
        out[f"{prefix}_path"] = f"s3://{s3.bucket_name()}/{key}"
        out[f"{prefix}_audit_id"] = audit.get("audit_id")
        if prefix == "output":
            out["output_filename"] = os.path.basename(local)
            out["output_size_bytes"] = audit.get("size_bytes")
            out["sha256"] = audit.get("sha256")
            out["version_id"] = audit.get("version_id")
        logger.info(
            "Uploaded ifccoord artifact %s → s3://%s/%s", prefix, s3.bucket_name(), key
        )

    return out


def run_coordination_task(job_data: dict) -> dict:
    """
    Process an IFC coordination/fixing job.

    When ``USE_OBJECT_STORAGE=true`` the inputs are pulled from MinIO/S3 into a
    temp dir and every produced artifact (patched IFCs, BCF, proposals JSON,
    manifest, fixed-only IFC) is uploaded back to S3 with audit lineage. This
    makes the dedicated ifccoord-worker usable from a separate worker VM that
    does not share ``/uploads`` with the control plane. Filesystem mode is kept
    as a fallback for single-host installs.
    """
    tmpdir: Optional[str] = None
    try:
        job = get_current_job()
        job_id = job.id if job else "manual"
        logger.info(f"Starting coordination job {job_id}")

        # Lazy import of ifc_coord modules to avoid import errors in non-worker environments
        from ifc_coord.runner import run_coordination
        from ifc_coord.policy import Policy

        # Parse and validate request
        request = IfcCoordRequest(**job_data)

        s3_enabled = s3.is_enabled()
        input_a_key: Optional[str] = None
        input_b_key: Optional[str] = None
        input_a_pin: Optional[str] = None
        input_b_pin: Optional[str] = None

        if s3_enabled:
            tmpdir = tempfile.mkdtemp(prefix="ifccoord-")
            inputs_dir = os.path.join(tmpdir, "inputs")
            os.makedirs(inputs_dir, exist_ok=True)
            input_a_pin = s3.pin_for(request, request.path_a)
            input_b_pin = s3.pin_for(request, request.path_b)
            input_a_key, path_a_resolved = _stage_input_from_s3(
                request.path_a, inputs_dir, "File A", input_a_pin
            )
            input_b_key, path_b_resolved = _stage_input_from_s3(
                request.path_b, inputs_dir, "File B", input_b_pin
            )
            output_dir = os.path.join(tmpdir, "out")
            os.makedirs(output_dir, exist_ok=True)
            policy_scratch = tmpdir
            logger.info("[s3] coordination staged into %s", tmpdir)
        else:
            path_a_resolved = resolve_ifc_path(request.path_a)
            path_b_resolved = resolve_ifc_path(request.path_b)
            if not os.path.exists(path_a_resolved):
                raise FileNotFoundError(f"File A not found: {request.path_a}")
            if not os.path.exists(path_b_resolved):
                raise FileNotFoundError(f"File B not found: {request.path_b}")
            subdir = request.output_subdir or f"job_{job_id}"
            output_dir = os.path.join("/output/coord", subdir)
            os.makedirs(output_dir, exist_ok=True)
            policy_scratch = output_dir

        # Resolve policy (inline / bundled / uploads / S3)
        policy, policy_path_resolved = _resolve_policy(request, policy_scratch, Policy)

        logger.info(
            f"Running coordination on {request.path_a} vs {request.path_b} (mode: {request.mode})"
        )
        logger.info(f"Output directory: {output_dir}")

        # Run coordination engine. work_root lives inside output_dir so it is
        # contained and (in S3 mode) cleaned up with the temp dir.
        result = run_coordination(
            path_a=path_a_resolved,
            path_b=path_b_resolved,
            work_root=os.path.join(output_dir, "work_root"),
            mode=request.mode,
            max_rounds=request.max_rounds,
            max_auto_apply=request.max_auto_apply,
            policy=policy,
            policy_path=policy_path_resolved,
            clash_options=request.clash_options,
            output_dir=output_dir,
            logger=logger,
        )

        summary_dict = result.summary.to_dict() if result.summary else {}

        def to_str(p):
            return str(p) if p else None

        output_payload: Dict[str, Any] = {
            "success": True,
            "message": "Coordination completed successfully",
            "case_id": result.case_id,
            "summary": summary_dict,
        }

        if s3_enabled:
            base_dir = _output_base_prefix(request, result.case_id)
            pins: Dict[str, str] = {}
            if input_a_key and input_a_pin:
                pins[input_a_key] = input_a_pin
            if input_b_key and input_b_pin:
                pins[input_b_key] = input_b_pin
            artifacts = _upload_artifacts_to_s3(
                result,
                base_dir,
                [input_a_key, input_b_key],
                pins,
                _current_job_id(),
            )
            output_payload.update(artifacts)
            output_payload.update(
                {
                    "storage": "s3",
                    "bucket": s3.bucket_name(),
                    "output_prefix": base_dir,
                    "input_keys": {"path_a": input_a_key, "path_b": input_b_key},
                }
            )
        else:
            output_payload.update(
                {
                    "storage": "filesystem",
                    "output_dir": to_str(output_dir),
                    "report_json_path": to_str(result.report_json_path),
                    "bcf_path": to_str(result.bcf_path),
                    "manifest_path": to_str(result.manifest_path),
                    "patched_ifc_a": to_str(result.patched_ifc_a),
                    "patched_ifc_b": to_str(result.patched_ifc_b),
                    "fixed_only_ifc_a": to_str(result.fixed_only_ifc_a),
                }
            )

        logger.info(
            f"Job {job_id} finished successfully. Applied count: {summary_dict.get('applied_count', 0)}"
        )
        return output_payload

    except Exception as e:
        logger.error(f"Error running coordination task: {str(e)}", exc_info=True)
        raise
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
