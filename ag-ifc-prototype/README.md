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










## Multi-attempt clashes, global regression, and BCF export

Each clash can be retried up to `max_attempts_per_clash` (cumulative moves). After every clash round a **full IfcClash** run checks for **new global clashes** (regression) vs the baseline snapshot.

```bash
./scripts/run_regression_suite.sh
```

Outputs per case under `reports/regression_work/<case_id>/`:

- `baseline_snapshot.json` — all clash pairs before fixes
- `regression_round_*.json` — snapshots after each clash
- `<case>_validated_fixes.bcf` — BCF 2.1 topics with viewpoints (clash location + proposed position)
- `<case>_validated_fixes.json` — manifest of validated fixes

Pre-filter IfcClash JSON before solve:

```bash
PYTHONPATH=. python3 -m ag_ifc.run_prefilter clash.json -o candidates.json --tiers solve
```

## AG suitability evaluation & IfcClash pre-filter

Evaluates **every clash** from the IFC scenario matrix with heuristics + optional DDAR proofs, then classifies into:

| Tier | Meaning |
|------|---------|
| **solve** | Suitable for auto-fix / workflow3d retest |
| **review** | AG may certify but coordination judgment needed |
| **exclude** | Poor candidate (dual structural, huge penetration, civil assemblies) |

```bash
./scripts/run_ag_suitability_eval.sh          # full eval → reports/ag_suitability_latest.json
PYTHONPATH=. python3 -m ag_ifc.run_prefilter clash.json -o candidates.json
```

Static rules: `scenarios/ag_prefilter_rules.json` (updated from eval).  
`run_clash_set_prefiltered()` in `clash_runner.py` applies the filter right after IfcClash.

## 3D clash routing + AEC reasoning workflow

Sorts clashes (severity, discipline, spatial cluster), plans an **orthogonal 3D polyline** around obstacle AABBs, applies the net translation to the movable element, and certifies with **multi-plane AlphaGeometry** (plan XY + section stubs + per-segment proofs).

```bash
./scripts/run_workflow3d.sh
# or
PYTHONPATH=. python3 -m ag_ifc.run_workflow3d
```

Reports: `reports/workflow3d_suite_latest.json` and `.md`.

| Module | Role |
|--------|------|
| `ag_ifc/clash_sorter.py` | AEC triage / fix order |
| `ag_ifc/routing3d.py` | Manhattan A* voxel routing |
| `ag_ifc/ifc_geometry.py` | IFC AABB + discipline extraction |
| `ag_ifc/reasoning3d.py` | Detect → sort → route → AG → fix → re-clash |
| `ag_ifc/compiler.py` | `clash_to_ag2_multiplane`, `route_segments_to_ag2_problems` |

## Iterative evaluation (clash → fix → re-clash until pass)

Primary **AG evaluation suite** for clash resolution:

```bash
./scripts/run_iterative_suite.sh
```

Loop per case:
1. **IfcClash** — detect clashes
2. **Fix** — translate movable element (MEP priority) along separation vector
3. **AG2 DDAR** — certify parallel-offset relation for the fix (optional)
4. **Re-clash** — repeat until zero clashes or `max_iterations`

Reports: `reports/iterative_suite_latest.json` and `.md`

Run a single case:

```bash
python3 -m ag_ifc.run_iterative_suite --case iter_arch_vs_beams
```

## IFC scenarios (real multi-discipline models)

```bash
pip install -r requirements-ifc.txt
./scripts/fetch_ifc_models.sh       # PCERT building + infrastructure (~7 MB)
./scripts/run_ifc_scenarios.sh      # 15 IfcClash scenarios
./scripts/run_ifc_scenarios.sh --formalize-ag
```

See **[IFC_MODELS.md](IFC_MODELS.md)** for open-source model catalog (buildingSMART, Duplex, Clinic, BIM4LCA, etc.).

## Scenario matrix (bulk AEC evaluation)

Run **179+ scenarios** to discover where AG2 helps in clash coordination:

```bash
./scripts/run_scenarios.sh
```

This generates parametric catalogs (offsets, spans, crossings) and writes:

- `reports/scenario_matrix_latest.json` — full results
- `reports/scenario_matrix_latest.md` — category summary
- `reports/scenario_matrix_latest.csv` — for Excel/BI

Read **[AEC_CAPABILITY_GUIDE.md](AEC_CAPABILITY_GUIDE.md)** for interpreted findings.

Options:

```bash
python3 -m ag_ifc.run_scenarios --base-only      # 19 hand-authored scenarios only
python3 scripts/generate_scenarios.py            # regenerate parametric catalog only
python3 -m ag_ifc.run_scenarios --fail-on-regression
```


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
