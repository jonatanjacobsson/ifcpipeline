# Example — Nobel Center A1 (reference implementation)

## Goal

- **Recipe 1:** `BIP.BSABe/Kostengruppe` (DIN 276 German) → `BIP.BSABe` (Swedish)
- **Recipe 2:** IFC selectors + Kostengruppe prefixes → `BIP.ContractID` (`DExxx` from Baserow)

## Discovery results

| Model | Unique Kostengruppe values |
|-------|---------------------------|
| `A1_2b_BIM_XXX_0001_00` (full) | 35 |
| After structural removal / `0002_00` | 27 |
| `0003_00` (rooms) | 0 |

Source property confirmed: **`BIP.BSABe/Kostengruppe`** (not `Kostengruppe6`).

`DuplicateOwnedBy`: NULL on openings; `"undefined"` on elements — treat both as “not duplicate” for ContractID rules.

## Mapping decisions

| Source | BSABe | ContractID | Notes |
|--------|-------|------------|-------|
| `342 Innenwände nicht tragend.ARC` | `43.CB` | DE306 | Largest count |
| `337 Fassade.ARC` | `42.B` | DE114 | |
| `440 Elektro PV` | `63` | DE213 | IfcCovering PV panels; DIN 440 = electrical |

**440:** User chose BSABe `63` (elkraft). Recipe writes **only** `BSABe`, not TypeID/SystemCode.

## Files created

| File | Git |
|------|-----|
| `TranslateKostengruppeToBSABe.py` | tracked |
| `AssignContractIDFromRules.py` | tracked |
| `_property_mapping_utils.py` | tracked |
| `mappings/nobel_a1_kostengruppe_bsabe.py` | **ignored** |
| `mappings/nobel_a1_contract_id.py` | **ignored** (166 Baserow rows + rules) |
| `mappings/*.example.py` | tracked |
| `scripts/generate_nobel_contract_id_from_baserow.py` | tracked |

## n8n

Workflow `lDABv1gGuH02O2sN` (Nobel Center DCA Pipeline), Chain A:

```text
Remove Brick Elements
  → Translate Kostengruppe to BSABe
  → Assign ContractID from DE rules
  → Move BaseQuantities to BIP
  → …
```

## Evaluation (smoke)

- ~2808 BSABe writes on v43 architectural model
- ~2701 ContractIDs set (8 DE codes dominant)
- 6× `440 Elektro PV` → BSABe `63`
- pytest: 51 passed (skip if local mappings missing)

## Lessons for next project

1. Run inventory on **every** model variant in the pipeline fork.
2. Separate **lookup** vs **rules** recipes — different mapping shapes.
3. Gitignore client tables; commit examples + generator scripts.
4. Ask explicitly which **single** BIP target property each recipe writes.
