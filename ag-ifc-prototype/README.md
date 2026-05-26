# AG-IFC Prototype Lab

Minimal, self-contained environment to **evaluate** AlphaGeometry2 (DDAR), prototype clash-to-formal pipelines, and optional IfcClash tooling—without coupling to the full IfcPipeline Docker stack.

## What this is

| Component | Purpose |
|-----------|---------|
| `vendor/alphageometry2` | Cloned on setup ([google-deepmind/alphageometry2](https://github.com/google-deepmind/alphageometry2)) |
| `ag_ifc/` | Python harness: run DDAR, compile clash fixtures, optional IfcClash smoke test |
| `fixtures/` | Sample clash JSON, BIM-inspired AG2 problems, evaluation manifest |
| `scripts/` | `setup_ag2.sh`, `run_evaluation.sh` |

This implements **Phase 0** from [ALPHAGEOMETRY_IFC_CLASH_RESEARCH.md](../.cursor/ALPHAGEOMETRY_IFC_CLASH_RESEARCH.md).

## Quick start

```bash
cd ag-ifc-prototype
./scripts/setup_ag2.sh          # clones AG2 into vendor/
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
./scripts/run_evaluation.sh     # writes reports/eval_report.json
```

Optional IfcClash evaluation (needs IfcOpenShell wheels, linux/amd64):

```bash
pip install -r requirements-ifc.txt
./scripts/run_evaluation.sh --with-ifc
```

## Evaluation report

`run_evaluation.sh` produces `reports/eval_report.json` with:

- **ag2_reference** — upstream `python -m test` smoke (IMO problems)
- **ag2_fixtures** — BIM-inspired problems in `fixtures/problems.json`
- **clash_compiler** — clash JSON → AG2 stub (formalization preview)
- **ifcclash_smoke** (optional) — clash sets on `../shared/examples/*.ifc`

## GitHub landscape (similar uses of AlphaGeometry)

Survey of public repos (May 2026). **No project integrates IFC/BIM clash resolution with AG**; usage falls into these buckets:

| Repo | Stars | Pattern |
|------|-------|---------|
| [google-deepmind/alphageometry](https://github.com/google-deepmind/alphageometry) | ~4.8k | Official AG v1: DDAR + LM, `python -m alphageometry --mode=ddar` |
| [google-deepmind/alphageometry2](https://github.com/google-deepmind/alphageometry2) | ~74 | Official AG2: DDAR only, `AGProblem.parse()` + `DDAR` ([test.py](https://github.com/google-deepmind/alphageometry2/blob/main/test.py)) |
| [ZJUVAI/open-alphageometry](https://github.com/ZJUVAI/open-alphageometry) | ~1 | Fork + random proof generation, custom `defs.txt` rules, premise↔problem experiments |
| [foldl/AlphaGeometryRE](https://github.com/foldl/AlphaGeometryRE) | ~40 | Reimplementation / research fork |
| [WellyZhang/alphageometry-test](https://github.com/WellyZhang/alphageometry-test) | ~4 | **Evaluation logs** on 125 competition problems (not IFC) |
| [litexlang/alphageometry-in-litex](https://github.com/litexlang/alphageometry-in-litex) | ~2 | Bridge toward formal proof language (Litex) |
| [fkatada/gg-dpm-alphageometry](https://github.com/fkatada/gg-dpm-alphageometry) | — | Mirror/fork for running DDAR |
| Others | 0–7 | Personal forks, presentations, translations |

**Takeaway for AG-IFC:** Community pattern is **wrap DDAR with custom problem strings + evaluation harness**, not embed AG inside CAD. This prototype follows the AG2 `test.py` pattern (programmatic `AGProblem` + `DDAR`) and adds clash JSON → AG2 stubs as the first BIM-facing step.

## Fixture problems

`fixtures/problems.json` includes:

- `bim_plan_parallel_duct` — duct centerline parallel to beam axis (plan abstraction)
- `bim_clearance_offset_goal` — prove offset segment congruent to target clearance

## Next steps

1. Replace stub compiler with real placement/centerline extraction from IFC.
2. Add `docker-compose.ag.yml` service to IfcPipeline when ready.
3. Wire `agclash-worker` RQ queue to this harness.

## License

Prototype code: same as IfcPipeline. AlphaGeometry2: Apache 2.0 (Google).
