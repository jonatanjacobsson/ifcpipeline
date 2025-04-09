import logging
import os
import ifcopenshell
from ifcdiff import IfcDiff
from shared.classes import IfcDiffRequest

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_ifcdiff(job_data: dict) -> dict:
    """
    Compare two IFC files and generate a diff report using the ifcdiff library.
    
    Args:
        job_data: Dictionary containing job parameters conforming to IfcDiffRequest.
        
    Returns:
        Dictionary containing the diff results.
    """
    try:
        request = IfcDiffRequest(**job_data)
        logger.info(f"Starting ifcdiff job: old='{request.old_file}', new='{request.new_file}', output='{request.output_file}'")
        
        models_dir = "/uploads" # Standard mount point
        output_dir = "/output/diff" # Standard diff output directory
        old_file_path = os.path.join(models_dir, request.old_file)
        new_file_path = os.path.join(models_dir, request.new_file)
        output_path = os.path.join(output_dir, request.output_file)
        
        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)

        # Validate input file existence
        logger.info(f"Checking existence: old='{old_file_path}', new='{new_file_path}'")
        if not os.path.exists(old_file_path):
            logger.error(f"Old IFC file not found: {old_file_path}")
            raise FileNotFoundError(f"Old IFC file {request.old_file} not found")
        if not os.path.exists(new_file_path):
            logger.error(f"New IFC file not found: {new_file_path}")
            raise FileNotFoundError(f"New IFC file {request.new_file} not found")
        logger.info("Input files found.")

        # Open IFC files
        logger.info("Opening IFC files...")
        old_ifc_file = ifcopenshell.open(old_file_path)
        new_ifc_file = ifcopenshell.open(new_file_path)
        logger.info("IFC files opened successfully.")

        # Initialize IfcDiff
        logger.info(f"Initializing IfcDiff: relationships={request.relationships}, shallow={request.is_shallow}, filter='{request.filter_elements}'")
        ifc_diff_instance = IfcDiff(
            old_ifc_file, 
            new_ifc_file, 
            relationships=request.relationships, 
            is_shallow=request.is_shallow, 
            filter_elements=request.filter_elements
        )

        # Perform the diff operation
        logger.info("Running diff comparison...")
        ifc_diff_instance.diff()
        logger.info("Diff comparison completed.")

        # Export the results
        logger.info(f"Exporting diff results to {output_path}...")
        ifc_diff_instance.export(output_path)
        logger.info("Diff results exported successfully.")
        
        # The ifcdiff library writes the file directly, return success and path
        return {
            "success": True,
            "message": f"IFC diff completed. Results saved to {output_path}",
            "output_path": output_path
            # We could potentially load and return the JSON content from output_path if needed
            # For now, just confirming success and output location.
        }

    except FileNotFoundError as e:
        logger.error(f"File not found error during IFC diff: {str(e)}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Error during IFC diff: {str(e)}", exc_info=True)
        raise # Re-raise for RQ failure 