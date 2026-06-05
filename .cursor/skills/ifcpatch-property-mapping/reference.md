# Reference — IfcPatch property mapping

## `_property_mapping_utils.py`

Shared helpers (module name starts with `_` — not a recipe):

| Function | Purpose |
|----------|---------|
| `parse_property_path("BIP.BSABe")` | → `(pset, prop)` |
| `get_pset_property(el, pset, prop)` | Read; treats `undefined` as empty |
| `is_not_duplicate_owned(el)` | True if `DuplicateOwnedBy` null/empty/`undefined` |
| `parse_kostengruppe(raw)` | `{prefix, suffix, raw}` — 3-digit DIN prefix |
| `set_pset_property(file, el, pset, prop, value, ...)` | Create/merge `IfcPropertySingleValue` |
| `PatchStats` | `matched`, `written`, `skipped`, `unmapped`, `errors` |

## Lookup mapping module template

```python
@dataclass(frozen=True, slots=True)
class SourceMapping:
    raw: str              # exact IFC string
    target: str | None    # e.g. BSABe code
    din_prefix: str
    contract_id_hint: str = ""
    in_dca_chain: bool = True
    notes: str = ""

KOSTENGRUPPE_REGISTRY: tuple[SourceMapping, ...] = (...)
KOSTENGRUPPE_TO_BSABE: dict[str, str | None] = {row.raw: row.target for row in KOSTENGRUPPE_REGISTRY}

def resolve_target(raw: str, ifc_class: str | None = None) -> str | None:
    if raw in KOSTENGRUPPE_TO_BSABE:
        return KOSTENGRUPPE_TO_BSABE[raw]
    # prefix + ifc_class hints, then PREFIX_DEFAULTS
```

## Rules mapping module template

```python
DELENTREPRENADER: dict[str, dict[str, Any]] = {
    "DE306": {
        "namn": "Mellanväggar",
        "nummer": "306",
        "huvudgrupp": "3. MINDRE BYGGENTREPRENADER",
        "baserow_row_id": 42,
        ...
    },
}
VALID_DE_CODES = frozenset(DELENTREPRENADER.keys())

_KG = 'BIP."BSABe/Kostengruppe"'  # quoted when property name has /

CONTRACT_ID_RULES: list[dict] = [
    {"selector": "IfcDoor", "contract_id": "DE315", "require_not_duplicate": True},
    {"selector": f'IfcWall, {_KG}*=342', "contract_id": "DE306", "require_not_duplicate": True},
]

def validate_contract_id(code: str) -> bool:
    return bool(code and code.strip() in DELENTREPRENADER)
```

**Rule order matters** — first match wins; put specific selectors before broad `IfcElement` prefix rules.

## Patcher skeleton (lookup)

```python
class Patcher:
    def __init__(self, file, logger, mapping_module="project_kostengruppe_bsabe", overwrite="true", dry_run="false"):
        self._mapping = importlib.import_module(f"mappings.{mapping_module}")
        self.stats = PatchStats()

    def patch(self):
        for element in self.file.by_type("IfcElement"):
            raw = get_pset_property(element, "BIP", "BSABe/Kostengruppe")
            if not raw:
                continue
            target = self._mapping.resolve_bsabe(raw, element.is_a())
            if target is None:
                self.stats.unmapped += 1
                continue
            set_pset_property(self.file, element, "BIP", "BSABe", target, ...)
```

## Patcher skeleton (rules)

```python
def patch(self):
    assigned: set[str] = set()
    for rule in self._mapping.CONTRACT_ID_RULES:
        for element in filter_elements(self.file, rule["selector"]):
            if element.GlobalId in assigned:
                continue
            if rule.get("require_not_duplicate") and not is_not_duplicate_owned(element):
                continue
            set_pset_property(self.file, element, "BIP", "ContractID", rule["contract_id"], ...)
            assigned.add(element.GlobalId)
```

## Baserow generator script

- Path: `ifcpatch-worker/scripts/generate_*_from_baserow.py`
- Env: `CDE_BASEROW_API_BASE`, `CDE_BASEROW_API_KEY`
- Preserve `CONTRACT_ID_RULES` block via regex when regenerating
- Output: gitignored `mappings/<project>_contract_id.py`

## Docker / worker

Recipes copied at image build:

```bash
cd /home/bimbot-ubuntu/apps/ifcpipeline
docker compose build ifcpatch-worker && docker compose up -d ifcpatch-worker
```

`restart` alone does not pick up new `custom_recipes/` — must **build**.

## ifcOpenShell selector notes

- Property with `/`: `BIP."BSABe/Kostengruppe"*=342`
- TypeID prefix: `BIP.TypeID*=OW`
- Test selectors on real model before encoding all rules

## BIP property semantics (quick)

| Property | Typical use |
|----------|-------------|
| `BSABe` | BSAB 96 **byggdel** (27.x, 32.x, 42.x, 63, …) |
| `BSABwr` | Produktionsresultat (AMA) — common on El types |
| `TypeID` | BIP typbeteckning (e.g. `GC1xx`) |
| `SystemCode` | Undersystem (EL=61, 63B) |
| `ContractID` | Delentreprenad `DExxx` |

El typbeteckningar in bipkoder often have **empty BSABe** — do not expect `GC1xx` → BSABe in JSON.
