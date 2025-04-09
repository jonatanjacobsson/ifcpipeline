# Proposed Solution: RQ (Redis Queue) + Redis Integration

This document outlines the implemented architecture for integrating RQ (Redis Queue) and Redis into the IFC Pipeline to handle microservice tasks asynchronously.

## Architecture Overview

1.  **API Gateway:**
    *   Receives incoming HTTP requests for processing tasks (e.g., `/ifcconvert`).
    *   Validates the request.
    *   **Instead of calling the microservice directly:** Serializes the task details (e.g., input file path, parameters), instantiates an RQ Queue object connected to Redis, and enqueues a job targeting a specific Python function, passing the details as arguments.
    *   Immediately returns a unique Job ID to the client.
    *   Provides a new endpoint (e.g., `/jobs/{job_id}/status`) for clients to poll the status and retrieve results once completed.

2.  **Redis:**
    *   Acts as the **Queue Backend:** Holds queues of jobs waiting to be processed by RQ workers.
    *   Acts as the **Result Storage:** Stores the state (`queued`, `started`, `finished`, `failed`) and results (or error details) of completed jobs, keyed by Job ID.

3.  **RQ Worker(s):**
    *   One or more worker services run independently.
    *   Connects to Redis using the `rq worker` command, listening for jobs on specific queues.
    *   When a job is received, the worker deserializes the arguments.
    *   Imports and executes the target Python function (defined in the worker's codebase).
    *   This function makes the actual synchronous HTTP call to the relevant internal microservice (e.g., `http://ifcconvert/ifcconvert`).
    *   Waits for the microservice to complete processing.
    *   If the function returns successfully, RQ marks the job as `finished` and stores the return value in Redis. If the function raises an exception, RQ marks the job as `failed` and stores the exception information.

4.  **RQ Dashboard:**
    *   Provides a web interface to monitor job queues, workers, and job statuses.
    *   Allows viewing job details, results, and exception information.
    *   Enables management capabilities like clearing queues or rescheduling failed jobs.
    *   Accessible at http://localhost:9181 when running locally.

5.  **Microservices (ifcconvert, ifcclash, etc.):**
    *   Remain largely unchanged. They continue to expose their specific HTTP endpoints for processing.
    *   They are now called by the Python functions executed by the RQ workers instead of directly by the API Gateway.

## Implementation Details

1.  **Redis Service:**
    *   Uses the `redis:alpine` image for a lightweight Redis server.
    *   Data persistence via mounted volume `redis-data`.
    
2.  **RQ Worker Service:**
    *   Custom built from Python base image.
    *   Access to the shared volumes (`/uploads`, `/output`).
    *   Contains worker task functions in `worker_tasks.py` that make HTTP requests to microservices.
    *   Listens to dedicated queues for each service type (ifcconvert, ifcclash, etc.).
    *   Uses string references to functions for job execution to avoid import issues.
    *   Configurable job timeouts (2 hours for clash detection, 1 hour for other tasks).

3.  **RQ Dashboard Service:**
    *   Uses the `eoranged/rq-dashboard` image.
    *   Provides a web interface for monitoring jobs, queues, and workers.
    *   Accessible on port 9181.

4.  **Worker Scaling:**
    *   Horizontal scaling through Docker Compose:
        ```bash
        docker-compose up -d --scale rq-worker=3
        ```
    *   Allows for dynamic adjustment of worker count based on load.

5.  **API Gateway Updates:**
    *   Connects to Redis and creates queue objects for each service type.
    *   Enqueues tasks with appropriate timeouts and parameters.
    *   Returns job IDs to clients.
    *   Provides a `/jobs/{job_id}/status` endpoint for checking job status and retrieving results.
    *   Uses robust serialization to handle different Pydantic model formats.

## Result Handling

Clients receive a `job_id` upon submitting a job. They then poll the `/jobs/{job_id}/status` endpoint periodically until the status changes from `queued` or `started` to `finished` or `failed`. If `finished`, the response body contains the actual result from the microservice. If `failed`, it contains error details.

## Sequence Diagram

```mermaid
sequenceDiagram
    participant Client
    participant API Gateway
    participant Redis (Queue + Result Store)
    participant RQ Worker
    participant Microservice (e.g., ifcconvert)

    Client->>+API Gateway: POST /ifcconvert (request_data)
    API Gateway->>+Redis (Queue + Result Store): Enqueue job (call_ifcconvert_func, request_data) on 'ifcconvert' queue
    API Gateway-->>-Client: Response {"job_id": "xyz"}
    Redis (Queue + Result Store)-->>-RQ Worker: Deliver job "xyz" from 'ifcconvert' queue
    RQ Worker->>+Microservice (e.g., ifcconvert): (Executes call_ifcconvert_func) POST /ifcconvert (request_data)
    Microservice (e.g., ifcconvert)-->>-RQ Worker: Response (result_data)
    RQ Worker->>+Redis (Queue + Result Store): (Function returns result_data) Update job "xyz" status=finished, result=result_data
    Note over Client: Polls periodically
    Client->>+API Gateway: GET /jobs/xyz/status
    API Gateway->>+Redis (Queue + Result Store): Fetch job "xyz" status/result
    Redis (Queue + Result Store)-->>-API Gateway: Return status=finished, result=result_data
    API Gateway-->>-Client: Response {"status": "finished", "result": result_data}
```

## Best Practices

1. **Task Timeouts:** Set appropriate timeouts based on the expected execution time of each task type. For example, clash detection jobs have a 2-hour timeout due to their intensive computation requirements.

2. **Worker Scaling:** Monitor queue sizes and worker utilization through RQ Dashboard. Scale workers up during high demand periods and down during idle times.

3. **Request Serialization:** When using Pydantic models, handle both newer model_dump() and older dict() methods to ensure compatibility. For complex nested models, consider normalization in the worker tasks.

4. **Resource Optimization:** Use slim Docker images and clean up cache during builds to minimize resource usage.

5. **Error Handling:** Implement robust error handling in worker tasks. Always re-raise exceptions to ensure RQ properly marks jobs as failed with the correct error information.

## Next Steps

1. **Monitoring and Alerting:** Implement monitoring for Redis and worker health, with alerts for queue backlogs or worker failures.

2. **Auto-scaling:** Implement automatic scaling of workers based on queue size and resource availability.

3. **Advanced Job Management:** Implement job dependencies, scheduled jobs, and job prioritization for more complex workflows.

4. **Result Expiry:** Configure result expiry policies to prevent Redis memory growth with long-term job storage.

5. **Performance Optimization:** Analyze and optimize worker performance for specific task types.
