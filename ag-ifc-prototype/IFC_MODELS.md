# Open-source multi-discipline IFC models

Catalog for `ag-ifc-prototype` clash scenarios. All models listed here are **openly redistributable** for testing (check each license before production use).

## Quick start

```bash
cd ag-ifc-prototype
pip install -r requirements-ifc.txt
./scripts/fetch_ifc_models.sh      # download PCERT samples (~7 MB total)
./scripts/run_ifc_scenarios.sh     # 15 clash scenarios → reports/ifc_scenarios_latest.*
./scripts/run_ifc_scenarios.sh --formalize-ag   # + AG2 stub on each clash found
```

---

## Recommended sets (work without Git LFS)

### 1. PCERT Sample Building — **already in IfcPipeline**

| File | Discipline | In repo |
|------|------------|---------|
| `Building-Architecture.ifc` | Architecture | `shared/examples/` |
| `Building-Structural.ifc` | Structural | `shared/examples/` |
| `Building-Hvac.ifc` | HVAC / MEP | `shared/examples/` |
| `Building-Landscaping.ifc` | Landscaping | `shared/examples/` |

- **Source:** [buildingSMART/Sample-Test-Files — PCERT Sample Scene](https://github.com/buildingSMART/Sample-Test-Files/tree/main/IFC%204.3.2.0%20(IFC4X3_ADD2)/PCERT-Sample-Scene)
- **Schema:** IFC4X3_ADD2
- **License:** CC BY 4.0
- **Clashes found:** Architecture vs structural beams, architecture vs full structure, etc.

### 2. PCERT Sample Infrastructure — **download via script**

| File | Discipline | Size (approx) |
|------|------------|---------------|
| `Infra-Bridge.ifc` | Bridge / structural | 1.8 MB |
| `Infra-Road.ifc` | Civil road | 0.4 MB |
| `Infra-Rail.ifc` | Rail | 0.2 MB |
| `Infra-Plumbing.ifc` | Underground plumbing | 0.5 MB |
| `Infra-Landscaping.ifc` | Site | 3.0 MB |

- **Same source repo** as building set (different files in PCERT scene).
- **Clashes found:** Plumbing vs road (utilities corridor), etc.

---

## Classic federated sets (Git LFS — optional local)

These are the **industry-standard** multi-discipline test sets but are stored with **Git LFS** on GitHub. The upstream org may hit LFS bandwidth limits in CI; clone locally:

```bash
git clone --depth 1 https://github.com/buildingsmart-community/Community-Sample-Test-Files.git /tmp/bsi-samples
cd /tmp/bsi-samples && git lfs pull
```

### Duplex Apartment (IFC2x3)

| File | Discipline |
|------|------------|
| `Duplex_A_20110907.ifc` | Architecture |
| `Duplex_MEP_20110907.ifc` | MEP |
| `Duplex_Plumbing_20121113.ifc` | Plumbing |
| `Duplex_Electrical_20121207.ifc` | Electrical |

- [Duplex folder](https://github.com/buildingsmart-community/Community-Sample-Test-Files/tree/main/IFC%202.3.0.1%20(IFC%202x3)/Duplex%20Apartment)
- CC BY 4.0

### Medical-Dental Clinic (IFC2x3)

| File | Discipline |
|------|------------|
| `Clinic_Architectural.ifc` | Architecture |
| `Clinic_Structural.ifc` | Structural |
| `Clinic_HVAC.ifc` | HVAC |
| `Clinic_Plumbing.ifc` | Plumbing |
| `Clinic_Electrical.ifc` | Electrical |

- [Clinic folder](https://github.com/buildingsmart-community/Community-Sample-Test-Files/tree/main/IFC%202.3.0.1%20(IFC%202x3)/Medical-Dental%20Clinic)
- CC BY 4.0

Mark manifest entry `"optional": true` until files are present locally.

---

## Additional open sources (not bundled)

| Resource | Content | Link |
|----------|---------|------|
| **BIMData research set** | 100+ IFC files, ARC/STR/MEP split | [BIMData IFC_FILES.md](https://github.com/bimdata/BIMData-Research-and-Development/blob/master/pages/IFC_FILES.md) |
| **BIMCollab examples** | Federated ARC/STR/MEP/Vent | [Example projects](https://www.bimcollab.com/en/try/) |
| **BIM4LCA (Nordic Innovation)** | Residential + office, multi-discipline IFC | [Download page](https://www.nordicsustainableconstruction.com/knowledge/2024/august/bim4lca-files) (CC BY-SA 4.0) |
| **Open IFC Model Repository** | Many research models | [openifcmodel.cs.auckland.ac.nz](http://openifcmodel.cs.auckland.ac.nz/) |
| **IfcOpenShell test files** | Unit test geometry | [IfcOpenShell/TestFiles](https://github.com/opensourceBIM/TestFiles) |

To add a new set: extend `scenarios/ifc_models/manifest.json` and `scenarios/ifc_clash_scenarios.json`.

---

## IfcPipeline integration

The **PCERT building** files are the same as `shared/examples/Building-*.ifc`. Run clashes via:

- **ifcclash-worker** API (`/ifcclash` job)
- **Prototype:** `./scripts/run_ifc_scenarios.sh`
- **n8n:** upload models → clash job → use report JSON

---

## Scenario catalog

Defined in `scenarios/ifc_clash_scenarios.json` (15 scenarios):

- Building: Arch↔Struct, HVAC↔Struct, selectors (beams/columns), clearance mode
- Infrastructure: plumbing↔road, rail↔road, bridge↔road, etc.
- Cross-set negative: building HVAC vs infra road (expects 0 clashes)

---

## Attribution

When publishing results, cite buildingSMART:

> buildingSMART International (2020+) PCERT Sample Scene / Duplex / Medical-Dental Test Files. CC BY 4.0. https://github.com/buildingSMART/Sample-Test-Files
