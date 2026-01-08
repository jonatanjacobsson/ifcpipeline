# Comprehensive Floor Plan Generator

## Overview
This system generates multiple floor plan view templates from IFC models, similar to Revit's View Templates but for IFC files.

## Configuration File: `floorplan-config.json`

### Models Section
Defines all IFC models and their element types:
- **architecture**: A1 model (walls, doors, windows, stairs, etc.)
- **spaces**: Space definitions with names and areas
- **structural**: S2 model (columns, beams, slabs, etc.)
- **electrical**: E1 model (cables, outlets, lights, etc.)
- **plumbing**: P1 model (pipes, valves, sanitary terminals, etc.)
- **mechanical**: M1 model (ducts, HVAC equipment, etc.)

### View Templates
Pre-configured view templates for different purposes:

#### 1. **Architectural** (`arch-`)
- Architecture + Spaces
- Full detail with room labels
- Black/white styling

#### 2. **Structural** (`struct-`)
- Architectural underlay (30% opacity)
- Structural elements (blue)
- Shows columns, beams, foundations

#### 3. **Electrical** (`elec-`)
- Architectural underlay (20% opacity)
- Structural underlay (15% opacity)
- Electrical systems (orange)
- Shows cables, outlets, panels

#### 4. **Plumbing** (`plumb-`)
- Architectural underlay (20% opacity)
- Structural underlay (15% opacity)
- Plumbing systems (cyan)
- Shows pipes, fixtures, valves

#### 5. **Mechanical** (`mech-`)
- Architectural underlay (20% opacity)
- Structural underlay (15% opacity)
- HVAC systems (green)
- Shows ducts, terminals, equipment

#### 6. **Coordinated** (`coord-`)
- All systems visible
- Architecture (40% opacity)
- Structural (30% opacity)
- MEP systems (80% opacity each)
- Color-coded by discipline

## Usage

### Generate All Floor Plans
```bash
cd ifcconvert-worker/floorplanmaker
python3 scripts/generation/generate-all-floorplans.py
```

This will generate floor plans for **all view templates** × **all building storeys**:
- 6 view templates
- 7 storeys
- **= 42 floor plans total**

### Generate Single View Template
```bash
# Example: Generate just architectural plans
./svg-floorplan-complete.sh "020 Mezzanine +5.40m" 6.60
```

### Customize Configuration
Edit `floorplan-config.json` to:
- Add/remove models
- Change element types
- Modify colors and opacity
- Create custom view templates
- Adjust CSS styling

## Output Files

Floor plans are saved to `/output/converted/` with naming convention:
```
{view_prefix}-{storey_slug}.svg
```

Examples:
- `arch-020_mezzanine_5_40m.svg` - Architectural plan
- `elec-020_mezzanine_5_40m.svg` - Electrical plan
- `struct-020_mezzanine_5_40m.svg` - Structural plan
- `coord-020_mezzanine_5_40m.svg` - Coordinated plan

## Building Storeys

Automatically detected from architecture model:
- 000 Sea Level (0.0m)
- 010 Quay Level +1.90m (1.90m)
- 020 Mezzanine +5.40m (5.40m)
- 030 Slussen Level +8.90m (8.90m)
- 040 Stora Tullhusplan +13.20m (13.20m)
- 100 Lower Roof +15.90m (15.90m)
- 110 Upper Roof +21.20m (21.20m)

Section cuts are taken at storey elevation + 1.2m offset.

## Technical Details

### Coordinate System
- **Scale**: 1:50 (1 meter = 20mm on drawing)
- **Units**: Millimeters in viewBox
- **Coordinates**: Scaled 20x from model meters
- **Canvas**: ~2000px × ~1400px

### Layer Compositing
- Underlays rendered with reduced opacity
- Primary systems at full opacity
- CSS applied per view template
- Stroke colors and fills configurable

### CSS Styling
Each view template can define:
- Text colors (fill/stroke)
- Line weights
- Element colors by type
- Opacity for underlays
- Special element styling

## Model Files

Latest versions used:
- Architecture: `A1_2b_BIM_XXX_0001_00.v24.0.ifc`
- Spaces: `A1_2b_BIM_XXX_0003_00.ifc`
- Structural: `S2_2B_BIM_XXX_0001_00.v12.0.ifc`
- Electrical: `E1_2b_BIM_XXX_600_00.v183.0.ifc`
- Plumbing: `P1_2b_BIM_XXX_5000_00.v12.0.ifc`
- Mechanical: `M1_2b_BIM_XXX_5700_00.v12.0.ifc`

## Adding New View Templates

Example: Add a "Fire Protection" view template:

```json
"fire_protection": {
  "name": "Fire Protection Floor Plans",
  "description": "Fire suppression systems",
  "layers": [
    {
      "model": "architecture",
      "opacity": 0.2
    },
    {
      "model": "plumbing",
      "opacity": 1.0,
      "stroke_color": "#CC0000"
    }
  ],
  "output_prefix": "fire",
  "css": {
    "sprinkler_color": "#CC0000"
  }
}
```

## Performance

Generation times (approximate):
- Single floor plan: ~10-30 seconds
- Full set (42 plans): ~10-15 minutes
- Depends on model complexity and system resources

## Requirements

- Docker container: `ifcpipeline-ifcconvert-worker-1`
- IfcConvert 0.8.3
- Python 3.10+
- 4 CPU cores recommended

