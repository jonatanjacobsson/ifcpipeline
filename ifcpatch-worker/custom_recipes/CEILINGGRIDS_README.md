# Ceiling Grid Generator Recipes

This document describes the two ceiling grid generator recipes available in the IFC Pipeline.

## Overview

Both recipes generate IFC beam elements from ceiling element footprints (exported from Revit 2025 as IfcCovering elements with FootPrint curve representations). They create:
- **L-profile beams** for perimeter segments (ceiling angle profiles)
- **T-profile beams** for interior segments (ceiling T-runners)

The key difference is in **coordinate placement** and **hierarchy**.

---

## Recipe Comparison

| Feature | CeilingGridsNested | CeilingGridsGlobal |
|---------|-------------------|-------------------|
| **Recipe Name** | `CeilingGridsNested` | `CeilingGridsGlobal` |
| **Coordinate System** | Local/Relative to parent IfcCovering | Global/World coordinates (absolute) |
| **Placement** | `PlacementRelTo = parent.ObjectPlacement` | `PlacementRelTo = None` |
| **Hierarchy** | Beams nested within parent IfcCovering | Beams assigned to BuildingStorey/Building |
| **Dependency** | Beams depend on parent element | Beams independent |
| **Output Options** | Original model + nested beams | Original model + beams, OR beams-only file |
| **Use Case** | Maintain logical grouping with ceilings | Create independent beams, extract separately |

---

## 1. CeilingGridsNested

### Description
Creates ceiling grid beams with **nested/local placement** within parent IfcCovering elements. Beams are positioned relative to their parent and maintain hierarchical dependency.

### Coordinate System
- **Local placement** relative to parent IfcCovering
- Coordinates are in parent's local coordinate system
- Beams transform with parent element

### Hierarchy
- Beams are **nested** within parent IfcCovering using `ifcopenshell.api.nest.assign_object()`
- Creates parent-child relationship
- Deleting parent will affect nested beams

### When to Use
- ✅ Maintain logical grouping between ceilings and their grids
- ✅ Beams should move/transform with parent ceiling
- ✅ Preserve hierarchical relationships
- ✅ Standard IFC workflow with nested elements

### Parameters
```python
args[0]: profile_height (str) - Height of T-profile in mm (default: 40.0)
args[1]: profile_width (str) - Width of profiles in mm (default: 20.0)
args[2]: profile_thickness (str) - Thickness of profiles in mm (default: 5.0)
args[3]: tolerance (str) - Connection tolerance in mm (default: 50.0)
```

### Example Usage
```python
from ifcpatch import execute

# Default parameters
output = execute({
    "input": "input.ifc",
    "recipe": "CeilingGridsNested",
    "arguments": []
})

# Custom dimensions
output = execute({
    "input": "input.ifc",
    "recipe": "CeilingGridsNested",
    "arguments": ["50.0", "25.0", "6.0", "5.0"]
})
```

### Output
- Original IFC file with beams nested within IfcCovering elements
- Beams maintain dependency on parent elements

---

## 2. CeilingGridsGlobal

### Description
Creates ceiling grid beams with **global/absolute placement** in world coordinates. Beams are independent of parent IfcCovering elements and assigned directly to spatial containers.

### Coordinate System
- **Global/world coordinates** (absolute placement)
- `PlacementRelTo = None` (no parent transformation)
- Coordinates transformed from local to global using transformation matrices
- Uses `ifcopenshell.util.placement.get_local_placement()` for accurate transformation

### Hierarchy
- Beams assigned to **spatial container** (BuildingStorey or Building)
- Uses `ifcopenshell.api.spatial.assign_container()`
- No nesting relationship with IfcCovering
- Beams are independent entities

### When to Use
- ✅ Create beams independent of parent ceiling elements
- ✅ Delete covering elements while preserving beams
- ✅ Export beams separately for analysis/coordination
- ✅ Avoid nested hierarchy complexities
- ✅ Need beams-only file for specific workflows

### Parameters
```python
args[0]: profile_height (str) - Height of T-profile in mm (default: 40.0)
args[1]: profile_width (str) - Width of profiles in mm (default: 20.0)
args[2]: profile_thickness (str) - Thickness of profiles in mm (default: 5.0)
args[3]: tolerance (str) - Connection tolerance in mm (default: 50.0)
args[4]: extract_beams (str) - "true" or "false" to extract beams to separate file (default: "false")
args[5]: output_path (str) - Path for extracted beams file (optional, auto-generated if not provided)
```

### Example Usage
```python
from ifcpatch import execute

# Default parameters, no extraction
output = execute({
    "input": "input.ifc",
    "recipe": "CeilingGridsGlobal",
    "arguments": []
})

# Custom dimensions with beam extraction
output = execute({
    "input": "input.ifc",
    "recipe": "CeilingGridsGlobal",
    "arguments": ["50.0", "25.0", "6.0", "5.0", "true", "/path/to/beams_only.ifc"]
})

# Extract to auto-generated path
output = execute({
    "input": "input.ifc",
    "recipe": "CeilingGridsGlobal",
    "arguments": ["40.0", "20.0", "5.0", "50.0", "true"]
})
```

### Output Options
1. **Standard Output**: Original IFC file with beams in global coordinates
2. **Extracted Output** (if `extract_beams="true"`): 
   - Original file with beams
   - PLUS separate file containing ONLY beams with proper spatial hierarchy

---

## Technical Details

### Perimeter vs Interior Detection
Both recipes use the same connectivity analysis algorithm:
- **Perimeter segments**: At least one endpoint has ≤2 connections
- **Interior segments**: Both endpoints have 3+ connections

### Profile Types
- **Perimeter beams**: `IfcLShapeProfileDef` (L-profile)
  - Depth: `profile_width`
  - Thickness: `profile_thickness`
  
- **Interior beams**: `IfcTShapeProfileDef` (T-profile)
  - Depth: `profile_height`
  - FlangeWidth: `profile_width`
  - WebThickness: `profile_thickness`
  - FlangeThickness: `profile_thickness`

### Beam Properties
Both recipes add:
- **Axis representation**: Centerline polyline
- **Body representation**: Extruded solid with profile
- **Style**: Black color
- **QTO properties**: Length, Height, Width

### Coordinate Transformation (Global Recipe Only)
The global recipe transforms coordinates using:
1. Get parent covering element's global transformation matrix
2. Transform start point: `global_point = matrix × local_point`
3. Transform direction vector: `global_direction = matrix × local_direction` (rotation only)
4. Normalize direction vector
5. Create beam with absolute placement

---

## Migration Guide

### From Nested to Global
If you want to convert from nested to global placement:
1. Use `CeilingGridsGlobal` recipe on original file
2. Optionally extract beams to separate file
3. Original covering elements can be safely deleted if not needed

### From Global to Nested
Not recommended. Global beams are independent and converting back to nested would require:
1. Re-establishing parent relationships
2. Converting coordinates from global to local
3. Re-creating nesting relationships

---

## Version History

### Version 0.2.0 (2025-01-01)
- Split into two recipes: `CeilingGridsNested` and `CeilingGridsGlobal`
- Added global coordinate transformation support
- Added spatial container assignment
- Added beam extraction feature
- Improved documentation

### Version 0.1.0 (2025-01-01)
- Initial release as `CeilingGrids`
- Nested placement only

---

## Support

For issues or questions:
- Check logs for detailed error messages
- Verify input IFC file has IfcCovering elements with FootPrint representations
- Ensure covering elements have valid ObjectPlacement

## See Also
- Original implementation: `ceilinggridgenerator_global.py` (standalone script)
- Related recipes: IFC patch recipes for element manipulation


