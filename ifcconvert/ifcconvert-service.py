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
    input_path = request.input_filename  # Use the full path as provided
    output_path = request.output_filename  # Use the full path as provided

    # Generate default log file path if not provided
    if not request.log_file:
        input_basename = os.path.basename(input_path)
        log_filename = f"{os.path.splitext(input_basename)[0]}_convert.txt"
        request.log_file = os.path.join("/output/converted", log_filename)

    # Ensure the output directory exists
    os.makedirs(os.path.dirname(request.log_file), exist_ok=True)

    if not os.path.exists(input_path):
        raise HTTPException(status_code=404, detail=f"File {input_path} not found")

    try:
        command = ["/usr/local/bin/IfcConvert"]
        
        # Add log format and file options
        command.extend(["--log-format", "plain"])
        command.extend(["--log-file", request.log_file])

        # Modified include/exclude handling
        if request.include:
            command.extend(["--include", "entities"])  # Add 'entities' keyword
            command.extend(request.include)  # Add each entity type as a separate argument
        if request.exclude:
            command.extend(["--exclude", "entities"])  # Add 'entities' keyword
            command.extend(request.exclude)  # Add each entity type as a separate argument

        # ... rest of the options remain the same ...
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
            "log_file": request.log_file,
            "stdout": result.stdout,
            "stderr": result.stderr
        }
    except Exception as e:
        logger.error(f"Error during IFC conversion: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    return {"status": "healthy"}