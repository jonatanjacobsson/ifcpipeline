# Revit Worker Tray App

Lightweight Windows system-tray application that runs the Revit RQ worker in the background, with a small settings window and Windows notifications when jobs are accepted.

## Build

**Full build (run unit tests then publish):**
```bash
cd revit-worker/app
bash build.sh
```

**Publish only (skip tests):**
```bash
cd revit-worker/app
bash build-publish-only.sh
```

Requires [.NET 8 SDK](https://dotnet.microsoft.com/download/dotnet/8.0). On Linux you can install it or use `./install-dotnet.sh` if available. The project targets `win-x64`; cross-compilation from Linux works with the SDK.

Example manual publish (self-contained):
```bash
dotnet publish RevitWorkerApp.csproj -r win-x64 -c Release --self-contained true \
  -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true -o ../
```

Output: `../RevitWorkerApp.exe` in the parent `revit-worker` folder.

## Deploy

1. **API gateway** (`/revit/logs` endpoint): from the repo root run `docker compose build api-gateway && docker compose up -d api-gateway`.
2. **Revit worker app**: copy the entire `revit-worker` folder (including `RevitWorkerApp.exe` and optional `settings.json`) to the Windows machine. In `settings.json`, set `redis_url` so the worker can connect; `api_gateway_url` and `api_key` are optional and only used for uploading job logs (jobs run and finish either way).

## Usage on Windows

1. Run `RevitWorkerApp.exe`.
3. Double-click the tray icon or use the context menu to open Settings.
4. Enter the Redis URL and click Connect.
5. Set the worker count with the slider and click Save.
6. The tray icon turns green when connected and idle, orange when a job is running, and red when disconnected or paused. A Windows notification is shown when a job is accepted.

## Requirements

- Windows 10 or later
- Python in PATH (only if running PyRevit or other Python-based scripts)
- Redis server reachable at the configured URL
