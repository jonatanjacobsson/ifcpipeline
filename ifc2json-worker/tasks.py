import logging
import os
import subprocess
from shared.classes import IFC2JSONRequest

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

        # Define paths within the container
        input_dir = "/uploads"
        output_dir = "/output/json"
        input_path = os.path.join(input_dir, request.filename)
        output_path = os.path.join(output_dir, request.output_filename)

        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)

        # Validate input file existence
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
        # Return success and the path to the generated JSON file
        return {
            "success": True,
            "message": f"File converted successfully to JSON.",
            "output_path": output_path
            # We don't read/return the JSON content itself to avoid memory issues with large files.
            # The caller can retrieve the file from the output_path if needed.
        }

    except FileNotFoundError as e:
        logger.error(f"File not found error during IFC to JSON conversion: {str(e)}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Error during IFC to JSON conversion: {str(e)}", exc_info=True)
        raise # Re-raise for RQ failure 