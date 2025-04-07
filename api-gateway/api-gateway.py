from fastapi import FastAPI, HTTPException, Depends, Request, UploadFile, File, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
import aiohttp
from aiohttp import ClientTimeout
from typing import Dict, List
import os
import json
import asyncio
import ipaddress
import logging
import shutil
import secrets
from datetime import datetime, timedelta
from shared.classes import (
    IfcConvertRequest,
    IfcCsvRequest,
    IfcClashRequest,
    IfcTesterRequest,
    IfcDiffRequest,
    IFC2JSONRequest,
    DownloadLink,
    DownloadRequest,
    IfcQtoRequest,
    IfcCsvImportRequest,
    DownloadUrlRequest,
)  
from pydantic import BaseModel, HttpUrl

# RQ Imports
import redis
from rq import Queue
from rq.job import Job
from rq.exceptions import NoSuchJobError

# Add this at the beginning of your file
logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'))
logger = logging.getLogger(__name__)

# Add this new dictionary to store download links
download_links: Dict[str, DownloadLink] = {}

# Define the load_config function
def load_config():
    # Default configuration
    default_config = {
        'api_keys': ["USE_ENV_VAR"],
        'allowed_ip_ranges': ['127.0.0.1/32']  # Default to localhost only
    }
    
    # Load configuration from environment variables
    env_api_key = os.getenv('IFC_PIPELINE_API_KEY')
    env_allowed_ip_ranges = os.getenv('IFC_PIPELINE_ALLOWED_IP_RANGES')
    
    config = default_config.copy()
    
    # Add API key from environment if available
    if env_api_key:
        config['api_keys'] = [env_api_key]
        logger.info("API key loaded from environment variable")
    
    # Add IP ranges from environment if available
    if env_allowed_ip_ranges:
        config['allowed_ip_ranges'] = env_allowed_ip_ranges.split(',')
        logger.info(f"Allowed IP ranges loaded from environment: {config['allowed_ip_ranges']}")
    
    # Log configuration (with redacted API keys)
    safe_config = config.copy()
    if 'api_keys' in safe_config and safe_config['api_keys']:
        safe_config['api_keys'] = ['*****' for _ in safe_config['api_keys']]
    logger.info(f"Using configuration: {safe_config}")
    
    return config


# Load configuration
config = load_config()
API_KEYS = config.get('api_keys', [])
ALLOWED_IP_RANGES = [ipaddress.ip_network(cidr) for cidr in config.get('allowed_ip_ranges', [])]
ALLOWED_UPLOADS: Dict[str, Dict[str, str]] = {
    "ifc": {"dir": "/uploads", "extensions": [".ifc"]},
    "ids": {"dir": "/uploads", "extensions": [".ids"]},
    "bcf": {"dir": "/uploads", "extensions": [".bcf", ".bcfzip"]}
}

app = FastAPI(
    title="IFC Pipeline API Gateway",
    description="API Gateway for a microservice-based IFC processing pipeline. This gateway orchestrates various IFC operations across multiple specialized services, including conversion, clash detection, CSV export, validation, and diff analysis.",
    version="1.0.0",
)

# Replace the existing CORS middleware configuration with:
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://e107e5b8-c6dc-4a30-af51-e7d0e1e5988c.lovableproject.com",
        "https://ifcpipeline.byggstyrning.se",
        "https://cde-gatekeeper.lovable.app",
        # Add any other specific domains you need
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=3600,
)

# Define service URLs
IFCCONVERT_URL = os.getenv("IFCCONVERT_URL", "http://ifcconvert")
IFCCSV_URL = os.getenv("IFCCSV_URL", "http://ifccsv")
IFCCLASH_URL = os.getenv("IFCCLASH_URL", "http://ifcclash")
IFCTESTER_URL = os.getenv("IFCTESTER_URL", "http://ifctester")
IFC2JSON_URL = os.getenv("IFC2JSON_URL", "http://ifc2json")
IFC5D_URL = os.getenv("IFC5D_URL", "http://ifc5d")

# Redis and RQ Setup
REDIS_HOST = os.getenv("REDIS_HOST", "localhost") # Default to localhost if not set, expecting 'redis' in Docker
REDIS_PORT = 6379
QUEUE_NAME = "ifcdiff-tasks"

try:
    redis_conn = redis.Redis(host=REDIS_HOST, port=REDIS_PORT)
    redis_conn.ping() # Check connection
    logger.info(f"Successfully connected to Redis at {REDIS_HOST}:{REDIS_PORT}")
    ifc_diff_queue = Queue(QUEUE_NAME, connection=redis_conn)
except redis.exceptions.ConnectionError as e:
    logger.error(f"Failed to connect to Redis at {REDIS_HOST}:{REDIS_PORT}. Error: {e}")
    # Depending on requirements, you might want to exit or handle this differently
    redis_conn = None
    ifc_diff_queue = None

# Set up API key header
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Define the verify_access function
async def verify_access(request: Request, api_key: str = Depends(api_key_header)):
    client_ip = ipaddress.ip_address(request.client.host)
    logger.info(f"Access attempt from IP: {client_ip}")
    
    # Debug log for troubleshooting IP ranges
    for ip_range in ALLOWED_IP_RANGES:
        logger.info(f"Checking if {client_ip} is in allowed range {ip_range}")
        if client_ip in ip_range:
            logger.info(f"Access granted to {client_ip} (IP in allowed range {ip_range})")
            return True
    
    logger.info(f"IP {client_ip} not in any allowed ranges, checking API key")
    
    # Only check API key if not from allowed IP range
    if not api_key:
        logger.warning(f"Access denied to {client_ip} (No API key provided and not in allowed IP range)")
        raise HTTPException(status_code=403, detail="API key required")
    
    if api_key not in API_KEYS:
        logger.warning(f"Access denied to {client_ip} (Invalid API key and not in allowed IP range)")
        raise HTTPException(status_code=403, detail="Invalid API key")
    
    logger.info(f"Access granted to {client_ip} (Valid API key)")
    return True

async def get_aiohttp_session():
    timeout = ClientTimeout(total=3600)
    return aiohttp.ClientSession(timeout=timeout)

async def make_request(url, data):
    # Ensure redis connection is available before proceeding
    if redis_conn is None:
         raise HTTPException(status_code=503, detail="Redis service unavailable")

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
        "ifc5d": IFC5D_URL,
        "redis": f"{REDIS_HOST}:{REDIS_PORT}" # Add Redis status
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

    tasks = [check_service(name, url) for name, url in services.items() if name not in ["api-gateway", "redis"]]
    results = await asyncio.gather(*tasks)

    health_status = dict(results)
    health_status["api-gateway"] = "healthy"

    # Check Redis connection separately
    try:
        if redis_conn and redis_conn.ping():
             health_status["redis"] = "healthy"
        else:
             health_status["redis"] = "unhealthy (connection failed)"
    except Exception as e:
        health_status["redis"] = f"unhealthy ({str(e)})"

    return {"status": "healthy" if all(status == "healthy" for status in health_status.values()) else "unhealthy",
            "services": health_status}

@app.post("/ifcconvert", tags=["Conversion"])
async def ifcconvert(request: IfcConvertRequest, _: str = Depends(verify_access)):
    return await make_request(f"{IFCCONVERT_URL}/ifcconvert", request.dict())

@app.post("/ifccsv", tags=["Conversion"])
async def ifccsv(request: IfcCsvRequest, _: str = Depends(verify_access)):
    return await make_request(f"{IFCCSV_URL}/ifccsv", request.dict())

@app.post("/ifccsv/import", tags=["Conversion"])
async def import_csv_to_ifc(request: IfcCsvImportRequest, _: str = Depends(verify_access)):
    return await make_request(f"{IFCCSV_URL}/ifccsv/import", request.dict())

@app.post("/ifcclash", tags=["Clash Detection"])
async def ifcclash(request: IfcClashRequest, _: str = Depends(verify_access)):
    return await make_request(f"{IFCCLASH_URL}/ifcclash", request.model_dump())

@app.post("/ifctester", tags=["Validation"])
async def ifctester(request: IfcTesterRequest, _: str = Depends(verify_access)):
    return await make_request(f"{IFCTESTER_URL}/ifctester", request.dict())

@app.post("/ifcdiff", status_code=status.HTTP_202_ACCEPTED, tags=["Diff"])
async def enqueue_ifcdiff(request: IfcDiffRequest, _: str = Depends(verify_access)):
    """
    Enqueue an IFC diff task.

    Receives the diff request and adds it to the background processing queue.
    Returns a job ID for status tracking.
    """
    if ifc_diff_queue is None:
        raise HTTPException(status_code=503, detail="RQ queue service unavailable due to Redis connection issue.")

    try:
        # Enqueue the job
        job = ifc_diff_queue.enqueue(
            'perform_ifc_diff', # Simple function name (available via the entrypoint script)
            request.old_file,
            request.new_file,
            request.output_file,
            request.relationships,
            request.is_shallow,
            request.filter_elements,
            job_timeout='2h' # Example: Set a 2-hour timeout for the job
        )
        logger.info(f"Enqueued ifcdiff job {job.id} for old='{request.old_file}', new='{request.new_file}'")
        return {"job_id": job.id}
    except Exception as e:
        logger.error(f"Failed to enqueue ifcdiff job: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to enqueue job: {str(e)}")

# New endpoint to check job status
@app.get("/tasks/{job_id}/status", tags=["Tasks"])
async def get_task_status(job_id: str, _: str = Depends(verify_access)):
    """
    Check the status of a background task.
    """
    if redis_conn is None:
        raise HTTPException(status_code=503, detail="Redis service unavailable")
    try:
        job = Job.fetch(job_id, connection=redis_conn)
    except NoSuchJobError:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")
    except Exception as e:
        logger.error(f"Error fetching job {job_id} status: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error checking job status.")

    status = job.get_status()
    result = None
    if status == 'finished':
        result = job.result
    elif status == 'failed':
        # Optionally include error details, be cautious about exposing too much
        result = job.exc_info # Contains traceback, might be too verbose/sensitive
        # Or provide a generic error message
        # result = "Job failed during execution."

    logger.info(f"Status check for job {job_id}: Status='{status}'")
    return {"job_id": job_id, "status": status, "result": result}

# New endpoint to get job result (if finished)
@app.get("/tasks/{job_id}/result", tags=["Tasks"])
async def get_task_result(job_id: str, _: str = Depends(verify_access)):
    """
    Get the result of a completed background task.
    If the result is a file path, consider providing a download link instead.
    """
    if redis_conn is None:
        raise HTTPException(status_code=503, detail="Redis service unavailable")
    try:
        job = Job.fetch(job_id, connection=redis_conn)
    except NoSuchJobError:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")
    except Exception as e:
        logger.error(f"Error fetching job {job_id} result: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error checking job result.")

    status = job.get_status()
    if status == 'finished':
        # The result from perform_ifc_diff is the relative output path
        # Construct the full path on the shared volume
        output_rel_path = job.result
        if output_rel_path and isinstance(output_rel_path, str):
             output_abs_path = os.path.join("/output", output_rel_path) # Assuming worker returns path relative to /output
             logger.info(f"Result for job {job_id}: File at '{output_abs_path}'")
             # Option 1: Return the path (client needs to know how to access shared volume)
             # return {"job_id": job_id, "status": status, "result": output_abs_path}

             # Option 2: Return file content if it's small/text (e.g., JSON diff)
             # Implement based on expected diff format

             # Option 3: Return a FileResponse to download the file directly
             if os.path.exists(output_abs_path):
                 return FileResponse(output_abs_path, filename=os.path.basename(output_rel_path))
             else:
                 logger.error(f"Result file for job {job_id} not found at expected path: {output_abs_path}")
                 raise HTTPException(status_code=404, detail=f"Result file for job {job_id} not found.")
        else:
             logger.warning(f"Job {job_id} finished but result is unexpected: {job.result}")
             return {"job_id": job_id, "status": status, "result": job.result} # Return raw result
    elif status == 'failed':
        logger.warning(f"Attempt to get result for failed job {job_id}")
        return JSONResponse(
            status_code=400,
            content={"job_id": job_id, "status": status, "error": "Job failed. Check status endpoint for details."}
        )
    else: # queued, started, deferred, scheduled
        logger.info(f"Result requested for job {job_id} which is not finished (Status: {status})")
        return JSONResponse(
            status_code=202, # Accepted or Processing
            content={"job_id": job_id, "status": status, "message": "Job is still processing."}
        )

@app.post("/ifc2json", tags=["Conversion"])
async def ifc2json(request: IFC2JSONRequest, _: str = Depends(verify_access)):
    return await make_request(f"{IFC2JSON_URL}/ifc2json", request.dict())

@app.get("/ifc2json/{filename}", tags=["Conversion"])
async def get_ifc2json(filename: str, _: str = Depends(verify_access)):
    output_path = f"/uploads/output/json/{filename}"
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
async def list_directories(_: str = Depends(verify_access)):
    """
    List directories and files in the /uploads/ and /examples/ and /output/ directories and their subdirectories.
    
    Returns:
        dict: A dictionary containing the directory structure and files.
    """
    base_dirs = ["/uploads", "/output", "/examples"]
    directory_structure = {}

    for base_dir in base_dirs:
        try:
            for root, dirs, files in os.walk(base_dir):
                relative_path = os.path.relpath(root, "/")
                
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
async def upload_file(file_type: str, file: UploadFile = File(...), _: str = Depends(verify_access)):
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

@app.post("/create_download_link", tags=["File Operations"])
async def create_download_link(request: DownloadRequest, _: str = Depends(verify_access)):
    """
    Create a temporary download link for a file.
    
    Args:
        request (DownloadRequest): The request containing the file path.
    
    Returns:
        dict: A dictionary containing the download token and expiry time.
    """
    file_path = request.file_path
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    token = secrets.token_urlsafe(32)
    expiry = datetime.now() + timedelta(minutes=30)
    
    download_links[token] = DownloadLink(file_path=file_path, token=token, expiry=expiry)
    
    return {"download_token": token, "expiry": expiry}

@app.get("/download/{token}", tags=["File Operations"])
async def download_file(token: str):
    """
    Download a file using a temporary token.
    
    Args:
        token (str): The temporary download token.
    
    Returns:
        FileResponse: The file to be downloaded.
    """
    if token not in download_links:
        raise HTTPException(status_code=404, detail="Invalid or expired download token")
    
    download_link = download_links[token]
    if datetime.now() > download_link.expiry:
        del download_links[token]
        raise HTTPException(status_code=404, detail="Download token has expired")
    
    file_path = download_link.file_path
    if not os.path.exists(file_path):
        del download_links[token]
        raise HTTPException(status_code=404, detail="File not found")
    
    return FileResponse(file_path, filename=os.path.basename(file_path))

@app.post("/calculate-qtos", tags=["Analysis"])
async def calculate_qtos(request: IfcQtoRequest, _: str = Depends(verify_access)):
    """
    Calculate quantities for an IFC file and insert them back into the file.
    
    Args:
        request (IfcQtoRequest): The request body containing the input file and optional output file.
    
    Returns:
        dict: The response from the IFC5D service.
    """
    return await make_request(f"{IFC5D_URL}/calculate-qtos", request.dict())

@app.post("/download-from-url", tags=["File Operations"])
async def download_from_url(request: DownloadUrlRequest, _: str = Depends(verify_access)):
    """
    Download a file from a URL and save it to the uploads directory.
    
    Args:
        request (DownloadUrlRequest): The request containing the download URL.
    
    Returns:
        dict: A message indicating success or failure, and the path to the downloaded file.
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        async with await get_aiohttp_session() as session:
            async with session.get(request.url, headers=headers, allow_redirects=True) as response:
                if response.status != 200:
                    raise HTTPException(
                        status_code=response.status,
                        detail=f"Failed to download file: HTTP {response.status}"
                    )
                
                # Get filename from URL or Content-Disposition header
                filename = None
                if 'Content-Disposition' in response.headers:
                    content_disposition = response.headers['Content-Disposition']
                    # Try to get filename from filename* parameter first (UTF-8 encoded)
                    if 'filename*=UTF-8' in content_disposition:
                        filename = content_disposition.split("filename*=UTF-8''")[-1].split(';')[0]
                    # Fall back to regular filename parameter
                    elif 'filename=' in content_disposition:
                        filename = content_disposition.split('filename=')[1].split(';')[0].strip('"\'')
                
                if not filename:
                    # Extract filename from URL path
                    url_path = str(request.url).split('?')[0]  # Remove query parameters
                    filename = url_path.split('/')[-1]
                
                # Clean up filename
                filename = filename.strip()
                if ';' in filename:
                    filename = filename.split(';')[0].strip()
                
                if not filename:
                    raise HTTPException(
                        status_code=400,
                        detail="Could not determine filename from URL or headers"
                    )
                
                # Use the provided output_filename if it exists, otherwise use the original filename
                if request.output_filename:
                    filename = request.output_filename
                
                # Always save to the uploads directory
                file_path = os.path.join("/uploads", filename)
                
                # Ensure uploads directory exists
                os.makedirs("/uploads", exist_ok=True)
                
                # Save the file
                with open(file_path, 'wb') as f:
                    while True:
                        chunk = await response.content.read(8192)  # Read in chunks
                        if not chunk:
                            break
                        f.write(chunk)
                
                return {
                    "message": f"File downloaded successfully as {filename}",
                    "file_path": file_path
                }
                
    except aiohttp.ClientError as e:
        raise HTTPException(status_code=500, detail=f"Failed to download file: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing file: {str(e)}")
