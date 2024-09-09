from fastapi import FastAPI, HTTPException, Depends, Request, UploadFile, File
from fastapi.security import APIKeyHeader
import aiohttp
from aiohttp import ClientTimeout
from typing import Dict, List
import os
import json
import asyncio
import ipaddress
import logging
import shutil
from shared.classes import (
    IfcConvertRequest,
    IfcCsvRequest,
    IfcClashRequest,
    IfcTesterRequest,
    IfcDiffRequest,
    IFC2JSONRequest
)  

# Add this at the beginning of your file
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Define the load_config function
def load_config():
    config_path = '/app/config.json'
    default_config = {
        'api_keys': [],
        'allowed_ip_ranges': ['127.0.0.1/32']  # Default to localhost only
    }
    
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        # Obscure API keys in logging
        safe_config = config.copy()
        if 'api_keys' in safe_config:
            safe_config['api_keys'] = ['*****' for _ in safe_config['api_keys']]
        logger.info(f"Loaded configuration: {safe_config}")
        return config
    except FileNotFoundError:
        logger.warning(f"Config file not found at {config_path}. Using default configuration.")
        return default_config
    except json.JSONDecodeError:
        logger.error(f"Error decoding {config_path}. Using default configuration.")
        return default_config


# Load configuration
config = load_config()
API_KEYS = config.get('api_keys', [])
ALLOWED_IP_RANGES = [ipaddress.ip_network(cidr) for cidr in config.get('allowed_ip_ranges', [])]
ALLOWED_UPLOADS: Dict[str, Dict[str, str]] = {
    "ifc": {"dir": "/app/uploads", "extensions": [".ifc"]},
    "ids": {"dir": "/app/uploads", "extensions": [".ids"]},
    "bcf": {"dir": "/app/uploads", "extensions": [".bcf", ".bcfzip"]}
}

app = FastAPI(
    title="IFC Pipeline API Gateway",
    description="API Gateway for a microservice-based IFC processing pipeline. This gateway orchestrates various IFC operations across multiple specialized services, including conversion, clash detection, CSV export, validation, and diff analysis.",
    version="1.0.0",
)
# Define service URLs
IFCCONVERT_URL = os.getenv("IFCCONVERT_URL", "http://ifcconvert")
IFCCSV_URL = os.getenv("IFCCSV_URL", "http://ifccsv")
IFCCLASH_URL = os.getenv("IFCCLASH_URL", "http://ifcclash")
IFCTESTER_URL = os.getenv("IFCTESTER_URL", "http://ifctester")
IFCDIFF_URL = os.getenv("IFCDIFF_URL", "http://ifcdiff")
IFC2JSON_URL = os.getenv("IFC2JSON_URL", "http://ifc2json")

# Set up API key header
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Define the verify_access function
async def verify_access(request: Request, api_key: str = Depends(api_key_header)):
    client_ip = ipaddress.ip_address(request.client.host)
    logger.info(f"Access attempt from IP: {client_ip}")
    
    if any(client_ip in ip_range for ip_range in ALLOWED_IP_RANGES):
        logger.info(f"Access granted to {client_ip} (IP in allowed range)")
        return True
    
    if not api_key:
        logger.warning(f"Access denied to {client_ip} (No API key provided)")
        raise HTTPException(status_code=403, detail="API key required")
    
    if api_key not in API_KEYS:
        logger.warning(f"Access denied to {client_ip} (Invalid API key)")
        raise HTTPException(status_code=403, detail="Invalid API key")
    
    logger.info(f"Access granted to {client_ip} (Valid API key)")
    return True

async def get_aiohttp_session():
    timeout = ClientTimeout(total=3600)
    return aiohttp.ClientSession(timeout=timeout)

async def make_request(url, data):
    async with await get_aiohttp_session() as session:
        async with session.post(url, json=data) as response:
            return await response.json()

@app.get("/health", tags=["Health"])
async def health_check():
    services = {
        "api-gateway": "healthy",
        "ifcconvert": IFCCONVERT_URL,
        "ifccsv": IFCCSV_URL,
        "ifcclash": IFCCLASH_URL,
        "ifctester": IFCTESTER_URL,
        "ifcdiff": IFCDIFF_URL
    }

    async def check_service(name, url):
        try:
            async with await get_aiohttp_session() as session:
                async with session.get(f"{url}/health") as response:
                    if response.status == 200:
                        return name, "healthy"
                    else:
                        return name, f"unhealthy (status code: {response.status})"
        except Exception as e:
            return name, f"unhealthy ({str(e)})"

    tasks = [check_service(name, url) for name, url in services.items() if name != "api-gateway"]
    results = await asyncio.gather(*tasks)

    health_status = dict(results)
    health_status["api-gateway"] = "healthy"

    return {"status": "healthy" if all(status == "healthy" for status in health_status.values()) else "unhealthy",
            "services": health_status}

@app.post("/ifcconvert", tags=["Conversion"])
async def ifcconvert(request: IfcConvertRequest, _: str = Depends(verify_access)):
    return await make_request(f"{IFCCONVERT_URL}/ifcconvert", request.dict())

@app.post("/ifccsv", tags=["Conversion"])
async def ifccsv(request: IfcCsvRequest, _: str = Depends(verify_access)):
    return await make_request(f"{IFCCSV_URL}/ifccsv", request.dict())

@app.post("/ifcclash", tags=["Clash Detection"])
async def ifcclash(request: IfcClashRequest, _: str = Depends(verify_access)):
    return await make_request(f"{IFCCLASH_URL}/ifcclash", request.model_dump())

@app.post("/ifctester", tags=["Validation"])
async def ifctester(request: IfcTesterRequest, _: str = Depends(verify_access)):
    return await make_request(f"{IFCTESTER_URL}/ifctester", request.dict())

@app.post("/ifcdiff", tags=["Diff"])
async def ifcdiff(request: IfcDiffRequest, _: str = Depends(verify_access)):
    return await make_request(f"{IFCDIFF_URL}/ifcdiff", request.dict())
    
@app.post("/ifc2json", tags=["Conversion"])
async def ifc2json(request: IFC2JSONRequest, _: str = Depends(verify_access)):
    return await make_request(f"{IFC2JSON_URL}/ifc2json", request.dict())

@app.get("/ifc2json/{filename}", tags=["Conversion"])
async def get_ifc2json(filename: str, _: str = Depends(verify_access)):
    output_path = f"/app/output/json/{filename}"
    if not os.path.exists(output_path):
        raise HTTPException(status_code=404, detail=f"File {filename} not found")
    
    try:
        with open(output_path, 'r') as file:
            json_content = json.load(file)
        return json_content
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Failed to parse the JSON file")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/list_directories", summary="List Available Directories and Files", tags=["File Operations"])
async def list_directories():
    """
    List directories and files in the /app/uploads/ and /app/output/ directories and their subdirectories.
    
    Returns:
        dict: A dictionary containing the directory structure and files.
    """
    base_dirs = ["/app/uploads", "/app/output"]
    directory_structure = {}

    for base_dir in base_dirs:
        try:
            for root, dirs, files in os.walk(base_dir):
                relative_path = os.path.relpath(root, "/app")
                
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                files = [f for f in files if not f.startswith('.') and f != '.gitkeep']

                current_dir = directory_structure
                for part in relative_path.split(os.sep):
                    current_dir = current_dir.setdefault(part, {})

                if files:
                    current_dir["files"] = files

        except Exception as e:
            return {"error": f"Error processing {base_dir}: {str(e)}"}

    return {"directory_structure": directory_structure}

@app.post("/upload/{file_type}", summary="Upload File", tags=["File Operations"])
async def upload_file(file_type: str, file: UploadFile = File(...)):
    """
    Upload a file to the appropriate directory based on its type.
    
    Args:
        file_type (str): The type of file being uploaded (e.g., 'ifc', 'ids').
        file (UploadFile): The file to upload.
    
    Returns:
        dict: A message indicating success or failure, and the full path to the uploaded file.
    """
    if file_type not in ALLOWED_UPLOADS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {file_type}")
    
    upload_config = ALLOWED_UPLOADS[file_type]
    if not any(file.filename.endswith(ext) for ext in upload_config["extensions"]):
        raise HTTPException(status_code=400, detail=f"File must have one of these extensions: {', '.join(upload_config['extensions'])}")
    
    upload_dir = upload_config["dir"]
    file_path = os.path.join(upload_dir, file.filename)
    
    try:
        os.makedirs(upload_dir, exist_ok=True)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        return {
            "message": f"{file_type.upper()} file {file.filename} uploaded successfully",
            "file_path": file_path
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload file: {str(e)}")