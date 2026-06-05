# MEP coordination — production finishing touches

Sample workflow: **Nobel — MEP Coordination (Elec vs Struct)**  
(`example n8n workflows/Nobel_MEP_Coordination_Elec_Struct.json`)

## What the sample workflow does

1. **IfcClash** — optional baseline clash count (elec A vs struct B).
2. **IfcPatch `CoordinateClashesFromReport`** — runs `ifc_coord` in `propose_and_apply` with production policy caps.
3. **Download + SharePoint upload** — coordinated IFC (side A), BCF zip, optional patched B IFC.
4. **StreamBIM sync** — `projectId: 13588`, same credentials as DCA pipeline.

SharePoint target (matches DCA): `General/StreamBIM` on Nobel Syncpoint site.

## Before morning BCF import

| Step | Owner | Notes |
|------|-------|-------|
| Rebuild worker | Dev | `docker compose -f docker-compose.workers.yml build ifcpatch-worker && up -d` after syncing `ifc-coord/` |
| Import workflow | Ops | Import JSON into n8n or push via n8nac; verify `CoordinateClashesFromReport` appears in recipe list |
| Pin IFC keys | Ops | Set `elec_s3_key` / `struct_s3_key` (defaults: Nobel E1 + S2 paths in MinIO) |
| Run once manually | Ops | Execute subflow; confirm SharePoint files + StreamBIM sync job |
| BCF import | You | In StreamBIM: import BCF from `General/StreamBIM/{prefix}_bcf.bcfzip`; check Resolved vs In Progress topics |
| IFC federation | You | Upload coordinated E1 replaces or supplements existing doc; confirm clash count dropped in viewer |

## Reduction target (>6%)

Root cause of ~6% was **multi-round queue starvation**: clashes blocked by `batch_cap` were marked `seen_stable` and never retried.

**Fix (runner):** only mark `seen_stable` when `_should_mark_seen()` is true; `batch_cap`-only failures stay in the queue across rounds. Drop applied stable IDs from the working snapshot between applies.

**Policy (production):** `scenarios/coord/nobel_elec_production.json`

- `max_auto_apply_per_round`: 20  
- `max_auto_apply_per_run`: 25  
- Branch topology enabled  

**Measured after fix (2026-05-27):** 165 → 135 clashes (**18.2%** reduction), **25 applied**, 3 bbox regressions correctly rejected, ~84 s wall time.

```bash
cd ifc-coord
PYTHONPATH=. python3 -m ifc_coord.benchmarks.nobel_elec_acceptance \
  --policy scenarios/coord/nobel_elec_production.json \
  --max-rounds 10 --max-auto-apply 25
```

After accepting new apply behaviour, refresh golden:

```bash
PYTHONPATH=. python3 -m ifc_coord.benchmarks.nobel_elec_acceptance \
  --policy scenarios/coord/nobel_elec_production.json \
  --update-golden
```

## Production checklist (remaining)

- [ ] Worker smoke test: patch job returns `bcf_key`, `manifest_key`, `coordination_summary`
- [ ] Wire `clash_report` arg to skip baseline re-clash when IfcClash already ran (future)
- [ ] SharePoint file naming: `{output_prefix}_coord.ifc`, `{output_prefix}.bcfzip`, `{output_prefix}_struct_coord.ifc`
- [ ] StreamBIM: confirm BCF topic GUIDs map to moved elements (branch moves list all translated GUIDs)
- [ ] Error path: Teams Error subflow on clash/patch failure (same as Propagate subflow)
- [ ] Schedule or webhook trigger once validated manually
- [ ] Document expected reduction band in run summary (target ≥15% on elec pilot; tune policy if tier gates block)

## n8n inputs (subflow)

| Input | Default | Purpose |
|-------|---------|---------|
| `elec_s3_key` | `uploads/.../E1_2b_BIM_XXX_600_00.ifc` | Side A (input to patch) |
| `struct_s3_key` | `uploads/.../S2_2B_BIM_XXX_0001_00.ifc` | Side B |
| `struct_s3_version_id` | optional | Version pin for struct |
| `output_prefix` | `nobel_elec_coord` | S3 + SharePoint stem |
| `mode` | `propose_and_apply` | |
| `max_rounds` | `10` | |
| `max_auto_apply` | `25` | |
| `policy_json` | inline production gates | Override without redeploy |

## Artifact keys (worker result)

| Field | Content |
|-------|---------|
| `output_key` | Patched electrical IFC (primary) |
| `bcf_key` | BCF 2.1 zip |
| `manifest_key` | CDE hand-off JSON |
| `patched_b_key` | Patched structural copy (when moves touch B) |
| `proposals_json_key` | Full audit trail |
| `coordination_summary` | applied / proposed / final clash counts |
