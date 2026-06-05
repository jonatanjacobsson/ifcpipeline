# Phase 4 — Coordination worker (BCF + patched IFC for CDE)

## Overview

The `ifc_coord` engine runs inside **ifcpatch-worker** via the custom recipe
`CoordinateClashesFromReport`. One job produces:

| Output | Worker field | Use in CDE |
|--------|--------------|------------|
| Patched IFC (side A) | `output_key` / `output_path` | StreamBIM document revision |
| BCF 2.1 | `bcf_key` / `bcf_path` | Attach to coordination topic |
| Proposals JSON | `proposals_json_key` | Baserow / audit |
| CDE manifest | `manifest_key` | n8n routing (applied fixes + moved GUIDs) |
| Patched IFC (side B) | `patched_b_key` (when present) | Second revision or federation upload |

## Docker

Rebuild ifcpatch-worker after syncing `ifc-coord/`:

```bash
cd /home/bimbot-ubuntu/apps/ifcpipeline
rsync -a --delete ../ifcpipeline-ag-review/ag-ifc-prototype/ifc_coord/ ifc-coord/ifc_coord/
rsync -a --delete ../ifcpipeline-ag-review/ag-ifc-prototype/ag_ifc/ ifc-coord/ag_ifc/
docker compose -f docker-compose.workers.yml build ifcpatch-worker
docker compose -f docker-compose.workers.yml up -d ifcpatch-worker
```

Image layout: `/app/ifc_coord`, `/app/ag_ifc` on `PYTHONPATH`.

## n8n workflow (orchestrator)

```
1. IfcClash
     → output_key: baseline clash report (optional reference; engine re-clashes today)

2. IfcPatch (custom recipe CoordinateClashesFromReport)
     input_file:  uploads/elec.ifc          (model A)
     output_file: output/patch/elec_coordinated.ifc
     use_custom:  true
     arguments:
       [0] output/struct.ifc               (file_b — partner model)
       [1] {{ $json.output_key }}          (clash_report — reserved)
       [2] {}                              (policy inline JSON or S3 key)
       [3] propose_and_apply
       [4] 10
       [5] 20
       [6] nobel_elec_apply

3. IfcPipeline Download File × N
     keys from job result: output_key, bcf_key, manifest_key, patched_b_key

4. StreamBIM
     - Document revision upload (patched IFC)
     - uploadToTopic (BCF on coordination topic)
```

### Job result keys (after patch)

```json
{
  "output_key": "output/patch/elec_coordinated.ifc",
  "bcf_key": "output/patch/elec_coordinated_bcf.bcf",
  "proposals_json_key": "output/patch/elec_coordinated_proposals_json.json",
  "manifest_key": "output/patch/elec_coordinated_manifest.json",
  "coordination_summary": { "applied_count": 5, "final_clash_count": 155, ... }
}
```

## BCF content

- **Resolved** — applied fixes; description lists fixed element GUIDs (branch moves list all translated GUIDs)
- **In Progress** — proposed-only fixes when policy includes them
- Viewpoints at clash midpoint + post-translation position for movable side

## Manifest (`{case_id}_manifest.json`)

```json
{
  "patched_ifc": { "a": "...", "b": "..." },
  "applied_fixes": [
    {
      "stable_id": "...",
      "movable_guid": "...",
      "branch_guids": ["...", "..."],
      "translation_m": [0.0, 0.2, 0.0],
      "fix_strategy": "branch_parallel_offset"
    }
  ]
}
```

Use this in n8n to drive CDE metadata (which elements moved, clash stable IDs cleared).

## Local CLI (without worker)

```bash
cd ag-ifc-prototype
PYTHONPATH=. python -m ifc_coord run \
  --a elec.ifc --b struct.ifc --apply \
  --output-dir /tmp/coord_out
# → proposals JSON, BCF, manifest, work-copy IFCs in output dir
```

## Remaining work

- [ ] Wire `clash_report` arg to skip baseline re-clash (use ifcclash-worker JSON)
- [ ] n8n workflow JSON in repo + Baserow `ifc_coord_proposals` table
- [ ] StreamBIM node helper: upload BCF + IFC bundle from patch job result in one subflow
- [x] Sample n8n workflow: `example n8n workflows/Nobel_MEP_Coordination_Elec_Struct.json`
- [ ] Golden snapshot refresh after branch-move policy changes (`--update-golden`)
