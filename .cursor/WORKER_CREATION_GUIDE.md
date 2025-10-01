# IFC Pipeline Worker Creation Guide

This guide provides a comprehensive template and requirements for creating new workers in the IFC Pipeline microservice architecture.

## Table of Contents
- [Overview](#overview)
- [Worker Architecture](#worker-architecture)
- [Required Files](#required-files)
- [Step-by-Step Creation Guide](#step-by-step-creation-guide)
- [Integration with API Gateway](#integration-with-api-gateway)
- [Deployment Configuration](#deployment-configuration)
- [Best Practices](#best-practices)
- [Examples](#examples)

---

## Overview

Workers in the IFC Pipeline are independent microservices that:
- Process specific IFC-related operations asynchronously
- Use Redis Queue (RQ) for job management
- Share common infrastructure through mounted volumes
- Communicate via the API Gateway
- Utilize a shared Python library for common classes and utilities

---

## Worker Architecture

### Core Components

Each worker consists of:
1. **tasks.py** - Contains the worker logic and job processing functions
2. **Dockerfile** - Defines the container build process
3. **requirements.txt** - Lists Python dependencies
4. **Queue Name** - Unique queue identifier for RQ

### Data Flow

```
Client → API Gateway → Redis Queue → Worker → Shared Volumes → Response
```

### Shared Resources

All workers have access to:
- `/uploads` - Input files directory
- `/output` - Output files directory (with subdirectories per worker)
- `/examples` - Example files directory
- `shared` library - Common Python classes and utilities

---

## Required Files

### 1. Directory Structure

```
ifc-pipeline/
├── your-worker-name-worker/
│   ├── tasks.py
│   ├── Dockerfile
│   └── requirements.txt
├── shared/
│   ├── classes.py
│   └── db_client.py (optional)
└── docker-compose.yml
```

### 2. tasks.py Template

```python
import logging
import os
from shared.classes import YourRequestClass

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_your_operation(job_data: dict) -> dict:
    """
    Process your specific IFC operation.
    
    Args:
        job_data: Dictionary containing job parameters conforming to YourRequestClass.
        
    Returns:
        Dictionary containing the operation results.
    """
    try:
        # Parse and validate request
        request = YourRequestClass(**job_data)
        logger.info(f"Starting {operation_name} job for: {request.input_file}")

        # Define paths
        models_dir = "/uploads"
        output_dir = "/output/your-subdirectory"
        input_path = os.path.join(models_dir, request.input_file)
        output_path = os.path.join(output_dir, request.output_file)
        
        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)

        # Validate input file existence
        if not os.path.exists(input_path):
            logger.error(f"Input file not found: {input_path}")
            raise FileNotFoundError(f"Input file {request.input_file} not found")
        
        logger.info(f"Input file found: {input_path}")

        # === YOUR PROCESSING LOGIC HERE ===
        # 1. Load IFC file or other inputs
        # 2. Perform operations
        # 3. Generate outputs
        
        # Example success response
        logger.info(f"Operation completed successfully: {output_path}")
        return {
            "success": True,
            "message": "Operation completed successfully",
            "output_path": output_path
        }

    except FileNotFoundError as e:
        logger.error(f"File not found error: {str(e)}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Error during operation: {str(e)}", exc_info=True)
        raise  # Re-raise for RQ failure handling
```

### 3. Dockerfile Template

```dockerfile
# your-worker-name-worker/Dockerfile
FROM python:3.10-slim AS base

WORKDIR /app

# Copy shared library and install it
COPY shared /app/shared
RUN pip install -e /app/shared

# Install service-specific dependencies
COPY your-worker-name-worker/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy worker code directly to /app
COPY your-worker-name-worker/tasks.py /app/

# Create necessary directories
RUN mkdir -p /output/your-subdirectory /uploads
RUN chmod -R 777 /output /uploads

# Run the RQ worker pointing to the specific queue
CMD ["rq", "worker", "your-queue-name", "--url", "redis://redis:6379/0"]
```

### 4. requirements.txt Template

```txt
# Core dependencies (always required)
rq
pydantic

# IFC-related dependencies (as needed)
ifcopenshell

# Your specific dependencies
your-library-name
another-dependency
```

---

## Step-by-Step Creation Guide

### Step 1: Define Your Request Model

Add your request class to `shared/classes.py`:

```python
class YourOperationRequest(BaseModel):
    input_file: str
    output_file: str
    # Add your specific parameters
    parameter1: str
    parameter2: Optional[int] = None
    parameter3: bool = False
```

### Step 2: Create Worker Directory

```bash
mkdir -p ifc-pipeline/your-worker-name-worker
cd ifc-pipeline/your-worker-name-worker
```

### Step 3: Create tasks.py

Create your `tasks.py` file following the template above. Key requirements:
- Import from `shared.classes`
- Use standard logging
- Follow the directory structure (`/uploads`, `/output`)
- Return a dictionary with `success`, `message`, and `output_path`
- Properly handle exceptions

### Step 4: Create Dockerfile

Create your `Dockerfile` following the template. Important notes:
- Always use `python:3.10` or `python:3.10-slim` base image
- Copy and install the `shared` library first
- Create necessary output directories
- Set proper permissions (777 for volumes)
- Use the correct queue name in CMD

### Step 5: Create requirements.txt

List all dependencies. Always include:
- `rq` - Redis Queue
- `pydantic` - For request validation
- Your specific libraries

### Step 6: Add to docker-compose.yml

Add your worker service to `docker-compose.yml`:

```yaml
your-worker-name-worker:
  build:
    context: .
    dockerfile: your-worker-name-worker/Dockerfile
  volumes:
    - ./shared/uploads:/uploads
    - ./shared/output:/output
    - ./shared/examples:/examples
  environment:
    - PYTHONUNBUFFERED=1
    - LOG_LEVEL=DEBUG
    - REDIS_URL=redis://redis:6379/0
    # Add database env vars if needed:
    # - POSTGRES_HOST=postgres
    # - POSTGRES_PORT=5432
    # - POSTGRES_DB=${POSTGRES_DB:-ifcpipeline}
    # - POSTGRES_USER=${POSTGRES_USER:-ifcpipeline}
    # - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
  depends_on:
    - redis
    # - postgres  # Add if using database
  restart: unless-stopped
  deploy:
    resources:
      limits:
        cpus: '0.5'  # Adjust based on your needs
        memory: 512M  # Adjust based on your needs
```

Also add your worker to the `api-gateway` dependencies:

```yaml
api-gateway:
  depends_on:
    - your-worker-name-worker  # Add this line
    - ifcconvert-worker
    # ... other workers
```

### Step 7: Register Queue in API Gateway

In `api-gateway/api-gateway.py`, create a queue for your worker:

```python
# Add near line 60
your_queue = Queue('your-queue-name', connection=redis_conn)
```

Add to health check (around line 218):

```python
all_queues = {
    "your_queue": your_queue,  # Add this line
    "ifcconvert_queue": ifcconvert_queue,
    # ... other queues
}
```

### Step 8: Create API Endpoint

Add an endpoint in `api-gateway/api-gateway.py`:

```python
@app.post("/your-operation", tags=["Your Category"])
async def your_operation(request: YourOperationRequest, _: str = Depends(verify_access)):
    """
    Description of your operation.
    
    Args:
        request (YourOperationRequest): The request body containing operation parameters.
    
    Returns:
        dict: A dictionary containing the job ID.
    """
    try:
        job = your_queue.enqueue(
            "tasks.run_your_operation",  # Must match function name in tasks.py
            request.dict(),
            job_timeout="1h"  # Adjust timeout as needed
        )
        
        logger.info(f"Enqueued your-operation job with ID: {job.id}")
        return {"job_id": job.id}
    except Exception as e:
        logger.error(f"Error enqueueing your-operation job: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
```

Import your request class at the top of the file:

```python
from shared.classes import (
    YourOperationRequest,  # Add this line
    IfcConvertRequest,
    # ... other imports
)
```

---

## Integration with API Gateway

### Request Flow

1. **Client sends request** to API Gateway endpoint
2. **API Gateway validates** request using Pydantic model
3. **Job is enqueued** to Redis with specific queue name
4. **Worker picks up job** from its designated queue
5. **Worker processes** and returns result to Redis
6. **Client polls** `/jobs/{job_id}/status` for completion

### Queue Naming Convention

- Queue name should match: `your-worker-name` (without `-worker` suffix)
- Examples: `ifc5d`, `ifccsv`, `ifcdiff`, `ifctester`

### Job Timeout Guidelines

- Simple operations: `1h`
- Complex operations (clash detection, diff): `2h`
- Very large files: Adjust accordingly

---

## Deployment Configuration

### Resource Limits

Set appropriate CPU and memory limits in `docker-compose.yml`:

**Light operations** (CSV export, simple conversions):
```yaml
cpus: '0.5'
memory: 512M
```

**Medium operations** (QTO calculations, validation):
```yaml
cpus: '0.5'
memory: 1G
```

**Heavy operations** (clash detection, diff):
```yaml
cpus: '4.0'
memory: 12G
```

### Scaling Workers

For high-load operations, add replicas:

```yaml
deploy:
  replicas: 2  # Run 2 instances of this worker
  resources:
    limits:
      cpus: '4.0'
      memory: '12G'
```

### Volume Mounts

Always mount these volumes:
```yaml
volumes:
  - ./shared/uploads:/uploads
  - ./shared/output:/output
  - ./shared/examples:/examples
```

---

## Best Practices

### 1. Logging

- Use structured logging with appropriate levels
- Log at the start and end of operations
- Include file paths and key parameters
- Log errors with stack traces using `exc_info=True`

```python
logger.info(f"Starting operation for {input_file}")
logger.error(f"Error during operation: {str(e)}", exc_info=True)
```

### 2. Error Handling

- Validate inputs before processing
- Use specific exception types (FileNotFoundError, ValueError)
- Always re-raise exceptions for RQ failure tracking
- Return structured error information

```python
try:
    # operation
except FileNotFoundError as e:
    logger.error(f"File not found: {str(e)}", exc_info=True)
    raise
except Exception as e:
    logger.error(f"Unexpected error: {str(e)}", exc_info=True)
    raise
```

### 3. Directory Management

- Always use `os.makedirs(output_dir, exist_ok=True)`
- Validate file existence before processing
- Use consistent path joining with `os.path.join()`
- Create worker-specific output subdirectories

```python
output_dir = "/output/your-operation"
os.makedirs(output_dir, exist_ok=True)
```

### 4. Return Structure

Always return a consistent dictionary structure:

```python
return {
    "success": True,  # or False
    "message": "Operation completed successfully",
    "output_path": "/output/your-operation/result.ifc",
    # Optional additional fields:
    "db_id": 123,  # If saved to database
    "statistics": {...}  # Operation-specific data
}
```

### 5. Database Integration (Optional)

If your worker needs database access:

1. Add database environment variables to docker-compose.yml
2. Import `db_client` from shared library
3. Add `depends_on: postgres` to your service
4. Save results for long-term storage and querying

```python
from shared.db_client import save_your_result

db_id = save_your_result(
    input_file=request.input_file,
    output_file=output_path,
    result_data=results
)
```

### 6. File Validation

Always validate input files:

```python
if not os.path.exists(input_path):
    logger.error(f"Input file not found: {input_path}")
    raise FileNotFoundError(f"Input file {request.input_file} not found")
```

### 7. Output Verification

Verify output was created:

```python
if not os.path.exists(output_path):
    logger.error(f"Output file was not created: {output_path}")
    raise RuntimeError("Output file was not created successfully")
```

---

## Examples

### Example 1: Simple File Converter Worker

**Purpose**: Convert IFC to a custom format

**Files**:

`ifc-custom-worker/tasks.py`:
```python
import logging
import os
import ifcopenshell
from shared.classes import CustomConvertRequest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_custom_convert(job_data: dict) -> dict:
    try:
        request = CustomConvertRequest(**job_data)
        logger.info(f"Converting {request.input_file}")
        
        input_path = os.path.join("/uploads", request.input_file)
        output_dir = "/output/custom"
        output_path = os.path.join(output_dir, request.output_file)
        
        os.makedirs(output_dir, exist_ok=True)
        
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"File {request.input_file} not found")
        
        # Load and process
        ifc_file = ifcopenshell.open(input_path)
        # ... your conversion logic ...
        
        logger.info(f"Conversion complete: {output_path}")
        return {
            "success": True,
            "message": "Conversion successful",
            "output_path": output_path
        }
        
    except Exception as e:
        logger.error(f"Conversion error: {str(e)}", exc_info=True)
        raise
```

`ifc-custom-worker/requirements.txt`:
```txt
ifcopenshell
rq
pydantic
```

`ifc-custom-worker/Dockerfile`:
```dockerfile
FROM python:3.10-slim

WORKDIR /app
COPY shared /app/shared
RUN pip install -e /app/shared

COPY ifc-custom-worker/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY ifc-custom-worker/tasks.py /app/

RUN mkdir -p /output/custom /uploads
RUN chmod -R 777 /output /uploads

CMD ["rq", "worker", "custom", "--url", "redis://redis:6379/0"]
```

### Example 2: Analysis Worker with Database

**Purpose**: Analyze IFC and save results to database

`ifc-analysis-worker/tasks.py`:
```python
import logging
import os
import json
import ifcopenshell
from shared.classes import AnalysisRequest
from shared.db_client import save_analysis_result

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_analysis(job_data: dict) -> dict:
    try:
        request = AnalysisRequest(**job_data)
        logger.info(f"Analyzing {request.input_file}")
        
        input_path = os.path.join("/uploads", request.input_file)
        output_dir = "/output/analysis"
        output_path = os.path.join(output_dir, f"{request.input_file}_analysis.json")
        
        os.makedirs(output_dir, exist_ok=True)
        
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"File {request.input_file} not found")
        
        # Perform analysis
        ifc_file = ifcopenshell.open(input_path)
        analysis_results = {
            "element_count": len(ifc_file.by_type("IfcProduct")),
            "schema": ifc_file.schema,
            # ... more analysis ...
        }
        
        # Save to file
        with open(output_path, 'w') as f:
            json.dump(analysis_results, f, indent=2)
        
        # Save to database
        db_id = save_analysis_result(
            input_file=request.input_file,
            output_file=output_path,
            analysis_data=analysis_results
        )
        
        return {
            "success": True,
            "message": "Analysis complete",
            "output_path": output_path,
            "db_id": db_id,
            "results": analysis_results
        }
        
    except Exception as e:
        logger.error(f"Analysis error: {str(e)}", exc_info=True)
        raise
```

---

## Testing Your Worker

### 1. Build and Start Services

```bash
cd ifc-pipeline
docker-compose build your-worker-name-worker
docker-compose up -d your-worker-name-worker
```

### 2. Check Worker Logs

```bash
docker-compose logs -f your-worker-name-worker
```

### 3. Test API Endpoint

```bash
curl -X POST "http://localhost:8000/your-operation" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "input_file": "test.ifc",
    "output_file": "result.ext",
    "parameter1": "value1"
  }'
```

### 4. Check Job Status

```bash
curl -X GET "http://localhost:8000/jobs/{job_id}/status" \
  -H "X-API-Key: your-api-key"
```

### 5. Monitor Queue

Access RQ Dashboard at: `http://localhost:9181`

---

## Troubleshooting

### Worker Not Starting
- Check Docker logs: `docker-compose logs your-worker-name-worker`
- Verify queue name matches in Dockerfile CMD and API Gateway
- Ensure Redis is running: `docker-compose ps redis`

### File Not Found Errors
- Verify volume mounts in docker-compose.yml
- Check file exists in `/uploads` directory
- Ensure proper path construction with `os.path.join()`

### Import Errors
- Verify `shared` library is installed in Dockerfile
- Check all dependencies are in requirements.txt
- Rebuild container: `docker-compose build your-worker-name-worker`

### Job Timeouts
- Increase job_timeout in API Gateway endpoint
- Check worker resource limits in docker-compose.yml
- Monitor worker logs for performance issues

### Permission Denied
- Ensure directories have proper permissions (777)
- Check RUN chmod command in Dockerfile
- Verify volumes are mounted correctly

---

## Checklist for New Workers

- [ ] Created worker directory structure
- [ ] Implemented tasks.py with proper error handling
- [ ] Created Dockerfile with shared library installation
- [ ] Listed all dependencies in requirements.txt
- [ ] Added request model to shared/classes.py
- [ ] Added worker service to docker-compose.yml
- [ ] Added worker to api-gateway dependencies
- [ ] Created queue in api-gateway.py
- [ ] Added queue to health check
- [ ] Created API endpoint in api-gateway.py
- [ ] Imported request class in api-gateway.py
- [ ] Set appropriate resource limits
- [ ] Added proper logging
- [ ] Implemented consistent return structure
- [ ] Tested with sample files
- [ ] Documented API endpoint with OpenAPI tags
- [ ] Verified worker appears in RQ Dashboard

---

## Additional Resources

- **Redis Queue (RQ) Documentation**: https://python-rq.org/
- **IfcOpenShell Documentation**: https://ifcopenshell.org/
- **FastAPI Documentation**: https://fastapi.tiangolo.com/
- **Docker Compose Documentation**: https://docs.docker.com/compose/

---

## Support and Contributing

For questions or issues with worker development:
1. Check existing worker implementations for reference
2. Review Docker logs for detailed error messages
3. Consult RQ Dashboard for queue status
4. Review this guide and follow the checklist

When contributing new workers:
- Follow the patterns established in existing workers
- Use consistent naming conventions
- Document your API endpoints
- Test thoroughly before submitting
- Update this guide if you discover new patterns or requirements

