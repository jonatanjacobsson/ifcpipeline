from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import subprocess
import os
import logging
from shared.classes import IfcConvertRequest

app = FastAPI()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@app.post("/ifcconvert")
async def api_ifcconvert(request: IfcConvertRequest):
    models_dir = "/app/models"
    output_dir = "/app/output/converted"
    input_path = os.path.join(models_dir, request.input_filename)
    output_path = os.path.join(output_dir, request.output_filename)

    if not os.path.exists(input_path):
        raise HTTPException(status_code=404, detail=f"File {request.input_filename} not found")

    try:
        command = ["/usr/local/bin/IfcConvert"]
        
        # Add options based on the request
        if request.include:
            command.append("--include")
            command.extend(request.include)
        if request.exclude:
            command.append("--exclude")
            command.extend(request.exclude)
        if request.verbose:
            command.append("--verbose")
        if request.plan:
            command.append("--plan")
        if not request.model:
            command.append("--no-model")
        if request.weld_vertices:
            command.append("--weld-vertices")
        if request.use_world_coords:
            command.append("--use-world-coords")
        if request.convert_back_units:
            command.append("--convert-back-units")
        if request.sew_shells:
            command.append("--sew-shells")
        if request.merge_boolean_operands:
            command.append("--merge-boolean-operands")
        if request.disable_opening_subtractions:
            command.append("--disable-opening-subtractions")
        if request.bounds:
            command.extend(["--bounds", request.bounds])
        
        # Add input and output files
        command.extend([input_path, output_path])

        logger.info(f"Running IfcConvert command: {' '.join(command)}")
        
        # Run the IfcConvert command
        result = subprocess.run(command, capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error(f"IfcConvert failed: {result.stderr}")
            raise HTTPException(status_code=500, detail=f"IfcConvert failed: {result.stderr}")

        return {
            "success": True,
            "message": f"File converted successfully to {request.output_filename}",
            "stdout": result.stdout,
            "stderr": result.stderr
        }
    except Exception as e:
        logger.error(f"Error during IFC conversion: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    return {"status": "healthy"}