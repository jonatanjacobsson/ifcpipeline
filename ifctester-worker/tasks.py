import logging
import os
from shared.classes import IfcTesterRequest
import ifcopenshell
import ifctester
from ifctester import reporter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_ifctester_validation(job_data: dict) -> dict:
    try:
        request = IfcTesterRequest(**job_data)
        logger.info(f"Processing ifctester job for file: {request.ifc_filename}")

        models_dir = "/uploads"
        ids_dir = "/uploads"
        output_dir = "/output/ids"
        
        ifc_path = os.path.join(models_dir, request.ifc_filename)
        ids_path = os.path.join(ids_dir, request.ids_filename)
        output_path = os.path.join(output_dir, request.output_filename)
        report_type = request.report_type

        if not os.path.exists(ifc_path):
            raise FileNotFoundError(f"IFC file {request.ifc_filename} not found")
        if not os.path.exists(ids_path):
            raise FileNotFoundError(f"IDS file {request.ids_filename} not found")
            
        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)

        # Load the IDS file using the ifctester package
        my_ids = ifctester.ids.open(ids_path)

        # Open the IFC file
        my_ifc = ifcopenshell.open(ifc_path)

        # Validate IFC model against IDS requirements
        my_ids.validate(my_ifc)

        result = {}
        
        if report_type == "json":
            # Generate JSON report using the directly imported reporter module
            json_reporter = reporter.Json(my_ids)
            json_reporter.report()
            json_reporter.to_file(output_path)

            # Get a summary of the results
            total_specs = len(my_ids.specifications)
            passed_specs = sum(1 for spec in my_ids.specifications if spec.status)
            failed_specs = total_specs - passed_specs

            result = {
                "success": True,
                "total_specifications": total_specs,
                "passed_specifications": passed_specs,
                "failed_specifications": failed_specs,
                "report": json_reporter.to_string(),
                "output_path": output_path
            }
        
        elif report_type == "html":
            # Generate HTML report using the directly imported reporter module
            html_reporter = reporter.Html(my_ids)
            html_reporter.report()
            html_reporter.to_file(output_path)

            result = {
                "success": True,
                "report": html_reporter.to_string(),
                "output_path": output_path
            }
            
        logger.info(f"IfcTester validation successful. Report at: {output_path}")
        return result

    except Exception as e:
        logger.error(f"Error during ifctester validation: {str(e)}", exc_info=True)
        # Re-raise the exception so RQ marks the job as failed
        raise 