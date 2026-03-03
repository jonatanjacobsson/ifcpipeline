# Revit Worker

Windows tray application that polls the IFC Pipeline **revit** RQ queue and runs Revit / PyRevit / PowerShell commands. The app is a native C# executable (`RevitWorkerApp.exe`) that talks to Redis directly.

## Prerequisites

| Requirement | Notes |
|---|---|
| Windows 10 or later | Tray app and subprocess host |
| Revit | Installed on the Windows machine |
| Network access | Machine must reach Redis on the pipeline host |

## Installation

1. Copy the **revit-worker** folder to the Windows machine (e.g. `C:\revit-worker`).
2. Ensure **RevitWorkerApp.exe** is in that folder (build from `app/` with .NET 8 SDK if needed; see `app/README.md`).
3. Optionally create **settings.json** next to the exe. Only **redis_url** is required for the worker to run and process the queue. **api_gateway_url** and **api_key** are optional and used only for uploading job log files to the API; if omitted, jobs still run and finish normally (you’ll see “Skipping log upload” in the log).

## Running the Worker

1. Run **RevitWorkerApp.exe**.
2. Use the tray icon: double-click or right-click → Settings.
3. Set Redis URL and worker count, then Connect.

The app polls the `revit` queue and runs one job at a time (Revit is single-instance per license). Tray icon: green = idle, orange = job running, red = disconnected.

## Supported Command Types

| `command_type` | What happens |
|---|---|
| `pyrevit` | Runs `pyrevit run <script_path> [model_path] [--revit=YYYY] [arguments...]` |
| `rtv` | Runs `powershell -File <script_path> [-BatchFile ...] [arguments...]` (RTV Tools) |
| `powershell` | Runs `powershell -File <script_path> [arguments...]` |

### PyRevit-specific fields

- `model_path` — The `.rvt` model file (first positional arg after script).
- `revit_version` — e.g. `"2025"` → `--revit=2025`.

## Job Payload (from API Gateway / n8n)

```json
{
  "command_type": "pyrevit",
  "script_path": "C:\\Scripts\\test_detach_to_temp.py",
  "model_path": "C:\\Models\\project.rvt",
  "revit_version": "2025",
  "arguments": [],
  "timeout_seconds": 3600,
  "working_directory": "C:\\Output"
}
```

## Result Format

Every job returns a structured dict:

```json
{
  "success": true,
  "exit_code": 0,
  "stdout": "...",
  "stderr": "...",
  "started_at": "...",
  "finished_at": "..."
}
```

### Structured Output (RW_RESULT)

Scripts can print a JSON line to stdout to add fields to the result:

- **PyRevit:** `print("RW_RESULT:" + json.dumps({"output_path": path, "model_name": "..."}))`
- **PowerShell:** `Write-Output "RW_RESULT:$($result | ConvertTo-Json -Compress)"`

### Log Files

**Optional:** If `api_gateway_url` and `api_key` are set in **settings.json**, the worker uploads log files (journal, pyrevit, rtv, worker log) to the API gateway. They appear in the job result as `log_files`; download via `POST /create_download_link` then `GET /download/{token}`. If these are not set, the worker still processes all jobs; it only skips uploading logs.

## Resilience

| Scenario | Behavior |
|---|---|
| Revit crashes | Non-zero exit; worker reports failure |
| Revit hangs | Subprocess timeout kills process |
| Worker exits | RQ job timeout; job moves to failed |
| Redis unreachable | App disconnects; reconnect when Redis is back |

## Verifying Connectivity

From the Windows machine, ensure Redis is reachable (e.g. open the URL in a browser or use a Redis client). The tray app shows red when disconnected.
