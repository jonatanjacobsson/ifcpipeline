from fastapi import FastAPI, HTTPException, Depends, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse
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
    IfcFastRequest,
    IfcClashRequest,
    IfcTesterRequest,
    IfcDiffRequest,
    IFC2JSONRequest,
    FragmentsRequest,
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
    IfcCoordRequest,
    TopologicpyRequest,
    TopologicIngestRequest,
)  
from pydantic import BaseModel, HttpUrl
from redis import Redis
from shared import object_storage as s3
from shared import audit_db
from rq import Queue, Retry
from rq.job import Job, JobStatus
from rq.worker import Worker
import aiohttp
from aiohttp import ClientTimeout
import httpx
from botocore.exceptions import ClientError

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
ifcfast_queue = Queue('ifcfast', connection=redis_conn)
ifcclash_queue = Queue('ifcclash', connection=redis_conn)
ifctester_queue = Queue('ifctester', connection=redis_conn)
# ifc_gherkin / beast_pdf_gherkin queues moved to cde/pipeline-gateway (2026-05).
ifcdiff_queue = Queue('ifcdiff', connection=redis_conn)
ifc2json_queue = Queue('ifc2json', connection=redis_conn)
ifcfrag_queue = Queue('ifcfrag', connection=redis_conn)
ifc5d_queue = Queue('ifc5d', connection=redis_conn)
ifcpatch_queue = Queue('ifcpatch', connection=redis_conn)
revit_queue = Queue('revit', connection=redis_conn)
ifccoord_queue = Queue('ifccoord', connection=redis_conn)
topologicpy_queue = Queue('topologicpy-worker', connection=redis_conn)

# Define job status response model
class JobStatusResponse(BaseModel):
    model_config = {"arbitrary_types_allowed": True}
    job_id: str
    status: str
    result: Optional[Any] = None
    error: Optional[str] = None
    progress: Optional[Dict[str, Any]] = None
    execution_time_seconds: Optional[float] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None


class JobRequeueResponse(BaseModel):
    job_id: str
    status: str
    requeued: bool

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
        "ifcfast_queue": "waiting",
        "ifctester_queue": "waiting",
        "ifcdiff_queue": "waiting",
        "ifc5d_queue": "waiting",
        "ifc2json_queue": "waiting",
        "ifcfrag_queue": "waiting",
        "ifcpatch_queue": "waiting",
        "revit_queue": "waiting",
        "ifccoord_queue": "waiting",
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
        "ifcfast_queue": ifcfast_queue,
        "ifctester_queue": ifctester_queue,
        "ifcdiff_queue": ifcdiff_queue,
        "ifc5d_queue": ifc5d_queue,
        "ifc2json_queue": ifc2json_queue,
        "ifcfrag_queue": ifcfrag_queue,
        "ifcpatch_queue": ifcpatch_queue,
        "revit_queue": revit_queue,
        "ifccoord_queue": ifccoord_queue,
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
    is_healthy = all(
        status in ["healthy", "waiting (no jobs yet)"]
        for key, status in health_status.items()
        if key != "api-gateway"
    )
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
        
        progress = getattr(job, "meta", None) or {}
        if isinstance(progress, dict) and progress.get("progress"):
            response["progress"] = progress["progress"]

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


@app.post("/jobs/{job_id}/requeue", response_model=JobRequeueResponse, tags=["Jobs"])
async def requeue_job(job_id: str, _: str = Depends(verify_access)):
    """
    Requeue a failed or stopped RQ job using the same job_id.

    Used by n8n wait sub-workflows for per-job retry without caller-supplied enqueue URLs.
    """
    try:
        job = Job.fetch(job_id, connection=redis_conn)
        status = job.get_status()
        if status not in (JobStatus.FAILED, JobStatus.STOPPED):
            raise HTTPException(
                status_code=400,
                detail=f"Job {job_id} is not in a requeueable state (status: {status})",
            )
        job.requeue()
        logger.info(f"Requeued job {job_id}")
        return {"job_id": job_id, "status": "queued", "requeued": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error requeueing job {job_id}: {str(e)}")
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
            request.model_dump(),
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
            request.model_dump(),
            job_timeout="1h",
            result_ttl=JOB_RESULT_TTL
        )
        
        logger.info(f"Enqueued ifccsv export job with ID: {job.id}")
        return {"job_id": job.id}
    except Exception as e:
        logger.error(f"Error enqueueing ifccsv export job: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


IFCFAST_OPERATIONS_DOC = {
    "export_products": "Tier-1 products → CSV/JSON/Parquet (default; replaces legacy export-only body).",
    "export_layer": "One data layer: products, psets, quantities, materials, classifications, drift, …",
    "extract_all": "All schema layers to separate files (artifacts[] in job result).",
    "summary": "Model summary JSON (schema, counts, cache key, parse_seconds).",
    "schemas": "Column dtypes for every extractable table.",
    "traverse": "Spatial graph: parent, children, ancestors, descendants, storey_of, building_of, products_in.",
    "types": "Entity type counts.",
    "type_bank": "TypeBank extraction sample.",
    "type_summary": "Per-type summary sample.",
    "preview": "First N rows of a table (preview_table, preview_n).",
    "diff": "Compare with other_filename (second IFC in uploads/).",
    "filter_products": "model.filter(entity/mode/storey_guid) → table.",
    "by_type": "model.by_type(entity_type) → table.",
    "mesh_qto": "Geometric QTO products + surfaces tables.",
    "point_cloud": "Surface point sample (Parquet recommended).",
    "meshes_summary": "Per-mesh vertex/face counts (not full topology).",
}


@app.get("/ifcfast/operations", tags=["Conversion"])
async def ifcfast_operations(_: str = Depends(verify_access)):
    """List native ifcfast operations and data layers exposed by POST /ifcfast."""
    from shared.ifcfast_ops import DATA_LAYERS, TRAVERSE_OPS

    return {
        "operations": IFCFAST_OPERATIONS_DOC,
        "data_layers": list(DATA_LAYERS),
        "traverse_ops": sorted(TRAVERSE_OPS),
        "output_formats": ["csv", "json", "parquet"],
    }


@app.post("/ifcfast", tags=["Conversion"])
async def ifcfast(request: IfcFastRequest, _: str = Depends(verify_access)):
    """
    Run a native **ifcfast** (Rust) operation on an IFC in uploads/.

    Set ``operation`` (default ``export_products``). See ``GET /ifcfast/operations``.
    For ifcopenshell/ifccsv import, XLSX, or arbitrary selectors use ``/ifccsv``.
    """
    try:
        validate_input_file_exists(request.filename)
        if request.operation == "diff" and request.other_filename:
            validate_input_file_exists(request.other_filename)
        timeout = "2h" if request.operation in {
            "mesh_qto",
            "point_cloud",
            "meshes_summary",
            "extract_all",
            "diff",
        } else "1h"
        job = ifcfast_queue.enqueue(
            "tasks.run_ifcfast_export",
            request.model_dump(exclude_none=True),
            job_timeout=timeout,
            result_ttl=JOB_RESULT_TTL,
        )
        logger.info(
            "Enqueued ifcfast job op=%s id=%s",
            request.operation,
            job.id,
        )
        return {"job_id": job.id, "operation": request.operation}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error enqueueing ifcfast job: {str(e)}")
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
            request.model_dump(),
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
            request.model_dump(),
            job_timeout="2h",  # Clash detection can be time-consuming
            result_ttl=JOB_RESULT_TTL
        )
        
        logger.info(f"Enqueued ifcclash job with ID: {job.id}")
        return {"job_id": job.id}
    except Exception as e:
        logger.error(f"Error enqueueing ifcclash job: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/ifccoord", tags=["Coordination"])
async def ifccoord(request: IfcCoordRequest, _: str = Depends(verify_access)):
    """
    Coordinate and fix clashes between federated IFC models.
    
    Args:
        request (IfcCoordRequest): The request body containing the coordination parameters.
    
    Returns:
        dict: A dictionary containing the job ID.
    """
    try:
        job = ifccoord_queue.enqueue(
            "tasks.run_coordination_task",  # Points directly to tasks.py in /app/tasks.py inside worker
            request.dict(),
            job_timeout="4h",  # Coordination can be time-consuming
            result_ttl=JOB_RESULT_TTL
        )
        
        logger.info(f"Enqueued ifccoord job with ID: {job.id}")
        return {"job_id": job.id}
    except Exception as e:
        logger.error(f"Error enqueueing ifccoord job: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/topologicpy/roomstamp", tags=["Topology"])
@app.post("/ifctopology/roomstamp", tags=["Topology"])
async def ifctopology_roomstamp(request: TopologicpyRequest, _: str = Depends(verify_access)):
    """
    Federate room/zone containment across spatial IFC files and target element
    IFC files. When stamp=true, matched room and zone data is written into a
    property set on stamped copies of the target element models.
    """
    try:
        for filename in request.spatial_files + request.element_files:
            validate_input_file_exists(filename)

        job = topologicpy_queue.enqueue(
            "tasks.run_roomstamp_benchmark",
            request.model_dump(),
            job_timeout="4h",
            result_ttl=JOB_RESULT_TTL,
        )

        logger.info(f"Enqueued topologicpy roomstamp job with ID: {job.id}")
        return {"job_id": job.id}
    except Exception as e:
        logger.error(f"Error enqueueing topologicpy roomstamp job: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/topologicpy/ingest", tags=["Topology"])
async def topologicpy_ingest(request: TopologicIngestRequest, _: str = Depends(verify_access)):
    """Run a topologic ingest script to extract graph relationships from IFC files.

    Available scripts: spaces, spatial, mep, structural.
    Use GET /topologicpy/ingest/scripts for discovery.
    """
    try:
        if not request.input_s3:
            for filename in request.input_files:
                validate_input_file_exists(filename)

        job = topologicpy_queue.enqueue(
            "tasks.run_ingest",
            request.model_dump(),
            job_timeout="2h",
            result_ttl=JOB_RESULT_TTL,
        )

        logger.info(f"Enqueued topologicpy ingest job (script={request.script}) with ID: {job.id}")
        return {"job_id": job.id}
    except Exception as e:
        logger.error(f"Error enqueueing topologicpy ingest job: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/topologicpy/ingest/scripts", tags=["Topology"])
async def topologicpy_ingest_scripts(_: str = Depends(verify_access)):
    """List available topologic ingest scripts with full parameter introspection.

    Dynamically introspects ingest script modules to return rich metadata
    including description, typed parameters, defaults, and per-param docs
    (same pattern as /patch/recipes/list).
    """
    import sys
    import importlib
    import inspect
    from pathlib import Path as _Path

    scripts_dir = _Path("/app/ingest_scripts")
    if not scripts_dir.exists():
        scripts_dir = _Path(__file__).parent.parent / "topologicpy-worker" / "ingest_scripts"

    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    if str(scripts_dir.parent) not in sys.path:
        sys.path.insert(0, str(scripts_dir.parent))

    def _format_type(annotation) -> str:
        if annotation is inspect.Parameter.empty:
            return "Any"
        if hasattr(annotation, "__name__"):
            return annotation.__name__
        type_str = str(annotation)
        for prefix in ("typing.", "<class '", "'>"):
            type_str = type_str.replace(prefix, "")
        return type_str

    def _parse_docstring_params(docstring: str) -> dict:
        params = {}
        if not docstring:
            return params
        for line in docstring.split("\n"):
            line = line.strip()
            if line.startswith(":param "):
                rest = line[7:]
                if ":" in rest:
                    pname, desc = rest.split(":", 1)
                    params[pname.strip()] = desc.strip()
        return params

    scripts = []
    try:
        import pkgutil
        for info in pkgutil.iter_modules([str(scripts_dir)]):
            if info.name.startswith("_"):
                continue
            try:
                mod = importlib.import_module(f"ingest_scripts.{info.name}")
                cls = getattr(mod, "Ingester", None)
                if cls is None:
                    continue

                sig = inspect.signature(cls.__init__)
                docstring = inspect.getdoc(cls.__init__) or ""
                param_docs = _parse_docstring_params(docstring)

                # Extract short description (first paragraph)
                description = docstring
                for sep in (":param", "Args:", "\n\n"):
                    if sep in description:
                        description = description.split(sep)[0]
                        break
                description = " ".join(description.split()).strip()
                if not description:
                    description = getattr(cls, "DESCRIPTION", "") or (cls.__doc__ or "").strip()

                parameters = []
                for param_name, param in sig.parameters.items():
                    if param_name in ("self", "ifc_files", "log", "kwargs"):
                        continue
                    parameters.append({
                        "name": param_name,
                        "type": _format_type(param.annotation),
                        "description": param_docs.get(param_name, ""),
                        "required": param.default is inspect.Parameter.empty,
                        "default": None if param.default is inspect.Parameter.empty else param.default,
                    })

                scripts.append({
                    "name": info.name,
                    "description": description,
                    "parameters": parameters,
                })
            except Exception as e:
                logger.warning("Failed to inspect ingest script %s: %s", info.name, e)
                scripts.append({
                    "name": info.name,
                    "description": f"Ingest script (introspection failed: {e})",
                    "parameters": [],
                })
    except Exception as e:
        logger.error("Failed to discover ingest scripts: %s", e)

    return {"scripts": scripts, "total_count": len(scripts)}


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
            request.model_dump(),
            job_timeout="1h",
            result_ttl=JOB_RESULT_TTL
        )

        logger.info(f"Enqueued ifctester job with ID: {job.id}")
        return {"job_id": job.id}
    except Exception as e:
        logger.error(f"Error enqueueing ifctester job: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# /ifc-gherkin moved to cde/pipeline-gateway (2026-05) — see CDE's
# app.services.validation._gherkin_endpoint for the new client wiring.


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
        # Enqueue job to the dedicated ifcdiff worker queue.
        #
        # `retry` recovers from non-deterministic SIGSEGV crashes inside
        # ifcopenshell/ifcdiff C extensions (signal 11 / "Work-horse terminated
        # unexpectedly; waitpid returned 139"). The same inputs reliably succeed
        # on a fresh work-horse, so up to 3 retries with backoff (5s, 30s, 90s)
        # gives close-to-100% completion without manual intervention.
        # Deterministic failures (e.g. botocore 404 on a missing pinned version)
        # still surface after the retries are exhausted.
        job = ifcdiff_queue.enqueue(
            "tasks.run_ifcdiff",  # Points to function in /app/tasks.py for ifcdiff-worker
            request.model_dump(),
            job_timeout="1h",
            result_ttl=JOB_RESULT_TTL,
            retry=Retry(max=3, interval=[5, 30, 90]),
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
            request.model_dump(),
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


def _frag_output_candidates(filename: str) -> list[str]:
    """Resolve possible S3 keys for a ``.frag`` artifact."""
    base = os.path.basename(filename)
    stem, ext = os.path.splitext(base)
    if ext.lower() == ".frag":
        names = [base]
    else:
        names = [f"{stem or base}.frag", f"{base}.frag"]
    keys: list[str] = []
    seen: set[str] = set()
    for name in names:
        for candidate in (
            s3.normalize_output_key(name, "frag"),
            s3.normalize_output_key(filename, "frag"),
            f"output/frag/{name}",
        ):
            if candidate not in seen:
                seen.add(candidate)
                keys.append(candidate)
    return keys


def _frag_input_from_filename(filename: str) -> str:
    """Derive the IFC input key used to bake a frag for ``filename``."""
    base = os.path.basename(filename)
    stem, ext = os.path.splitext(base)
    if ext.lower() == ".frag":
        ifc_name = f"{stem}.ifc"
    elif ext.lower() == ".ifc":
        ifc_name = base
    else:
        ifc_name = f"{base}.ifc"
    return s3.normalize_input_key(ifc_name)


@app.post("/fragments", tags=["Conversion"])
async def fragments_generate(request: FragmentsRequest, _: str = Depends(verify_access)):
    """Enqueue IFC → ``.frag`` conversion (ifcfrag-worker)."""
    try:
        validate_input_file_exists(request.input_filename)
        job = ifcfrag_queue.enqueue(
            "tasks.run_ifcfrag",
            request.model_dump(),
            job_timeout="1h",
            result_ttl=JOB_RESULT_TTL,
        )
        logger.info("Enqueued ifcfrag job with ID: %s", job.id)
        return {"job_id": job.id}
    except Exception as e:
        logger.error("Error enqueueing ifcfrag job: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/fragments/{filename:path}", tags=["Conversion"])
async def fragments_ensure(
    filename: str,
    _: str = Depends(verify_access),
    input_version_id: Optional[str] = None,
):
    """Return an existing ``output/frag/*.frag`` or enqueue generation on first request."""
    if not s3.is_enabled():
        raise HTTPException(status_code=503, detail="Object storage is disabled")

    frag_key: Optional[str] = None
    frag_meta: Optional[dict] = None
    for candidate in _frag_output_candidates(filename):
        try:
            if s3.object_exists(candidate):
                frag_key = candidate
                frag_meta = s3.head_metadata(candidate)
                break
        except Exception as exc:
            logger.warning("frag lookup for %s failed: %s", candidate, exc)

    if frag_key:
        presigned = s3.presigned_get_url_public(frag_key, expires_in=1800)
        return {
            "status": "ready",
            "bucket": s3.bucket_name(),
            "object_key": frag_key,
            "presigned_url": presigned,
            "sha256": (frag_meta or {}).get("sha256"),
            "size_bytes": (frag_meta or {}).get("size_bytes"),
            "version_id": (frag_meta or {}).get("version_id"),
        }

    input_key = _frag_input_from_filename(filename)
    validate_input_file_exists(input_key)
    payload: dict = {"input_filename": input_key}
    if input_version_id:
        payload["input_version_id"] = input_version_id
    job = ifcfrag_queue.enqueue(
        "tasks.run_ifcfrag",
        payload,
        job_timeout="1h",
        result_ttl=JOB_RESULT_TTL,
    )
    logger.info("Lazy-enqueued ifcfrag job %s for %s", job.id, input_key)
    return {
        "status": "generating",
        "job_id": job.id,
        "input_key": input_key,
        "expected_output_prefix": "output/frag/",
    }


@app.get("/artifacts/{subdir}/{filename:path}", tags=["File Operations"])
async def get_artifact_bytes(
    subdir: str,
    filename: str,
    _: str = Depends(verify_access),
):
    """Download a worker output file from object storage (e.g. output/xlsx/…)."""
    from fastapi.responses import Response

    base_name = os.path.basename(filename)
    if s3.is_enabled():
        key = s3.build_output_key(subdir, base_name)
        if not s3.object_exists(key):
            raise HTTPException(
                status_code=404, detail=f"Artifact not found: {key}"
            )
        obj = s3.get_client().get_object(Bucket=s3.bucket_name(), Key=key)
        body = obj["Body"].read()
        ct = obj.get("ContentType") or "application/octet-stream"
        return Response(content=body, media_type=ct)

    local = os.path.join("/output", subdir, base_name)
    if os.path.isfile(local):
        return FileResponse(local)
    raise HTTPException(status_code=404, detail=f"Artifact not found: {local}")


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


@app.get("/audit/guid-duplicates", tags=["Audit"])
async def audit_guid_duplicates(
    limit: int = 100,
    object_keys: Optional[str] = None,
    products_only: bool = True,
    _: str = Depends(verify_access),
):
    """Find IFC GUIDs that appear in multiple files.
    
    Returns duplicate GUIDs across indexed files, useful for detecting
    copy-paste issues or unintended element sharing between models.
    
    Args:
        limit: Maximum number of duplicate GUIDs to return (default 100).
        object_keys: Optional comma-separated list of object keys to filter by.
                    If provided, only checks for duplicates within these files.
        products_only: If True (default), only return IfcProduct duplicates,
                      excluding property sets, relationships, and other non-physical entities.
    
    Returns:
        dict: Summary stats and list of duplicate GUIDs with their occurrences.
    """
    from shared.db_client import db_client
    
    conn = db_client.get_connection()
    if not conn:
        raise HTTPException(status_code=503, detail="Database unavailable")
    
    # Entity types to exclude (non-physical elements and type definitions)
    excluded_types = (
        # Property sets and properties
        'IFCPROPERTYSET', 'IFCPROPERTYSINGLEVALUE', 'IFCPROPERTYLISTVALUE',
        # Relationships
        'IFCRELDEFINESBYPROPERTIES', 'IFCRELDEFINESBYTYPE', 'IFCRELDEFINESBYOBJECT',
        'IFCRELASSOCIATESMATERIAL', 'IFCRELASSOCIATESCLASSIFICATION', 'IFCRELASSOCIATESDOCUMENT',
        'IFCRELAGGREGATES', 'IFCRELCONTAINEDINSPATIALSTRUCTURE', 'IFCRELVOIDSELEMENT',
        'IFCRELFILLSELEMENT', 'IFCRELSPACEBOUNDARY', 'IFCRELCONNECTS',
        'IFCRELASSIGNS', 'IFCRELASSIGNSTOGROUP', 'IFCRELNESTS',
        # Materials
        'IFCMATERIALLAYERSETUSAGE', 'IFCMATERIALLAYERSET', 'IFCMATERIALLAYER', 'IFCMATERIAL',
        # Geometry
        'IFCSHAPEREPRESENTATION', 'IFCPRODUCTDEFINITIONSHAPE', 'IFCGEOMETRICREPRESENTATIONCONTEXT',
        'IFCLOCALPLACEMENT', 'IFCAXIS2PLACEMENT3D', 'IFCCARTESIANPOINT', 'IFCDIRECTION',
        # Project/ownership
        'IFCOWNERHISTORY', 'IFCPERSON', 'IFCORGANIZATION', 'IFCPERSONANDORGANIZATION',
        'IFCAPPLICATION', 'IFCUNITASSIGNMENT', 'IFCSIUNIT', 'IFCDERIVEDUNIT',
        # Classification
        'IFCCLASSIFICATION', 'IFCCLASSIFICATIONREFERENCE',
        # Presentation
        'IFCPRESENTATIONSTYLEASSIGNMENT', 'IFCSURFACESTYLE', 'IFCSURFACESTYLERENDERING',
        'IFCCOLOURRGB', 'IFCSTYLEDITEM', 'IFCSTYLEDREPRESENTATION',
        # Type definitions (we only want instances, not types)
        'IFCWALLTYPE', 'IFCSLABTYPE', 'IFCBEAMTYPE', 'IFCCOLUMNTYPE', 'IFCMEMBERTYPE',
        'IFCPLATETYPE', 'IFCDOORTYPE', 'IFCWINDOWTYPE', 'IFCCOVERINGTYPE', 'IFCRAILINGTYPE',
        'IFCSTAIRTYPE', 'IFCSTAIRFLIGHTTYPE', 'IFCRAMPTYPE', 'IFCRAMPFLIGHTTYPE',
        'IFCROOFTYPE', 'IFCCURTAINWALLTYPE', 'IFCBUILDINGELEMENTPROXYTYPE',
        'IFCFLOWTERMINALTYPE', 'IFCFLOWSEGMENTTYPE', 'IFCFLOWFITTINGTYPE',
        'IFCFLOWCONTROLLERTYPE', 'IFCFLOWMOVINGDEVICETYPE', 'IFCFLOWSTORAGEDEVICETYPE',
        'IFCFLOWTREATMENTDEVICETYPE', 'IFCENERGYCONVERSIONDEVICETYPE',
        'IFCDISTRIBUTIONELEMENTTYPE', 'IFCDISTRIBUTIONFLOWELEMENTTYPE',
        'IFCDISTRIBUTIONCONTROLELEMENTTYPE', 'IFCDISTRIBUTIONCHAMBERELEMENTTYPE',
        'IFCFURNISHINGELEMENTTYPE', 'IFCFURNITURETYPE', 'IFCSYSTEMFURNITUREELEMENTTYPE',
        'IFCSPACETYPE', 'IFCOPENINGELEMENTTYPE', 'IFCPABOREANTYPE',
        'IFCELEMENTASSEMBLYTYPE', 'IFCTRANSPORTELEMENTTYPE',
    )
    
    try:
        cursor = conn.cursor()
        
        # Build query based on whether object_keys filter is provided
        if object_keys:
            keys = [k.strip() for k in object_keys.split(",") if k.strip()]
            if products_only:
                cursor.execute("""
                    SELECT 
                        og.ifc_guid,
                        og.entity_type,
                        COUNT(DISTINCT ov.object_key) as file_count,
                        array_agg(DISTINCT ov.object_key ORDER BY ov.object_key) as files
                    FROM object_guids og
                    JOIN object_versions ov ON og.object_version_id = ov.id
                    WHERE ov.object_key = ANY(%s)
                      AND og.entity_type NOT IN %s
                    GROUP BY og.ifc_guid, og.entity_type
                    HAVING COUNT(DISTINCT ov.object_key) > 1
                    ORDER BY file_count DESC, og.ifc_guid
                    LIMIT %s;
                """, (keys, excluded_types, limit))
            else:
                cursor.execute("""
                    SELECT 
                        og.ifc_guid,
                        og.entity_type,
                        COUNT(DISTINCT ov.object_key) as file_count,
                        array_agg(DISTINCT ov.object_key ORDER BY ov.object_key) as files
                    FROM object_guids og
                    JOIN object_versions ov ON og.object_version_id = ov.id
                    WHERE ov.object_key = ANY(%s)
                    GROUP BY og.ifc_guid, og.entity_type
                    HAVING COUNT(DISTINCT ov.object_key) > 1
                    ORDER BY file_count DESC, og.ifc_guid
                    LIMIT %s;
                """, (keys, limit))
        else:
            if products_only:
                cursor.execute("""
                    SELECT 
                        og.ifc_guid,
                        og.entity_type,
                        COUNT(DISTINCT ov.object_key) as file_count,
                        array_agg(DISTINCT ov.object_key ORDER BY ov.object_key) as files
                    FROM object_guids og
                    JOIN object_versions ov ON og.object_version_id = ov.id
                    WHERE og.entity_type NOT IN %s
                    GROUP BY og.ifc_guid, og.entity_type
                    HAVING COUNT(DISTINCT ov.object_key) > 1
                    ORDER BY file_count DESC, og.ifc_guid
                    LIMIT %s;
                """, (excluded_types, limit))
            else:
                cursor.execute("""
                    SELECT 
                        og.ifc_guid,
                        og.entity_type,
                        COUNT(DISTINCT ov.object_key) as file_count,
                        array_agg(DISTINCT ov.object_key ORDER BY ov.object_key) as files
                    FROM object_guids og
                    JOIN object_versions ov ON og.object_version_id = ov.id
                    GROUP BY og.ifc_guid, og.entity_type
                    HAVING COUNT(DISTINCT ov.object_key) > 1
                    ORDER BY file_count DESC, og.ifc_guid
                    LIMIT %s;
                """, (limit,))
        
        duplicates = []
        for row in cursor.fetchall():
            duplicates.append({
                "guid": row[0],
                "entity_type": row[1],
                "file_count": row[2],
                "files": row[3],
            })
        
        # Get summary stats
        cursor.execute("""
            SELECT 
                COUNT(*) as total_guids,
                COUNT(DISTINCT ifc_guid) as unique_guids,
                COUNT(DISTINCT object_version_id) as indexed_files
            FROM object_guids;
        """)
        stats = cursor.fetchone()
        
        # Get list of indexed files
        cursor.execute("""
            SELECT DISTINCT ov.object_key
            FROM object_guids og
            JOIN object_versions ov ON og.object_version_id = ov.id
            ORDER BY ov.object_key;
        """)
        indexed_files_list = [row[0] for row in cursor.fetchall()]
        
        return {
            "total_guids_indexed": stats[0] if stats else 0,
            "unique_guids": stats[1] if stats else 0,
            "indexed_files": stats[2] if stats else 0,
            "indexed_files_list": indexed_files_list,
            "duplicate_count": len(duplicates),
            "duplicates": duplicates,
        }
    finally:
        conn.close()


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
            request.model_dump(),
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

    safe_basename = s3.safe_upload_basename(filename)
    upload_dir = upload_config["dir"]
    file_path = os.path.join(upload_dir, safe_basename)

    try:
        if s3.is_enabled():
            s3.ensure_bucket()
            key = s3.build_upload_key(safe_basename)
            content_type = file.content_type or None
            try:
                uploaded = s3.upload_fileobj_and_hash(
                    file.file, key, content_type=content_type
                )
                sha256 = uploaded["sha256"]
                size_bytes = uploaded["size_bytes"]
                s3_version_id = uploaded.get("version_id")
            except ClientError as e:
                err = (e.response or {}).get("Error") or {}
                code = err.get("Code") or ""
                msg = err.get("Message") or str(e)
                if code in ("XMinioStorageFull", "InsufficientStorage"):
                    logger.error(
                        "S3 storage full for key %s: %s %s", key, code, msg
                    )
                    raise HTTPException(
                        status_code=507,
                        detail=(
                            "Object storage is full; free disk space on the host or prune the "
                            f"MinIO/S3 bucket. ({code}: {msg})"
                        ),
                    ) from e
                raise
            # Shadow (SeaweedFS pilot): when the dual-write path produced a
            # second VersionId, stash it in the audit metadata so the parity
            # monitor + weekly summary can cross-check primary vs shadow
            # without a schema migration.
            audit_metadata = {
                "file_type": file_type,
                "original_filename": filename,
            }
            shadow_payload = uploaded.get("shadow") if isinstance(uploaded, dict) else None
            if shadow_payload:
                audit_metadata["shadow_version_id"] = shadow_payload.get("version_id")
                audit_metadata["shadow_sha256"] = shadow_payload.get("sha256")
                audit_metadata["shadow_size_bytes"] = shadow_payload.get("size_bytes")
                audit_metadata["shadow_bucket"] = shadow_payload.get("bucket")
                audit_metadata["shadow_object_key"] = shadow_payload.get("object_key")
            audit_id = audit_db.record_upload(
                bucket=s3.bucket_name(),
                object_key=key,
                sha256=sha256,
                size_bytes=size_bytes,
                version_id=s3_version_id,
                content_type=content_type,
                metadata=audit_metadata,
            )
            out = {
                "message": f"{file_type.upper()} file {filename} uploaded successfully",
                "storage": "s3",
                "bucket": s3.bucket_name(),
                "object_key": key,
                "object_url": f"s3://{s3.bucket_name()}/{key}",
                "file_path": f"s3://{s3.bucket_name()}/{key}",
                "original_filename": filename,
                "sha256": sha256,
                "size_bytes": size_bytes,
                "audit_id": audit_id,
            }
            if s3_version_id:
                out["version_id"] = s3_version_id
            return out

        os.makedirs(upload_dir, exist_ok=True)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        return {
            "message": f"{file_type.upper()} file {filename} uploaded successfully",
            "file_path": file_path,
            "original_filename": filename,
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


# Legacy-FS download jail: realpath(file_path) must live under one of these
# roots. Prevents /create_download_link from turning the api-gateway into an
# arbitrary-file-read primitive for anyone who has API key / allow-listed IP.
_DOWNLOAD_ROOTS = ("/uploads", "/output", "/examples")


def _is_path_under_allowed_roots(file_path: str) -> bool:
    try:
        real = os.path.realpath(file_path)
    except Exception:
        return False
    for root in _DOWNLOAD_ROOTS:
        root_real = os.path.realpath(root)
        if real == root_real or real.startswith(root_real + os.sep):
            return True
    return False


@app.post("/create_download_link", tags=["File Operations"])
async def create_download_link(request: DownloadRequest, _: str = Depends(verify_access)):
    """Create a temporary download link for a file. When the file lives in
    object storage, the token is backed by an S3 key and the `/download/{token}`
    endpoint will redirect to a short-lived presigned URL.

    Security: in legacy-filesystem mode the resolved path must live under
    /uploads, /output or /examples. In S3 mode the caller must either pass a
    bucket-relative key or an s3:// URI that resolves inside our bucket;
    arbitrary host paths are rejected.
    """
    file_path = request.file_path
    _, s3_key = _resolve_download_path(file_path)

    if s3_key is None:
        # Legacy filesystem mode (or S3 mode with a path not in the bucket).
        # Reject paths that escape the allowed roots before we even check
        # existence — we don't want to leak whether /etc/shadow exists.
        if not _is_path_under_allowed_roots(file_path):
            raise HTTPException(
                status_code=400,
                detail="file_path must live under /uploads, /output or /examples",
            )
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="File not found")

    token = secrets.token_urlsafe(32)
    expiry = datetime.now() + timedelta(minutes=30)
    download_links[token] = DownloadLink(
        file_path=s3_key if s3_key else file_path,
        token=token,
        expiry=expiry,
    )

    external = os.environ.get("IFC_PIPELINE_PREVIEW_EXTERNAL_URL")
    if not external or not external.strip():
        # No deployment-specific fallback: leaking a hardcoded production
        # hostname (ifcpipeline.byggstyrning.se, or any similar) would
        # deanonymise every fork that imports the code. Fail loud instead.
        logger.error(
            "IFC_PIPELINE_PREVIEW_EXTERNAL_URL not configured; cannot mint preview link"
        )
        raise HTTPException(
            status_code=500,
            detail="Preview URL not configured (set IFC_PIPELINE_PREVIEW_EXTERNAL_URL in .env)",
        )
    base_url = external.strip().rstrip('/')

    response = {"preview_url": f"{base_url}/{token}", "download_token": token, "expiry": expiry}
    if s3_key:
        response["storage"] = "s3"
        response["object_key"] = s3_key
    return response


def _attachment_disposition_for_basename(basename: str) -> str:
    """RFC 6266-style Content-Disposition for S3 ResponseContentDisposition."""
    base = (basename or "model.ifc").strip() or "model.ifc"
    ascii_safe = "".join(
        c if 32 <= ord(c) <= 126 and c not in ('"', "\\", ";") else "_"
        for c in base
    )[:200] or "model.ifc"
    encoded = quote(base, safe="")
    return f"attachment; filename=\"{ascii_safe}\"; filename*=UTF-8''{encoded}"


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
        disp = _attachment_disposition_for_basename(os.path.basename(target))
        url = s3.presigned_get_url_public(
            target,
            expires_in=remaining,
            response_content_disposition=disp,
        )
        return RedirectResponse(url=url, status_code=307)

    if not os.path.exists(target):
        del download_links[token]
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(target, filename=os.path.basename(target))

class _PinnedResolver(aiohttp.abc.AbstractResolver):
    """aiohttp resolver that serves a fixed (hostname, [ips]) mapping.

    Used to defeat DNS-rebinding TOCTOU in /download-from-url: we resolve the
    URL's hostname once, validate every returned IP is public, then force the
    outbound HTTP connection to use exactly those IPs. Any other hostname
    lookup (e.g. from a redirect we didn't disable) raises OSError.
    """

    def __init__(self, hostname: str, ips: list[str]) -> None:
        self._hostname = hostname
        self._ips = ips

    async def resolve(self, host, port=0, family=socket.AF_INET):
        if host != self._hostname:
            raise OSError(f"resolver is pinned to {self._hostname}, got {host}")
        return [
            {
                "hostname": host,
                "host": ip,
                "port": port,
                "family": family,
                "proto": 0,
                "flags": socket.AI_NUMERICHOST,
            }
            for ip in self._ips
        ]

    async def close(self) -> None:
        pass


def _resolve_and_validate_public(url: str) -> tuple[str, list[str]]:
    """Parse `url`, resolve its hostname once, and return (hostname, ips).

    Raises HTTPException(400) if the URL is malformed, the scheme is not
    HTTP(S), or any resolved IP is not globally routable. Caller should use
    the returned IPs to pin the connection so a later DNS lookup (rebinding)
    can't redirect the fetch to an internal address.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid URL")
    if parsed.scheme.lower() not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Only HTTP(S) URLs allowed")
    host = parsed.hostname
    if not host:
        raise HTTPException(status_code=400, detail="URL must have a valid host")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise HTTPException(status_code=400, detail="Cannot resolve hostname")
    ips: list[str] = []
    for info in infos:
        ip_str = info[4][0]
        ip_obj = ipaddress.ip_address(ip_str)
        if not ip_obj.is_global:
            raise HTTPException(status_code=400, detail="URL points to restricted network")
        if ip_str not in ips:
            ips.append(ip_str)
    if not ips:
        raise HTTPException(status_code=400, detail="Cannot resolve hostname")
    return host, ips


@app.post("/download-from-url", tags=["File Operations"])
async def download_from_url(request: DownloadUrlRequest, _: str = Depends(verify_access)):
    """
    Download a file from a URL and save it to the uploads directory.

    When object storage is enabled, the file is uploaded to S3 and an audit
    record is created. If `source_etag` is provided and matches an existing
    audit row, the download is skipped and the cached file metadata is returned.

    Args:
        request (DownloadUrlRequest): The request containing the download URL.

    Returns:
        dict: A message indicating success or failure, file metadata, and
        version_id when using S3.
    """
    # DNS-pin to defeat rebinding TOCTOU: resolve once, validate every IP is
    # public, then force aiohttp to use those exact IPs for the connection.
    hostname, resolved_ips = _resolve_and_validate_public(str(request.url))

    # Determine the filename we'll use for storage
    raw_filename = request.output_filename or os.path.basename(urlparse(str(request.url)).path) or "download"
    try:
        original_filename, storage_basename, key = s3.build_upload_key_from_original(raw_filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Short-circuit: if source_etag provided, check if we already have this exact version
    source_etag = (request.source_etag or "").strip()
    if source_etag and s3.is_enabled():
        existing = audit_db.lookup_by_source_etag(source_etag, object_key=key)
        if not existing:
            # Key may differ across sanitization rules; etag is still unique per SP version.
            existing = audit_db.lookup_by_source_etag(source_etag)
            if existing and existing.get("object_key"):
                key = existing["object_key"]
        if existing:
            # Verify the audited version still exists in MinIO before returning a cache
            # hit. Lifecycle expiration of non-current versions (or manual cleanup) can
            # leave the audit row pointing at a now-deleted VersionId, in which case
            # downstream callers that pin to that VersionId hit 404. If the pinned
            # version is gone but the current version of the key has the same sha256,
            # the bytes are still available — return a cache hit using the current
            # VersionId. Otherwise fall through and re-download.
            cached_version_id = existing.get("version_id")
            cached_sha256 = (existing.get("sha256") or "").lower()
            resolved_version_id = None

            if cached_version_id and s3.object_exists(key, version_id=cached_version_id):
                resolved_version_id = cached_version_id
            else:
                current = s3.head_metadata(key)
                if current is not None:
                    current_sha = (current.get("sha256") or "").lower()
                    if cached_sha256 and current_sha and current_sha == cached_sha256:
                        resolved_version_id = current.get("version_id")
                        logger.info(
                            "download_from_url: audited version %s missing, using current "
                            "version %s (sha256 matches) for key=%s",
                            cached_version_id, resolved_version_id, key,
                        )

            if resolved_version_id is not None or (not cached_version_id and s3.object_exists(key)):
                logger.info("download_from_url: cache hit for source_etag=%s, key=%s", source_etag, key)
                out = {
                    "message": f"File cached (source_etag match) as {original_filename}",
                    "storage": "s3",
                    "bucket": s3.bucket_name(),
                    "object_key": key,
                    "object_url": f"s3://{s3.bucket_name()}/{key}",
                    "file_path": f"s3://{s3.bucket_name()}/{key}",
                    "original_filename": original_filename,
                    "storage_basename": os.path.basename(key),
                    "sha256": existing.get("sha256"),
                    "size_bytes": existing.get("size_bytes"),
                    "audit_id": existing.get("id"),
                    "cached": True,
                }
                if resolved_version_id:
                    out["version_id"] = resolved_version_id
                return out

            logger.warning(
                "download_from_url: stale cache for source_etag=%s key=%s "
                "(audited version_id=%s missing and current sha256 mismatch); re-downloading",
                source_etag, key, cached_version_id,
            )

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        connector = aiohttp.TCPConnector(resolver=_PinnedResolver(hostname, resolved_ips))
        timeout = ClientTimeout(total=3600)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.get(request.url, headers=headers, allow_redirects=False) as response:
                if response.status != 200:
                    # Upstream (e.g. S3/Autodesk presigned URL) errors — 403 is common when
                    # the presign expired or the signature no longer matches.
                    err_body = (await response.text())[:500]
                    logger.warning(
                        "download_from_url: upstream HTTP %s for host %s, body head: %r",
                        response.status,
                        hostname,
                        err_body,
                    )
                    raise HTTPException(
                        status_code=response.status,
                        detail=f"Failed to download file: HTTP {response.status}",
                    )

                # For S3 mode: stream to a temporary file, then upload to S3 with audit
                if s3.is_enabled():
                    import tempfile
                    import hashlib

                    s3.ensure_bucket()
                    content_type = response.headers.get('Content-Type')

                    # Stream to temp file while computing hash
                    sha256_hash = hashlib.sha256()
                    size_bytes = 0

                    with tempfile.NamedTemporaryFile(delete=False) as tmp_f:
                        tmp_path = tmp_f.name
                        while True:
                            chunk = await response.content.read(8192)
                            if not chunk:
                                break
                            tmp_f.write(chunk)
                            sha256_hash.update(chunk)
                            size_bytes += len(chunk)

                    sha256 = sha256_hash.hexdigest()

                    try:
                        # Upload to S3 (may be cached by hash)
                        with open(tmp_path, 'rb') as upload_f:
                            uploaded = s3.upload_fileobj_and_hash(
                                upload_f, key, content_type=content_type
                            )
                        s3_version_id = uploaded.get("version_id")

                        # Shadow (SeaweedFS pilot) pass-through, same as the
                        # /upload route above.
                        audit_metadata = {
                            "original_filename": original_filename,
                            "source_url": str(request.url)[:500],  # Truncate for safety
                            "source_etag": source_etag or None,
                        }
                        shadow_payload = uploaded.get("shadow") if isinstance(uploaded, dict) else None
                        if shadow_payload:
                            audit_metadata["shadow_version_id"] = shadow_payload.get("version_id")
                            audit_metadata["shadow_sha256"] = shadow_payload.get("sha256")
                            audit_metadata["shadow_size_bytes"] = shadow_payload.get("size_bytes")
                            audit_metadata["shadow_bucket"] = shadow_payload.get("bucket")
                            audit_metadata["shadow_object_key"] = shadow_payload.get("object_key")
                        # Record to audit DB
                        audit_id = audit_db.record_upload(
                            bucket=s3.bucket_name(),
                            object_key=key,
                            sha256=sha256,
                            size_bytes=uploaded.get("size_bytes", size_bytes),
                            version_id=s3_version_id,
                            content_type=content_type,
                            metadata=audit_metadata,
                        )

                        out = {
                            "message": f"File downloaded successfully as {original_filename}",
                            "storage": "s3",
                            "bucket": s3.bucket_name(),
                            "object_key": key,
                            "object_url": f"s3://{s3.bucket_name()}/{key}",
                            "file_path": f"s3://{s3.bucket_name()}/{key}",
                            "original_filename": original_filename,
                            "storage_basename": storage_basename,
                            "sha256": sha256,
                            "size_bytes": uploaded.get("size_bytes", size_bytes),
                            "audit_id": audit_id,
                            "cached": False,
                        }
                        if s3_version_id:
                            out["version_id"] = s3_version_id
                        return out
                    finally:
                        # Clean up temp file
                        try:
                            os.unlink(tmp_path)
                        except Exception:
                            pass
                else:
                    # Legacy local filesystem mode
                    file_path = os.path.join("/uploads", storage_basename)
                    os.makedirs("/uploads", exist_ok=True)

                    with open(file_path, 'wb') as f:
                        while True:
                            chunk = await response.content.read(8192)
                            if not chunk:
                                break
                            f.write(chunk)

                    return {
                        "message": f"File downloaded successfully as {original_filename}",
                        "file_path": file_path,
                        "original_filename": original_filename,
                        "storage_basename": storage_basename,
                    }

    except HTTPException:
        # Do not wrap — upstream HTTP status (403, 404, etc.) must reach the client.
        raise
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
            request.model_dump(),
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
        import re as _re
        # Validate job_id: RQ emits canonical UUIDs; accept that shape only so
        # a malicious caller can't smuggle a traversal segment through
        # f"{job_id}-{log_type}" into os.path.join below.
        if not _re.match(
            r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$',
            job_id,
        ):
            raise HTTPException(
                status_code=400,
                detail="Invalid job_id. Must be a canonical UUID (the RQ job id).",
            )

        # Validate log_type: alphanumeric, hyphens and underscores only
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
                json=request.model_dump(),
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
                json=request.model_dump(),
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


# Static-asset extensions the viewer SPA legitimately requests from
# /assets/* and /node_modules/*. These routes are intentionally unauthenticated
# because a browser following a public `/{token}` download link needs to fetch
# them without an API key. We restrict the extension set so only bundle files
# can be served — no `.env`, `.py`, `.json` configs, etc. leak through.
_VIEWER_ASSET_EXTS = (
    ".js", ".mjs", ".cjs", ".css", ".map", ".wasm", ".data",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".html", ".txt",
)


def _assert_viewer_asset(path: str) -> None:
    """Raise 404 unless `path` ends with an allow-listed viewer-bundle
    extension. Called after _sanitize_proxy_path so path-traversal is already
    blocked."""
    lower = path.lower().split("?", 1)[0]
    if not lower.endswith(_VIEWER_ASSET_EXTS):
        raise HTTPException(status_code=404, detail="Not found")


@app.get("/assets/{path:path}")
async def viewer_assets(path: str):
    """
    Serve viewer assets (CSS, JS, etc.).

    Intentionally unauthenticated: the ifc-viewer SPA is loaded by browsers
    following a public `/{token}` download link and cannot carry an API key.
    `_assert_viewer_asset` caps the surface to known bundle extensions.
    """
    try:
        _sanitize_proxy_path(path)
        _assert_viewer_asset(path)
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
    Serve node_modules assets (including WebWorkers).

    Same rationale + extension cap as /assets/ above.
    """
    try:
        _sanitize_proxy_path(path)
        _assert_viewer_asset(path)
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
