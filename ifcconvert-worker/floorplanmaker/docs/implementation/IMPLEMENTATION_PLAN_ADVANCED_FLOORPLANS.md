# Implementation Plan: Advanced Floor Plan Generation

## Overview
Enhance the floor plan generation system with:
1. **Configurable section heights** per discipline/model
2. **Multiple view directions** (plan view vs. reflected ceiling plan)
3. **Coordinated multi-discipline views** (all systems in one drawing)

## Current Status ✓
- ✅ 8 Architectural floor plans (with spaces)
- ✅ 32 MEP + Structural floor plans (4 disciplines × 8 levels)
- ✅ Docker worker upgraded to 8 CPUs
- ✅ Scripts updated to use `-j 8`
- ✅ Coordinate system alignment solved

## Architecture Changes

### 1. Enhanced Configuration Schema (floorplan-config.yaml)

```yaml
project:
  name: "2b BIM Project"
  code: "XXX"
  scale: "1:50"
  output_dir: "/output/converted/floorplans"

models:
  architecture:
    file: "/uploads/A1_2b_BIM_XXX_0001_00.v24.0.ifc"
    description: "Architecture Model"
    elements: [IfcWall, IfcDoor, IfcWindow, IfcStair, IfcRailing, ...]
    
    # NEW: Section height configuration
    section_heights:
      default_offset: 1.2        # meters above storey elevation
      ceiling_offset: 2.8        # meters for reflected ceiling plans
      view_direction: "down"     # "down" (normal) or "up" (RCP)
  
  spaces:
    file: "/uploads/A1_2b_BIM_XXX_0003_00.ifc"
    description: "Space Definitions"
    elements: [IfcSpace]
    print_names: true
    print_areas: true
    section_heights:
      default_offset: 1.2
      view_direction: "down"
  
  electrical:
    file: "/uploads/E1_2b_BIM_XXX_600_00.v183.0.ifc"
    description: "Electrical Model (E1)"
    elements: [IfcFlowSegment, IfcFlowFitting, ...]
    
    # NEW: Multiple section heights for different views
    section_heights:
      default_offset: 1.2        # Floor-level devices (outlets, panels)
      ceiling_offset: 2.8        # Ceiling-level devices (lights, cable trays)
      view_direction: "down"     # Can be "up" for RCP
      
      # NEW: Multi-height capture
      capture_range:
        enabled: true
        min_height: 0.0          # meters above storey
        max_height: 3.5          # meters above storey
        # This will capture ALL electrical elements between floor and ceiling
  
  mechanical:
    file: "/uploads/M1_2b_BIM_XXX_5700_00.v12.0.ifc"
    description: "Mechanical/HVAC Model (M1)"
    elements: [IfcFlowSegment, IfcFlowFitting, ...]
    section_heights:
      default_offset: 2.8        # Most ducts at ceiling level
      ceiling_offset: 2.8
      view_direction: "up"       # RCP view to see underside of ducts
      capture_range:
        enabled: true
        min_height: 2.0
        max_height: 3.5

view_templates:
  # Existing templates...
  
  # NEW: Reflected Ceiling Plans
  electrical_rcp:
    name: "Electrical Reflected Ceiling Plans"
    description: "Ceiling-level electrical with architectural context"
    view_direction: "up"         # Look UP at ceiling
    section_offset: 2.8          # Cut at ceiling height
    layers:
      - model: "architecture"
        opacity: 0.2
        section_offset: 1.2      # Floor-level context
      - model: "electrical"
        opacity: 1.0
        section_offset: 2.8      # Ceiling-level main
    output_prefix: "elec_rcp"
  
  mechanical_rcp:
    name: "Mechanical Reflected Ceiling Plans"
    description: "HVAC ducts and equipment at ceiling"
    view_direction: "up"
    section_offset: 2.8
    layers:
      - model: "architecture"
        opacity: 0.2
      - model: "mechanical"
        opacity: 1.0
        section_offset: 2.8
    output_prefix: "mech_rcp"
  
  # NEW: Coordinated multi-discipline view
  coordinated_all:
    name: "Coordinated Floor Plans - All Disciplines"
    description: "Architecture + Structural + MEP in one view"
    layers:
      - model: "architecture"
        opacity: 0.4
        stroke_color: "#222222"
        section_offset: 1.2
      - model: "spaces"
        opacity: 0.3
        section_offset: 1.2
      - model: "structural"
        opacity: 0.8
        stroke_color: "#0066CC"
        section_offset: 1.2
      - model: "mechanical"
        opacity: 0.9
        stroke_color: "#00CC66"
        section_offset: 2.8      # Different height!
      - model: "electrical"
        opacity: 0.9
        stroke_color: "#FF6600"
        section_offset: 2.8
      - model: "plumbing"
        opacity: 0.9
        stroke_color: "#0099CC"
        section_offset: 1.5
    output_prefix: "coord_all"
    css:
      architectural_opacity: "0.4"
      structural_stroke: "#0066CC"
      mechanical_stroke: "#00CC66"
      electrical_stroke: "#FF6600"
      plumbing_stroke: "#0099CC"
```

### 2. Script Enhancements

#### A. New Script: `generate-coordinated-floorplan.sh`
```bash
#!/bin/bash
# Generate coordinated multi-discipline floor plans

STOREY_NAME="$1"
STOREY_ELEVATION="$2"

# Parse config to get all layers for "coordinated_all" template
# For each layer:
#   1. Calculate section height = STOREY_ELEVATION + layer.section_offset
#   2. Export layer with IfcConvert
#   3. Scale coordinates 20x (1:50)
# 
# Combine all layers with different opacities and colors
# Output single coordinated SVG
```

#### B. Enhanced: `generate-mep-floorplan.sh`
```bash
# Add support for:
# - Custom section offset per layer (not just storey elevation)
# - View direction (--section-height-up for RCP)
# - Multi-height capture range
```

### 3. IfcConvert Parameters for View Direction

```bash
# Normal plan view (looking down)
IfcConvert --section-height 6.6 ...

# Reflected ceiling plan (looking up)
# Note: IfcConvert doesn't have --section-height-up, so we simulate:
# - Use section height at ceiling level
# - Invert Y-axis in post-processing (flip vertically)
# - Or accept that "up" view is same as "down" but at different height
```

**IfcConvert Limitation**: IfcConvert 0.8.3 doesn't support true "view direction" parameter.
**Workaround**: Use different section heights and accept that the view is always "down". 
For true RCP (looking up), we would need to:
- Export at ceiling height
- Optionally flip Y-axis in SVG post-processing

### 4. Implementation Phases

#### Phase 1: Multiple Section Heights ✅ (Current capability)
- Already possible by passing different `SECTION_HEIGHT` to generator
- Just need to formalize in config

#### Phase 2: Coordinated Multi-Discipline Views (Next)
- Create `generate-coordinated-floorplan.sh`
- Support multiple layers with different section heights
- Merge all layers into single SVG with correct opacities

#### Phase 3: Reflected Ceiling Plans (Advanced)
- Evaluate if Y-axis flip is needed
- Add post-processing step to flip SVG vertically
- Update CSS for RCP-specific styling (different line weights)

#### Phase 4: Configuration-Driven Generation (Final)
- Parse YAML config
- Auto-generate all view templates
- Single command: `./generate-all-from-config.sh`

## Technical Implementation Details

### Multi-Height Section Cuts
```python
# In calculate-bounds.py or new script
def export_multi_height_range(ifc_file, storey_elev, min_offset, max_offset):
    """
    Export elements visible in a height range.
    IfcConvert limitation: Can only cut at ONE section height.
    
    Workaround:
    - Export at middle height (storey_elev + (min + max) / 2)
    - Use bounding box filtering in IfcOpenShell to pre-filter elements
    - OR: Make multiple exports at different heights and merge
    """
    pass
```

### Coordinate System for Multi-Layer
```python
# All layers MUST use same coordinate system
# Solution: Don't use --model-offset for ANY layer
# Let them all export in raw IFC coordinates
# Scale all by 20x (1:50) consistently
```

### SVG Layering and Opacity
```python
# Combine SVGs with proper z-order:
# 1. Architecture (bottom, light gray)
# 2. Structural (above, blue)
# 3. Mechanical (above, green, ceiling-level)
# 4. Electrical (above, orange, ceiling-level)
# 5. Plumbing (above, cyan, mid-level)
# 6. Spaces (top, text labels)

# Apply opacity via CSS:
# .architecture-layer { opacity: 1; }
# .mechanical-layer { opacity: 0.9; stroke: #00CC66; }
```

## New Scripts to Create

1. **`generate-coordinated-floorplan.sh`**
   - Multi-layer coordinated view
   - Takes storey name and elevation
   - Outputs single SVG with all disciplines

2. **`generate-all-coordinated.sh`**
   - Batch script for all 8 storeys
   - Calls `generate-coordinated-floorplan.sh` for each

3. **`generate-rcp-floorplan.sh`** (Phase 3)
   - Reflected ceiling plan generator
   - Optional Y-axis flip
   - Ceiling-specific section heights

4. **`generate-from-config.py`** (Phase 4)
   - Parse `floorplan-config.yaml`
   - Generate ALL view templates automatically
   - Single source of truth

## Testing Strategy

### Test Case 1: Multi-Height MEP
```bash
# Mechanical at ceiling (2.8m offset)
./generate-mep-floorplan.sh mechanical "020 Mezzanine +5.40m" 8.2

# Electrical at mid-height (1.5m offset)
./generate-mep-floorplan.sh electrical "020 Mezzanine +5.40m" 6.9

# Plumbing at floor (1.0m offset)
./generate-mep-floorplan.sh plumbing "020 Mezzanine +5.40m" 6.4
```

### Test Case 2: Coordinated View
```bash
./generate-coordinated-floorplan.sh "020 Mezzanine +5.40m" 5.40
# Should output: coord_all_020_mezzanine_5_40m.svg
# With 6 layers at different heights
```

### Test Case 3: Reflected Ceiling Plan
```bash
./generate-rcp-floorplan.sh electrical "020 Mezzanine +5.40m" 5.40
# Section at 5.40 + 2.8 = 8.2m
# Shows ceiling-level electrical
```

## Performance Considerations

- **8 CPU cores** available
- Each IfcConvert export: ~10-30 seconds
- Coordinated view (6 layers): ~1-3 minutes per storey
- Full batch (8 storeys × 6 disciplines): ~10-15 minutes

### Optimization
- Parallel export of layers (if safe)
- Cache intermediate files
- Reuse bounds calculations

## File Structure

```
/output/converted/floorplans/
├── arch_000_sea_level.svg
├── arch_010_quay_level_1_90m.svg
├── ...
├── electrical_000_sea_level.svg
├── electrical_010_quay_level_1_90m.svg
├── ...
├── coord_all_000_sea_level.svg          # NEW: Coordinated view
├── coord_all_010_quay_level_1_90m.svg   # NEW
├── ...
├── elec_rcp_000_sea_level.svg           # NEW: RCP view
├── elec_rcp_010_quay_level_1_90m.svg    # NEW
├── ...
```

## Next Steps (Immediate)

1. ✅ Upgrade to 8 CPUs - DONE
2. ✅ Update scripts to `-j 8` - DONE
3. 🔄 Create `generate-coordinated-floorplan.sh` (NEXT)
4. 🔄 Test coordinated view on one storey
5. 🔄 Create batch script for all storeys
6. 🔄 Document section height offsets for each discipline
7. 🔄 (Optional) Implement Y-axis flip for RCP

## Questions to Resolve

1. **Y-axis flip for RCP**: Do we need true "looking up" view, or is "down at ceiling height" sufficient?
2. **Section height auto-detection**: Should we detect element Z-ranges and auto-suggest section heights?
3. **Multi-height ranges**: Do we export multiple section cuts and merge, or single cut at optimal height?
4. **Config format**: YAML or JSON for configuration?

## Success Criteria

- ✅ Generate coordinated floor plans with 6 layers
- ✅ Each layer at its optimal section height
- ✅ Proper opacity and color coding by discipline
- ✅ Maintains coordinate alignment across all layers
- ✅ Performance: <3 minutes per coordinated floor plan
- ✅ Output suitable for professional construction documents

