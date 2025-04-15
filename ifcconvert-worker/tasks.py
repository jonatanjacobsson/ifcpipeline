import subprocess
import os
import logging
from shared.classes import IfcConvertRequest
from shared.db_client import save_conversion_result

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_ifcconvert(job_data: dict) -> dict:
    """
    Process an IFC conversion job using the IfcConvert executable.
    
    Args:
        job_data: Dictionary containing the job parameters conforming to IfcConvertRequest.
        
    Returns:
        Dictionary containing the conversion results.
    """
    try:
        # Parse the request from the job data
        request = IfcConvertRequest(**job_data)
        
        # Define paths (assuming they are absolute within the container context)
        input_path = request.input_filename 
        output_path = request.output_filename
        log_file_path = request.log_file

        # Generate default log file path if not provided
        if not log_file_path:
            # Ensure the default output directory exists before creating log path
            default_output_dir = "/output/converted" # Matching original service convention
            os.makedirs(default_output_dir, exist_ok=True) 
            
            input_basename = os.path.basename(input_path)
            log_filename = f"{os.path.splitext(input_basename)[0]}_convert.txt"
            log_file_path = os.path.join(default_output_dir, log_filename)
        else:
            # Ensure the specified log directory exists
             os.makedirs(os.path.dirname(log_file_path), exist_ok=True)

        # Ensure the output directory for the main conversion exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # Validate input file existence
        if not os.path.exists(input_path):
            logger.error(f"Input file not found: {input_path}")
            raise FileNotFoundError(f"Input file {input_path} not found")

        # Construct the IfcConvert command
        command = ["/usr/local/bin/IfcConvert"]
        
        # Add log format and file options first
        command.extend(["--log-format", "plain"])
        command.extend(["--log-file", log_file_path])

        # Include/exclude entities
        if request.include:
            command.extend(["--include", "entities"]) 
            command.extend(request.include)
        if request.exclude:
            command.extend(["--exclude", "entities"])
            command.extend(request.exclude)

        # Boolean flags
        if request.verbose: command.append("--verbose")
        if request.plan: command.append("--plan")
        if not request.model: command.append("--no-model")
        if request.weld_vertices: command.append("--weld-vertices")
        if request.use_world_coords: command.append("--use-world-coords")
        if request.convert_back_units: command.append("--convert-back-units")
        if request.sew_shells: command.append("--sew-shells")
        if request.merge_boolean_operands: command.append("--merge-boolean-operands")
        if request.disable_opening_subtractions: command.append("--disable-opening-subtractions")
        
        # Options with values
        if request.bounds: command.extend(["--bounds", request.bounds])
        
        # Input and output files must be last
        command.extend([input_path, output_path])

        logger.info(f"Running IfcConvert command: {' '.join(command)}")
        
        # Run the IfcConvert command
        result = subprocess.run(command, capture_output=True, text=True, check=False) # check=False to handle errors manually
        
        # Check for errors
        if result.returncode != 0:
            error_message = f"IfcConvert failed with return code {result.returncode}. Stderr: {result.stderr}"
            logger.error(error_message)
            # Try reading the log file for more details if it exists
            try:
                with open(log_file_path, 'r') as log_f:
                    log_content = log_f.read()
                logger.error(f"IfcConvert log ({log_file_path}):
{log_content}")
                error_message += f"
Log content:
{log_content}"
            except Exception as log_e:
                logger.error(f"Could not read log file {log_file_path}: {log_e}")
                
            raise RuntimeError(error_message) # Raise for RQ

        # Create a dictionary of conversion options for database storage
        conversion_options = {
            "verbose": request.verbose,
            "plan": request.plan,
            "model": request.model,
            "weld_vertices": request.weld_vertices,
            "use_world_coords": request.use_world_coords,
            "convert_back_units": request.convert_back_units,
            "sew_shells": request.sew_shells,
            "merge_boolean_operands": request.merge_boolean_operands,
            "disable_opening_subtractions": request.disable_opening_subtractions,
            "bounds": request.bounds,
            "include": request.include,
            "exclude": request.exclude,
            "log_file": log_file_path
        }
        
        # Save to database
        logger.info("Saving conversion result to database...")
        db_id = save_conversion_result(
            input_filename=request.input_filename,
            output_filename=output_path,
            conversion_options=conversion_options
        )

        # Success
        logger.info(f"File converted successfully to {output_path}")
        result_dict = {
            "success": True,
            "message": f"File converted successfully to {output_path}",
            "log_file": log_file_path,
            "stdout": result.stdout,
            "stderr": result.stderr # Might contain warnings even on success
        }
        
        # Add database ID if available
        if db_id:
            result_dict["db_id"] = db_id
            
        return result_dict

    except FileNotFoundError as e:
        logger.error(f"File not found error during IFC conversion: {str(e)}", exc_info=True)
        # Re-raise specific error for clarity in logs/RQ failure
        raise 
    except Exception as e:
        logger.error(f"Unexpected error during IFC conversion: {str(e)}", exc_info=True)
        # Re-raise for RQ to mark as failed
        raise 