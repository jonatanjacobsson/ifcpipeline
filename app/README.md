# IFC Pipeline dashboard — HTMX UI (`app/`)

Server-rendered dashboard for the pipeline, on **http://localhost:9181**. It replaced the
patched `rq-dashboard-fast` image that used to run on that port.

The UI is [FastHTML](https://fastht.ml) components rendered to HTML on the server and
swapped in by [htmx](https://htmx.org) — no build step, no bundler, no client framework.
Data comes from the same JSON API the pages themselves expose, in
[`../dashboard/`](../dashboard/).

## Pages

| Nav | Path | Source |
|-----|------|--------|
| Dashboard | `/htmx/` | queue totals, Redis memory, recent jobs, service health |
| Jobs | `/htmx/jobs/` | live RQ registries — filter by queue/state, sort, delete, requeue |
| Job detail | `/htmx/jobs/<id>` | args, result, traceback, per-job log files |
| History | `/htmx/history/` | finished jobs mirrored into Postgres (outlives RQ's result TTL) |
| Workers | `/htmx/workers/` | live workers, their queues and current job |
| Network Share | `/htmx/network-share/` | file browser + text editor + IFC/PDF preview |
| n8n | `/htmx/n8n/` | workflows and recent executions |
| Database | `/htmx/database/` | row counts and recent tester/clash/diff results |

`/` redirects to `/htmx/`.

## Layout

| Path | Role |
|------|------|
| [`main.py`](main.py) | FastAPI entry: CORS, lifespan, JSON routers, `/htmx` router, `/static` |
| [`pipeline_ui/routes.py`](pipeline_ui/routes.py) | every `/htmx/*` HTML route |
| [`pipeline_ui/renderers/`](pipeline_ui/renderers/) | one module per page, plus shared layout/nav/helpers |
| [`../dashboard/routers/`](../dashboard/routers/) | JSON `/api/*` — `rq`, `system`, `n8n`, `network_share` |
| [`../dashboard/services/`](../dashboard/services/) | domain layer: Redis/RQ, Postgres, n8n, file share |
| [`../dashboard/static/`](../dashboard/static/) | `style.css`, the `htmx-*.js` helpers, and the bundled IFC viewer |

Each page is a **shell** (`/htmx/<page>/`, full document with sidebar and header) plus a
**fragment** (`/htmx/<page>/content`, swapped in on load and on a poll interval). Sidebar
navigation swaps `#htmx-page-inner` and pushes the URL, so pages are bookmarkable.
Network Share is the exception — it needs head assets and `DOMContentLoaded` init, so its
nav entry does a full page load.

## Configuration

Everything is environment-driven ([`../dashboard/config.py`](../dashboard/config.py)):

| Variable | Default | Purpose |
|----------|---------|---------|
| `REDIS_URL` | `redis://redis:6379/0` | queue source |
| `POSTGRES_*` | `postgres` / `ifcpipeline` | History and Database pages |
| `N8N_API_URL` | `http://n8n:5678` | n8n page |
| `N8N_API_KEY` | *(empty)* | required to list workflows/executions |
| `NETWORK_SHARE_PATH` | `/share` | root the file browser is confined to |
| `WORKER_LOGS_DIR` | `/share/logs` | scanned for `<job_id>-*` log files |
| `ENABLE_BACKGROUND_PG_SYNC` | `true` | set `false` on extra replicas — only one should sync |
| `JOB_HISTORY_SYNC_INTERVAL` | `90` | seconds between Redis→Postgres syncs |
| `STALE_WORKER_AFTER_SECONDS` | `900` | heartbeat age past which a worker is a leaked registration |

The Network Share root is mounted in
[`../docker-compose.control-plane.yml`](../docker-compose.control-plane.yml) from
`./shared/uploads` and `./shared/output`. Point it at a CIFS/NFS mount to browse a real
file share instead. It is mounted read-write so the editor can save; change to `:ro` for a
read-only dashboard.

## Run

```bash
docker compose up -d --build dashboard
```

Locally, from the repo root:

```bash
pip install -r dashboard/requirements.txt -r app/requirements.txt
PYTHONPATH="$PWD/dashboard:$PWD/app" python3 app/main.py
```

## Tests

```bash
cd app && PYTHONPATH=../dashboard:. python3 -m unittest discover -s tests -v
```

Route wiring and rendering only — pages degrade to an inline error panel when Redis or
Postgres are unreachable, so the tests assert HTML, not data.

## Notes for future work

- **The dashboard has no worker task code on its path, deliberately.** Any RQ attribute
  that unpickles the job payload (`func_name`, `args`) raises `DeserializationError` for
  jobs enqueued against worker functions. Read those fields through
  [`../dashboard/services/rq_compat.py`](../dashboard/services/rq_compat.py) — one
  unguarded access aborts a whole history sync.
- **`Worker.all()` is not safe here.** It reads the `rq:workers` set, which on this
  deployment holds bare worker names instead of `rq:worker:<name>` keys and makes it
  raise. `redis_service._collect_workers` enumerates the hashes directly instead.
- Ported from the Specialfastigheter fork's `app/`, dropping the Interaxo, StreamBIM,
  Revit Analytics, IFC Analysis and Worker Analytics pages along with the classic SPA.
