# IFC Diff Asynchronous Processing Setup with RQ and Redis

This document summarizes the implementation of an asynchronous task queue system using RQ and Redis to handle IFC difference calculations (`ifcdiff`).

## Goal

The primary goal was to decouple potentially long-running `ifcdiff` operations from the main `api-gateway` request thread. This prevents blocking the gateway, improving its responsiveness and scalability.

## Components

1.  **Redis:** Acts as the message broker and result backend for RQ. Runs as a dedicated service (`redis`) in `docker-compose.yml`.
2.  **RQ (Redis Queue):** Python library used for queuing jobs and managing workers. Added as a dependency to `api-gateway` and `ifcdiff`.
3.  **`api-gateway`:**
    *   No longer performs diffs directly.
    *   Connects to Redis (`REDIS_HOST=redis` environment variable).
    *   `/ifcdiff` endpoint: Enqueues a job by sending the function path (`worker.perform_ifc_diff`) and arguments to the `ifcdiff-tasks` queue via RQ. Returns HTTP 202 with a `job_id`.
    *   `/tasks/{job_id}/status` endpoint: Allows clients to check the status (`queued`, `started`, `finished`, `failed`) of a job.
    *   `/tasks/{job_id}/result` endpoint: Allows clients to retrieve the result of a finished job. If successful, returns a `FileResponse` for the generated diff file.
4.  **`ifcdiff` Worker:**
    *   The original FastAPI service (`ifcdiff-service.py`) was refactored into `worker.py`.
    *   Runs as an RQ worker process, listening to the `ifcdiff-tasks` queue (command: `sh -c "PYTHONPATH=. rq worker -u redis://redis:6379 ifcdiff-tasks"` in `docker-compose.yml`).
    *   Defines the `perform_ifc_diff` function which contains the core diff logic.
    *   Picks up jobs, executes the function, saves output to the shared `/output/diff/` volume, and returns the relative output path to RQ.
5.  **`rq-dashboard`:**
    *   A separate service added for web-based monitoring of RQ queues, jobs, and workers.
    *   Accessible via port 9181 (e.g., `http://localhost:9181`).

## Workflow

1.  Client sends a `POST` request to `/ifcdiff` on the `api-gateway` with IFC file details.
2.  `api-gateway` enqueues the task `worker.perform_ifc_diff` with arguments onto the `ifcdiff-tasks` queue in Redis and immediately responds with `202 Accepted` and a `job_id`.
3.  The `ifcdiff` RQ worker process picks up the job from the queue.
4.  The worker executes the `perform_ifc_diff` function using the provided arguments.
5.  The function performs the diff, saves the output file (e.g., `/output/diff/my_diff.json`), and returns the relative path (`diff/my_diff.json`). RQ stores this result in Redis.
6.  Client polls the `GET /tasks/{job_id}/status` endpoint on the `api-gateway`.
7.  `api-gateway` fetches the job status from Redis via RQ.
8.  Once the status is `finished`, the client calls `GET /tasks/{job_id}/result`.
9.  `api-gateway` fetches the job result (the relative path) from Redis, constructs the absolute path, and returns the diff file via `FileResponse`.
10. If the job status becomes `failed`, the status endpoint will reflect this, potentially including error information. The result endpoint will return an error.

## Key Configuration Changes

*   **`docker-compose.yml`:** Added `redis` and `rq-dashboard` services. Modified `ifcdiff` service to run the RQ worker command (including setting `PYTHONPATH`). Added `REDIS_HOST` environment variable and `depends_on: redis` to `api-gateway`.
*   **`api-gateway/api-gateway.py`:** Added RQ/Redis setup, modified `/ifcdiff`, added `/tasks/...` endpoints.
*   **`ifcdiff/worker.py`:** Replaced original service file. Contains the task function `perform_ifc_diff`.
*   **`ifcdiff/Dockerfile`:** Updated `COPY` command and removed old `CMD`.
*   **`requirements.txt`:** Added `rq` and `redis` to both `api-gateway` and `ifcdiff`.
