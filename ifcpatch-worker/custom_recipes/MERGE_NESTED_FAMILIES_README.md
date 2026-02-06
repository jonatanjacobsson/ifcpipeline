# MergeNestedFamilies Recipe

Merge nested shared family elements (door handles, frames, hardware) into their parent elements in IFC files exported from Revit.

## Problem Statement

When Revit exports families containing nested shared components (e.g., doors with nested leafs, frames, hardware), the IFC output contains separate disconnected elements with no relationship to their parent. This is a [known issue (Autodesk/revit-ifc#374)](https://github.com/Autodesk/revit-ifc/issues/374) that has been open since 2021.

This recipe provides a **post-processing step** that:
1. Identifies parent elements and their nested children using multiple discovery methods
2. Merges child geometry into the parent element's representation
3. Optionally copies child property sets to the parent
4. Optionally removes the child elements after merging

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `parent_selector` | string | `"IfcDoor"` | IFC selector for parent elements. Supports extended syntax for property filtering. |
| `child_types` | string | `"IfcBuildingElementProxy,IfcDoor"` | Comma-separated list of IFC types to consider as potential children |
| `discovery_methods` | string | `"explicit_parent,guid_prefix,revit_id"` | Comma-separated list of methods to discover children, in priority order |
| `guid_prefix_length` | int | `20` | Number of characters to match for GUID prefix method |
| `revit_id_range` | int | `10` | Maximum Revit Element ID distance for revit_id method |
| `merge_properties` | bool | `True` | Whether to copy child property sets to parent |
| `remove_children` | bool | `True` | Whether to delete children after merging |
| `dry_run` | bool | `False` | If True, only report what would be merged without modifying |

## Discovery Methods

The recipe uses a **best match** approach where each child is assigned to only its closest/best matching parent, preventing the same child from being merged into multiple parents.

### Match Score Priorities

| Method | Points | Description |
|--------|--------|-------------|
| `explicit_parent` | 200 | Definitive match via custom property |
| `guid_prefix` | 100+ | Strong correlation from Revit export |
| `revit_id` | up to 50 | Proximity in Revit element IDs |
| `spatial` | 25 | Same building storey |
| `naming` | 10 | Name pattern matching |

### explicit_parent (Most Reliable)

Checks for a custom shared parameter on child elements that explicitly references the parent's Revit Element ID or IFC GlobalId. This is the **most reliable method** when a pyRevit plugin has been used to populate these properties before IFC export.

**Supported property names** (checked in any property set):
- `NestedParentId` - Integer Revit Element ID of parent
- `IFC_ParentElementId` - Alternative name for parent Element ID
- `NestedParentGuid` - String IFC GlobalId of parent
- `IFC_ParentGlobalId` - Alternative name for parent GlobalId

See [pyRevit Integration](#pyrevit-integration) section for how to set up this workflow.

### guid_prefix (Recommended Fallback)
Matches elements with the same IFC GUID prefix. In Revit exports, nested shared families often share a common GUID prefix with their host.

**Example:**
- Parent door: `3pi9p_npX0XvCuLtNwB$ZG`
- Handle 1: `3pi9p_npX0XvCuLtNwB$ZF`
- Handle 2: `3pi9p_npX0XvCuLtNwB$ZC`

### revit_id
Matches elements with Revit Element IDs within a configurable range of the parent. **Important:** Only children with IDs *higher* than the parent are considered valid matches (nested families are created after their host in Revit).

**Example:**
- Parent door ID: `29176772`
- Handle IDs: `29176775`, `29176776` (within range of 10)

### spatial
Matches elements in the same spatial container (building storey). Use as a **filter** rather than primary method to avoid false positives.

### naming
Matches elements with related family names (e.g., `Door_Handle` contains keyword `Door`).

## Extended Selector Syntax

The recipe supports property filtering with spaces in property names:

```
IfcDoor[Phasing.Phase Created=Etapp 1A]
```

Format: `IFCType[PsetName.PropertyName=Value]`

## Usage Examples

### Basic: Merge door handles into all doors

```python
ifcpatch.execute({
    "input": "model.ifc",
    "file": ifc_file,
    "recipe": "MergeNestedFamilies",
    "arguments": [
        "IfcDoor",                          # parent_selector
        "IfcBuildingElementProxy",          # child_types
        "explicit_parent,guid_prefix,revit_id",  # discovery_methods (default)
        20,                                 # guid_prefix_length
        10,                                 # revit_id_range
        True,                               # merge_properties
        True,                               # remove_children
        False                               # dry_run
    ]
})
```

### With explicit_parent only (most reliable, requires pyRevit setup)

```python
ifcpatch.execute({
    "input": "model.ifc",
    "file": ifc_file,
    "recipe": "MergeNestedFamilies",
    "arguments": [
        "IfcDoor",
        "IfcBuildingElementProxy,IfcDoor",
        "explicit_parent",                  # Only use explicit references
        20,
        10,
        True,
        True,
        False
    ]
})
```

### Phase-filtered: Merge handles for specific construction phase

```python
ifcpatch.execute({
    "input": "model.ifc",
    "file": ifc_file,
    "recipe": "MergeNestedFamilies",
    "arguments": [
        "IfcDoor[Phasing.Phase Created=Etapp 1A]",
        "IfcBuildingElementProxy,IfcDoor",
        "guid_prefix,revit_id",
        20,
        10,
        True,
        True,
        False
    ]
})
```

### Dry run: Preview merges without modifying

```python
ifcpatch.execute({
    "input": "model.ifc",
    "file": ifc_file,
    "recipe": "MergeNestedFamilies",
    "arguments": [
        "IfcDoor",
        "IfcBuildingElementProxy",
        "guid_prefix,revit_id",
        20,
        10,
        False,                              # don't merge properties
        True,
        True                                # DRY RUN - no changes made
    ]
})
```

### Windows: Merge nested window components

```python
ifcpatch.execute({
    "input": "model.ifc",
    "file": ifc_file,
    "recipe": "MergeNestedFamilies",
    "arguments": [
        "IfcWindow",
        "IfcBuildingElementProxy,IfcWindow",
        "guid_prefix,revit_id",
        20,
        15,                                 # wider range for windows
        True,
        True,
        False
    ]
})
```

## Dry Run Output Example

When `dry_run=True`, the recipe outputs a detailed report:

```
============================================================
DRY RUN REPORT - No modifications made
============================================================

PARENT: Door-Double-Sweco-MultiPanel:GPD15:29176772
  GUID: 3pi9p_npX0XvCuLtNwB$ZG
  Revit ID (Tag): 29176772
  IFC Type: IfcDoor
  Would merge 2 children:
    - Door_Handle-Sweco-Standard:Door_Handle-Sweco-Standard:29176775
      GUID: 3pi9p_npX0XvCuLtNwB$ZF
      Revit ID: 29176775
      Type: IfcBuildingElementProxy
      Geometry items: 1
    - Door_Handle-Sweco-Standard:Door_Handle-Sweco-Standard:29176776
      GUID: 3pi9p_npX0XvCuLtNwB$ZC
      Revit ID: 29176776
      Type: IfcBuildingElementProxy
      Geometry items: 1

============================================================
DRY RUN SUMMARY
============================================================
Parents that would be modified: 1
Children that would be merged: 2
Children that would be removed: 2
```

## Best Practices

1. **Always run a dry run first** to verify the matches are correct before modifying the model.

2. **Implement pyRevit plugin for `explicit_parent`** - This is the most reliable method and eliminates false positives from heuristic matching.

3. **Use `explicit_parent,guid_prefix,revit_id` combination** (default) for best results. The explicit_parent method takes precedence when properties are available, with fallback to heuristic methods.

4. **Avoid spatial method as primary** - it matches all elements in the same building storey, causing many false positives.

5. **Adjust `revit_id_range`** based on your model's complexity. Nested families typically have IDs within 5-10 of their parent.

6. **Filter by phase or other properties** when processing large models to limit scope.

7. **Remember: children have higher Revit IDs than parents** - This is enforced by the `revit_id` method to prevent false matches.

## pyRevit Integration

For the most reliable parent-child matching, you can create a pyRevit plugin that populates a custom shared parameter on nested family instances before IFC export. This enables the `explicit_parent` discovery method.

### Recommended Shared Parameter Setup

1. **Create a Shared Parameter** in your Revit Shared Parameter file:
   - Name: `NestedParentId`
   - Type: `Integer`
   - Group: `IFC Parameters` (or your preferred group)

2. **Add to Nested Family Categories** (e.g., Generic Models, Specialty Equipment)

3. **pyRevit Plugin Logic** (pseudo-code):

```python
# pyRevit script to populate NestedParentId on nested shared families
from Autodesk.Revit.DB import *

doc = __revit__.ActiveUIDocument.Document
t = Transaction(doc, "Set Nested Parent IDs")
t.Start()

# Get all family instances
collector = FilteredElementCollector(doc).OfClass(FamilyInstance)

for fi in collector:
    # Check if this instance is nested inside another family
    super_component = fi.SuperComponent
    if super_component and isinstance(super_component, FamilyInstance):
        # Get the parent's Element ID
        parent_id = super_component.Id.IntegerValue
        
        # Set the shared parameter on the nested instance
        param = fi.LookupParameter("NestedParentId")
        if param and not param.IsReadOnly:
            param.Set(parent_id)

t.Commit()
```

### Alternative: Use GlobalId Reference

If you prefer to use IFC GlobalIds instead of Revit Element IDs:

1. **Create Shared Parameter**: `NestedParentGuid` (Type: `Text`)
2. **Note**: You may need to run a pre-export step to calculate expected IFC GlobalIds, or use the Revit `UniqueId` and transform it.

### Why Not Use Existing `Host Id`?

The built-in `Host Id` property on doors/windows references the **wall host**, not the parent family. For example:
- Parent door's `Host Id` → Wall's Element ID
- Nested handle → No `Host Id` property (only text "Host" referencing wall name)

This is why a custom `NestedParentId` parameter is necessary for reliable explicit matching.

## Known Limitations

1. **False positives with coincidental GUID prefixes**: Some unrelated elements may share GUID prefixes by coincidence. Use dry run to verify.

2. **Geometry transformation not applied**: Child geometry is merged without coordinate transformation. This works for nested families but may cause issues if elements are in different locations.

3. **No undo**: Once changes are made, they cannot be reverted. Always keep a backup of the original IFC file.

## Related Issues

- [Autodesk/revit-ifc#374](https://github.com/Autodesk/revit-ifc/issues/374) - Decomposition relationship between nested shared family and its parent
