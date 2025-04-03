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
    models_dir = "/uploads"
    output_dir = "/output/diff"
    old_file_path = os.path.join(models_dir, request.old_file)
    new_file_path = os.path.join(models_dir, request.new_file)
    output_path = os.path.join(output_dir, request.output_file)

    if not os.path.exists(old_file_path):
        raise HTTPException(status_code=404, detail=f"Old file {request.old_file} not found")
    if not os.path.exists(new_file_path):
        raise HTTPException(status_code=404, detail=f"New file {request.new_file} not found")

    try:
        ifc_diff = IfcDiff(old_file_path, new_file_path, output_path)
        
        ifc_diff.diff()

        # Custom JSON serialization
        diff_results = {
            "added": [str(guid) for guid in ifc_diff.added],
            "deleted": [str(guid) for guid in ifc_diff.deleted],
            "changed": {str(guid): changes for guid, changes in ifc_diff.changed.items()},
            "moved": {str(guid): new_parent for guid, new_parent in ifc_diff.moved.items()},
            "renamed": {str(guid): new_name for guid, new_name in ifc_diff.renamed.items()}
        }

        # Save results to file
        with open(output_path, 'w') as json_file:
            json.dump(diff_results, json_file, indent=2)

        return {
            "success": True,
            "message": f"IFC diff completed successfully. Results saved to {request.output_file}",
            "results": diff_results
        }
    except Exception as e:
        logger.error(f"Error during IFC diff: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    

@app.get("/health")
async def health_check():
    return {"status": "healthy"}