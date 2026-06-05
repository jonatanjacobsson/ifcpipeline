# Local mapping tables (not in git)

Project-specific mapping modules for `TranslateKostengruppeToBSABe`,
`TranslateKostengruppeToBSABwr`, and `AssignContractIDFromRules` are **gitignored**
so they are not published.

## Setup on a new machine

1. **ContractID + Baserow DE metadata**

   ```bash
   cd ifcpatch-worker
   # Uses CDE_BASEROW_API_BASE + CDE_BASEROW_API_KEY from ../cde/.env or env
   python3 scripts/generate_nobel_contract_id_from_baserow.py
   ```

   Creates `mappings/nobel_a1_contract_id.py` with `DELENTREPRENADER` (166 rows from
   Baserow table 1182) and keeps existing `CONTRACT_ID_RULES` on regenerate.

2. **Kostengruppe → BSABe**

   Copy from a secure project store or from a colleague:

   ```bash
   cp mappings/nobel_a1_kostengruppe_bsabe.example.py mappings/nobel_a1_kostengruppe_bsabe.py
   # then merge in your full KOSTENGRUPPE_REGISTRY from the project vault
   ```

3. **Kostengruppe → BSABwr** (AMA produktionsresultat, paired with TypeID)

   After `nobel_a1_kostengruppe_bsabe.py` exists:

   ```bash
   python3 scripts/generate_nobel_bsabwr_from_bsabe.py
   python3 scripts/prep_bsab_naviate_reference.py   # refreshes nobel_bsabwr_validation.txt
   ```

   IFC patch recipe: `TranslateKostengruppeToBSABwr` (writes `BIP.BSABwr`).

   The `.example.py` files document structure only; they are safe to commit.

## Standard code references (txt)

Regenerable catalogues for DIN 276 ↔ BSAB/BIP research:

```bash
python3 scripts/export_code_library_reference.py   # DIN 276 + bipkoder JSON
python3 scripts/prep_bsab_naviate_reference.py     # Naviate BSAB 96 HUS txt → tsv/json
```

Place Naviate keynotes in `reference/` (`BSAB 96 Byggdelar HUS.txt`, etc.) — see
[reference/README.md](reference/README.md) for prepared `bsab96_*.tsv`, lookup JSON, and Nobel validation.

## Tracked files

| File | Purpose |
|------|---------|
| `__init__.py` | Package marker |
| `reference/*.txt` | DIN 276, BIP JSON, Naviate BSAB keynotes (source) |
| `reference/bsab96_*.tsv` | Parsed BSAB byggdel / produktionsresultat |
| `reference/bsab96_lookup.json` | BSAB code → title lookup (generated) |
| `*.example.py` | Structure templates |
| `README.md` | This file |
| `../scripts/generate_nobel_contract_id_from_baserow.py` | Baserow → local contract module |
| `../scripts/export_code_library_reference.py` | Refresh reference txt from online catalogues |
