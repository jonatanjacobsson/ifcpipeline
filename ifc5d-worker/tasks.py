import logging
import os
import ifcopenshell
import ifcopenshell.geom
from ifc5d import qto
from shared.classes import IfcQtoRequest

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

        # Define paths within the container
        models_dir = "/uploads"  # Assuming uploads are mounted here
        output_dir = "/output/qto" # Specific directory for QTO outputs
        input_file_path = os.path.join(models_dir, request.input_file)
        
        # Determine output path
        if request.output_file:
            # If an output filename is provided, use it in the standard output dir
            output_file_path = os.path.join(output_dir, request.output_file)
            logger.info(f"Output will be saved to specified file: {output_file_path}")
        else:
            # If no output filename, modify in place (conceptually) but save to output dir
            # To avoid modifying the original upload, we save to the output dir with a default name.
            base, ext = os.path.splitext(request.input_file)
            default_output_name = f"{base}_qto{ext}"
            output_file_path = os.path.join(output_dir, default_output_name)
            logger.info(f"No output file specified. Saving modified file to: {output_file_path}")
            
        # Ensure output directory exists
        os.makedirs(os.path.dirname(output_file_path), exist_ok=True)
        
        # Validate input file existence
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
        return {
            "success": True,
            "message": f"Quantities calculated and inserted. Results saved to {output_file_path}",
            "output_path": output_file_path
        }

    except FileNotFoundError as e:
        logger.error(f"File not found error during QTO calculation: {str(e)}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Error during QTO calculation: {str(e)}", exc_info=True)
        raise # Re-raise for RQ failure 