import logging
import os
import shutil
from typing import Any, Dict
from rq import get_current_job
from shared.classes import IfcCoordRequest

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def resolve_ifc_path(filename: str) -> str:
    """Resolve an IFC file path safely under the mounted /uploads folder."""
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

def run_coordination_task(job_data: dict) -> dict:
    """
    Process an IFC coordination/fixing job.
    """
    try:
        # Get RQ job info
        job = get_current_job()
        job_id = job.id if job else "manual"
        logger.info(f"Starting coordination job {job_id}")

        # Lazy import of ifc_coord modules to avoid import errors in non-worker environments
        from ifc_coord.runner import run_coordination
        from ifc_coord.policy import Policy

        # Parse and validate request
        request = IfcCoordRequest(**job_data)

        # Resolve paths
        path_a_resolved = resolve_ifc_path(request.path_a)
        path_b_resolved = resolve_ifc_path(request.path_b)

        if not os.path.exists(path_a_resolved):
            raise FileNotFoundError(f"File A not found: {request.path_a}")
        if not os.path.exists(path_b_resolved):
            raise FileNotFoundError(f"File B not found: {request.path_b}")

        # Resolve output directory
        subdir = request.output_subdir or f"job_{job_id}"
        output_dir = os.path.join("/output/coord", subdir)
        os.makedirs(output_dir, exist_ok=True)

        # Resolve policy
        policy = None
        policy_path_resolved = None
        if request.policy_inline:
            policy = Policy.from_dict(request.policy_inline)
        elif request.policy_path:
            if os.path.isabs(request.policy_path):
                policy_path_resolved = request.policy_path
            else:
                uploads_path = os.path.join("/uploads", request.policy_path)
                scenarios_path = os.path.join("/app", "scenarios", "coord", request.policy_path)
                if os.path.exists(uploads_path):
                    policy_path_resolved = uploads_path
                elif os.path.exists(scenarios_path):
                    policy_path_resolved = scenarios_path
                else:
                    policy_path_resolved = uploads_path  # fallback

        logger.info(f"Running coordination on {request.path_a} vs {request.path_b} (mode: {request.mode})")
        logger.info(f"Output directory: {output_dir}")

        # Run coordination engine
        # We specify work_root inside output_dir so it is cleaned up/contained
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
        
        # Prepare response (convert Path objects to strings)
        def to_str(p):
            return str(p) if p else None

        output_payload = {
            "success": True,
            "message": "Coordination completed successfully",
            "case_id": result.case_id,
            "summary": summary_dict,
            "output_dir": to_str(output_dir),
            "report_json_path": to_str(result.report_json_path),
            "bcf_path": to_str(result.bcf_path),
            "manifest_path": to_str(result.manifest_path),
            "patched_ifc_a": to_str(result.patched_ifc_a),
            "patched_ifc_b": to_str(result.patched_ifc_b),
            "fixed_only_ifc_a": to_str(result.fixed_only_ifc_a),
        }

        logger.info(f"Job {job_id} finished successfully. Applied count: {summary_dict.get('applied_count', 0)}")
        return output_payload

    except Exception as e:
        logger.error(f"Error running coordination task: {str(e)}", exc_info=True)
        raise
