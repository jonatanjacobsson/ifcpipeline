from fastapi import FastAPI, HTTPException, Depends, Request, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
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
from redis import Redis
from rq import Queue
from rq.job import Job, JobStatus
from rq.worker import Worker
import aiohttp
from aiohttp import ClientTimeout

# Add this at the beginning of your file
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add this new dictionary to store download links
download_links: Dict[str, DownloadLink] = {}

# Configure Redis connection
redis_url = os.getenv('REDIS_URL', 'redis://redis:6379/0')
redis_conn = Redis.from_url(redis_url)

# Create RQ queues
default_queue = Queue('default', connection=redis_conn)
ifcconvert_queue = Queue('ifcconvert', connection=redis_conn)
ifccsv_queue = Queue('ifccsv', connection=redis_conn)
ifcclash_queue = Queue('ifcclash', connection=redis_conn)
ifctester_queue = Queue('ifctester', connection=redis_conn)
ifcdiff_queue = Queue('ifcdiff', connection=redis_conn)
ifc2json_queue = Queue('ifc2json', connection=redis_conn)
ifc5d_queue = Queue('ifc5d', connection=redis_conn)

# Define job status response model
class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    result: dict = None
    error: str = None

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

# Re-add get_aiohttp_session function needed for /download-from-url
async def get_aiohttp_session():
    timeout = ClientTimeout(total=3600) # Set a reasonable timeout for downloads
    return aiohttp.ClientSession(timeout=timeout)

@app.get("/health", tags=["Health"])
async def health_check():
    """Checks the health of the API Gateway, Redis, and Worker Queues."""
    health_status = {
        "api-gateway": "healthy",
        "redis": "unhealthy", 
        "ifcconvert_queue": "unhealthy",
        "ifcclash_queue": "unhealthy",
        "ifccsv_queue": "unhealthy",
        "ifctester_queue": "unhealthy",
        "ifcdiff_queue": "unhealthy",
        "ifc5d_queue": "unhealthy",
        "ifc2json_queue": "unhealthy",
        "default_queue": "unhealthy",
    }

    # Check Redis health
    try:
        redis_alive = redis_conn.ping()
        if redis_alive:
            health_status["redis"] = "healthy"
        else:
             logger.warning("Redis ping failed.")
    except Exception as e:
        logger.error(f"Redis connection error: {str(e)}")
        health_status["redis"] = f"unhealthy ({str(e)})"
        # If Redis is down, queues can't be checked
        overall_status = "unhealthy"
        return {"status": overall_status, "services": health_status}
        
    # Check Queue Health (requires Redis connection)
    all_queues = {
        "ifcconvert_queue": ifcconvert_queue,
        "ifcclash_queue": ifcclash_queue,
        "ifccsv_queue": ifccsv_queue,
        "ifctester_queue": ifctester_queue,
        "ifcdiff_queue": ifcdiff_queue,
        "ifc5d_queue": ifc5d_queue,
        "ifc2json_queue": ifc2json_queue,
        "default_queue": default_queue
    }
    
    try:
        # Fetch all registered workers
        workers = Worker.all(connection=redis_conn)
        active_queues_by_workers = set()
        for worker in workers:
            active_queues_by_workers.update(worker.queue_names())
        logger.info(f"Active workers are listening to queues: {active_queues_by_workers}")

        for key, queue_obj in all_queues.items():
            queue_name = queue_obj.name
            # Basic check: Does the queue exist in Redis?
            if redis_conn.exists(queue_obj.key):
                # More advanced check: Is at least one worker listening to this queue?
                if queue_name in active_queues_by_workers:
                    health_status[key] = "healthy"
                else:
                    health_status[key] = "degraded (queue exists, no active worker)"
                    logger.warning(f"Queue '{queue_name}' exists but no worker is actively listening.")
            else:
                health_status[key] = "unhealthy (queue key not found in Redis)"
                logger.warning(f"Queue key '{queue_obj.key}' not found in Redis for queue '{queue_name}'.")
                
    except Exception as e:
        logger.error(f"Error checking RQ queues/workers: {str(e)}")
        # Mark all unchecked queues as unknown or error state
        for key in all_queues.keys():
            if health_status[key] == "unhealthy": # Only update if not already checked
                 health_status[key] = f"error checking ({str(e)})"

    # Determine overall status
    # Healthy only if API Gateway, Redis, and all queues are healthy
    is_healthy = all(status == "healthy" for key, status in health_status.items() if key != "api-gateway")
    is_degraded = any("degraded" in status for status in health_status.values())
    
    if is_healthy and health_status["redis"] == "healthy": # Double check redis explicitly
        overall_status = "healthy"
    elif is_degraded:
        overall_status = "degraded"
    else:
        overall_status = "unhealthy"

    return {"status": overall_status, "services": health_status}

# Add endpoint to check job status
@app.get("/jobs/{job_id}/status", response_model=JobStatusResponse, tags=["Jobs"])
async def get_job_status(job_id: str, _: str = Depends(verify_access)):
    """
    Get the status of a job.
    
    Args:
        job_id (str): The ID of the job to check.
    
    Returns:
        JobStatusResponse: A response containing the job status and result if available.
    """
    try:
        job = Job.fetch(job_id, connection=redis_conn)
        status = job.get_status()
        
        response = {"job_id": job_id, "status": status}
        
        if status == JobStatus.FINISHED:
            response["result"] = job.result
        elif status == JobStatus.FAILED and job.exc_info:
            response["error"] = job.exc_info
            
        return response
    except Exception as e:
        logger.error(f"Error getting job status: {str(e)}")
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

@app.post("/ifcconvert", tags=["Conversion"])
async def ifcconvert(request: IfcConvertRequest, _: str = Depends(verify_access)):
    """
    Convert an IFC file to another format.
    
    Args:
        request (IfcConvertRequest): The request body containing the conversion parameters.
    
    Returns:
        dict: A dictionary containing the job ID.
    """
    try:
        # Enqueue job to the dedicated ifcconvert worker queue
        job = ifcconvert_queue.enqueue(
            "tasks.run_ifcconvert",  # Points directly to function in /app/tasks.py for ifcconvert-worker
            request.dict(),
            job_timeout="1h"  # Adjust timeout as needed
        )
        
        logger.info(f"Enqueued ifcconvert job with ID: {job.id}")
        return {"job_id": job.id}
    except Exception as e:
        logger.error(f"Error enqueueing ifcconvert job: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/ifccsv", tags=["Conversion"])
async def ifccsv(request: IfcCsvRequest, _: str = Depends(verify_access)):
    """
    Export IFC data to CSV, ODS, or XLSX.
    
    Args:
        request (IfcCsvRequest): The request body containing the export parameters.
    
    Returns:
        dict: A dictionary containing the job ID.
    """
    try:
        # Enqueue job to the dedicated ifccsv worker queue
        job = ifccsv_queue.enqueue(
            "tasks.run_ifc_to_csv_conversion", # Points to function in /app/tasks.py for ifccsv-worker
            request.dict(),
            job_timeout="1h"
        )
        
        logger.info(f"Enqueued ifccsv export job with ID: {job.id}")
        return {"job_id": job.id}
    except Exception as e:
        logger.error(f"Error enqueueing ifccsv export job: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/ifccsv/import", tags=["Conversion"])
async def import_csv_to_ifc(request: IfcCsvImportRequest, _: str = Depends(verify_access)):
    """
    Import data from CSV, ODS, or XLSX into an IFC model.
    
    Args:
        request (IfcCsvImportRequest): The request body containing the import parameters.
    
    Returns:
        dict: A dictionary containing the job ID.
    """
    try:
        # Enqueue job to the dedicated ifccsv worker queue
        job = ifccsv_queue.enqueue(
            "tasks.run_csv_to_ifc_import", # Points to function in /app/tasks.py for ifccsv-worker
            request.dict(),
            job_timeout="1h"
        )
        
        logger.info(f"Enqueued ifccsv import job with ID: {job.id}")
        return {"job_id": job.id}
    except Exception as e:
        logger.error(f"Error enqueueing ifccsv import job: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/ifcclash", tags=["Clash Detection"])
async def ifcclash(request: IfcClashRequest, _: str = Depends(verify_access)):
    """
    Detect clashes between IFC models.
    
    Args:
        request (IfcClashRequest): The request body containing the clash detection parameters.
    
    Returns:
        dict: A dictionary containing the job ID.
    """
    try:
        # Use the direct function path to the worker task
        job = ifcclash_queue.enqueue(
            "tasks.run_ifcclash_detection",  # Points directly to function in /app/tasks.py
            request.dict(),
            job_timeout="2h"  # Clash detection can be time-consuming
        )
        
        logger.info(f"Enqueued ifcclash job with ID: {job.id}")
        return {"job_id": job.id}
    except Exception as e:
        logger.error(f"Error enqueueing ifcclash job: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/ifctester", tags=["Validation"])
async def ifctester(request: IfcTesterRequest, _: str = Depends(verify_access)):
    """
    Validate an IFC file against IDS rules.
    
    Args:
        request (IfcTesterRequest): The request body containing the validation parameters.
    
    Returns:
        dict: A dictionary containing the job ID.
    """
    try:
        # Use the correct path now that tasks.py is directly in /app for this worker
        job = ifctester_queue.enqueue(
            "tasks.run_ifctester_validation", # Correct path relative to /app
            request.dict(),
            job_timeout="1h"
        )

        logger.info(f"Enqueued ifctester job with ID: {job.id}")
        return {"job_id": job.id}
    except Exception as e:
        logger.error(f"Error enqueueing ifctester job: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/ifcdiff", tags=["Diff"])
async def ifcdiff(request: IfcDiffRequest, _: str = Depends(verify_access)):
    """
    Compare two IFC files and generate a diff report.
    
    Args:
        request (IfcDiffRequest): The request body containing the diff parameters.
    
    Returns:
        dict: A dictionary containing the job ID.
    """
    try:
        # Enqueue job to the dedicated ifcdiff worker queue
        job = ifcdiff_queue.enqueue(
            "tasks.run_ifcdiff",  # Points to function in /app/tasks.py for ifcdiff-worker
            request.dict(),
            job_timeout="1h"
        )
        
        logger.info(f"Enqueued ifcdiff job with ID: {job.id}")
        return {"job_id": job.id}
    except Exception as e:
        logger.error(f"Error enqueueing ifcdiff job: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    
@app.post("/ifc2json", tags=["Conversion"])
async def ifc2json(request: IFC2JSONRequest, _: str = Depends(verify_access)):
    """
    Convert an IFC file to JSON.
    
    Args:
        request (IFC2JSONRequest): The request body containing the conversion parameters.
    
    Returns:
        dict: A dictionary containing the job ID.
    """
    try:
        # Enqueue job to the dedicated ifc2json worker queue
        job = ifc2json_queue.enqueue(
            "tasks.run_ifc_to_json_conversion", # Points to function in /app/tasks.py for ifc2json-worker
            request.dict(),
            job_timeout="1h"
        )
        
        logger.info(f"Enqueued ifc2json job with ID: {job.id}")
        return {"job_id": job.id}
    except Exception as e:
        logger.error(f"Error enqueueing ifc2json job: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

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

@app.post("/calculate-qtos", tags=["Analysis"])
async def calculate_qtos(request: IfcQtoRequest, _: str = Depends(verify_access)):
    """
    Calculate quantities for an IFC file and insert them back into the file.
    
    Args:
        request (IfcQtoRequest): The request body containing the calculation parameters.
    
    Returns:
        dict: A dictionary containing the job ID.
    """
    try:
        # Enqueue job to the dedicated ifc5d worker queue
        job = ifc5d_queue.enqueue(
            "tasks.run_qto_calculation", # Points to function in /app/tasks.py for ifc5d-worker
            request.dict(),
            job_timeout="1h"
        )
        
        logger.info(f"Enqueued calculate-qtos job with ID: {job.id}")
        return {"job_id": job.id}
    except Exception as e:
        logger.error(f"Error enqueueing calculate-qtos job: {str(e)}")
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
