# Requirements for Asynchronous Task Processing

## Problem Statement

The current API Gateway directly calls downstream microservices (ifcconvert, ifcclash, etc.) using asynchronous HTTP requests (`aiohttp`). While `aiohttp` is non-blocking, handling many concurrent, potentially long-running processing tasks directly within the gateway process can lead to:

1.  **Resource Contention:** The gateway process becomes responsible for managing numerous outbound connections and waiting for responses, potentially consuming significant memory and CPU.
2.  **Scalability Bottleneck:** Scaling the gateway also scales the direct request handling. A dedicated worker pool allows independent scaling of request intake and task execution.
3.  **Reliability Issues:** If a downstream service is slow or unresponsive, it can tie up resources in the gateway. If the gateway restarts, any in-flight requests are lost.
4.  **Coupling:** The gateway is tightly coupled to the immediate availability and responsiveness of the worker services.

## Implemented Solution

We have successfully implemented a decoupled architecture using:

1.  **Redis Queue (RQ):** A Python library for queueing jobs and processing them asynchronously with workers.
2.  **Redis:** As both the message broker and result backend, storing queue data and job results.
3.  **RQ Dashboard:** For monitoring and managing queues, workers, and jobs through a web interface.
4.  **Horizontally Scalable Workers:** Multiple worker processes can be spun up to handle increased load.

## Key Features Implemented

1.  **Decoupling:**
    * API Gateway now immediately responds with a job ID after enqueueing tasks
    * Task execution is handled by separate worker processes
    * System components can scale independently

2.  **Scalability:**
    * Workers can be scaled horizontally using Docker Compose's `--scale` functionality
    * Different queue types (ifcconvert, ifcclash, etc.) allow specialized workers if needed

3.  **Reliability:**
    * Jobs persist in Redis even if workers or the gateway restart
    * Failed jobs retain error information for debugging
    * Long-running tasks have appropriate timeouts (2h for clash detection, 1h for others)

4.  **Monitoring:**
    * RQ Dashboard provides real-time visibility into queue depth, job status, and worker health
    * Jobs can be manually requeued or cancelled if needed

5.  **Improved Client Experience:**
    * Clients get immediate response with job ID
    * Status endpoint provides clear job status and results when complete
    * Gateway remains responsive even during high load periods

## Technical Details

1.  **API Gateway:**
    * Uses Redis connection and RQ Queue objects
    * Enqueues jobs with string references to worker functions
    * Provides `/jobs/{job_id}/status` endpoint for checking job progress
    * Handles different Pydantic model serialization methods

2.  **Worker Service:**
    * Uses HTTP requests to call microservices
    * Handles job argument normalization when needed
    * Properly propagates errors to RQ job status
    * Configured with appropriate timeouts for different task types

3.  **Redis:**
    * Stores job data, status, and results
    * Provides persistence for job information

4.  **Infrastructure:**
    * Docker Compose for service orchestration
    * Volume mounting for data persistence
    * Horizontal scaling for workers

## Future Enhancements

1.  **Real-time Updates:** Add WebSocket or Server-Sent Events for push notifications of job status changes.
2.  **Job Prioritization:** Implement priority queues for critical tasks.
3.  **Job Dependencies:** Support for chaining jobs together in workflows.
4.  **Auto-scaling:** Implement automatic worker scaling based on queue size.
5.  **Enhanced Monitoring:** Add metrics collection and alerting for system health.