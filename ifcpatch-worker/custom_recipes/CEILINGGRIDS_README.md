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
args[0]: query (str) - IfcOpenShell selector for the coverings to process
         (default: "IfcCovering", every covering)
args[1]: profile_height (str) - Height of T-profile in mm (default: 40.0)
args[2]: profile_width (str) - Width of profiles in mm (default: 20.0)
args[3]: profile_thickness (str) - Thickness of profiles in mm (default: 5.0)
args[4]: require_interior (str) - "true" or "false"; skip coverings whose FootPrint
         is only an outline, with no lines inside it (default: "true")
args[5]: interior_z_offset (str) - Vertical nudge for T-runners in mm, + is up (default: 0.0)
```

Note the different order from CeilingGridsGlobal: this recipe has no `extract_beams`
or `output_path`, so its dimensions start at args[1]. Footprint reading, perimeter
classification, coverage filtering and profile placement are otherwise identical to
the global recipe - see its section above for what those arguments do.

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
args[0]: query (str) - IfcOpenShell selector for the coverings to process
         (default: "IfcCovering", every covering)
args[1]: extract_beams (str) - "true" or "false" to write a beams-only file (default: "false")
args[2]: profile_height (str) - Height of T-profile in mm (default: 40.0)
args[3]: profile_width (str) - Width of profiles in mm (default: 20.0)
args[4]: profile_thickness (str) - Thickness of profiles in mm (default: 5.0)
args[5]: tolerance (str) - Connection tolerance in mm (default: 50.0)
args[6]: output_path (str) - Path for extracted beams file (optional, auto-generated if not provided)
args[7]: require_interior (str) - "true" or "false"; skip coverings whose FootPrint
         is only an outline, with no lines inside it (default: "true")
args[8]: interior_z_offset (str) - Vertical nudge for T-runners in mm, + is up (default: 0.0)
```

**Lateral placement**: the angle is offset half a profile width towards the ceiling,
so its upstand's back face sits on the FootPrint line and the leg reaches inwards. The
inward direction is found by probing both sides of the segment against the perimeter
loop (even-odd), voting over three points along it, with the footprint centre as
fallback; the beam is then reversed if needed, because the L profile's leg always
extends towards `direction x Z`. Where a covering's perimeter loop is not closed -
the classifier having assigned some boundary lines to the interior - the probe has
nothing solid to test against and the angle can land on the wrong side; measured at
10 of 300 sampled angles on M0042-005-A-40-V-0001_Undertak.

**Vertical placement**: both profiles are positioned by their bounding-box centre.
The perimeter angle's leg sits at -profile_thickness below the FootPrint plane with
its top face flush to it, so the ceiling bears on the leg and a thickness-deep strip
shows from below. T-runners are placed to match: flange underside at
-profile_thickness, bearing face on the FootPrint plane. Use `interior_z_offset` to
raise or lower the runners from there without touching the perimeter.

**On `require_interior`**: a suspended ceiling exports a FootPrint with runner lines
inside its boundary; a fixed plasterboard ceiling exports only the boundary. Without
this filter every plasterboard ceiling gets ringed with an angle profile it does not
have. On `M0042-005-A-40-V-0001_Undertak` the filter drops 709 of 1819 coverings and
3516 stray perimeter beams while leaving all 16154 interior beams untouched.

**On `query` vs `require_interior`**: they answer different questions and are meant
to be combined. The geometry tells you a footprint has lines inside it; only the type
name tells you whether those lines are grid hardware. A suspended, demountable ceiling
("pendlat och demonterbart") hangs on a T-grid; a direct-mounted absorber
("diktmonterad") is fixed straight to the soffit and its interior footprint lines are
panel joints, not runners. On M0042-005-A-40-V-0001_Undertak, `require_interior` alone
builds 1110 ceilings / 21256 beams; adding `IfcCovering, Name=/.*pendlat.*/` leaves 709
ceilings / 14680 beams, dropping 4133 runners on UT21-24 Diktmonterad panels that have
no grid to model. The selector only ever subtracts - `require_interior` still skips the
137 UT35 niches that match the name but carry no grid lines.

The selector grammar has no `!` negation in that position and no `/i` flag, but Python
lookahead works: `Name=/^(?!.*Diktmonterad).*$/` keeps everything except the
direct-mounted types.

### Example Usage
```python
from ifcpatch import execute

# Defaults: beams added to the source model, outline-only coverings skipped
output = execute({
    "input": "input.ifc",
    "recipe": "CeilingGridsGlobal",
    "arguments": []
})

# Beams-only output file (blank query = every covering)
output = execute({
    "input": "input.ifc",
    "recipe": "CeilingGridsGlobal",
    "arguments": ["", "true"]
})

# Suspended ceilings only - the usual production call
output = execute({
    "input": "input.ifc",
    "recipe": "CeilingGridsGlobal",
    "arguments": ["IfcCovering, Name=/.*pendlat.*/"]
})

# Custom dimensions, beams-only, and every covering ringed regardless of grid
output = execute({
    "input": "input.ifc",
    "recipe": "CeilingGridsGlobal",
    "arguments": ["", "true", "50.0", "25.0", "6.0", "5.0", "", "false"]
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

### Version 0.4.0 (2026-09-10)
- Read `IfcIndexedPolyCurve` footprints, not just `IfcPolyline`. Revit 2025 / ODA
  SDAI 24.12 exports use the former, so both recipes previously produced zero beams
  on current models while still reporting success
- Classify a segment as interior when an endpoint lands mid-span on another line,
  not only when 3+ segments share an endpoint. Revit does not split footprint lines
  at intersections, so T-runners were being given angle profiles
- Skip coverings whose footprint has no lines inside it (`require_interior`), and
  accept a selector `query` as args[0], matching the convention in RemoveElements
  and ExtractElementsExcludeSpaces
- Place the T-runner flange flush with the perimeter angle's leg so both carry the
  ceiling at one level, with `interior_z_offset` to tune it
- Offset the angle towards the ceiling interior instead of along world X, reversing
  the beam where needed so its leg reaches inwards and its upstand sits on the line
- Both recipes now produce identical geometry; they differ only in placement
  (absolute + spatial containment vs relative + nesting)

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


