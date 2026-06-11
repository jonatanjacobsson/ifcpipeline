# TopologicPy Worker

`topologicpy-worker` is a dedicated RQ worker for federating spatial
relationships into IFC element properties. It targets the common workflow of
stamping thousands of MEP elements from one or more target models against rooms
and zones from one or more spatial/architecture models.

## Endpoint

```http
POST /topologicpy/roomstamp
```

Example request:

```json
{
  "spatial_files": ["Building-Architecture.ifc"],
  "element_files": ["Building-Hvac.ifc"],
  "output_file": "topology_roomstamp_report.json",
  "element_query": "IfcElement",
  "space_query": "IfcSpace",
  "include_zones": true,
  "engine": "auto",
  "sample_strategy": "bbox_centroid",
  "stamp": false,
  "stamp_ambiguous": false,
  "tolerance": 0.01
}
```

Run with `stamp=false` first to review the match report. Set `stamp=true` to
write a new stamped IFC for each target model. By default, ambiguous matches
are skipped and reported; set `stamp_ambiguous=true` only if your workflow
accepts choosing the smallest matching space.

Stamping writes `Pset_IfcPipelineRoomStamp` to matched target elements with:

- `SpatialMatchStatus` — `Contained`, `Resolved`, `Proximity`, or `Unmatched`
- `SpatialMatchMethod` — `overlap_majority`, `overlap_geometric`, `proximity`, or `legacy`
- `SpatialMatchConfidence` — vote or overlap ratio (0–1)
- `SpatialMatchCount` — number of room candidates considered
- `SpatialSourceFile`
- `SpatialRelationshipEngine`
- `SpaceGlobalId`
- `SpaceName`
- `SpaceLongName`
- `RoomGlobalId`
- `RoomName`
- `RoomLongName`
- `BuildingStoreyName`
- `ZoneNames`
- `ZoneGlobalIds`
- `StampedBy`

## What it benchmarks

The report JSON includes:

- model load time
- room/space geometry indexing time
- target element geometry time
- containment match time
- optional IFC stamping/write time
- candidate test count
- matched, unmatched, and ambiguous match counts
- match method buckets (`overlap_majority_count`, `overlap_geometric_count`, `proximity_forced_count`)
- confidence histogram (`confidence_histogram`)
- stamped, skipped ambiguous, and skipped unmatched counts
- TopologicPy import availability/version/import overhead

The `bbox` engine uses IfcOpenShell world-coordinate geometry bounding boxes.
The `topologicpy` engine converts those room bounding boxes into TopologicPy
prism cells and runs TopologicPy point containment against each target element
sample point. This is intentionally simple and repeatable: it establishes the
minimum viable throughput and highlights model quality issues before investing
in exact TopologicPy cells generated from room solids or richer spatial topology.

## Workflow usage

Supported input references match normal IfcPipeline chaining:

- `model.ifc` or `uploads/model.ifc`
- `output/patch/model_patched.ifc`
- `examples/Building-Architecture.ifc`
- S3 object keys when object storage is enabled

Outputs are always rooted under `output/topology/` in S3 mode or
`/output/topology/` in filesystem mode.

Stamped IFC files use the **input basename** by default (for example
`Building-Hvac.ifc` in → `output/topology/Building-Hvac.ifc` out). Set
`output_ifc_prefix` to an explicit `.ifc` filename when stamping a single
element file (like ifcpatch `output_file`), or to a subdirectory when stamping
multiple element files.

## Job result shape

`GET /jobs/{job_id}/status` → `result` matches **ifcpatch** at the top level for
stamped IFC output (`output_key`, `output_path`, `recipe`, `sha256`, etc.).
`summary`, `benchmark`, and report metadata live one level below in
`topology_report`.

**Dry run (`stamp=false`):** no IFC fields at the top level — only
`success`/`message` plus `topology_report` (use `result.topology_report.report_key`
for the benchmark JSON).

**Single stamped IFC:** ifcpatch-shaped fields directly on `result`, plus
`result.topology_report` for metrics.

**Multiple stamped IFCs:** `result.outputs` is an array of ifcpatch-shaped
objects; `result.topology_report` holds the shared benchmark/report metadata.

n8n chaining examples:

- Stamped IFC (single): `$json.result.output_key`
- Stamped IFC (multi): `$json.result.outputs[0].output_key`
- Dry-run report: `$json.result.topology_report.report_key`
- Match stats: `$json.result.topology_report.summary`

The on-disk/S3 report JSON (`output_file`) still contains the full space/element
detail; only the RQ job return is split this way.

## Production tuning

Default worker env (see `docker-compose.workers.yml`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `IFCTOPOLOGY_CELL_MODE` | `prism` | Fast bbox prism cells (use `mesh` only for QA) |
| `IFCTOPOLOGY_DISTANCE_MODE` | `bbox` | Fast bbox distance for ambiguous/unmatched (use `topologic` for QA) |
| `IFCTOPOLOGY_MAX_PROXIMATE_SPACES` | `32` | Cap nearest-room search for unmatched elements |
| `IFCTOPOLOGY_PROGRESS_LOG_EVERY` | `250` | Structured `[roomstamp]` progress log interval |
| `IFCTOPOLOGY_OVERLAP_SAMPLES` | `24` | Max sample points per element for footprint voting |
| `IFCTOPOLOGY_OVERLAP_CONFIDENCE_MARGIN` | `0.55` | Min dominant vote share to accept without geometric fallback |
| `IFCTOPOLOGY_OVERLAP_COVERAGE_MIN` | `0.35` | Min fraction of sample points hitting any room for fast accept |

Per-request overrides: `cell_mode`, `distance_mode`, `max_proximate_spaces`, `overlap_resolution`, `overlap_samples`, `overlap_confidence_margin`, `overlap_coverage_min`, `hybrid_geometric_fallback` on
`POST /topologicpy/roomstamp` (also exposed in the n8n TopologicPy node Options).

**Tracking:** worker logs grep-friendly lines like
`[roomstamp] phase=matching_progress processed=250 ...`. Poll
`GET /jobs/{job_id}/status` for `progress` (phase, processed, total, eta_seconds).

**Production profile (recommended):** `engine=topologicpy`, `cell_mode=prism`,
`distance_mode=bbox`, `stamp=true`, n8n timeout ≥ 14400s for 5k+ elements.

## TopologicPy decision criteria

Use this worker to answer and then tune:

1. Can TopologicPy be installed reliably in the worker image alongside
   IfcOpenShell?
2. Does import/runtime overhead remain acceptable for queued jobs?
3. How many room candidates and MEP element samples are involved per federated
   workflow?
4. How often do bounding-box matches become ambiguous or miss expected rooms?
5. Which models need precise room cell topology instead of fast bbox indexing?

If bbox-derived cells show high ambiguous or false-positive rates, the next
step is to generate TopologicPy cells from IfcSpace solids while keeping the
same request/response contract.

## A vs A_spaces preset (architecture elements vs spaces model)

Use one `RoomStamp` job — no separate recipe. Federate **rooms** from the
spaces export against **elements** from the architecture export:

```json
{
  "spatial_files": ["A1_2b_BIM_XXX_0003_00.ifc"],
  "element_files": ["A1_2b_BIM_XXX_0001_00.ifc"],
  "element_query": "IfcElement",
  "space_query": "IfcSpace",
  "engine": "auto",
  "cell_mode": "mesh",
  "stamp": false,
  "report_detail": "summary",
  "overlap_resolution": true,
  "overlap_samples": 24,
  "hybrid_geometric_fallback": true,
  "resolve_unmatched_with_topologicpy": true,
  "stamp_ambiguous": true
}
```

**Input mapping**

| Role | Typical file | Contains |
|------|--------------|----------|
| `spatial_files` | `*_0003_*.ifc` (spaces) | `IfcSpace` room geometry |
| `element_files` | `*_0001_*.ifc` (architecture) | Walls, doors, MEP, etc. |

**Reading match quality**

After stamping, filter on the new honesty fields:

| Field | Trust level |
|-------|-------------|
| `SpatialMatchStatus=Contained` + `SpatialMatchMethod=overlap_majority` | High — decisive multi-point vote |
| `SpatialMatchStatus=Resolved` + `SpatialMatchMethod=overlap_geometric` | Medium — footprint overlap tie-break |
| `SpatialMatchStatus=Proximity` | Low — nearest room fallback; check `SpatialMatchConfidence` |
| `SpatialMatchConfidence` < 0.5 | Review manually |

Dry-run first (`stamp=false`) and inspect `topology_report.summary` buckets:
`overlap_majority_count`, `overlap_geometric_count`, `proximity_forced_count`,
and `confidence_histogram`.

Then stamp with `stamp=true`. With `stamp_ambiguous=false`, only
`SpatialMatchStatus=Contained` elements are written; `Resolved` and `Proximity`
matches are reported but skipped.

## Local smoke request

After starting Docker Compose and uploading/copying the sample files to
`shared/uploads`, enqueue a dry run through the API:

```bash
curl -X POST http://localhost:8100/topologicpy/roomstamp \
  -H "X-API-Key: ${IFC_PIPELINE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "spatial_files": ["Building-Architecture.ifc"],
    "element_files": ["Building-Hvac.ifc"],
    "output_file": "topology_roomstamp_report.json",
    "engine": "auto",
    "stamp": false
  }'
```

Review `shared/output/topology/topology_roomstamp_report.json` or
`result.topology_report.report_key` / `result.topology_report.report_path` from
the job status before enabling `stamp=true`.

Then stamp reviewed matches into a new IFC (default output keeps the input
basename under `output/topology/`):

```bash
curl -X POST http://localhost:8100/topologicpy/roomstamp \
  -H "X-API-Key: ${IFC_PIPELINE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "spatial_files": ["Building-Architecture.ifc"],
    "element_files": ["Building-Hvac.ifc"],
    "output_file": "topology_roomstamp_stamp_report.json",
    "engine": "auto",
    "stamp": true,
    "stamp_ambiguous": false
  }'
```

Optional explicit output filename for a single element file:

```json
"output_ifc_prefix": "Building-Hvac-stamped.ifc"
```
