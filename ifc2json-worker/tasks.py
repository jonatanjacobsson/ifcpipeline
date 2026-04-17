import logging
import os
import subprocess
import tempfile
import shutil
from shared.classes import IFC2JSONRequest
from shared import object_storage as s3

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WORKER_NAME = "ifc2json-worker"


def _current_job_id():
    try:
        from rq import get_current_job
        job = get_current_job()
        return job.id if job else None
    except Exception:
        return None

def run_ifc_to_json_conversion(job_data: dict) -> dict:
    """
    Convert an IFC file to JSON format using the ConvertIfc2Json external tool.
    
    Args:
        job_data: Dictionary containing job parameters conforming to IFC2JSONRequest.
        
    Returns:
        Dictionary containing the conversion results.
    """
    try:
        request = IFC2JSONRequest(**job_data)
        logger.info(f"Starting IFC to JSON conversion for: {request.filename}")

        s3_ctx = None
        if s3.is_enabled():
            tmpdir = tempfile.mkdtemp(prefix="ifc2json-")
            input_key = s3.normalize_input_key(request.filename)
            output_key = s3.normalize_output_key(request.output_filename, "json")
            input_path = os.path.join(tmpdir, os.path.basename(input_key) or "input.ifc")
            output_path = os.path.join(tmpdir, os.path.basename(output_key) or "output.json")
            s3.get_client().download_file(Bucket=s3.bucket_name(), Key=input_key, Filename=input_path)
            s3_ctx = {"tmpdir": tmpdir, "output_key": output_key, "input_key": input_key}
            logger.info("[s3] staged ifc2json input → %s, output → s3://%s/%s", input_path, s3.bucket_name(), output_key)
        else:
            input_dir = "/uploads"
            output_dir = "/output/json"
            input_path = os.path.join(input_dir, request.filename)
            output_path = os.path.join(output_dir, request.output_filename)
            os.makedirs(output_dir, exist_ok=True)
            if not os.path.exists(input_path):
                logger.error(f"Input IFC file not found: {input_path}")
                raise FileNotFoundError(f"Input IFC file {request.filename} not found")
            logger.info(f"Input file found: {input_path}")

        # Construct the command for the external tool
        # Assumes ConvertIfc2Json is in the PATH or at a known location (/ConvertIfc2Json based on original Dockerfile)
        command = ["/ConvertIfc2Json", input_path, output_path]
        logger.info(f"Running command: {' '.join(command)}")

        # Run the conversion tool
        result = subprocess.run(command, capture_output=True, text=True, check=False)

        # Check for errors
        if result.returncode != 0:
            error_message = f"ConvertIfc2Json tool failed with return code {result.returncode}. Stderr: {result.stderr}"
            logger.error(error_message)
            raise RuntimeError(error_message) # Raise for RQ

        # Verify output file creation
        if not os.path.exists(output_path):
             logger.error(f"Output JSON file was expected but not found at {output_path}")
             raise RuntimeError("Output JSON file was not created successfully by the tool.")

        logger.info(f"Successfully converted {request.filename} to {output_path}")
        result = {
            "success": True,
            "message": f"File converted successfully to JSON.",
            "output_path": output_path,
        }
        if s3_ctx is not None:
            try:
                audit = s3.upload_and_audit(
                    output_path,
                    key=s3_ctx["output_key"],
                    operation="ifc2json",
                    worker=WORKER_NAME,
                    job_id=_current_job_id(),
                    parents=[("input", s3_ctx["input_key"])],
                    metadata={
                        "tool": "ConvertIfc2Json",
                        "input_filename": request.filename,
                    },
                    content_type="application/json",
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

    except FileNotFoundError as e:
        logger.error(f"File not found error during IFC to JSON conversion: {str(e)}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Error during IFC to JSON conversion: {str(e)}", exc_info=True)
        raise # Re-raise for RQ failure 