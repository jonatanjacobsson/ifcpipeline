# Revit Worker

Windows system-tray application that polls the IFC Pipeline **revit** RQ queue and executes Revit / PyRevit / RTV Tools / PowerShell commands on a Windows machine. The app is a native C# executable (`RevitWorkerApp.exe`) built with .NET 8 WinForms, communicating with Redis directly using `StackExchange.Redis` and deserialising RQ jobs via `Razorvine.Pickle`.

Originally a Python RQ worker running on Windows, it was rewritten in C# to eliminate the Python runtime dependency and Windows `fork()` limitations. The C# version implements a full RQ-compatible consumer with pickle deserialisation, zlib decompression, and the complete job lifecycle — all without requiring Python on the target machine.

## Architecture Overview

```
┌──────────────┐  POST /revit/execute   ┌───────────────┐   enqueue   ┌───────────┐
│  n8n / API   │ ─────────────────────► │  api-gateway   │ ──────────► │   Redis   │
│  clients     │                        │  (port 8000)   │             │  queue:   │
└──────────────┘                        └───────┬────────┘             │  "revit"  │
                                                │                     └─────┬─────┘
       ┌────────────────────────────────────────┘                           │
       │  GET /jobs/{id}/status (polling)                          poll & dequeue
       │  POST /revit/logs (log upload)                                     │
       │  GET /jobs/{id}/logs (download)                                    ▼
       │                                                ┌─────────────────────────────┐
       │                                                │  RevitWorkerApp.exe          │
       └──────────────────────────────────────────────► │  (Windows tray app)          │
                                                        │  Runs Revit/PyRevit/RTV/PS1  │
                                                        └─────────────────────────────┘
```

The revit-worker runs **outside Docker** on a Windows machine with Revit installed. All other pipeline services (api-gateway, Redis, dashboard, n8n, etc.) run in Docker Compose on a Linux host.

## Codebase Structure

```
revit-worker/
├── README.md
├── RevitWorkerApp.exe            # Build output (single-file, ~1.5 MB)
├── app/
│   ├── Program.cs                # Entry point, single-instance mutex, .NET 8 runtime check
│   ├── TrayApplicationContext.cs  # Tray icon lifecycle, context menu, worker/Redis wiring
│   ├── MainForm.cs               # Settings UI (Redis URL, API gateway, worker count, log)
│   ├── WorkerProcessManager.cs   # Worker threads, job polling loop, start-delay gate
│   ├── RqJobConsumer.cs          # RQ-compatible consumer (pickle/unpickle, zlib, FIFO LPOP)
│   ├── TaskRunner.cs             # Builds and runs pyrevit/rtv/powershell subprocesses
│   ├── RedisMonitor.cs           # Redis connection management (StackExchange.Redis)
│   ├── AppSettings.cs            # settings.json load/save
│   ├── AppLog.cs                 # File + in-app logging
│   ├── IconGenerator.cs          # Tray icons (green/orange/red)
│   ├── RevitWorkerApp.csproj     # .NET 8 WinForms project (win-x64)
│   ├── build.sh                  # Tests + publish (cross-compile from Linux)
│   └── build-publish-only.sh     # Publish only
├── tests/
│   ├── ParseSentinelTests.cs     # RW_RESULT sentinel parsing tests
│   ├── LogFinderTests.cs         # Log file discovery tests
│   ├── UploadLogsTests.cs        # Log upload early-return tests
│   └── RevitWorkerApp.Tests.csproj
└── publish.old/                  # Legacy build output (deprecated)
```

### Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| StackExchange.Redis | 2.8.16 | Redis client (connection management, pub/sub, sorted sets) |
| Razorvine.Pickle | 1.5 | Python pickle deserialisation for RQ job payloads |

## Prerequisites

| Requirement | Notes |
|---|---|
| Windows 10 or later | Tray app host |
| .NET 8 Desktop Runtime (x64) | Required to run the exe ([download](https://dotnet.microsoft.com/download/dotnet/8.0)) |
| Revit (e.g. 2024/2025) | Installed on the Windows machine |
| PyRevit | Required for `pyrevit` command type jobs |
| RTV Tools (Xporter Pro) | Required for `rtv` command type jobs |
| Network access to Redis | Machine must reach Redis on the pipeline host (default port 6379) |

## Installation & Configuration

1. Copy the **revit-worker** folder to the Windows machine (e.g. `C:\revit-worker`).
2. Ensure **RevitWorkerApp.exe** is in the folder (build from `app/` — see [Building](#building)).
3. Create **settings.json** next to the exe (or configure via the UI):

```json
{
  "redis_url": "redis://bim-host-ubnt:6379/0",
  "queue_names": "revit",
  "worker_count": 1,
  "api_gateway_url": "http://bim-host-ubnt:8000",
  "api_key": "YOUR_API_KEY",
  "job_start_delay_seconds": 5
}
```

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `redis_url` | Yes | `redis://bim-host-ubnt:6379/0` | Redis connection URL |
| `queue_names` | No | `"revit"` | Comma-separated queue names |
| `worker_count` | No | 1 | Number of worker threads (1–8; Revit is single-instance per license) |
| `api_gateway_url` | No | — | Base URL for log uploads; if omitted, jobs still run normally |
| `api_key` | No | — | API key for log uploads to the gateway |
| `job_start_delay_seconds` | No | 5 | Delay between consecutive job starts (prevents race conditions) |

## Running

1. Run **RevitWorkerApp.exe** (single-instance — a second launch shows a warning).
2. Double-click the tray icon (or right-click → Settings) to open the settings window.
3. Enter the Redis URL and click **Connect**.
4. Adjust worker count with the slider and click **Save**.

**Tray icon states:** green = idle, orange = job running, red = disconnected/paused. A Windows notification is shown when a job is accepted.

The app starts with the settings window visible on first launch. All settings are persisted to `settings.json` next to the exe.

## Building

### Docker cross-compile (recommended)

Build from Linux using the .NET 8 SDK Docker image — avoids local SDK version issues:

```bash
cd revit-worker
docker run --rm -v "$(pwd):/src" -w /src/app mcr.microsoft.com/dotnet/sdk:8.0 \
  dotnet publish RevitWorkerApp.csproj -r win-x64 -c Release \
  --self-contained false -p:PublishSingleFile=true -o ../
```

### Shell scripts

```bash
cd revit-worker/app
bash build.sh              # run tests + publish
bash build-publish-only.sh # publish only (skip tests)
```

### Build flags

| Flag | Purpose |
|------|---------|
| `-p:PublishSingleFile=true` | Bundles all assemblies into one exe (~1.5 MB). Without this, the exe is only ~148 KB (a non-functional stub). |
| `--self-contained false` | Framework-dependent; requires .NET 8 Desktop Runtime on the target machine. |
| `-o ../` | Outputs to the `revit-worker/` root directory. |

Output: `RevitWorkerApp.exe` (~1.5 MB) in the `revit-worker/` folder, targeting `win-x64`.

### Deploy to Windows

Copy the exe to the INTERAXO sync folder (automatically synced to the Windows machine):

```bash
cp revit-worker/RevitWorkerApp.exe /home/jonatan/INTERAXO/RevitWorkerApp.exe
```

## Supported Command Types

| `command_type` | What happens |
|---|---|
| `pyrevit` | Runs `pyrevit run <script_path> [model_path] [--revit=YYYY] [arguments...]` |
| `rtv` | Runs `powershell -ExecutionPolicy Bypass -NonInteractive -File <script_path> [-BatchFile ...] [-JobId ...] [arguments...]` |
| `powershell` | Runs `powershell -ExecutionPolicy Bypass -NonInteractive -File <script_path> [-ModelPath ...] [-RevitVersion ...] [arguments...]` |

The `-JobId` parameter on RTV jobs ensures unique schedule XML filenames when multiple jobs run concurrently.

## API Gateway Integration

Jobs are submitted via the api-gateway (FastAPI, port 8000):

### Submit a job

```
POST /revit/execute
X-API-Key: <key>
```

Request body (`RevitExecuteRequest`):

```json
{
  "command_type": "pyrevit | rtv | powershell",
  "script_path": "C:\\Scripts\\my_script.py",
  "model_path": "C:\\Models\\project.rvt",
  "revit_version": "2025",
  "batch_file": "C:\\Batches\\export.rbxml",
  "arguments": [],
  "timeout_seconds": 3600,
  "working_directory": "C:\\Output",
  "meta": { "project": "Example", "model_name": "project.rvt" }
}
```

| Field | Required | Used by | Description |
|-------|----------|---------|-------------|
| `command_type` | Yes | all | `pyrevit`, `rtv`, or `powershell` |
| `script_path` | Yes | all | Path to script on the Windows machine |
| `model_path` | No | pyrevit | `.rvt` model file |
| `revit_version` | No | pyrevit | e.g. `"2025"` → `--revit=2025` |
| `batch_file` | No | rtv | `.rbxml` batch file → `-BatchFile` arg |
| `arguments` | No | all | Additional CLI arguments |
| `timeout_seconds` | No | all | Max execution time (default 3600, range 10–86400) |
| `working_directory` | No | all | Working directory for the subprocess |
| `meta` | No | all | Arbitrary metadata stored in RQ job meta (visible in dashboard) |

Response: `{"job_id": "<uuid>"}`

### Poll job status

```
GET /jobs/{job_id}/status
```

### Download logs

```
GET /jobs/{job_id}/logs              # list log files
GET /jobs/{job_id}/logs/{filename}   # download specific log
```

### Upload logs (called by the worker)

```
POST /revit/logs
```

Multipart form with `job_id`, `log_type`, and the log file (`.log` or `.txt`).

## n8n Example Workflows

The `example n8n workflows/` folder in the repo root contains ready-to-import workflows. All three Revit job runners follow the same pattern: **Config → Submit → Poll → Succeed/Fail**.

### RTV Xporter Pro Job Runner

Runs an RTV Tools batch export (IFC export from Revit via Xporter Pro).

| Setting | Example value |
|---------|---------------|
| `command_type` | `rtv` |
| `script_path` | `C:\Scripts\Run-RTVXporterBatch.ps1` |
| `batch_file` | `C:\Batches\K-01-T4302-MOD-RIV-01.rbxml` |
| `timeout_seconds` | 7200 |

**Flow:** Manual trigger → Config node → POST `/revit/execute` → Wait 15 s → GET `/jobs/{id}/status` → loop until `finished` or `failed`.

### Revit PyRevit Job Runner

Runs a PyRevit script inside Revit (e.g. detach model, export, data extraction).

| Setting | Example value |
|---------|---------------|
| `command_type` | `pyrevit` |
| `script_path` | `C:\code\pyRevit Extensions\pySPF.extension\test_detach_to_temp.py` |
| `model_path` | `C:\Models\project.rvt` |
| `revit_version` | `2025` |
| `timeout_seconds` | 3600 |

**Flow:** Manual trigger → Config node → POST `/revit/execute` → Wait 10 s → GET `/jobs/{id}/status` → loop until `finished` or `failed`.

### Revit PowerShell Job Runner

Runs a PowerShell script on the Windows machine (e.g. file copy, model preparation).

| Setting | Example value |
|---------|---------------|
| `command_type` | `powershell` |
| `script_path` | `C:\Scripts\copy_models.ps1` |
| `arguments` | `["-Source", "\\\\fileserver\\Projects\\Models", "-Dest", "C:\\Temp\\Models"]` |
| `timeout_seconds` | 600 |
| `working_directory` | `C:\Temp` |

**Flow:** Manual trigger → Config node → POST `/revit/execute` → Wait 5 s → GET `/jobs/{id}/status` → loop until `finished` or `failed`.

### Interaxo Upload Triggers

Separate workflows watch local output folders (per discipline: A, E, BR, K) for new IFC files and upload them to Interaxo via the Interaxo API. These run independently from the Revit job runners.

### process-rvt Webhook

The dashboard can trigger an n8n workflow via `POST /webhook/process-rvt` when a new `.rvt` file appears on Interaxo. This is configured in the dashboard's Interaxo integration and calls n8n with file metadata (content URL, path, community/room IDs, access token). The matching n8n workflow chains Revit processing steps.

## Result Format

Every job returns a structured result stored in Redis:

```json
{
  "success": true,
  "exit_code": 0,
  "stdout": "...",
  "stderr": "...",
  "started_at": "2025-06-15T10:30:00",
  "finished_at": "2025-06-15T10:35:12"
}
```

### Structured Output (RW_RESULT)

Scripts can emit a JSON line on stdout to add custom fields to the result:

- **PyRevit:** `print("RW_RESULT:" + json.dumps({"output_path": path, "model_name": "..."}))`
- **PowerShell:** `Write-Output "RW_RESULT:$($result | ConvertTo-Json -Compress)"`

The worker scans stdout for lines matching `RW_RESULT:{json}` and merges the parsed JSON into the job result before storing it in Redis.

### Log Files

If `api_gateway_url` and `api_key` are configured, the worker discovers and uploads log files to the API gateway after each job completes. Log types discovered automatically:

| Log type | Source | Discovery method |
|----------|--------|------------------|
| RTVXporter log | RTV Xporter Pro | Stdout path parsing + time-window search |
| PyRevit runner log | PyRevit | Stdout path parsing |
| Revit journal | Revit | `%LOCALAPPDATA%\Autodesk\Revit` journal files |
| Worker log | RevitWorkerApp | Internal log buffer |
| Detach/repath log | Custom scripts | Stdout path parsing |

Logs are uploaded via `POST /revit/logs` and stored at `/uploads/revit-logs/{job_id}-*.log` on the gateway. They can be downloaded via `GET /jobs/{job_id}/logs`.

If not configured, jobs still run normally — only log upload is skipped.

## Redis Queue Protocol

The worker implements an RQ-compatible consumer in C# (no Python dependency):

| Redis key | Type | Purpose |
|-----------|------|---------|
| `rq:queue:revit` | List | Job queue (LPOP to dequeue, FIFO) |
| `rq:job:{id}` | Hash | Job data, status, result (pickle/zlib) |
| `rq:wip:revit` | Sorted set | Jobs currently being executed |
| `rq:finished:revit` | Sorted set | Completed jobs |
| `rq:failed:revit` | Sorted set | Failed jobs |
| `rq:workers` | Set | Registered worker names |
| `rq:worker:{name}` | Hash | Worker state, heartbeat, current job |

**Job lifecycle:** Dequeue (LPOP, FIFO) → Mark started (+ add to WIP) → Heartbeat every 15 s → Mark finished/failed (move from WIP to finished/failed) → Upload logs → Store result.

### RQ Data Formats

| Field | Encoding | Notes |
|-------|----------|-------|
| `data` | zlib + pickle | Full RQ function call args |
| `meta` | pickle (plain) | Arbitrary metadata dict |
| `result` | pickle (plain) | Job result dict |
| `status`, `exc_info`, timestamps | UTF-8 strings | Human-readable |

## Resilience

| Scenario | Behaviour |
|---|---|
| Revit crashes | Non-zero exit code; worker reports failure |
| Revit hangs | Subprocess timeout kills the process |
| Worker exits mid-job | RQ job timeout expires; job moves to failed |
| Redis unreachable | App disconnects; reconnects automatically when Redis returns |
| Concurrent job starts | `job_start_delay_seconds` gate prevents race conditions |
| .NET 8 runtime missing | Startup dialog with download link |
| Second instance launched | Warning message; only one instance allowed (mutex) |

## Dashboard Integration

The IFC Pipeline dashboard (port 9181) reads the same Redis instance and shows:

- Revit queue depth, workers, and job status
- Job history (synced to PostgreSQL every 90 s)
- RTV/detach log summaries parsed from uploaded logs
- n8n execution status linked to Revit jobs
- Per-model Interaxo file status with workflow trigger buttons

## Cursor AI Skills

The following Cursor AI skills automate common operations related to the revit-worker:

| Skill | Description |
|-------|-------------|
| `build-revitworker` | Cross-compile `RevitWorkerApp.exe` from Linux using Docker, run tests, deploy to INTERAXO sync folder |
| `analyze-rtv-jobs` | Inspect failed RTV jobs in Redis — decode pickle/zlib payloads, check queue stats, diagnose common failure patterns |
| `batch-trigger-rvt-workflow` | Batch-trigger n8n Revit processing workflows for RVT files via the dashboard API, sorted by file size |
| `create-rtv-batch-files` | Create `.rbxml` batch files for RTV Xporter Pro, find missing batch files, submit test jobs |
| `debug-rtv-exports` | Audit RTV export results — check RTVXporter logs for view filter mismatches, batch file errors, missing IFC output |
| `deploy-ifcpipeline-dashboard` | Rebuild and redeploy the dashboard Docker container after code changes |

## Development History

The revit-worker evolved from a Python RQ worker to a standalone C# application through a series of iterative improvements, all developed with Cursor AI assistance.

### Phase 1: Python RQ Worker
[Initial Python worker](d19daf23) — Designed and implemented the Redis-based job queue architecture. Created the Python RQ worker (`tasks.py`, `worker_entry.py`), added `POST /revit/execute` to the API gateway, and created example n8n workflows for PyRevit, PowerShell, and RTV Xporter. Worked around Windows `fork()` limitations with `SimpleWorker`.

### Phase 2: C# Rewrite
[Revit Worker tray app](ad86d34e) — Full rewrite as a C# .NET 8 WinForms system-tray application. Implemented StackExchange.Redis for Redis communication, Razorvine.Pickle for Python pickle deserialisation, settings UI with live log viewer, worker thread management, tray icon states (green/orange/red), single-instance mutex, and .NET 8 runtime detection with download prompt.

### Phase 3: Result Enrichment & Log Upload
[Result enrichment](14fc4386) — Added `RW_RESULT:` structured output parsing so scripts can emit JSON on stdout to enrich job results. Implemented automatic log file discovery (RTVXporter, PyRevit, Revit journal) with time-window filtering and PID matching. Added log upload to the API gateway via `POST /revit/logs`. Created unit tests for sentinel parsing, log discovery, and upload logic.

### Phase 4: Bug Fixes & Reliability
[FIFO queue fix](cc742e0b) — Discovered the worker used `ListRightPop` (LIFO) instead of `ListLeftPop` (FIFO). Fixed `RqJobConsumer.cs` so jobs are processed in submission order. Also established the Docker-based cross-compilation workflow and created the `build-revitworker` skill.

[RTV race condition fix](d35afba4) — Diagnosed a race condition where concurrent RTV jobs shared the same schedule XML filename. Fixed by adding `-JobId` parameter to `Run-RTVXporterBatch.ps1` (unique filenames per job) and threading the job ID through `TaskRunner.BuildCommand`. Added configurable `job_start_delay_seconds` as a shared start gate between worker threads to prevent simultaneous Revit launches.

### Phase 5: API & Dashboard Integration
[Unserializable result fix](af64c387) — Diagnosed "Unserializable return value" errors caused by older worker binaries that zlib-compressed pickle results differently. Added `_read_job_result()` helper to the API gateway to handle both compressed and raw pickle. Added `GET /jobs/{job_id}/logs` endpoints for log download.

[Meta support](09ac9d24) — Added `meta: Optional[dict]` to `RevitExecuteRequest`. The API gateway passes metadata to RQ's `enqueue()` and strips it from job args so the C# worker doesn't receive unexpected fields. Meta is displayed in the dashboard with toggleable columns.

[Timezone fix](eeea6e2a) — Fixed UTC timestamps in the API gateway and dashboard. Added `TZ=Europe/Stockholm` to Docker services. The C# worker uses `DateTime.UtcNow` internally.

### Phase 6: Documentation & Deployment
[README documentation](5cc3f3d7) — Comprehensive README with architecture diagrams, codebase structure, configuration reference, API gateway integration, n8n workflow examples, Redis queue protocol, and dashboard integration.

[Repository cleanup](0e7b1415) — Removed exposed API tokens, split uncommitted changes into logical git commits (infrastructure, revit worker, rq-dashboard, example workflows), and pushed to GitHub.

[Move to app/](ad86d34e) — Restructured the project by moving the C# source into `revit-worker/app/`, updated build paths, added `bin/`/`obj/` to `.gitignore`, and disabled PDB generation.
