# IFC Topology Worker Feasibility

`ifctopology-worker` is an experimental RQ worker for evaluating room/zone
stamping across federated IFC models. The first benchmark targets the common
workflow of stamping thousands of MEP elements from one or more target models
against rooms and zones from one or more spatial/architecture models.

## Endpoint

```http
POST /ifctopology/roomstamp
```

Example request:

```json
{
  "spatial_files": ["Building-Architecture.ifc"],
  "element_files": ["Building-Hvac.ifc"],
  "output_file": "topology_roomstamp_report.json",
  "element_query": "IfcDistributionElement",
  "space_query": "IfcSpace",
  "include_zones": true,
  "engine": "auto",
  "sample_strategy": "bbox_centroid",
  "stamp": false,
  "tolerance": 0.01
}
```

Set `stamp=true` only after reviewing a dry-run report. Stamping writes
`Pset_IfcPipelineRoomStamp` to matched target elements with:

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
- TopologicPy import availability/version/import overhead

The `bbox` engine uses IfcOpenShell world-coordinate geometry bounding boxes.
The `topologicpy` engine converts those room bounding boxes into TopologicPy
prism cells and runs TopologicPy point containment against each target element
sample point. This is intentionally simple and repeatable: it establishes the
minimum viable throughput and highlights model quality issues before investing
in exact TopologicPy cells generated from room solids or richer spatial topology.

## TopologicPy decision criteria

Use this worker to answer:

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

## Local smoke request

After starting Docker Compose and uploading/copying the sample files to
`shared/uploads`, enqueue a dry run through the API:

```bash
curl -X POST http://localhost:8100/ifctopology/roomstamp \
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

Review `shared/output/topology/topology_roomstamp_report.json` or the returned
S3 output key before enabling `stamp=true`.
