# IFC Pipeline: Worker Migration Plan

## Overview

This document outlines the plan to convert the existing HTTP-based microservice architecture to a specialized RQ worker architecture. This will eliminate HTTP overhead between the API Gateway and the processing services, while maintaining the isolation and scaling benefits of specialized services.

## Goals

- Eliminate HTTP calls between the API Gateway and processing services
- Maintain service isolation (dependencies, resources, fault tolerance)
- Reuse existing directory structure and code where possible
- Allow for independent scaling of worker types based on workload
- Preserve the clean API interface for clients

## Successfully Migrated Services

### 1. ifctester → ifctester-worker

The ifctester service has been successfully migrated to a worker-based approach with the following characteristics:

- Dedicated container that only processes `ifctester` queue jobs
- Direct Python code execution rather than HTTP calls
- Files stored in `/app` directory without using a Python package structure
- Explicit imports needed for external packages like `ifctester`

## Migration Process (Refined Based on Experience)

Based on our experience with migrating the ifctester service, we've refined our approach for future migrations.

### Step 1: Create Worker Directory

Create a new directory for the worker service using the format `{service-name}-worker`. For example, `ifcclash-worker`.

```bash
mkdir -p ifcclash-worker
```

### Step 2: Create Core Task File

Create the main task file that implements the core functionality. Avoid using `__init__.py` files to prevent module import confusion.

```python
# ifcclash-worker/tasks.py
import logging
import os
from shared.classes import IfcClashRequest
# Import required libraries explicitly with specific aliases if needed
import ifcopenshell

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_ifcclash_detection(job_data: dict) -> dict:
    """
    Process an IFC clash detection job
    
    Args:
        job_data: Dictionary containing the job parameters
        
    Returns:
        Dictionary containing the job results
    """
    try:
        request = IfcClashRequest(**job_data)
        logger.info(f"Processing ifcclash job")
        
        # Core logic from the original ifcclash service
        # ...
        
        return {"success": True, "result": "clash results"}
    except Exception as e:
        logger.error(f"Error during clash detection: {str(e)}", exc_info=True)
        # Re-raise for RQ to mark as failed
        raise
```

### Step 3: Create Requirements File

Copy the existing requirements from the HTTP service and add RQ.

```
# ifcclash-worker/requirements.txt
ifcopenshell
numpy
shapely
rq
```

### Step 4: Create Dockerfile

Create a Dockerfile that copies all files directly to the `/app` directory:

```dockerfile
# ifcclash-worker/Dockerfile
FROM python:3.9

WORKDIR /app

# Copy shared library and install it
COPY shared /app/shared
RUN pip install -e /app/shared

# Install service-specific dependencies
COPY ifcclash-worker/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy worker code directly to /app
COPY ifcclash-worker/ /app/

# Run the RQ worker pointing to the specific queue
CMD ["rq", "worker", "ifcclash", "--url", "redis://redis:6379/0"]
```

### Step 5: Modify API Gateway

Update the API Gateway to use the new worker function:

```python
@app.post("/ifcclash", tags=["Clash Detection"])
async def ifcclash(request: IfcClashRequest, _: str = Depends(verify_access)):
    """
    Detect clashes between IFC models.
    """
    try:
        job = ifcclash_queue.enqueue(
            "tasks.run_ifcclash_detection",  # Points directly to function in /app/tasks.py
            request.dict(),
            job_timeout="2h"
        )
        
        logger.info(f"Enqueued ifcclash job with ID: {job.id}")
        return {"job_id": job.id}
    except Exception as e:
        logger.error(f"Error enqueueing ifcclash job: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
```

### Step 6: Update docker-compose.yml

Update the docker-compose.yml to add the new worker service and remove the old HTTP service:

```yaml
# Replace old service
# ifcclash:
#   build:
#     context: .
#     dockerfile: ifcclash/Dockerfile
#   volumes:
#     - ./shared/uploads:/uploads
#     - ./shared/output:/output
#     - ./shared/examples:/examples
#   environment:
#     - PYTHONUNBUFFERED=1
#     - LOG_LEVEL=DEBUG
#   restart: unless-stopped

# New worker service
ifcclash-worker:
  build:
    context: .
    dockerfile: ifcclash-worker/Dockerfile
  volumes:
    - ./shared/uploads:/uploads
    - ./shared/output:/output
    - ./shared/examples:/examples
  environment:
    - PYTHONUNBUFFERED=1
    - LOG_LEVEL=DEBUG
    - REDIS_URL=redis://redis:6379/0
  depends_on:
    - redis
  restart: unless-stopped
  deploy:
    resources:
      limits:
        cpus: '4.0'
        memory: 6G
```

Also update the rq-worker service to remove the processed queue:

```yaml
rq-worker:
  # ... existing configuration ...
  command: rq worker default ifcconvert ifccsv ifcdiff ifc5d --url redis://redis:6379/0  # Remove ifcclash
```

## Migration Order

Based on service complexity and importance, we'll migrate the remaining services in this order:

1. ✅ ifctester → ifctester-worker (completed)
2. 🔲 ifcclash → ifcclash-worker (next to implement)
3. 🔲 ifcconvert → ifcconvert-worker
4. 🔲 ifccsv → ifccsv-worker
5. 🔲 ifcdiff → ifcdiff-worker
6. 🔲 ifc5d → ifc5d-worker
7. 🔲 ifc2json → ifc2json-worker

## Key Learnings from ifctester-worker Migration

1. **Direct Function Calls**: Avoid import paths by placing workers directly in `/app` directory.
2. **No `__init__.py`**: Avoid Python package structures to prevent import resolution issues.
3. **Explicit Package Imports**: Be specific when importing external libraries to prevent naming conflicts. Use aliases if necessary.
4. **Simplified Docker Images**: Copy files directly to `/app` rather than to a subpackage directory.
5. **Job Parameters**: Ensure job parameters match between the API gateway and the worker.

## Implementation Plan for ifcclash

### 1. Core Functionality

The ifcclash-worker must:
- Process clash detection requests from IFC models
- Calculate spatial interference between different models
- Generate clash reports in JSON format
- Support visualization data for clashes

### 2. Key Files

- `/ifcclash-worker/tasks.py`: Main task implementation
- `/ifcclash-worker/requirements.txt`: Dependencies
- `/ifcclash-worker/Dockerfile`: Container configuration

### 3. Special Considerations

- **Memory Requirements**: Clash detection is resource-intensive, maintain the higher memory allocation
- **Processing Time**: Use longer job_timeout values (2h recommended)
- **Spatial Libraries**: Needs special handling for geometric calculation libraries 