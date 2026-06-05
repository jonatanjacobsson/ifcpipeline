# ifcfast-worker

Runs **[ifcfast](https://pypi.org/project/ifcfast/)** jobs for IfcPipeline — native IFC read, layer extraction, spatial traverse, and geometry analytics on the **ifcfast** queue (`redis`).

## Credit

**This worker is a thin wrapper.** The hard problems — Rust STEP tokenization, tier‑1 `products_df`, Parquet layer cache, spatial graph, mesh QTO — are solved by **[ifcfast](https://pypi.org/project/ifcfast/)**, created and maintained by **[Edvard Granskogen Kjorstad](https://github.com/EdvardGK)**.

| | |
|---|---|
| **Author** | [Edvard Granskogen Kjorstad](https://github.com/EdvardGK) |
| **Source** | [github.com/EdvardGK/ifcfast](https://github.com/EdvardGK/ifcfast) |
| **Package** | [pypi.org/project/ifcfast](https://pypi.org/project/ifcfast/) |
| **Workbench origin** | [github.com/EdvardGK/ifc-workbench](https://github.com/EdvardGK/ifc-workbench) |

Please **star**, **cite**, and **file bugs** on the upstream project when you hit parser or API limits. IfcPipeline’s role is deployment (Docker, S3, RQ, n8n) — not reimplementing ifcfast.

## When to use ifcfast vs ifccsv

| **ifcfast** (`POST /ifcfast`) | **ifccsv** (`POST /ifccsv`) |
|-------------------------------|-----------------------------|
| Fast reads on large IFCs (Rust hot path) | Full IfcOpenShell model + selectors |
| 14 schema layers, Parquet cache | CSV / XLSX / ODS export |
| `summary`, `traverse`, `diff`, `mesh_qto` | Import and write-back to IFC |
| `export_products`, `filter_products`, `by_type` | Arbitrary attribute / grouping rules |

On a ~125 MiB Nobel plumbing model, `export_products` is on the order of **~30× faster** in worker CPU time than an IfcOpenShell `ifccsv` export of comparable products (see benchmarks below).

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/ifcfast/operations` | List operations, data layers, traverse ops, output formats |
| `POST` | `/ifcfast` | Enqueue any operation (body: `IfcFastRequest` in `shared/classes.py`) |

Job results land under `s3://…/output/ifcfast/` (or `./shared/output/ifcfast/` when object storage is off).

### Operations

| `operation` | Output |
|-------------|--------|
| `export_products` | Filtered `products_df` → CSV / JSON / Parquet (**default**) |
| `export_layer` | One layer (`layer=psets`, …) |
| `extract_all` | All layers → multiple files (`artifacts[]` in result) |
| `summary` | Model summary JSON |
| `schemas` | Column schemas JSON |
| `traverse` | `traverse` + `guid` → JSON |
| `types` | Type counts JSON |
| `type_bank` / `type_summary` | Type introspection JSON |
| `preview` | `preview_table` + `preview_n` |
| `diff` | `other_filename` required (second IFC in uploads) |
| `filter_products` | `filter_entity` / `filter_mode` / `filter_storey_guid` |
| `by_type` | `entity_type` |
| `mesh_qto` | Products + surfaces tables |
| `point_cloud` | Sampled points (prefer Parquet; tune `point_cloud_max_points`) |
| `meshes_summary` | Per-mesh counts (not full `meshes()` topology) |

### Data layers (`export_layer` / `extract_all`)

`products`, `storeys`, `spaces`, `type_objects`, `contained_in`, `aggregates`, `storey_building`, `voids`, `psets`, `quantities`, `materials`, `classifications`, `drift`, `segments`

### Example

```json
POST /ifcfast
{
  "filename": "model.ifc",
  "operation": "export_layer",
  "layer": "psets",
  "output_filename": "model_psets.parquet",
  "output_format": "parquet"
}
```

## Deploy

```bash
cd ifcpipeline   # repository root
docker compose -f docker-compose.control-plane.yml \
  -f docker-compose.workers.yml \
  up -d --build ifcfast-worker api-gateway
```

Pin in `ifcfast-worker/requirements.txt`: `ifcfast>=0.4.18` (rebuild image after bumps).

### Worker environment

| Variable | Purpose |
|----------|---------|
| `HOME=/tmp` | Writable home for non-root user `1000:1000` |
| `XDG_CACHE_HOME=/tmp/.cache` | ifcfast Parquet layer cache (`~/.cache/ifcfast/…`) |
| `USE_OBJECT_STORAGE`, `S3_*` | Same as other workers |

### Resources (default in `docker-compose.workers.yml`)

- **1 CPU**, **4 GB RAM** — enough for layer export + `mesh_qto` on ~100 MB IFCs after cache warm-up.
- **`point_cloud`** on very large models may still OOM; lower `point_cloud_max_points` or raise memory.

## Implementation layout

| Path | Role |
|------|------|
| `ifcfast-worker/tasks.py` | RQ entrypoint `run_ifcfast_export` |
| `shared/ifcfast_ops.py` | Operation dispatcher → PyPI `ifcfast` |
| `shared/ifcfast_export.py` | `export_products` helpers |
| `shared/classes.py` | `IfcFastRequest` |

## Benchmarks

From repo root (API key in `.env`):

```bash
# ifcfast vs ifccsv on default ~125 MiB P1 model
./n8n-tests/bench-ifcfast-vs-ifccsv.sh

# All ifcfast operations (timing table)
./n8n-tests/bench-ifcfast-operations.sh
IFC_BENCH_SKIP_HEAVY=0 ./n8n-tests/bench-ifcfast-operations.sh   # includes extract_all, mesh_qto, …

# Local PyPI vs IfcOpenShell (no Docker)
PYTHONPATH=shared python3 n8n-tests/profile-ifcfast-diagnosis.py
```

Indicative RQ times on **P1_2b** (~124 MiB, warm cache): `export_products` ~1.6 s, `extract_all` ~2.2 s, `mesh_qto` ~5.4 s; introspection ops (`summary`, `types`, …) ~1.5–1.8 s.

## Not exposed via API

- Full `meshes()` triangle buffers (use `meshes_summary` or `mesh_qto`; raw meshes are huge).
- IFC authoring / import (use `/ifccsv` or IfcOpenShell).

## n8n

`n8n-nodes-ifcpipeline` **IFC Fast** node (v2) maps UI operations to `POST /ifcfast`. Deploy nodes:

```bash
cd /path/to/n8n-nodes-ifcpipeline && ./deploy-local.sh
```

See [`n8n-tests/README.md`](../n8n-tests/README.md) for smoke and benchmark scripts.
