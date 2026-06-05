# n8n parallel IfcPipeline jobs + per-job retry

This document describes the workflow pattern for running multiple IfcPipeline worker jobs in parallel with per-job retry, without caller-supplied enqueue URLs.

## Sub-workflow: IfcPipeline: Wait for jobs

**Production ID:** `G1ArKSIzZRE2o3Vk`  
**Export:** [`example n8n workflows/IfcPipeline_Wait_for_jobs_G1ArKSIzZRE2o3Vk.json`](../example%20n8n%20workflows/IfcPipeline_Wait_for_jobs_G1ArKSIzZRE2o3Vk.json)

### Flow (simplified)

```
Start → Init state → Loop driver (splitInBatches)
  → Wall timeout? → Get job status → Merge status → Switch
      queued/started → Wait poll → Get job status (again)
      finished     → Set success → Loop driver (exit, ok: true)
      failed       → Classify → Can retry?
          yes → Wait retry → Requeue job → After re-enqueue → Get job status
          no  → Set failure → Loop driver (exit, ok: false)
```

Same completion pattern as the original sub-workflow: **success and failure both return through `Loop driver` output 0**, with `ok: true/false` on the item.

On retryable failure the sub-workflow calls **`POST /jobs/{job_id}/requeue`** (RQ native requeue, **same job_id**) via the IfcPipeline **Requeue Job** operation.

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `job_id` | yes | — | RQ job to poll |
| `max_retries` | no | `3` | Requeue attempts after retryable failure |
| `retry_delay_seconds` | no | `5` | Backoff before requeue |
| `poll_interval_seconds` | no | `1` | Delay between status polls |
| `timeout_seconds` | no | `7200` | Wall-clock limit for wait + retries |
| `job_label` | no | — | Pass-through label for aggregation |
| `attempt` | no | `0` | Initial attempt counter |

Callers only pass **`job_id`** (plus optional retry/timing settings). No enqueue URL or body is required.

### Outputs

- **Success:** `{ ok: true, status: "finished", result, job_id, job_label, attempt }`
- **Failure:** `{ ok: false, status: "failed", error, job_id, job_label, attempt, retryable }`

Error classification uses the same regex rules as `n8n-nodes-ifcpipeline` (`GenericFunctions.isRetryableError`).

## Caller pattern (parallel)

```mermaid
flowchart LR
  fanout[Fan-out items] --> enqueue[Enqueue each job]
  enqueue --> attach[Set wait context]
  attach --> execwf[Execute Workflow once per item]
  execwf --> wait[Wait for jobs sub-workflow]
  wait --> merge[Merge or Aggregate]
```

1. **Enqueue** with `waitForCompletion: false` (IFC node) or HTTP POST (RTV).
2. **Set** per item: `job_id`, optional `job_label`, retry settings.
3. **Execute Workflow** → `G1ArKSIzZRE2o3Vk`, mode **Run once for each item**.
4. **Aggregate** results; check `ok` per item.

Do **not** use `Retry On Fail` on the enqueue node when the wait sub-workflow handles retries.

### Pilot: Nobel RTV Export Pipeline

**ID:** `ZFwUkgsAVn4WO02p`  
**Export:** [`example n8n workflows/Nobel_RTV_Export_Pipeline_ZFwUkgsAVn4WO02p.json`](../example%20n8n%20workflows/Nobel_RTV_Export_Pipeline_ZFwUkgsAVn4WO02p.json)

Changes:
- Removed `Limit` node (was serializing batch fan-out).
- `Attach wait context` passes retry settings only (no enqueue URL/body).
- `Wait for Job` runs once per batch item (parallel sub-workflow executions).

## Phase 2: custom nodes

Worker nodes (`IfcPatch`, `IfcClash`, `IfcDiff`, `IfcTester`, etc.) support:

- **Execution Mode:** Sequential | Parallel (when `waitForCompletion: true`)
- **Max Concurrency** (parallel mode)
- **Max Retries Per Job** — RQ requeue via `POST /jobs/{job_id}/requeue` on retryable failure (**same** `job_id`)
- **Retry Delay**

See `n8n-nodes-ifcpipeline/nodes/shared/GenericFunctions.ts` → `waitForJob`, `executeItemsWithOrchestration`.

The **File Operations** node also exposes **Requeue Job** for sub-workflows that poll by `job_id` only.

**Note:** IfcDiff enqueue also sets RQ `Retry(max=3)` at the gateway. Worker-level and n8n-level retries can stack for transient failures — this is expected.

## Gateway API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/jobs/{job_id}/status` | Poll job state |
| POST | `/jobs/{job_id}/requeue` | Requeue a failed/stopped job (same `job_id`) |

## Concurrency notes

- Match **Max Concurrency** / parallel fan-out to RQ worker replica counts per queue.
- Parallel wait holds one n8n execution per job; monitor memory on large batches.
