---
name: ifcpatch-property-mapping
description: >-
  Designs and implements ifcpipeline custom IfcPatch property-mapping recipes
  (code + in-code mapping tables + tests + n8n). Use when adding Translate*/Assign*
  recipes, mapping German/source IFC properties to BIP (BSABe, ContractID), ifccsv
  inventory, Baserow delentreprenader, or when the user asks for a reusable ifcpatch
  mapping script like TranslateKostengruppeToBSABe or AssignContractIDFromRules.
---

# IfcPatch property-mapping recipes

Build **code-oriented** custom recipes — not `SetPropertyBySelector` JSON ops — when mappings need tables, research, disambiguation, or external data (Baserow, bipkoder).

## When to use this pattern

| Use custom recipe | Use SetPropertyBySelector |
|-------------------|-------------------------|
| Many source values → lookup table | Single literal or `from` copy |
| Ordered selector rules (first match wins) | ≤5 fixed JSON operations |
| Cross-reference Baserow / typbeteckningar | No external catalogue |
| Gitignored project-specific tables | OK in workflow JSON |

## What to ask the user

Gather before coding:

1. **Source property** — exact pset path (e.g. `BIP.BSABe/Kostengruppe`). Confirm real IFC name via inventory, not assumptions.
2. **Target property(ies)** — e.g. `BIP.BSABe` only, or also `BIP.ContractID`. **One recipe = one target family** (do not mix BSABe + TypeID + SystemCode unless user explicitly asks).
3. **IFC scope** — `IfcElement` only? Filter `BIP-PROCESS.DuplicateOwnedBy` (NULL / empty / `undefined`)?
4. **Models** — which uploads (e.g. `A1_2b_BIM_XXX_0001_00`)? Which workflow chain (n8n workflow id / node insertion point)?
5. **Catalogues** — Baserow table id for DE codes? bipkoder typbeteckningar URL? Publish mappings in git? (default: **no** — gitignore `mappings/<project>_*.py`).
6. **Overwrite** — replace existing target values or skip when already set?

## Data to collect

```text
Discovery checklist:
- [ ] ifccsv export: source + target + GlobalId + Class + DuplicateOwnedBy
- [ ] Unique source values (count + list) per model variant
- [ ] Target property empty vs placeholder ("undefined")
- [ ] Element class distribution for ambiguous prefixes
- [ ] Baserow / bipkoder rows for allowed target codes
- [ ] n8n workflow chain + version pinning pattern
```

### ifccsv inventory (always first)

POST `/ifccsv` or n8n IfcCsv node:

```json
{
  "query": "IfcElement",
  "attributes": "GlobalId,Class,BIP.<SourceProp>,BIP.<TargetProp>,BIP-PROCESS.DuplicateOwnedBy",
  "output_filename": "output/csv/<project>_inventory.csv"
}
```

Or locally with ifcopenshell + `get_psets` on production IFC from MinIO (`uploads/...`).

### Reference catalogues

| Source | Use for |
|--------|---------|
| [typbeteckningar.json](https://storage.googleapis.com/storage.infopack.io/bim-alliance/bipkoder-data/latest/typbeteckningar.json) | BSABe hints (`Bygg` has BSABe; `El` uses TypeID/BSABwr, often empty BSABe) |
| Baserow delentreprenader (table 1182 in Nobel) | `VALID_DE_CODES` + metadata (`namn`, `huvudgrupp`, …) |
| Existing BIP docs / `nobel-project-hub/public/baserow.example.json` | Property definitions, SystemCode examples |

### Research rules

1. **Exact string keys** — map every distinct source string found in IFC, not prefix-only guesses.
2. **Prefix fallbacks** — add `PREFIX_DEFAULTS` + `_PREFIX_IFC_CLASS_BSABE` only for proven ambiguities (e.g. DIN `361` → stairs vs roof).
3. **Cross-discipline** — arch IFC may carry EL cost groups (e.g. DIN 440); decide BSABe (`63` elkraft vs `32.G` yttertak) with user; **do not** silently write extra BIP properties unless requested.
4. **Document unmapped** — explicit `None` in table + log / stats counter.

## Repository layout

```text
ifcpatch-worker/custom_recipes/
├── _property_mapping_utils.py      # shared (gitignored stem _* from recipe_loader)
├── <RecipeName>.py                 # Patcher class (tracked)
├── mappings/
│   ├── README.md
│   ├── <project>_<target>.py        # gitignored — mapping tables + rules
│   └── <project>_<target>.example.py # tracked template
└── scripts/
    └── generate_<project>_from_baserow.py  # optional, tracked

ifcpatch-worker/tests/test_<project>_property_mappings.py
```

Add to **`.gitignore`**:

```gitignore
ifcpatch-worker/custom_recipes/mappings/<project>_*.py
!ifcpatch-worker/custom_recipes/mappings/<project>_*.example.py
```

## Implement mapping module

Structure (see [reference.md](reference.md)):

- **Lookup recipe** — `KOSTENGRUPPE_REGISTRY` / `KOSTENGRUPPE_TO_BSABE`, `resolve_*()`, `iter_*()`, `mapping_audit_table()`.
- **Rules recipe** — `DELENTREPRENADER` dict + `CONTRACT_ID_RULES` list, `validate_*()`, `get_*()` for metadata.
- Import `parse_kostengruppe`, helpers from `_property_mapping_utils`.

Regenerate DE metadata from Baserow:

```bash
cd ifcpatch-worker
python3 scripts/generate_nobel_contract_id_from_baserow.py
```

## Implement Patcher recipe

1. Copy patterns from `TranslateKostengruppeToBSABe.py` or `AssignContractIDFromRules.py`.
2. `__init__(file, logger, mapping_module="...", overwrite="true")` — load `mappings.<module>` via `importlib`.
3. `patch()` — iterate or rule-loop; use `PatchStats`; log summary.
4. **Only write** the agreed target property (e.g. `BIP.BSABe` via `set_pset_property`).
5. `get_output()` → `return self.file`.

Recipe loader picks up any `*.py` in `custom_recipes/` except `_*.py` and `example_recipe.py`.

## Tests

File: `ifcpatch-worker/tests/test_<project>_property_mappings.py`

```python
pytestmark = pytest.mark.skipif(
    not (CUSTOM / "mappings" / "<project>_....py").is_file(),
    reason="Local mapping files missing",
)
```

Cover:

- Parametrize **every** exact source string → expected target
- `VALID_DE_CODES` / registry row count
- `is_not_duplicate_owned` edge cases
- Optional: smoke `Patcher` on trimmed IFC fixture

Run:

```bash
cd ifcpatch-worker && python3 -m pytest tests/test_<project>_property_mappings.py -q
```

Local smoke:

```python
# Add custom_recipes to sys.path; open production IFC; run Patcher; count written
```

## n8n orchestration

1. Pull workflow: `npx n8nac pull <workflowId>` from `/home/bimbot-ubuntu/apps`.
2. Insert **after** model-specific cleanup, **before** generic BIP enrichment.
3. Chain: `inputFile`/`outputFile` = `$json.result.output_key`, `inputVersionId` = `$json.result.version_id`.
4. Node type: `n8n-nodes-ifcpipeline.ifcPatch`, `use_custom: true`, recipe name = class name.
5. Push workflow; rebuild worker: `docker compose build ifcpatch-worker && docker compose up -d ifcpatch-worker`.

## Evaluate before done

| Check | Pass criteria |
|-------|----------------|
| Coverage | 100% of inventory source strings mapped or explicitly `None` |
| DCA subset | If workflow strips elements, mapping table notes `in_dca_chain` |
| No stray writes | Only target property touched |
| Contract IDs | All rule outputs ∈ `VALID_DE_CODES` / Baserow set |
| Stats | Smoke: `unmapped` / `errors` acceptable per user |
| Git | Mapping tables gitignored; examples + recipes tracked |
| Security | `aikido_full_scan` on new Python before commit only (if MCP available; skip if not committing) |

## Anti-patterns

- Using `SetPropertyBySelector` for 30+ heterogeneous mappings
- Committing `mappings/<project>_*.py` with client data
- Writing `TypeID` / `SystemCode` when user asked only for `BSABe`
- Assuming property name (`Kostengruppe6`) without ifccsv proof
- `log_summary(self.logger, "prefix")` — `PatchStats.log_summary` takes only `logger`

## Additional resources

- Code patterns and snippets: [reference.md](reference.md)
- Nobel A1 walkthrough: [examples.md](examples.md)
- Upstream docs: `ifcpatch-worker/custom_recipes/README.md`, `mappings/README.md`
