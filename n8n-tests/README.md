# ifcpipeline n8n test suite — ifcfast

Tests the **ifcfast** worker end-to-end: API enqueue, job completion, and (optionally)
the `n8n-nodes-ifcpipeline` **IFC Fast CSV** node via an importable workflow.

## Prerequisites

1. Stack up with object storage (same as main smoke test):

   ```bash
   cd /home/bimbot-ubuntu/apps/ifcpipeline
   docker compose -f docker-compose.control-plane.yml \
     -f docker-compose.workers.yml up -d --build api-gateway ifcfast-worker redis seaweedfs seaweedfs-setup postgres
   ```

2. API key in `.env` as `IFC_PIPELINE_API_KEY` (default smoke: `pocsecret`).

3. For the n8n workflow test, deploy the node package:

   ```bash
   cd /home/bimbot-ubuntu/apps/n8n-nodes-ifcpipeline && ./deploy-local.sh
   ```

## API-only test (no n8n UI)

```bash
./n8n-tests/run-ifcfast-api-test.sh
```

## n8n workflow

1. Import `n8n-tests/IfcFast_Export_Smoke.json` into n8n.
2. Assign **IFC Pipeline API** credential (`http://api-gateway`, your API key).
3. Run manually — expects `Building-Architecture.ifc` under `/examples` in the stack.

## Compare with ifccsv

`run-ifcfast-api-test.sh` prints row counts from both `/ifcfast` and `/ifccsv`
on the same query so you can sanity-check parity.

## Benchmark (~100MB IFC)

```bash
# Default model: Nobel P1_2b (~125 MiB) under ifc-coord/reports/…
./n8n-tests/bench-ifcfast-vs-ifccsv.sh

# Or point at any large IFC:
IFC_BENCH_FILE=/path/to/model.ifc IFC_BENCH_RUNS=2 ./n8n-tests/bench-ifcfast-vs-ifccsv.sh
```

Reports median wall-clock and RQ `execution_time_seconds` for each worker.

## Benchmark all ifcfast operations

```bash
./n8n-tests/bench-ifcfast-operations.sh              # skips mesh_qto / point_cloud / extract_all
IFC_BENCH_SKIP_HEAVY=0 ./n8n-tests/bench-ifcfast-operations.sh
```
