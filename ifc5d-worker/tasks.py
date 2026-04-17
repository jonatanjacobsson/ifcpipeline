import logging
import os
import tempfile
import shutil
import ifcopenshell
import ifcopenshell.geom
from ifc5d import qto
from shared.classes import IfcQtoRequest
from shared import object_storage as s3

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WORKER_NAME = "ifc5d-worker"


def _current_job_id():
    try:
        from rq import get_current_job
        job = get_current_job()
        return job.id if job else None
    except Exception:
        return None

def run_qto_calculation(job_data: dict) -> dict:
    """
    Calculate quantities for elements in an IFC file using ifc5d rules 
    and insert them back into a new or the original IFC file.
    
    Args:
        job_data: Dictionary containing job parameters conforming to IfcQtoRequest.
        
    Returns:
        Dictionary containing the operation results.
    """
    try:
        request = IfcQtoRequest(**job_data)
        logger.info(f"Starting QTO calculation job for input: {request.input_file}")

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
            s3.get_client().download_file(Bucket=s3.bucket_name(), Key=input_key, Filename=input_file_path)
            s3_ctx = {"tmpdir": tmpdir, "output_key": output_key, "input_key": input_key}
            logger.info("[s3] staged ifc5d input, output → s3://%s/%s", s3.bucket_name(), output_key)
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
                logger.error(f"Input IFC file not found: {input_file_path}")
                raise FileNotFoundError(f"Input IFC file {request.input_file} not found")
            logger.info(f"Input file found: {input_file_path}")

        # Load the input IFC file
        logger.info("Loading IFC file...")
        ifc_file = ifcopenshell.open(input_file_path)
        logger.info("IFC file loaded.")

        # Get elements to process (e.g., all IfcProduct)
        # TODO: Allow filtering elements via request?
        elements = set(ifc_file.by_type("IfcProduct"))
        logger.info(f"Processing {len(elements)} IfcProduct elements.")

        # Calculate quantities
        # TODO: Make the rule selection configurable via request?
        qto_rule = qto.rules.get("IFC4QtoBaseQuantities")
        if not qto_rule:
             logger.error("QTO rule 'IFC4QtoBaseQuantities' not found in ifc5d library.")
             raise ValueError("Required QTO rule not found.")
             
        logger.info("Calculating quantities using rule: IFC4QtoBaseQuantities")
        qto_results = qto.quantify(ifc_file, elements, qto_rule)
        logger.info(f"Quantification results generated for {len(qto_results)} elements.")

        # Insert calculated quantities back into the IFC model
        logger.info("Inserting calculated quantities into IFC model...")
        qto.edit_qtos(ifc_file, qto_results)
        logger.info("Quantities inserted.")

        # Save the modified IFC file
        logger.info(f"Saving modified IFC file to: {output_file_path}")
        try:
            ifc_file.write(output_file_path)
            logger.info("Successfully wrote modified IFC file.")
        except Exception as write_error:
            logger.error(f"Failed to write modified IFC file to {output_file_path}: {str(write_error)}", exc_info=True)
            # Raise a more specific error if possible, otherwise re-raise for RQ
            raise RuntimeError(f"Failed to save the modified IFC file: {str(write_error)}")
        
        # Verify file creation (optional but good practice)
        if not os.path.exists(output_file_path):
            logger.error(f"Output IFC file was expected but not found at {output_file_path}")
            # This indicates a problem despite write not throwing an error
            raise RuntimeError("Output IFC file was not created successfully.")

        logger.info(f"QTO calculation job completed successfully for {request.input_file}")
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
                    metadata={
                        "rule": "IFC4QtoBaseQuantities",
                        "element_count": len(elements),
                        "qto_result_count": len(qto_results),
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

    except FileNotFoundError as e:
        logger.error(f"File not found error during QTO calculation: {str(e)}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Error during QTO calculation: {str(e)}", exc_info=True)
        raise # Re-raise for RQ failure 