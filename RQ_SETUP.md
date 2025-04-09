# Redis Queue (RQ) Setup and Usage Guide

This document provides detailed instructions for setting up, using, and managing the Redis Queue (RQ) implementation in the IFC Pipeline.

## Components Overview

The asynchronous task processing system in IFC Pipeline consists of:

1. **Redis**: Message broker and result backend
2. **RQ Workers**: Process jobs from the queues
3. **RQ Dashboard**: Web interface for monitoring and management
4. **API Gateway**: Enqueues jobs and provides status endpoint

## Setup Instructions

### Prerequisites

- Docker and Docker Compose installed
- Access to the IFC Pipeline codebase

### Configuration Files

Three main files configure the RQ implementation:

1. **docker-compose.yml**: Defines the Redis, RQ Worker, and RQ Dashboard services
2. **rq-worker/worker_tasks.py**: Contains task functions executed by workers
3. **api-gateway/api-gateway.py**: Contains queue initialization and job enqueueing logic

### Running the System

1. **Start all services**:
   ```bash
   docker-compose up -d
   ```

2. **Scale workers** (optional):
   ```bash
   docker-compose up -d --scale rq-worker=3
   ```

3. **Access RQ Dashboard**:
   Open http://localhost:9181 in your browser

## Using the API

### Submitting Jobs

Submit jobs to any of the service endpoints as before. Instead of waiting for completion, you'll receive a job ID:

```json
{
  "job_id": "abcd1234-ef56-gh78-ij90-klmn12345678"
}
```

### Checking Job Status

Poll the status endpoint to check job progress and retrieve results:

```
GET /jobs/{job_id}/status
```

Example response for a running job:
```json
{
  "job_id": "abcd1234-ef56-gh78-ij90-klmn12345678",
  "status": "started"
}
```

Example response for a completed job:
```json
{
  "job_id": "abcd1234-ef56-gh78-ij90-klmn12345678",
  "status": "finished",
  "result": {
    "success": true,
    "message": "Processing completed successfully",
    "data": { ... }
  }
}
```

Example response for a failed job:
```json
{
  "job_id": "abcd1234-ef56-gh78-ij90-klmn12345678",
  "status": "failed",
  "error": "Error message and traceback..."
}
```

## Queue Management

The system uses dedicated queues for each service type:

- `default`: General purpose queue
- `ifcconvert`: IFC conversion tasks
- `ifccsv`: CSV export/import tasks
- `ifcclash`: Clash detection tasks
- `ifctester`: IFC validation tasks
- `ifcdiff`: IFC diff analysis tasks
- `ifc2json`: JSON conversion tasks
- `ifc5d`: Quantity takeoff tasks

## Worker Management

### Monitoring Workers

Use RQ Dashboard to monitor:
- Active workers
- Queue depths
- Running/completed/failed jobs
- Job details and results

### Scaling Workers

To increase processing capacity:
```bash
docker-compose up -d --scale rq-worker=5
```

To decrease workers:
```bash
docker-compose up -d --scale rq-worker=1
```

### Specialized Workers (Advanced)

For environments with specific needs, you can create dedicated workers for particular queues:

1. Add specialized worker services to docker-compose.yml:
   ```yaml
   rq-worker-clash:
     build:
       context: .
       dockerfile: rq-worker/Dockerfile
     command: rq worker ifcclash --url redis://redis:6379/0
     # ...other config...
   ```

2. Start with specific profile:
   ```bash
   docker-compose --profile specialized up -d
   ```

## Troubleshooting

### Common Issues

1. **Jobs stuck in "queued" state**:
   - Check if workers are running: `docker-compose ps`
   - Check worker logs: `docker-compose logs rq-worker`
   - Verify worker can connect to Redis: Check worker logs for connection errors

2. **Workers restarting**:
   - Check for memory issues: `docker stats`
   - Check for Python errors in worker logs

3. **Job failures**:
   - Check RQ Dashboard for error details
   - View worker logs for detailed traceback

### Maintenance Tasks

1. **Clear all queues** (use RQ Dashboard or):
   ```python
   from redis import Redis
   from rq import Queue
   redis_conn = Redis.from_url('redis://redis:6379/0')
   queue = Queue('ifcconvert', connection=redis_conn)
   queue.empty()
   ```

2. **Restart workers**:
   ```bash
   docker-compose restart rq-worker
   ```

3. **Check Redis memory usage**:
   ```bash
   docker-compose exec redis redis-cli info memory
   ```

## Best Practices

1. **Job Timeouts**: Set appropriate timeouts for different job types:
   - Standard jobs: 1 hour
   - Complex clash detection: 2 hours

2. **Error Handling**: Always ensure worker tasks properly handle and propagate errors:
   ```python
   try:
       # Task work...
   except Exception as e:
       logger.error(f"Error occurred: {str(e)}")
       raise  # Important: Re-raise so RQ marks the job as failed
   ```

3. **Resource Management**: Monitor system resources and scale accordingly:
   - CPU-bound tasks benefit from more worker processes
   - Memory-bound tasks may require fewer workers with more RAM each

4. **Client Implementation**: Advise clients to:
   - Use exponential backoff when polling status
   - Implement timeouts for long-running jobs
   - Display progress indicators to users