from fastapi import FastAPI, HTTPException
from shared.classes import IfcQtoRequest
import ifcopenshell
import logging
import os
import ifcopenshell.geom
from ifc5d import qto

app = FastAPI()

# Add this at the beginning of your file
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@app.post("/calculate-qtos", summary="Calculate and Insert Quantities", tags=["Analysis"])
async def api_calculate_qtos(request: IfcQtoRequest):
    """
    Calculate quantities for an IFC file and insert them back into the file.
    
    Args:
        request (IfcQtoRequest): The request body containing the input file and optional output file.
    
    Returns:
        dict: A dictionary containing the success status and output file path.
    """
    models_dir = "/uploads"
    input_file_path = os.path.join(models_dir, request.input_file)
    
    logger.info(f"Received request to process file: {input_file_path}")
    logger.info(f"Current working directory: {os.getcwd()}")
    logger.info(f"Contents of /uploads: {os.listdir('/uploads')}")
    
    if not os.path.exists(input_file_path):
        logger.error(f"Input file not found: {input_file_path}")
        raise HTTPException(status_code=404, detail=f"Input file {request.input_file} not found")

    try:
        if request.output_file:
            output_file_path = os.path.join(models_dir, request.output_file)  # Keep output in the same directory
        else:
            output_file_path = input_file_path  # Use the input file path as the output path

        # Load the input IFC file
        ifc_file = ifcopenshell.open(input_file_path)

        # Get all elements in the file
        elements = set(ifc_file.by_type("IfcProduct"))

        # Calculate quantities using IfcOpenShell rules
        results = qto.quantify(ifc_file, elements, qto.rules["IFC4QtoBaseQuantities"])

        # Insert the calculated quantities into the IFC file
        qto.edit_qtos(ifc_file, results)

        # Save the modified IFC file
        try:
            ifc_file.write(output_file_path)
            logger.info(f"Successfully wrote IFC file to {output_file_path}")
        except Exception as write_error:
            logger.error(f"Failed to write IFC file to {output_file_path}: {str(write_error)}")
            raise HTTPException(status_code=500, detail=f"Failed to save the modified IFC file: {str(write_error)}")
        
        # Verify that the file was actually written
        if not os.path.exists(output_file_path):
            logger.error(f"IFC file was not created at {output_file_path}")
            raise HTTPException(status_code=500, detail="Failed to create the output IFC file")

        # After writing the file
        logger.info(f"Attempted to write file to: {output_file_path}")
        logger.info(f"File exists after write: {os.path.exists(output_file_path)}")
        logger.info(f"Contents of /uploads after operation: {os.listdir('/uploads')}")

        return {
            "success": True,
            "message": f"Quantities calculated and inserted successfully. Results saved to {output_file_path}",
            "output_file": output_file_path
        }
    except Exception as e:
        logger.error(f"Error during quantity calculation: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    return {"status": "healthy"}