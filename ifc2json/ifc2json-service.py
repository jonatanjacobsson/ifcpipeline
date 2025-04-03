from fastapi import FastAPI, HTTPException
from shared.classes import IFC2JSONRequest
import json
import subprocess
import os

app = FastAPI()

@app.post("/ifc2json", summary="Convert IFC to JSON", tags=["Conversion"])
async def api_ifc2json(request: IFC2JSONRequest):
    """
    Convert an IFC file to JSON format using the ConvertIfc2Json CLI tool.
    
    Args:
        request (IFC2JSONRequest): The request body containing conversion parameters.
    
    Returns:
        dict: A dictionary containing the conversion results, including the JSON content.
    """
    # Define directories
    input_dir = "/uploads"
    output_dir = "/output/json"
    input_path = os.path.join(input_dir, request.filename)
    output_path = os.path.join(output_dir, request.output_filename)

    if not os.path.exists(input_path):
        raise HTTPException(status_code=404, detail=f"File {request.filename} not found")

    try:
        os.makedirs(output_dir, exist_ok=True)
        
        # Run the ConvertIfc2Json CLI tool
        result = subprocess.run(["/ConvertIfc2Json", input_path, output_path], capture_output=True, text=True)
        
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Conversion failed: {result.stderr}")

        return {
            "success": True,
            "message": f"{request.output_filename}"
        }
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Failed to parse the generated JSON file")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}