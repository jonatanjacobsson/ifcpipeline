from fastapi import FastAPI, HTTPException, Depends, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
import socket
import os
import json
import asyncio
import ipaddress
import logging
import shutil
import secrets
import pickle
import zlib
import glob
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
    IfcClassifyRequest,
    IfcClassifyBatchRequest,
    IfcClassifyResponse,
    IfcClassifyBatchResponse,
    IfcPatchRequest,
    IfcPatchListRecipesRequest,
    IfcPatchListRecipesResponse,
    RevitExecuteRequest,
)  
from pydantic import BaseModel, HttpUrl
from redis import Redis
from shared import object_storage as s3
from shared import audit_db
from rq import Queue
from rq.job import Job, JobStatus
from rq.worker import Worker
import aiohttp
from aiohttp import ClientTimeout
import httpx

# Add this at the beginning of your file
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add this new dictionary to store download links
download_links: Dict[str, DownloadLink] = {}

# Configure Redis connection
redis_url = os.getenv('REDIS_URL', 'redis://redis:6379/0')
redis_conn = Redis.from_url(redis_url)

# Job result TTL - how long to keep job results in Redis (in seconds)
# Set to 24 hours to allow enough time for polling job status
JOB_RESULT_TTL = 86400  # 24 hours

# Create RQ queues
default_queue = Queue('default', connection=redis_conn)
ifcconvert_queue = Queue('ifcconvert', connection=redis_conn)
ifccsv_queue = Queue('ifccsv', connection=redis_conn)
ifcclash_queue = Queue('ifcclash', connection=redis_conn)
ifctester_queue = Queue('ifctester', connection=redis_conn)
ifcdiff_queue = Queue('ifcdiff', connection=redis_conn)
ifc2json_queue = Queue('ifc2json', connection=redis_conn)
ifc5d_queue = Queue('ifc5d', connection=redis_conn)
ifcpatch_queue = Queue('ifcpatch', connection=redis_conn)
revit_queue = Queue('revit', connection=redis_conn)

# Define job status response model
class JobStatusResponse(BaseModel):
    model_config = {"arbitrary_types_allowed": True}
    job_id: str
    status: str
    result: Optional[Any] = None
    error: Optional[str] = None
    execution_time_seconds: Optional[float] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None

# Define the load_config function
def load_config():
    # Default configuration
    default_config = {
        'api_keys': ["USE_ENV_VAR"],
        'allowed_ip_ranges': ['127.0.0.1/32'],  # Default to localhost only
        'docker_gateway_ips': ['172.18.0.1']   # Docker bridge gateway - external traffic arrives via this IP
    }
    
    # Load configuration from environment variables
    env_api_key = os.getenv('IFC_PIPELINE_API_KEY')
    env_allowed_ip_ranges = os.getenv('IFC_PIPELINE_ALLOWED_IP_RANGES')
    env_docker_gateway = os.getenv('DOCKER_GATEWAY_IP')
    
    config = default_config.copy()
    
    # Add API key from environment if available
    if env_api_key:
        config['api_keys'] = [env_api_key]
        logger.info("API key loaded from environment variable")
    
    # Add IP ranges from environment if available
    if env_allowed_ip_ranges:
        config['allowed_ip_ranges'] = [r.strip() for r in env_allowed_ip_ranges.split(',') if r.strip()]
        logger.info(f"Allowed IP ranges loaded from environment: {config['allowed_ip_ranges']}")
    
    # Docker gateway IPs are never whitelisted - external traffic arrives via bridge gateway
    if env_docker_gateway:
        config['docker_gateway_ips'] = [ip.strip() for ip in env_docker_gateway.split(',') if ip.strip()]
        logger.info(f"Docker gateway IPs (deny-list for IP whitelist): {config['docker_gateway_ips']}")
    
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
DOCKER_GATEWAY_IPS = {ipaddress.ip_address(ip) for ip in config.get('docker_gateway_ips', [])}
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
# Configure CORS origins from environment variables
cors_origins = []
if os.environ.get("IFC_PIPELINE_EXTERNAL_URL"):
    cors_origins.append(os.environ.get("IFC_PIPELINE_EXTERNAL_URL"))
if os.environ.get("IFC_PIPELINE_PREVIEW_EXTERNAL_URL"):
    cors_origins.append(os.environ.get("IFC_PIPELINE_PREVIEW_EXTERNAL_URL"))

# Add additional origins from environment variable (comma-separated)
# Use IFC_PIPELINE_CORS_ORIGINS for local dev (e.g. http://localhost:5173)
additional_origins = os.environ.get("IFC_PIPELINE_CORS_ORIGINS", "")
if additional_origins:
    cors_origins.extend([origin.strip() for origin in additional_origins.split(",") if origin.strip()])

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "Authorization"],
    expose_headers=["Content-Disposition"],
    max_age=3600,
)

# Set up API key header
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Define the verify_access function
async def verify_access(request: Request, api_key: str = Depends(api_key_header)):
    client_ip = ipaddress.ip_address(request.client.host)
    logger.info(f"Access attempt from IP: {client_ip}")
    
    # Docker bridge gateway IP(s) - external traffic arrives via this, never whitelist
    if client_ip in DOCKER_GATEWAY_IPS:
        logger.info(f"IP {client_ip} is Docker gateway (external traffic), requiring API key")
        if not api_key:
            logger.warning(f"Access denied to {client_ip} (Docker gateway - API key required)")
            raise HTTPException(status_code=403, detail="API key required")
        if api_key not in API_KEYS:
            logger.warning(f"Access denied to {client_ip} (Docker gateway - Invalid API key)")
            raise HTTPException(status_code=403, detail="Invalid API key")
        logger.info(f"Access granted to {client_ip} (Valid API key)")
        return True
    
    # Check if IP is in allowed ranges (internal Docker containers, localhost)
    for ip_range in ALLOWED_IP_RANGES:
        if client_ip in ip_range:
            logger.info(f"Access granted to {client_ip} (IP in allowed range {ip_range})")
            return True
    
    # Not in allowed range, require API key
    logger.info(f"IP {client_ip} not in any allowed ranges, checking API key")
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

def validate_input_file_exists(filename: str, base_dir: str = "/uploads") -> None:
    """Validate that an input file exists before enqueuing. Prevents wasted worker jobs.

    When object storage is enabled, accept any path that resolves to an existing
    S3 key — not just `uploads/<basename>`. This matters for chained pipelines
    (e.g. `chain/n8n/A1-building-elements.ifc`) where a worker output is fed
    straight into another recipe: the output key lives under `chain/…` or
    `output/patch/…`, never under `uploads/`. Previously those were rejected
    as 404 even though the worker would have downloaded them fine.
    """
    path = os.path.join(base_dir, os.path.basename(filename))
    if os.path.exists(path):
        return
    if s3.is_enabled():
        # 1) Legacy basename lookup under uploads/
        key = s3.build_upload_key(os.path.basename(filename))
        if s3.object_exists(key):
            return
        # 2) Treat the caller's path as an arbitrary bucket key — same rules
        #    the workers use via `normalize_input_key`.
        key = s3.normalize_input_key(filename)
        if s3.object_exists(key):
            return
    raise HTTPException(status_code=404, detail=f"Input file not found: {filename}")


def validate_url_for_ssrf(url: str) -> None:
    """Validate URL to prevent SSRF attacks."""
    try:
        parsed = urlparse(url)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid URL")

    if parsed.scheme.lower() not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Only HTTP(S) URLs allowed")

    host = parsed.hostname
    if not host:
        raise HTTPException(status_code=400, detail="URL must have a valid host")

    # Resolve and check all IPs are public/global
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if not ip.is_global:
                raise HTTPException(status_code=400, detail="URL points to restricted network")
    except socket.gaierror:
        raise HTTPException(status_code=400, detail="Cannot resolve hostname")

@app.get("/health", tags=["Health"])
async def health_check():
    """Checks the health of the API Gateway, Redis, and Worker Queues."""
    health_status = {
        "api-gateway": "healthy",
        "redis": "waiting", 
        "ifcconvert_queue": "waiting",
        "ifcclash_queue": "waiting",
        "ifccsv_queue": "waiting",
        "ifctester_queue": "waiting",
        "ifcdiff_queue": "waiting",
        "ifc5d_queue": "waiting",
        "ifc2json_queue": "waiting",
        "ifcpatch_queue": "waiting",
        "revit_queue": "waiting",
        "default_queue": "waiting",
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
        "ifcpatch_queue": ifcpatch_queue,
        "revit_queue": revit_queue,
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
                # Queue not yet initialized - this is normal on first run
                health_status[key] = "waiting (no jobs yet)"
                logger.info(f"Queue '{queue_name}' not yet initialized - this is normal on first startup.")
                
    except Exception as e:
        logger.error(f"Error checking RQ queues/workers: {str(e)}")
        # Mark all unchecked queues as error state
        for key in all_queues.keys():
            if health_status[key] == "waiting": # Only update if not already checked
                 health_status[key] = f"error checking ({str(e)})"

    # Determine overall status
    # Healthy only if API Gateway, Redis, and all queues are healthy or waiting
    is_healthy = all(status in ["healthy", "waiting (no jobs yet)"] for key, status in health_status.items() if key != "api-gateway")
    is_degraded = any("degraded" in status for status in health_status.values())
    
    if is_healthy and health_status["redis"] == "healthy": # Double check redis explicitly
        overall_status = "healthy"
    elif is_degraded:
        overall_status = "degraded"
    else:
        overall_status = "unhealthy"

    return {"status": overall_status, "services": health_status}

def _unpickle_result(raw: bytes) -> Optional[dict]:
    """Try raw pickle then zlib-compressed pickle (legacy C# worker builds)."""
    for decoder in (lambda b: b, zlib.decompress):
        try:
            result = pickle.loads(decoder(raw))
        except Exception:
            continue
        if isinstance(result, dict):
            return result
        return {"raw": str(result)}
    return None


def _read_job_result(job_id: str) -> Optional[dict]:
    """Return the worker's return value for a finished job.

    RQ ≥ 1.12 stores successful return values in a dedicated Redis stream at
    ``rq:results:<job_id>`` with a ``return_value`` field (pickled). Older RQ
    releases stored it on the job hash under ``result``. We check both so the
    gateway keeps working after an RQ upgrade (this is the delta from the
    original ifcpipeline, which pinned RQ 1.x).
    """
    # Preferred path: RQ 2.x results stream via the high-level API.
    try:
        job = Job.fetch(job_id, connection=redis_conn)
        latest = job.latest_result()
        if latest is not None and getattr(latest, "return_value", None) is not None:
            rv = latest.return_value
            if isinstance(rv, dict):
                return rv
            return {"raw": str(rv)}
    except Exception:
        pass

    # Direct stream read (bypasses any library-side deserialization quirks).
    try:
        entries = redis_conn.xrevrange(f"rq:results:{job_id}", count=1)
        if entries:
            _, fields = entries[0]
            raw = fields.get(b"return_value") or fields.get("return_value")
            if raw is not None:
                result = _unpickle_result(raw)
                if result is not None:
                    return result
    except Exception:
        pass

    # Legacy RQ 1.x layout: single pickle on the job hash.
    raw = redis_conn.hget(f"rq:job:{job_id}", "result")
    if raw:
        result = _unpickle_result(raw)
        if result is not None:
            return result
    return None


REVIT_LOGS_DIR = "/uploads/revit-logs"


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
        
        
        # Calculate execution time if both started_at and ended_at are available
        if job.started_at and job.ended_at:
            execution_time = (job.ended_at - job.started_at).total_seconds()
            response["execution_time_seconds"] = execution_time
        
        # Add timing information
        response["created_at"] = job.created_at
        response["started_at"] = job.started_at
        response["ended_at"] = job.ended_at
        
        if status == JobStatus.FINISHED:
            result = _read_job_result(job_id)
            if result is not None:
                response["result"] = result
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
        validate_input_file_exists(request.input_filename)
        # Enqueue job to the dedicated ifcconvert worker queue
        job = ifcconvert_queue.enqueue(
            "tasks.run_ifcconvert",  # Points directly to function in /app/tasks.py for ifcconvert-worker
            request.dict(),
            job_timeout="1h",  # Adjust timeout as needed
            result_ttl=JOB_RESULT_TTL
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
        validate_input_file_exists(request.filename)
        # Enqueue job to the dedicated ifccsv worker queue
        job = ifccsv_queue.enqueue(
            "tasks.run_ifc_to_csv_conversion", # Points to function in /app/tasks.py for ifccsv-worker
            request.dict(),
            job_timeout="1h",
            result_ttl=JOB_RESULT_TTL
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
        validate_input_file_exists(request.ifc_filename)
        validate_input_file_exists(request.csv_filename)
        # Enqueue job to the dedicated ifccsv worker queue
        job = ifccsv_queue.enqueue(
            "tasks.run_csv_to_ifc_import", # Points to function in /app/tasks.py for ifccsv-worker
            request.dict(),
            job_timeout="1h",
            result_ttl=JOB_RESULT_TTL
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
        for clash_set in request.clash_sets:
            for cf in clash_set.a + clash_set.b:
                validate_input_file_exists(cf.file)
        # Use the direct function path to the worker task
        job = ifcclash_queue.enqueue(
            "tasks.run_ifcclash_detection",  # Points directly to function in /app/tasks.py
            request.dict(),
            job_timeout="2h",  # Clash detection can be time-consuming
            result_ttl=JOB_RESULT_TTL
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
        validate_input_file_exists(request.ifc_filename)
        validate_input_file_exists(request.ids_filename)
        # Use the correct path now that tasks.py is directly in /app for this worker
        job = ifctester_queue.enqueue(
            "tasks.run_ifctester_validation", # Correct path relative to /app
            request.dict(),
            job_timeout="1h",
            result_ttl=JOB_RESULT_TTL
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
        validate_input_file_exists(request.old_file)
        validate_input_file_exists(request.new_file)
        # Enqueue job to the dedicated ifcdiff worker queue
        job = ifcdiff_queue.enqueue(
            "tasks.run_ifcdiff",  # Points to function in /app/tasks.py for ifcdiff-worker
            request.dict(),
            job_timeout="1h",
            result_ttl=JOB_RESULT_TTL
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
        validate_input_file_exists(request.filename)
        # Enqueue job to the dedicated ifc2json worker queue
        job = ifc2json_queue.enqueue(
            "tasks.run_ifc_to_json_conversion", # Points to function in /app/tasks.py for ifc2json-worker
            request.dict(),
            job_timeout="1h",
            result_ttl=JOB_RESULT_TTL
        )
        
        logger.info(f"Enqueued ifc2json job with ID: {job.id}")
        return {"job_id": job.id}
    except Exception as e:
        logger.error(f"Error enqueueing ifc2json job: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/ifc2json/{filename}", tags=["Conversion"])
async def get_ifc2json(filename: str, _: str = Depends(verify_access)):
    """Fetch the converted JSON. Tries S3 first (when enabled), then falls
    back to the legacy local path. The `filename` may be a bare name or any
    path under the `output/json/` hierarchy."""
    base = os.path.basename(filename)

    if s3.is_enabled():
        for candidate in (
            s3.normalize_output_key(filename, "json"),
            f"output/json/{base}",
        ):
            try:
                if s3.object_exists(candidate):
                    body = s3.get_client().get_object(
                        Bucket=s3.bucket_name(), Key=candidate
                    )["Body"].read()
                    try:
                        return json.loads(body.decode("utf-8"))
                    except json.JSONDecodeError:
                        raise HTTPException(
                            status_code=500,
                            detail=f"Failed to parse JSON from s3://{s3.bucket_name()}/{candidate}",
                        )
            except HTTPException:
                raise
            except Exception as e:
                logger.warning(f"S3 lookup for {candidate} failed: {e}")

    for local in (
        f"/output/json/{base}",
        f"/uploads/output/json/{base}",
    ):
        if os.path.exists(local):
            try:
                with open(local, "r") as fh:
                    return json.load(fh)
            except json.JSONDecodeError:
                raise HTTPException(status_code=500, detail="Failed to parse the JSON file")

    raise HTTPException(status_code=404, detail=f"File {filename} not found")

@app.get("/lineage/job/{job_id}", tags=["Audit"])
async def lineage_for_job(job_id: str, _: str = Depends(verify_access)):
    """Return every object version produced by the given RQ job along with
    its parent objects. Empty list if the job produced no audited output."""
    versions = audit_db.fetch_job_lineage(job_id)
    return {"job_id": job_id, "versions": versions}


@app.get("/audit/roots", tags=["Audit"])
async def audit_roots(
    limit: int = 50,
    since: Optional[str] = None,
    _: str = Depends(verify_access),
):
    """List recent first-time uploads (audit roots).

    `since` is an optional ISO-8601 timestamp; `limit` caps the page size.
    """
    limit = max(1, min(limit, 500))
    since_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError:
            raise HTTPException(status_code=400, detail="`since` must be ISO-8601")
    return {"roots": audit_db.fetch_roots(limit=limit, since=since_dt)}


@app.get("/audit/dedupe/{sha256}", tags=["Audit"])
async def audit_dedupe(sha256: str, _: str = Depends(verify_access)):
    """List every recorded object key that currently shares the given
    content hash. Useful for deduplication and tamper detection."""
    import re as _re
    if not _re.fullmatch(r"[a-fA-F0-9]{64}", sha256):
        raise HTTPException(status_code=400, detail="sha256 must be 64 hex chars")
    return {"sha256": sha256.lower(), "versions": audit_db.fetch_by_hash(sha256.lower())}


@app.get("/lineage/{object_key:path}", tags=["Audit"])
async def lineage_for_key(object_key: str, depth: int = 10, _: str = Depends(verify_access)):
    """Return the full lineage tree (ancestors + descendants) of the latest
    version of `object_key`. 404 if the key has never been audited."""
    depth = max(1, min(depth, 25))
    key = object_key.lstrip("/")
    data = audit_db.fetch_lineage(key, depth=depth)
    if data is None:
        # Try the "uploads/" prefix as a convenience for callers that pass
        # the bare filename (e.g. lineage/model.ifc).
        if "/" not in key:
            data = audit_db.fetch_lineage(f"uploads/{key}", depth=depth)
    if data is None:
        raise HTTPException(status_code=404, detail=f"No audit record for {object_key}")
    return data


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
        validate_input_file_exists(request.input_file)
        # Enqueue job to the dedicated ifc5d worker queue
        job = ifc5d_queue.enqueue(
            "tasks.run_qto_calculation", # Points to function in /app/tasks.py for ifc5d-worker
            request.dict(),
            job_timeout="1h",
            result_ttl=JOB_RESULT_TTL
        )
        
        logger.info(f"Enqueued calculate-qtos job with ID: {job.id}")
        return {"job_id": job.id}
    except Exception as e:
        logger.error(f"Error enqueueing calculate-qtos job: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/list_directories", summary="List Available Directories and Files", tags=["File Operations"])
async def list_directories(_: str = Depends(verify_access)):
    """List every file available to the pipeline.

    - Legacy filesystem deployments walk `/examples`, `/uploads`, `/output`
      and `/interaxo` on the shared volume.
    - Object-storage deployments additionally enumerate the bucket so that
      S3-only uploads and worker outputs show up in downstream file pickers
      (e.g. the n8n community nodes' dropdowns).

    Returns a deduplicated, sorted list of paths. Filesystem paths are
    returned with a leading slash (`/uploads/model.ifc`); S3 keys are
    prefixed with a leading slash too (`/uploads/model.ifc`) so the two
    representations compare equal and the client does not have to branch.
    """
    base_dirs = ["/examples", "/uploads", "/output", "/interaxo"]
    all_files: set[str] = set()

    for base_dir in base_dirs:
        try:
            for root, dirs, files in os.walk(base_dir):
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                files = [f for f in files if not f.startswith('.') and f != '.gitkeep']
                for file in files:
                    full_path = os.path.join(root, file)
                    all_files.add(full_path)
        except Exception as e:
            logger.warning("list_directories: error walking %s: %s", base_dir, e)

    if s3.is_enabled():
        try:
            paginator = s3.get_client().get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=s3.bucket_name()):
                for obj in page.get("Contents", []) or []:
                    key = obj.get("Key")
                    if not key or key.endswith("/"):
                        continue
                    # Normalise to the leading-slash form so the dedupe set
                    # collapses filesystem/S3 twins into a single entry.
                    all_files.add("/" + key.lstrip("/"))
        except Exception as e:
            logger.warning("list_directories: S3 listing failed: %s", e)

    return {"files": sorted(all_files)}

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

    # Strip any directory components from the client-supplied filename so a
    # value like "../etc/cron.d/evil.ifc" can't traverse out of the uploads
    # directory (legacy FS mode) or produce an S3 key with "../" segments.
    filename = os.path.basename(file.filename)
    if not filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    upload_dir = upload_config["dir"]
    file_path = os.path.join(upload_dir, filename)

    try:
        if s3.is_enabled():
            s3.ensure_bucket()
            key = s3.build_upload_key(filename)
            content_type = file.content_type or None
            sha256, size_bytes = s3.upload_fileobj_and_hash(
                file.file, key, content_type=content_type
            )
            audit_id = audit_db.record_upload(
                bucket=s3.bucket_name(),
                object_key=key,
                sha256=sha256,
                size_bytes=size_bytes,
                content_type=content_type,
                metadata={
                    "file_type": file_type,
                    "original_filename": filename,
                },
            )
            return {
                "message": f"{file_type.upper()} file {filename} uploaded successfully",
                "storage": "s3",
                "bucket": s3.bucket_name(),
                "object_key": key,
                "object_url": f"s3://{s3.bucket_name()}/{key}",
                "file_path": f"s3://{s3.bucket_name()}/{key}",
                "sha256": sha256,
                "size_bytes": size_bytes,
                "audit_id": audit_id,
            }

        os.makedirs(upload_dir, exist_ok=True)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        return {
            "message": f"{file_type.upper()} file {filename} uploaded successfully",
            "file_path": file_path,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload failed for {filename}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to upload file: {str(e)}")

def _resolve_download_path(file_path: str) -> tuple[str, str | None]:
    """Return (resolved_path, s3_key_or_none).

    When S3 is enabled and the caller-supplied path lives in the bucket, the
    S3 key is returned so callers can issue a presigned URL. Otherwise the
    local path is returned and expected to exist on disk.
    """
    if file_path.startswith("s3://"):
        _, _, rest = file_path.partition("s3://")
        _, _, key = rest.partition("/")
        return file_path, key

    if s3.is_enabled():
        # Try resolving the legacy path directly as an S3 key.
        for candidate in (
            s3.normalize_input_key(file_path) if file_path.startswith("/uploads") else file_path.lstrip("/"),
            file_path.lstrip("/"),
        ):
            if candidate and s3.object_exists(candidate):
                return file_path, candidate

    return file_path, None


@app.post("/create_download_link", tags=["File Operations"])
async def create_download_link(request: DownloadRequest, _: str = Depends(verify_access)):
    """Create a temporary download link for a file. When the file lives in
    object storage, the token is backed by an S3 key and the `/download/{token}`
    endpoint will redirect to a short-lived presigned URL."""
    file_path = request.file_path
    _, s3_key = _resolve_download_path(file_path)

    if s3_key is None and not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    token = secrets.token_urlsafe(32)
    expiry = datetime.now() + timedelta(minutes=30)
    download_links[token] = DownloadLink(
        file_path=s3_key if s3_key else file_path,
        token=token,
        expiry=expiry,
    )

    external = os.environ.get("IFC_PIPELINE_PREVIEW_EXTERNAL_URL")
    if external and external.strip():
        base_url = external.strip().rstrip('/')
    else:
        base_url = "https://ifcpipeline.byggstyrning.se"

    response = {"preview_url": f"{base_url}/{token}", "download_token": token, "expiry": expiry}
    if s3_key:
        response["storage"] = "s3"
        response["object_key"] = s3_key
    return response


@app.get("/download/{token}", tags=["File Operations"])
async def download_file(token: str):
    """Download a file using a temporary token. For S3-backed tokens we issue
    a redirect to a presigned URL so the client streams straight from MinIO."""
    if token not in download_links:
        raise HTTPException(status_code=404, detail="Invalid or expired download token")

    download_link = download_links[token]
    if datetime.now() > download_link.expiry:
        del download_links[token]
        raise HTTPException(status_code=404, detail="Download token has expired")

    target = download_link.file_path

    if s3.is_enabled() and not target.startswith("/"):
        if not s3.object_exists(target):
            del download_links[token]
            raise HTTPException(status_code=404, detail="File not found in object storage")
        remaining = max(int((download_link.expiry - datetime.now()).total_seconds()), 60)
        url = s3.presigned_get_url_public(target, expires_in=remaining)
        return RedirectResponse(url=url, status_code=307)

    if not os.path.exists(target):
        del download_links[token]
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(target, filename=os.path.basename(target))

@app.post("/download-from-url", tags=["File Operations"])
async def download_from_url(request: DownloadUrlRequest, _: str = Depends(verify_access)):
    """
    Download a file from a URL and save it to the uploads directory.
    
    Args:
        request (DownloadUrlRequest): The request containing the download URL.
    
    Returns:
        dict: A message indicating success or failure, and the path to the downloaded file.
    """
    validate_url_for_ssrf(str(request.url))
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        async with await get_aiohttp_session() as session:
            async with session.get(request.url, headers=headers, allow_redirects=False) as response:
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
                
                # Sanitize filename to prevent path traversal
                filename = os.path.basename(filename)
                
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

@app.post("/patch/execute", tags=["Patch"])
async def execute_patch(request: IfcPatchRequest, _: str = Depends(verify_access)):
    """
    Execute an IfcPatch recipe on an IFC file.
    
    Supports both built-in recipes from IfcOpenShell and custom user-defined recipes.
    
    Args:
        request (IfcPatchRequest): The request body containing patch parameters.
    
    Returns:
        dict: A dictionary containing the job ID.
    """
    try:
        validate_input_file_exists(request.input_file)
        job = ifcpatch_queue.enqueue(
            "tasks.run_ifcpatch",
            request.dict(),
            job_timeout="2h",  # Patches can be time-consuming
            result_ttl=JOB_RESULT_TTL
        )
        
        logger.info(f"Enqueued ifcpatch job with ID: {job.id}")
        return {"job_id": job.id}
    except Exception as e:
        logger.error(f"Error enqueueing ifcpatch job: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/patch/recipes/list", tags=["Patch"])
async def list_patch_recipes(
    request: IfcPatchListRecipesRequest = IfcPatchListRecipesRequest(),
    _: str = Depends(verify_access)
):
    """
    List all available IfcPatch recipes (built-in and custom).
    
    This endpoint executes synchronously without requiring a worker, so it works
    even when no workers are running.
    
    Args:
        request (IfcPatchListRecipesRequest): Filter parameters for recipe listing.
    
    Returns:
        dict: A dictionary containing available recipes.
    """
    try:
        import sys
        import importlib
        import inspect
        from pathlib import Path
        from typing import get_type_hints
        
        # Helper function to parse type annotations
        def format_type_annotation(type_annotation) -> str:
            """Format type annotation for display."""
            try:
                if type_annotation is inspect.Parameter.empty:
                    return "Any"
                if hasattr(type_annotation, '__name__'):
                    return type_annotation.__name__
                type_str = str(type_annotation)
                if 'typing.' in type_str:
                    type_str = type_str.replace('typing.', '')
                return type_str
            except:
                return "Any"
        
        # Helper function to extract parameters from recipe
        def extract_recipe_parameters(patcher_class):
            """Extract parameter information from a Patcher class."""
            parameters = []
            try:
                sig = inspect.signature(patcher_class.__init__)
                docstring = inspect.getdoc(patcher_class.__init__) or ""
                
                for param_name, param in sig.parameters.items():
                    if param_name in ['self', 'file', 'logger']:
                        continue
                    
                    param_info = {
                        "name": param_name,
                        "type": format_type_annotation(param.annotation),
                        "required": param.default is inspect.Parameter.empty,
                        "default": None if param.default is inspect.Parameter.empty else str(param.default),
                        "description": ""
                    }
                    
                    # Try to extract description from docstring
                    if docstring:
                        for line in docstring.split('\n'):
                            line = line.strip()
                            if line.startswith(f'{param_name}:') or line.startswith(f':param {param_name}:'):
                                desc = line.split(':', 2)[-1].strip()
                                param_info["description"] = desc
                                break
                    
                    parameters.append(param_info)
            except Exception as e:
                logger.debug(f"Could not extract parameters: {str(e)}")
            
            return parameters
        
        recipes = []
        
        # Get built-in recipes
        if request.include_builtin:
            logger.info("Discovering built-in recipes...")
            try:
                import ifcpatch.recipes
                recipes_dir = Path(ifcpatch.recipes.__file__).parent
                
                for recipe_file in recipes_dir.glob("*.py"):
                    if recipe_file.stem.startswith('_'):
                        continue
                    
                    recipe_name = recipe_file.stem
                    try:
                        recipe_module = importlib.import_module(f'ifcpatch.recipes.{recipe_name}')
                        
                        if hasattr(recipe_module, 'Patcher'):
                            patcher_class = recipe_module.Patcher
                            parameters = extract_recipe_parameters(patcher_class)
                            
                            description = inspect.getdoc(patcher_class.__init__) or "Built-in IfcPatch recipe"
                            if '\n\n' in description:
                                description = description.split('\n\n')[0]
                            elif ':param' in description:
                                description = description.split(':param')[0]
                            elif 'Args:' in description:
                                description = description.split('Args:')[0]
                            description = ' '.join(description.split()).strip()
                            
                            recipes.append({
                                "name": recipe_name,
                                "description": description,
                                "is_custom": False,
                                "parameters": parameters,
                                "output_type": None
                            })
                    except Exception as e:
                        logger.debug(f"Could not inspect {recipe_name}: {str(e)}")
                        recipes.append({
                            "name": recipe_name,
                            "description": "Built-in IfcPatch recipe",
                            "is_custom": False,
                            "parameters": [],
                            "output_type": None
                        })
                
                logger.info(f"Found {len([r for r in recipes if not r['is_custom']])} built-in recipes")
            except Exception as e:
                logger.error(f"Error discovering built-in recipes: {str(e)}", exc_info=True)
        
        # Get custom recipes
        if request.include_custom:
            logger.info("Discovering custom recipes...")
            try:
                # Mount path where custom recipes should be accessible
                custom_recipes_path = Path("/app/custom_recipes")
                
                # Add to path if not already there
                if str(custom_recipes_path) not in sys.path:
                    sys.path.insert(0, str(custom_recipes_path))
                
                if custom_recipes_path.exists():
                    for recipe_file in custom_recipes_path.glob("*.py"):
                        if recipe_file.stem.startswith('_') or recipe_file.stem == 'example_recipe':
                            continue
                        
                        recipe_name = recipe_file.stem
                        try:
                            module = importlib.import_module(recipe_name)
                            
                            if hasattr(module, 'Patcher'):
                                patcher_class = module.Patcher
                                parameters = extract_recipe_parameters(patcher_class)
                                
                                description = inspect.getdoc(patcher_class.__init__) or "Custom IfcPatch recipe"
                                if '\n\n' in description:
                                    description = description.split('\n\n')[0]
                                elif ':param' in description:
                                    description = description.split(':param')[0]
                                elif 'Args:' in description:
                                    description = description.split('Args:')[0]
                                description = ' '.join(description.split()).strip()
                                
                                recipes.append({
                                    "name": recipe_name,
                                    "description": description,
                                    "is_custom": True,
                                    "parameters": parameters,
                                    "output_type": None
                                })
                        except Exception as e:
                            logger.debug(f"Could not load custom recipe {recipe_name}: {str(e)}")
                            recipes.append({
                                "name": recipe_name,
                                "description": "Custom IfcPatch recipe",
                                "is_custom": True,
                                "parameters": [],
                                "output_type": None
                            })
                    
                    logger.info(f"Found {len([r for r in recipes if r['is_custom']])} custom recipes")
                else:
                    logger.warning(f"Custom recipes directory not found: {custom_recipes_path}")
            except Exception as e:
                logger.error(f"Error discovering custom recipes: {str(e)}", exc_info=True)
        
        return {
            "recipes": recipes,
            "total_count": len(recipes),
            "builtin_count": len([r for r in recipes if not r['is_custom']]),
            "custom_count": len([r for r in recipes if r['is_custom']])
        }
        
    except Exception as e:
        logger.error(f"Error listing recipes: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/revit/execute", tags=["Revit"])
async def execute_revit_command(request: RevitExecuteRequest, _: str = Depends(verify_access)):
    """
    Enqueue a Revit/PyRevit command for execution on the Windows worker.

    The job runs on a remote Windows machine that polls the 'revit' queue.
    Use GET /jobs/{job_id}/status to poll for results.

    Args:
        request (RevitExecuteRequest): The command to execute.

    Returns:
        dict: A dictionary containing the job ID.
    """
    try:
        job_timeout = f"{request.timeout_seconds + 300}s"
        job_data = json.loads(request.json())
        job = revit_queue.enqueue(
            "tasks.run_revit_command",
            job_data,
            job_timeout=job_timeout,
            result_ttl=JOB_RESULT_TTL,
        )

        logger.info(f"Enqueued revit job with ID: {job.id} (type={request.command_type}, script={request.script_path})")
        return {"job_id": job.id}
    except Exception as e:
        logger.error(f"Error enqueueing revit job: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/revit/logs", tags=["Revit"])
async def upload_revit_log(
    job_id: str = Form(...),
    log_type: str = Form(...),
    file: UploadFile = File(...),
    _: str = Depends(verify_access)
):
    """
    Upload a log file from the Revit worker.

    Args:
        job_id (str): The job ID to associate with the log file.
        log_type (str): The type of log (journal, pyrevit, rtv, worker).
        file (UploadFile): The log file to upload.

    Returns:
        dict: A dictionary containing the file path where the log was saved.
    """
    try:
        # Validate log_type: alphanumeric, hyphens and underscores only
        import re as _re
        if not _re.match(r'^[\w\-]{1,128}$', log_type):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid log_type '{log_type}'. Must be alphanumeric with hyphens/underscores, max 128 chars."
            )

        # Validate file extension (.log or .txt)
        if not any(file.filename.endswith(ext) for ext in (".log", ".txt")):
            raise HTTPException(
                status_code=400,
                detail="File must have a .log or .txt extension."
            )

        # Create upload directory
        upload_dir = "/uploads/revit-logs"
        os.makedirs(upload_dir, exist_ok=True)

        # Generate filename: {job_id}-{log_type}.{ext}
        file_extension = os.path.splitext(file.filename)[1]
        filename = f"{job_id}-{log_type}{file_extension}"
        file_path = os.path.join(upload_dir, filename)

        # Save the file
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        logger.info(f"Uploaded Revit log file: {file_path}")
        return {"file_path": file_path}

    except Exception as e:
        logger.error(f"Error uploading Revit log file: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to upload log file: {str(e)}")

@app.get("/jobs/{job_id}/logs", tags=["Jobs"])
async def list_job_logs(job_id: str, _: str = Depends(verify_access)):
    """
    List all log files associated with a job.

    Looks up log files on disk (uploaded by the worker) and also checks
    the job result dict for a ``log_files`` list.
    """
    import re as _re
    if not _re.match(r'^[\w\-]+$', job_id):
        raise HTTPException(status_code=400, detail="Invalid job_id")

    logs: List[Dict[str, Any]] = []

    # Discover log files on disk: {REVIT_LOGS_DIR}/{job_id}-*.{log,txt}
    pattern = os.path.join(REVIT_LOGS_DIR, f"{job_id}-*")
    for path in sorted(glob.glob(pattern)):
        fname = os.path.basename(path)
        size = os.path.getsize(path)
        logs.append({
            "filename": fname,
            "size_bytes": size,
            "path": path,
        })

    # Also pull log_files from the job result (may include paths not on this host)
    result = _read_job_result(job_id)
    result_log_paths: List[str] = []
    if isinstance(result, dict) and "log_files" in result:
        val = result["log_files"]
        if isinstance(val, list):
            result_log_paths = [str(v) for v in val]
        elif isinstance(val, str):
            result_log_paths = [val]

    return {
        "job_id": job_id,
        "logs": logs,
        "result_log_paths": result_log_paths,
    }

@app.get("/jobs/{job_id}/logs/{filename}", tags=["Jobs"])
async def get_job_log(job_id: str, filename: str, _: str = Depends(verify_access)):
    """
    Return the contents of a specific log file for a job.

    The filename must start with the job_id prefix to prevent path traversal.
    """
    import re as _re
    if not _re.match(r'^[\w\-]+$', job_id):
        raise HTTPException(status_code=400, detail="Invalid job_id")

    safe_name = os.path.basename(filename)
    if not safe_name.startswith(job_id):
        raise HTTPException(status_code=403, detail="Filename must belong to the requested job")

    file_path = os.path.join(REVIT_LOGS_DIR, safe_name)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail=f"Log file not found: {safe_name}")

    try:
        with open(file_path, "r", errors="replace") as f:
            content = f.read()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read log file: {str(e)}")

    return Response(content=content, media_type="text/plain; charset=utf-8")

@app.post("/classify", response_model=IfcClassifyResponse, tags=["Classification"])
async def classify_single(request: IfcClassifyRequest, _: str = Depends(verify_access)):
    """
    Classify a single IFC element using the CatBoost model.
    
    Args:
        request (IfcClassifyRequest): The element to classify with category, family, type, etc.
    
    Returns:
        IfcClassifyResponse: The classification result with IFC class, predefined type, and confidence.
    """
    try:
        # Call the classifier service
        classifier_url = "http://ifc-classifier:8000/classify"
        
        async with await get_aiohttp_session() as session:
            async with session.post(
                classifier_url,
                json=request.dict(),
                headers={"Content-Type": "application/json"}
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Classifier service returned {response.status}: {error_text}")
                    raise HTTPException(
                        status_code=500,
                        detail=f"Classification service error: {response.status}"
                    )
                
                result = await response.json()
                return result
                
    except aiohttp.ClientError as e:
        logger.error(f"Error connecting to classifier service: {str(e)}")
        raise HTTPException(
            status_code=503,
            detail="Classification service unavailable"
        )
    except Exception as e:
        logger.error(f"Error in classification: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Classification failed: {str(e)}")

@app.post("/classify/batch", response_model=IfcClassifyBatchResponse, tags=["Classification"])
async def classify_batch(request: IfcClassifyBatchRequest, _: str = Depends(verify_access)):
    """
    Classify multiple IFC elements using the CatBoost model.
    
    Args:
        request (IfcClassifyBatchRequest): The elements to classify.
    
    Returns:
        IfcClassifyBatchResponse: The classification results for all elements.
    """
    try:
        # Call the classifier service
        classifier_url = "http://ifc-classifier:8000/classify/batch"
        
        async with await get_aiohttp_session() as session:
            async with session.post(
                classifier_url,
                json=request.dict(),
                headers={"Content-Type": "application/json"}
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Classifier service returned {response.status}: {error_text}")
                    raise HTTPException(
                        status_code=500,
                        detail=f"Classification service error: {response.status}"
                    )
                
                result = await response.json()
                return result
                
    except aiohttp.ClientError as e:
        logger.error(f"Error connecting to classifier service: {str(e)}")
        raise HTTPException(
            status_code=503,
            detail="Classification service unavailable"
        )
    except Exception as e:
        logger.error(f"Error in batch classification: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Batch classification failed: {str(e)}")


def _sanitize_proxy_path(path: str) -> str:
    """Reject path traversal and dangerous path components."""
    if ".." in path or path.startswith("/"):
        raise HTTPException(status_code=404, detail="Not found")
    for part in path.split("/"):
        if part in (".", "..") or "\\" in part:
            raise HTTPException(status_code=404, detail="Not found")
    return path


# Viewer routes - serve the IFC viewer at token URLs
@app.get("/{token}")
async def viewer_with_token_direct(token: str):
    """
    Serve the IFC viewer directly at the token URL (e.g., /token123).
    Only valid download tokens are allowed - token must exist in download_links.
    """
    if token not in download_links:
        raise HTTPException(status_code=404, detail="Not found")
    download_link = download_links[token]
    if datetime.now() > download_link.expiry:
        del download_links[token]
        raise HTTPException(status_code=404, detail="Not found")
    
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            req = client.build_request(
                "GET",
                "http://ifc-viewer:3000/",
                headers={"Accept-Encoding": "identity"},
            )
            resp = await client.send(req, stream=True)

            hop_by_hop = {
                "connection",
                "keep-alive", 
                "proxy-authenticate",
                "proxy-authorization",
                "te",
                "trailer",
                "transfer-encoding",
                "upgrade",
            }
            headers = {k: v for k, v in resp.headers.items() if k.lower() not in hop_by_hop and k.lower() not in {"content-length", "content-encoding"}}

            from starlette.responses import StreamingResponse

            return StreamingResponse(
                resp.aiter_bytes(),
                status_code=resp.status_code,
                media_type=resp.headers.get("content-type", "text/html"),
                headers=headers,
            )
    except Exception as e:
        logger.error(f"Error proxying to viewer: {str(e)}")
        raise HTTPException(status_code=503, detail="Viewer service unavailable")


@app.get("/assets/{path:path}")
async def viewer_assets(path: str):
    """
    Serve viewer assets (CSS, JS, etc.)
    """
    try:
        _sanitize_proxy_path(path)
        async with httpx.AsyncClient(follow_redirects=True) as client:
            req = client.build_request(
                "GET",
                f"http://ifc-viewer:3000/assets/{path}",
                headers={"Accept-Encoding": "identity"},
            )
            resp = await client.send(req, stream=True)

            hop_by_hop = {
                "connection",
                "keep-alive",
                "proxy-authenticate", 
                "proxy-authorization",
                "te",
                "trailer",
                "transfer-encoding",
                "upgrade",
            }
            headers = {k: v for k, v in resp.headers.items() if k.lower() not in hop_by_hop and k.lower() not in {"content-length", "content-encoding"}}

            from starlette.responses import StreamingResponse

            return StreamingResponse(
                resp.aiter_bytes(),
                status_code=resp.status_code,
                media_type=resp.headers.get("content-type"),
                headers=headers,
            )
    except Exception as e:
        logger.error(f"Error proxying viewer asset {path}: {str(e)}")
        raise HTTPException(status_code=404, detail="Asset not found")


@app.get("/node_modules/{path:path}")
async def viewer_node_modules(path: str):
    """
    Serve node_modules assets (including WebWorkers)
    """
    try:
        _sanitize_proxy_path(path)
        async with httpx.AsyncClient(follow_redirects=True) as client:
            req = client.build_request(
                "GET",
                f"http://ifc-viewer:3000/node_modules/{path}",
                headers={"Accept-Encoding": "identity"},
            )
            resp = await client.send(req, stream=True)

            hop_by_hop = {
                "connection",
                "keep-alive",
                "proxy-authenticate", 
                "proxy-authorization",
                "te",
                "trailer",
                "transfer-encoding",
                "upgrade",
            }
            headers = {k: v for k, v in resp.headers.items() if k.lower() not in hop_by_hop and k.lower() not in {"content-length", "content-encoding"}}

            # Ensure proper MIME type for JavaScript modules
            if path.endswith('.mjs') or path.endswith('.js'):
                headers['content-type'] = 'application/javascript'

            from starlette.responses import StreamingResponse

            return StreamingResponse(
                resp.aiter_bytes(),
                status_code=resp.status_code,
                media_type=headers.get("content-type", "application/javascript"),
                headers=headers,
            )
    except Exception as e:
        logger.error(f"Error proxying node_modules asset {path}: {str(e)}")
        raise HTTPException(status_code=404, detail="Asset not found")
