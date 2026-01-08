# Floor Plan Maker

A comprehensive system for generating professional floor plans from IFC (Industry Foundation Classes) models using IfcConvert.

## Overview

The Floor Plan Maker transforms IFC building models into high-quality SVG floor plans with support for:

- **Multi-discipline coordination** (Architecture, Structure, MEP)
- **Configurable view templates** (similar to Revit's View Templates)
- **Professional CSS styling** with room labels and areas
- **Batch processing** for multiple building levels
- **Perfect alignment** using unified coordinate systems

## Directory Structure

```
floorplanmaker/
├── scripts/
│   ├── generation/      # Core generation scripts
│   ├── testing/         # Test and validation scripts
│   ├── processing/      # Post-processing utilities
│   └── utilities/       # Helper scripts
├── config/
│   ├── templates/       # Configuration templates (YAML/JSON)
│   └── styles/          # CSS styling
├── docs/
│   ├── guides/          # User documentation
│   └── implementation/  # Technical implementation docs
├── logs/                # Generation logs
└── README.md            # This file
```

## Quick Start

### 1. Generate a Single Floor Plan

```bash
cd ifcconvert-worker/floorplanmaker

# Generate a coordinated floor plan (all disciplines)
./scripts/generation/generate-coordinated-floorplan.sh "020 Mezzanine +5.40m" 5.40

# Generate an MEP floor plan
./scripts/generation/generate-mep-floorplan.sh electrical "020 Mezzanine +5.40m" 5.40
```

### 2. Generate All Floor Plans (Batch)

```bash
# Generate coordinated plans for all building levels
./scripts/generation/generate-all-coordinated.sh

# Generate all levels using config file
./scripts/generation/generate-all-levels-complete.sh
```

### 3. Test SVG Export

```bash
# Run comprehensive tests
./scripts/testing/test-svg-focused.sh

# Run basic export test
./scripts/testing/test-svg-export.sh
```

## Configuration

### Main Configuration Files

Located in `config/templates/`:

- **floorplan-config.yaml** - Main configuration (recommended)
- **floorplan-config.json** - JSON format alternative

### Configuration Structure

```yaml
project:
  name: "Project Name"
  code: "XXX"
  scale: "1:50"
  output_dir: "/output/converted/floorplans"

models:
  architecture:
    file: "/uploads/A1_model.ifc"
    elements: [IfcWall, IfcDoor, IfcWindow, IfcStair]
    section_heights:
      default_offset: 1.2    # meters above storey
      
  electrical:
    file: "/uploads/E1_model.ifc"
    elements: [IfcCableSegment, IfcLightFixture, IfcOutlet]
    section_heights:
      default_offset: 2.4    # higher cut for MEP

storeys:
  - name: "000 Sea Level"
    elevation: 0.0
    section_height: 1.20
  - name: "010 Quay Level +1.90m"
    elevation: 1.90
    section_height: 3.10
```

### CSS Styling

Located in `config/styles/floorplan-styles.css`:

- Room fills and labels
- Element styling (walls, doors, windows)
- Layer colors and opacities
- Professional typography

See [CSS Guide](docs/guides/FLOORPLAN_CSS_GUIDE.md) for details.

## Key Scripts

### Generation Scripts (`scripts/generation/`)

| Script | Description |
|--------|-------------|
| `generate-coordinated-floorplan.sh` | Generate single coordinated plan with all disciplines |
| `generate-all-coordinated.sh` | Batch generate coordinated plans for all levels |
| `generate-mep-floorplan.sh` | Generate MEP floor plans with arch underlay |
| `generate-all-levels-complete.sh` | Generate all levels from config |
| `generate-floorplans-from-config.py` | Python-based config-driven generator |
| `generate-all-floorplans.py` | Comprehensive view template generator |

### Testing Scripts (`scripts/testing/`)

| Script | Description |
|--------|-------------|
| `test-svg-focused.sh` | Focused SVG export tests |
| `test-svg-export.sh` | Basic SVG export test |
| `test-svg-comprehensive.sh` | Comprehensive test suite |
| `diagnose-alignment.sh` | Check alignment of multi-layer plans |

### Processing Scripts (`scripts/processing/`)

| Script | Description |
|--------|-------------|
| `svg-floorplan-complete.sh` | Complete floor plan with geometry + spaces |
| `svg-style-rooms.py` | Apply room styling and labels |
| `svg-split-storeys.py` | Split multi-storey SVG into separate files |
| `combine-and-scale-svgs.py` | Combine multiple SVG layers |

### Utility Scripts (`scripts/utilities/`)

| Script | Description |
|--------|-------------|
| `calculate-bounds.py` | Calculate unified bounds from multiple IFC files |
| `config_parser.py` | Parse YAML/JSON configuration |
| `detect-mep-storeys.py` | Detect storeys in MEP models |
| `list-storeys.sh` | List all building storeys |

## Documentation

### User Guides (`docs/guides/`)

- **FLOORPLAN_GENERATOR_README.md** - Detailed generator documentation
- **FLOORPLAN_CSS_GUIDE.md** - CSS styling guide
- **SVG_EXPORT_GUIDE.md** - SVG export workflow
- **CSS_DOCUMENTATION_INDEX.md** - Complete CSS documentation
- **TEST_SCRIPTS.md** - Testing documentation

### Technical Documentation (`docs/implementation/`)

- **ALIGNMENT_SOLUTION.md** - Technical solution for SVG alignment
- **IMPLEMENTATION_PLAN_ADVANCED_FLOORPLANS.md** - Advanced features plan

## Workflow

### 1. Coordinate System Alignment

The system uses a unified coordinate approach to ensure perfect alignment:

1. **Calculate bounds** from ALL input IFC files
2. **Export with `--model-offset`** only (no `--bounds` or `--scale`)
3. **Manually set viewBox** for consistent coordinate space

See [ALIGNMENT_SOLUTION.md](docs/implementation/ALIGNMENT_SOLUTION.md) for details.

### 2. Multi-Layer Generation

For coordinated floor plans with multiple disciplines:

```
1. Export geometry (architecture) → arch-geometry.svg
2. Export spaces → arch-spaces.svg
3. Export structural → struct.svg
4. Export MEP systems → mep.svg
5. Combine all layers with CSS → final.svg
```

### 3. Styling and Post-Processing

1. Apply CSS classes to elements
2. Add room labels and areas
3. Set layer opacities
4. Apply professional typography

## Output

Generated floor plans are saved to:
- `/output/converted/floorplans/` - Final floor plans
- `/output/converted/temp/` - Intermediate files

### File Naming Convention

```
{prefix}_{storey_slug}.svg

Examples:
- coord_all_020_mezzanine_5_40m.svg
- FP_ELEC_040_first_floor_14_23m.svg
- arch_030_slussen_level_8_90m.svg
```

## Requirements

- **IfcConvert** (IfcOpenShell)
- **Docker** (for containerized execution)
- **Python 3.8+** (for Python scripts)
- **bash** (for shell scripts)

### Python Dependencies

```bash
pip install pyyaml
```

## Troubleshooting

### Alignment Issues

If floor plan layers are misaligned:

1. Check that all layers use the same `--model-offset`
2. Verify bounds are calculated from ALL input files
3. Run `./scripts/testing/diagnose-alignment.sh`

See [ALIGNMENT_SOLUTION.md](docs/implementation/ALIGNMENT_SOLUTION.md).

### Missing Elements

If elements don't appear in floor plans:

1. Check section height matches storey elevation
2. Verify element types are included in config
3. Check IFC file contains expected elements

### Performance

- Use `-j 8` flag for parallel processing
- Pre-calculate bounds once and reuse
- Use batch scripts for multiple levels

## Examples

### Example 1: Generate Single Level

```bash
# Generate Level 2 with all disciplines
./scripts/generation/generate-coordinated-floorplan.sh \
  "020 Mezzanine +5.40m" \
  5.40
```

### Example 2: Generate Electrical Plans for All Levels

```bash
# Edit config to enable only electrical
# Then run:
./scripts/generation/generate-all-levels-complete.sh
```

### Example 3: Custom View Template

```bash
# Edit config/templates/floorplan-config.yaml
# Add custom view template:

view_templates:
  - name: "Custom Electrical"
    output_prefix: "CUSTOM_ELEC"
    layers:
      - model: architecture
        layer_type: underlay
        elements: [IfcWall, IfcDoor]
      - model: electrical
        layer_type: main
        elements: [IfcCableSegment, IfcLightFixture]

# Generate:
./scripts/generation/generate-floorplans-from-config.py \
  config/templates/floorplan-config.yaml
```

## Contributing

When adding new scripts:

1. Place in appropriate `scripts/` subdirectory
2. Add documentation to relevant `docs/` file
3. Update this README with usage examples
4. Test with sample IFC files

## License

Part of the IFC Pipeline project.

## Support

- Check `docs/guides/` for detailed documentation
- Review `logs/` for generation logs
- Run diagnostic scripts in `scripts/testing/`

---

**Generated by Floor Plan Maker** | [Technical Documentation](docs/implementation/)

