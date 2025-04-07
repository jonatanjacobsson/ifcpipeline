from fastapi import FastAPI, HTTPException, Depends
from shared.classes import IfcDiffRequest
import ifcopenshell
from ifcdiff import IfcDiff
import logging
import json
import os

app = FastAPI()

# Add this at the beginning of your file
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@app.post("/ifcdiff", summary="Compare Two IFC Files", tags=["Analysis"])
async def api_ifcdiff(request: IfcDiffRequest):
    """
    Compare two IFC files and generate a diff report.
    
    Args:
        request (IfcDiffRequest): The request body containing the files to compare.
    
    Returns:
        dict: A dictionary containing the diff results and success status.
    """
    logger.info(f"Received ifcdiff request for old_file='{request.old_file}', new_file='{request.new_file}', output_file='{request.output_file}', relationships={request.relationships}, is_shallow={request.is_shallow}, filter_elements='{request.filter_elements}'")
    
    models_dir = "/uploads"
    output_dir = "/output/diff"
    old_file_path = os.path.join(models_dir, request.old_file)
    new_file_path = os.path.join(models_dir, request.new_file)
    output_path = os.path.join(output_dir, request.output_file)
    
    logger.info(f"Checking existence of files: old='{old_file_path}', new='{new_file_path}'")

    if not os.path.exists(old_file_path):
        logger.error(f"Old file not found: {old_file_path}")
        raise HTTPException(status_code=404, detail=f"Old file {request.old_file} not found")
    if not os.path.exists(new_file_path):
        logger.error(f"New file not found: {new_file_path}")
        raise HTTPException(status_code=404, detail=f"New file {request.new_file} not found")
        
    logger.info("Input files found.")

    try:
        logger.info(f"Opening IFC files: old='{old_file_path}', new='{new_file_path}'")
        old_ifc_file = ifcopenshell.open(old_file_path)
        new_ifc_file = ifcopenshell.open(new_file_path)
        logger.info("IFC files opened successfully.")
        
        logger.info(f"Initializing IfcDiff with parameters: relationships={request.relationships}, is_shallow={request.is_shallow}, filter_elements='{request.filter_elements}'")
        ifc_diff = IfcDiff(
            old_ifc_file, 
            new_ifc_file, 
            relationships=request.relationships, 
            is_shallow=request.is_shallow, 
            filter_elements=request.filter_elements
        )
        
        logger.info("Starting IfcDiff.diff() process...")
        ifc_diff.diff()
        logger.info("IfcDiff.diff() process completed.")

        logger.info(f"Saving diff results to {output_path}...")
        ifc_diff.export(output_path)
        logger.info("Diff results saved successfully.")

        return {
            "success": True,
            "message": f"IFC diff completed successfully. Results saved to {request.output_file}",
        }
    except Exception as e:
        logger.error(f"Error occurred during IFC diff process: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    

@app.get("/health")
async def health_check():
    return {"status": "healthy"}