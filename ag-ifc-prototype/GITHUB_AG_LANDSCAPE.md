# GitHub landscape: AlphaGeometry usage patterns

Survey date: 2026-05-26 (GitHub search `alphageometry`, sorted by stars).

## Official

| Repository | Stars | Usage pattern |
|------------|-------|----------------|
| [google-deepmind/alphageometry](https://github.com/google-deepmind/alphageometry) | ~4846 | AG v1: CLI `python -m alphageometry --mode=ddar|alphageometry`, problems in `imo_ag_30.txt` |
| [google-deepmind/alphageometry2](https://github.com/google-deepmind/alphageometry2) | ~74 | AG2: programmatic `AGProblem.parse()` + `DDAR`, `python -m test` |

## Community (non-trivial)

| Repository | Stars | Relevance to AG-IFC |
|------------|-------|---------------------|
| [foldl/AlphaGeometryRE](https://github.com/foldl/AlphaGeometryRE) | ~40 | Research reimplementation |
| [ZJUVAI/open-alphageometry](https://github.com/ZJUVAI/open-alphageometry) | ~1 | Custom `defs.txt`, random proof generation, premise↔problem mapping experiments |
| [WellyZhang/alphageometry-test](https://github.com/WellyZhang/alphageometry-test) | ~4 | **Benchmark logs** for 125 competition problems—good reference for our eval report format |
| [litexlang/alphageometry-in-litex](https://github.com/litexlang/alphageometry-in-litex) | ~2 | Export/interop with formal proof language |
| [fkatada/gg-dpm-alphageometry](https://github.com/fkatada/gg-dpm-alphageometry) | — | Runnable fork of official repo |
| [jacubero/AlphaGeometry](https://github.com/jacubero/AlphaGeometry) | ~7 | Community fork |

## What does *not* exist (yet)

- No public repo combining **IFC / IfcClash / BIM** with AlphaGeometry.
- No hosted API for AG or AG2.
- MEP routing projects ([IfcOpenShell #6521](https://github.com/IfcOpenShell/IfcOpenShell/issues/6521), [mep_engineering](https://github.com/red1oon/mep_engineering)) use **pathfinding**, not DDAR proofs.

## Pattern we follow in `ag-ifc-prototype`

1. Vendor-pin **alphageometry2** (like most forks).
2. **Programmatic DDAR** calls (like `test.py`), not shell-only.
3. Separate **evaluation manifest** + JSON report (like `alphageometry-test`).
4. Domain-specific problem strings (our BIM fixtures)—similar spirit to `open-alphageometry` custom rules.
