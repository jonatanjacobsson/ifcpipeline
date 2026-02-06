# IFC Element Coloring Performance Research

**Date**: 2026-01-16 (Updated)  
**Context**: Optimizing `SetColorBySelector` recipe performance and fixing color conflicts

## Summary

Achieved **13.8x speedup** (125s → 9s) for coloring 9,403 IFC elements by switching from manual `IfcStyledItem` creation to IfcOpenShell's bulk styling API with MappingSource deduplication.

**Update**: Fixed critical bug where shared geometry Items caused incorrect color assignment. Implemented Item-level conflict detection and resolution.

## Benchmark Results

| Version | Approach | Patch Time | Speedup |
|---------|----------|------------|---------|
| V1 | Manual IfcStyledItem per geometry item | 125.62s | baseline |
| V2 | V1 + caching optimizations | 121.94s | 1.03x |
| **V3** | **Bulk API + MappingSource dedup** | **9.13s** | **13.8x** |
| **V3.1** | **V3 + Item-level conflict resolution** | **9.40s** | **13.4x** |

Test file: `V--57_V01000R.ifc` (~63MB, 36,810 elements, 9,403 colored)

## Key Findings

### 1. Use `assign_representation_styles()` API (10x faster)

**Slow approach** (V1):
```python
# Manual IfcStyledItem creation per geometry item
for geometry_item in all_geometry_items:
    self.file.create_entity(
        "IfcStyledItem",
        Item=geometry_item,
        Styles=[style],
        Name=style.Name
    )
```

**Fast approach** (V3):
```python
# Bulk API - handles multiple items in one call
ifcopenshell.api.style.assign_representation_styles(
    self.file,
    shape_representation=rep,
    styles=[style],
    replace_previous_same_type_style=True,
    should_use_presentation_style_assignment=(self.file.schema == "IFC2X3")
)
```

The bulk API:
- Handles style assignment internally with optimized loops
- Manages existing style replacement efficiently
- Reduces Python overhead from multiple `create_entity` calls

### 2. MappingSource Deduplication (additional 25% faster)

Many IFC elements share geometry via `IfcMappedItem` → `IfcRepresentationMap` → `MappedRepresentation`.

**Before deduplication**: Style 9,403 representations (one per element)  
**After deduplication**: Style 1,371 unique MappingSources (85% reduction)

```python
def _get_mapping_sources_for_elements(self, elements):
    """Get unique MappingSources instead of per-element representations."""
    mapping_source_to_elements = defaultdict(list)
    
    for elem in elements:
        for rep in elem.Representation.Representations:
            for item in rep.Items:
                if item.is_a('IfcMappedItem'):
                    # Key insight: style the shared MappingSource once
                    mapping_source = item.MappingSource
                    mapping_source_to_elements[mapping_source].append(elem)
    
    return mapping_source_to_elements
```

### 3. Caching Optimizations (minimal impact)

Tested but had minimal impact (~3%):
- Geometry items cache by representation ID
- Python `id()` vs IFC `entity.id()` for cache keys
- Batch styled item removal

These optimizations are overshadowed by the bulk API approach.

## Alternative Approaches Researched (Not Implemented)

### Material-Based Styling
If elements share materials, use `assign_material_style()`:
```python
ifcopenshell.api.style.assign_material_style(
    file, material, style, context
)
```
- Styles once per material, propagates to all elements
- Not applicable when coloring by property filters (elements may not share materials)

### Parallelization
- IfcOpenShell is not fully thread-safe for write operations
- Geometry iterator in multi-threaded mode causes high memory usage
- Not recommended for style assignment

## IFC Schema Considerations

### IFC2X3 vs IFC4+
- IFC2X3 requires `IfcPresentationStyleAssignment` wrapper
- IFC4+ can directly assign `IfcSurfaceStyle` to `IfcStyledItem`
- Use `should_use_presentation_style_assignment=True` for IFC2X3

### Transparency Support
- Both schemas support transparency via `IfcSurfaceStyleRendering`
- Use `IfcSurfaceStyleShading` (no transparency attribute) when not needed

## Implementation Details

### Files Modified
- `/apps/ifcpipeline/ifcpatch-worker/custom_recipes/SetColorBySelector.py` - Production recipe (V3)
- `/apps/ifcpipeline/ifcpatch-worker/scripts/test_set_color_by_selector_v3.py` - Test script

### Key Methods
1. `_get_mapping_sources_for_elements()` - Deduplicate by MappingSource
2. `_style_mapping_source()` - Style shared geometry definition
3. `_style_representation()` - Style direct (non-mapped) representations

## Critical Bug Fix: Shared Item Color Conflicts (V3.1)

### The Problem

After implementing V3 (MappingSource deduplication), some elements received incorrect colors. For example, element `2V23ck7$n9muA2I6X870ud` matched the F selector (should be GREEN) but displayed YELLOW (U selector color).

### Root Cause Analysis

The issue was **Item-level sharing across MappingSources**:

1. **MappingSource deduplication** identifies unique `IfcRepresentationMap` entities
2. **But** different MappingSources can share the same geometry **Items** (e.g., `IfcShellBasedSurfaceModel`)
3. When we style a MappedRepresentation, we style its Items
4. If Item `#1426` is shared by 4 different representations with 4 different selectors:
   - A selector styles it BLUE
   - F selector styles it GREEN (overwrites BLUE)
   - T selector styles it BLUE (overwrites GREEN)
   - U selector styles it YELLOW (overwrites BLUE) ← **Last one wins!**

### Investigation Process

```python
# Item 1426 was shared across 4 representations:
Rep 1427   → MappingSource 1429  → A selector (156 elements)
Rep 889666 → MappingSource 889667 → F selector (857 elements) ← Our target
Rep 889668 → MappingSource 889669 → T selector (1505 elements)
Rep 889670 → MappingSource 889671 → U selector (1 element)

# All share Item 1426, so the last styling operation (U) wins
```

### The Solution

Implemented **two-level conflict detection**:

1. **MappingSource-level conflicts**: Same MappingSource requested by multiple operations
2. **Item-level conflicts** (NEW): Same Item shared by MappingSources with different styles

```python
def _resolve_item_conflicts(self, mapping_source_to_operation):
    """
    Find Items shared across MappingSources with different styles,
    and duplicate them so each representation can be styled independently.
    """
    # Build map: Item ID -> list of (MappingSource, style)
    item_to_styles = defaultdict(list)
    
    for ms, (op_idx, style, elems) in mapping_source_to_operation.items():
        mapped_rep = ms.MappedRepresentation
        for item in mapped_rep.Items:
            item_to_styles[item.id()].append((ms, style, item))
    
    # Find Items with multiple different styles
    for item_id, styles_list in item_to_styles.items():
        if len(styles_list) <= 1:
            continue
        
        style_ids = set(s.id() for (_, s, _) in styles_list)
        if len(style_ids) > 1:
            # Conflict! Duplicate Item for all but the first MappingSource
            for ms, style, item in styles_list[1:]:
                self._duplicate_item_in_representation(ms.MappedRepresentation, item)
```

### Item Duplication Strategy

When duplicating an Item (e.g., `IfcShellBasedSurfaceModel`):

```python
def _deep_copy_geometry_item(self, item):
    """Create a new Item that references the same underlying geometry."""
    if item.is_a("IfcShellBasedSurfaceModel"):
        return self.file.create_entity(
            "IfcShellBasedSurfaceModel",
            SbsmBoundary=tuple(item.SbsmBoundary)  # Same shells, new SBSM
        )
    # ... handle other geometry types
```

The new Item:
- Has a unique ID
- References the same underlying shells/geometry (no data duplication)
- Has NO `StyledByItem` relationship initially
- Can be styled independently

### Results

```
WARNING: Detected 189 Item(s) shared across MappingSources with different styles
INFO: Resolved 196 conflict(s)
INFO: SetColorBySelector completed: 9123 elements colored, 1524 MappingSources styled
```

Before fix:
```
Element 2V23ck7$n9muA2I6X870ud:
  Item: 1426 (IfcShellBasedSurfaceModel) ← Shared
  Color: #ffff65 (YELLOW) ← WRONG!
```

After fix:
```
Element 2V23ck7$n9muA2I6X870ud:
  Item: 894719 (IfcShellBasedSurfaceModel) ← Duplicated
  Color: #00a853 (GREEN) ← CORRECT!
```

### Key Learnings

1. **MappingSource != isolated styling scope**: Even with unique MappingSources, Items can be shared
2. **IfcStyledItem.Item is the key**: Styling targets Items, not representations
3. **Last operation wins**: Without duplication, the last styling operation overwrites all others
4. **Item duplication is cheap**: Creating a new `IfcShellBasedSurfaceModel` that references existing shells adds minimal file size
5. **Conflict detection order matters**: Sort by operation index so earlier operations keep original Items

### IFC Entity Hierarchy (for styling)

```
IfcProduct (element)
  └── IfcProductDefinitionShape
        └── IfcShapeRepresentation
              └── IfcMappedItem
                    └── IfcRepresentationMap (MappingSource)
                          └── MappedRepresentation (IfcShapeRepresentation)
                                └── Items (e.g., IfcShellBasedSurfaceModel)  ← STYLED HERE
                                      └── StyledByItem → IfcStyledItem
                                            └── Styles → IfcSurfaceStyle
```

## References

- IfcOpenShell API: `ifcopenshell.api.style.assign_representation_styles`
- IfcOpenShell API: `ifcopenshell.api.style.assign_material_style`
- GitHub Issue: Performance regressions in IfcOpenShell 0.8.x vs 0.7.x
- IFC Schema: IfcStyledItem, IfcMappedItem, IfcRepresentationMap, IfcShellBasedSurfaceModel
