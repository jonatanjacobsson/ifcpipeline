# Floor Plan Export Guide

## Quick Start

Generate floor plans from IFC files using two ready-to-use scripts:

### 1. Standard Floor Plans (with room labels)

```bash
./svg-export-WORKING.sh
```

**Includes:**
- Walls, doors, windows, stairs, railings
- Room names and areas
- Door swing arcs
- All building storeys

**Output:** 2.3MB, 14,835 elements, ~60 seconds

### 2. Simple Floor Plans (clean, no labels)

```bash
./svg-floorplan-simple.sh
```

**Includes:**
- Walls, doors, windows, stairs, railings only
- Door swing arcs
- No room labels (cleaner appearance)

**Output:** Smaller file, faster processing

## Key Configuration

### Critical Parameters for Floor Plans

Both scripts use these essential settings:

```bash
--model                           # Use 3D geometry
--auto-section                    # Create horizontal sections
--section-height-from-storeys     # Cut at each floor level
--include entities IfcWall IfcDoor IfcWindow IfcStair IfcRailing
--door-arcs                       # Show door swings
--bounds 3000x2000               # High-quality canvas
-j 4                             # Use 4 CPU cores
```

### What Makes This Work

1. **`--include entities`** - MUST explicitly specify building elements
   - Default (IfcSpace) produces empty SVGs
   - Include: IfcWall, IfcDoor, IfcWindow, IfcStair, IfcRailing

2. **`--section-height-from-storeys`** - Automatically cuts at each floor level
   - Detects building storeys from IFC
   - Creates proper floor plan sections
   - Alternative: `--section-height 1.5` for manual 1.5m cut

3. **`--auto-section`** - Creates horizontal section planes
   - Essential for converting 3D to 2D
   - Works with storey-based sections

## Customization

### Change Canvas Size

```bash
# Standard quality (faster)
--bounds 2048x1536

# High quality (current)
--bounds 3000x2000

# Print quality (slower, larger files)
--bounds 4096x3072
```

### Include Different Elements

```bash
# Minimal (walls only)
--include entities IfcWall

# Standard (current)
--include entities IfcWall IfcDoor IfcWindow IfcStair IfcRailing

# With furniture
--include entities IfcWall IfcDoor IfcWindow IfcFurnishingElement

# Structural elements
--include entities IfcWall IfcSlab IfcColumn IfcBeam

# Complete architectural
--include entities IfcWall IfcSlab IfcDoor IfcWindow IfcStair IfcRailing IfcColumn
```

### Section Height Options

```bash
# Automatic from storeys (recommended)
--section-height-from-storeys

# Fixed height (1.5m is typical for floor plans)
--section-height 1.5

# Automatic East-West and North-South sections
--auto-section
```

### Add Room Labels

```bash
# Must include IfcSpace entity
--include entities IfcWall IfcDoor IfcWindow IfcSpace
--print-space-names
```

## Performance

| Configuration | Elements | Time (4 CPUs) | File Size |
|--------------|----------|---------------|-----------|
| Simple (walls only) | ~5,000 | 20-30s | ~1MB |
| Standard (no labels) | ~10,000 | 40-50s | ~1.5MB |
| Full (with labels) | ~15,000 | 50-60s | ~2.3MB |

## Output Format

The generated SVG contains:

- **One `<g>` group per building storey** with class `IfcBuildingStorey`
- **Each element as a `<path>`** with:
  - `class="IfcWall"`, `class="IfcDoor"`, etc.
  - `data-name="Element Name"`
  - `data-guid="IFC Global ID"`
- **CSS styles** for consistent rendering
- **Coordinate system** maintains IFC spatial relationships

### Example Structure

```xml
<svg xmlns="http://www.w3.org/2000/svg">
    <style>/* CSS styles */</style>
    
    <g class="IfcBuildingStorey" data-name="Level 1" data-guid="...">
        <g class="IfcWall" data-name="Wall_200mm" data-guid="...">
            <path d="M100,200 L300,200 L300,210 L100,210 Z"/>
        </g>
        <g class="IfcDoor" data-name="Door_900x2100" data-guid="...">
            <path d="M150,200 L150,280"/>
            <path d="M150,200 A80,80 0 0,1 230,200"/> <!-- door arc -->
        </g>
    </g>
    
    <g class="IfcBuildingStorey" data-name="Level 2" data-guid="...">
        <!-- Level 2 elements -->
    </g>
</svg>
```

## Integration with Worker

### API Request Example

```python
{
    "input_filename": "/uploads/building.ifc",
    "output_filename": "/output/floorplan.svg",
    "model": true,
    "auto_section": true,
    "section_height_from_storeys": true,
    "bounds": "3000x2000",
    "include": ["IfcWall", "IfcDoor", "IfcWindow", "IfcStair", "IfcRailing"],
    "include_type": "entities",
    "door_arcs": true,
    "threads": 4
}
```

### With Room Labels

```python
{
    "input_filename": "/uploads/building.ifc",
    "output_filename": "/output/floorplan.svg",
    "model": true,
    "auto_section": true,
    "section_height_from_storeys": true,
    "bounds": "3000x2000",
    "include": ["IfcWall", "IfcDoor", "IfcWindow", "IfcStair", "IfcRailing", "IfcSpace"],
    "include_type": "entities",
    "door_arcs": true,
    "print_space_names": true,
    "print_space_areas": true,
    "threads": 4
}
```

## Troubleshooting

### Empty SVG

**Problem:** SVG file created but no geometry visible

**Solutions:**
1. Ensure `--include entities` is specified with building elements
2. Check that `--model` flag is present
3. Verify input file has the specified entity types

### Missing Storeys

**Problem:** Not all floors appear in output

**Solutions:**
1. Use `--auto-section` instead of `--section-height-from-storeys`
2. Check IFC file has proper `IfcBuildingStorey` hierarchy
3. Try manual section height: `--section-height 1.5`

### Too Many/Few Details

**Problem:** Floor plan too cluttered or too sparse

**Solutions:**
- **Too cluttered:** Remove elements like `IfcSlab`, `IfcFurnishingElement`
- **Too sparse:** Add elements like `IfcColumn`, `IfcFurnishingElement`
- Adjust with `--include entities` list

### Large File Size

**Problem:** SVG file too large (>10MB)

**Solutions:**
1. Reduce canvas size: `--bounds 2048x1536`
2. Include fewer element types
3. Remove `--print-space-names` and `--print-space-areas`
4. Consider exporting single storey only

### Slow Processing

**Problem:** Conversion takes >2 minutes

**Solutions:**
1. Increase CPU cores: `-j 8` (if available)
2. Include fewer element types (walls only)
3. Reduce canvas size
4. Split large buildings by storey or zone

## Best Practices

### For Architectural Floor Plans

```bash
--include entities IfcWall IfcDoor IfcWindow IfcStair IfcRailing IfcSpace
--section-height-from-storeys
--print-space-names
--print-space-areas
--door-arcs
--bounds 3000x2000
```

**Result:** Complete architectural floor plans with room labels

### For Furniture Layout

```bash
--include entities IfcWall IfcDoor IfcWindow IfcFurnishingElement IfcSpace
--section-height-from-storeys
--print-space-names
--door-arcs
--bounds 3000x2000
```

**Result:** Floor plans showing furniture placement

### For Quick Previews

```bash
--include entities IfcWall
--auto-section
--bounds 1024x768
-j 4
```

**Result:** Fast wall-only outline for quick review

### For Print/Presentation

```bash
--include entities IfcWall IfcDoor IfcWindow IfcStair IfcRailing IfcColumn IfcSpace
--section-height-from-storeys
--print-space-names
--print-space-areas
--door-arcs
--bounds 4096x3072
```

**Result:** High-quality detailed floor plans

## Comparison: Floor Plan Export Methods

| Method | Quality | Speed | File Size | Use Case |
|--------|---------|-------|-----------|----------|
| **SVG** | ⭐⭐⭐⭐⭐ | Medium | Small | Floor plans (best option) |
| **DWG/DXF** | ⭐⭐⭐⭐⭐ | Slow | Small | CAD software |
| **PDF** | ⭐⭐⭐⭐ | Fast | Small | Print/sharing |
| **PNG/JPG** | ⭐⭐⭐ | Fast | Medium | Raster images |
| **glTF** | ⭐⭐ | Fast | Large | 3D view (not floor plan) |

**Recommendation:** SVG is the best format for floor plans from IFC.

## Files

- `scripts/processing/svg-export-WORKING.sh` - Standard floor plans with labels
- `scripts/processing/svg-floorplan-simple.sh` - Simple floor plans without labels
- `docs/guides/FLOORPLAN_EXPORT.md` - This guide

## Summary

✅ **Floor plan export is fully working**  
✅ **Two ready-to-use scripts available**  
✅ **Optimized for horizontal sections only**  
✅ **Customizable element selection**  
✅ **Automatic storey detection**  
✅ **Production ready**

The key to successful floor plan generation is **explicitly including building entities** and using **storey-based section heights**.

---

**Last updated:** October 15, 2025  
**IfcConvert version:** 0.8.3-d7cf803  
**Status:** Production ready ✅


