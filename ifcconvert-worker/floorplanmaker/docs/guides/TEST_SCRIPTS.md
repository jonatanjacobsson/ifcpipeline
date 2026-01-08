# IfcConvert Test Scripts

Test scripts for generating SVG floor plans from IFC files using IfcConvert.

## Available Scripts

### 1. test-svg-export.sh (Basic)

Simple test script that generates a single SVG floor plan with good defaults.

**Usage:**
```bash
./test-svg-export.sh
```

**Settings:**
- Input: `/uploads/Building-Architecture.ifc`
- Output: `/output/converted/Building-Architecture-floorplan.svg`
- Bounds: 2048x1536
- Threads: 4
- Features:
  - Excludes IfcOpeningElement and IfcSpace
  - Prints space names and areas
  - Shows door arcs

**Output:**
- SVG file: `shared/output/converted/Building-Architecture-floorplan.svg`
- Log file: `shared/output/converted/Building-Architecture-convert.log`

### 2. test-svg-export-advanced.sh (Advanced)

Advanced test script that generates multiple SVG variants with different settings.

**Usage:**
```bash
./test-svg-export-advanced.sh
```

**Generated Variants:**

1. **basic** - Minimal floor plan
   - Excludes openings and spaces
   - No labels or decorations

2. **with-labels** - Floor plan with space labels
   - 2048x1536 bounds
   - Space names and areas
   - Excludes openings and spaces

3. **with-doors** - Floor plan with door arcs
   - 2048x1536 bounds
   - Door opening arcs
   - Space names

4. **scaled** - Scaled floor plan (1:100)
   - 1024x768 bounds
   - 1:100 scale
   - Centered layout
   - All labels and door arcs

5. **sections** - Auto-generated sections/elevations
   - Auto sections
   - Auto elevations
   - Storey height lines

6. **complete** - Everything included
   - No exclusions
   - All labels and features

**Output:**
All files in: `shared/output/converted/`
- `Building-Architecture-basic.svg`
- `Building-Architecture-with-labels.svg`
- `Building-Architecture-with-doors.svg`
- `Building-Architecture-scaled.svg`
- `Building-Architecture-sections.svg`
- `Building-Architecture-complete.svg`

## Viewing SVG Files

### On Linux Desktop:
```bash
firefox shared/output/converted/Building-Architecture-floorplan.svg
# or
chromium shared/output/converted/Building-Architecture-floorplan.svg
```

### Via File Manager:
Navigate to: `/home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/`

### Via Web Browser:
1. Copy file to web-accessible location
2. Open in browser with file:// protocol

## Customizing Scripts

### Common IfcConvert Options for SVG:

**Bounds and Scale:**
```bash
--bounds 1024x768          # Set canvas size
--scale 1:100              # Set scale ratio
--center 0.5x0.5           # Center point (0-1)
```

**Filtering:**
```bash
--exclude entities IfcOpeningElement IfcSpace    # Exclude elements
--include entities IfcWall IfcSlab               # Include only these
```

**Labels and Annotations:**
```bash
--print-space-names        # Show space names
--print-space-areas        # Show space areas (m²)
--door-arcs               # Show door opening arcs
--draw-storey-heights full # Show storey height lines
```

**Sections and Elevations:**
```bash
--auto-section            # Auto-generate sections
--auto-elevation          # Auto-generate elevations
--section-height 1.2      # Section cut height
--section-height-from-storeys  # Use storey elevations
```

**Performance:**
```bash
-j 8                      # Use 8 threads
--threads 8              # Alternative syntax
```

**SVG Rendering:**
```bash
--svg-poly               # Use polygonal HLR
--svg-prefilter         # Prefilter shapes
--svg-no-css            # Don't emit CSS
--svg-without-storeys   # Skip storey drawings
```

## Testing with Different IFC Files

To test with a different IFC file:

1. Copy your IFC file to the uploads directory:
```bash
cp /path/to/your/model.ifc shared/uploads/
```

2. Edit the script to change INPUT_FILE:
```bash
INPUT_FILE="/uploads/your-model.ifc"
OUTPUT_FILE="/output/converted/your-model.svg"
```

3. Run the script:
```bash
./test-svg-export.sh
```

## Troubleshooting

### Script fails with "file not found"
- Ensure IFC file is in `shared/uploads/` directory
- Check file permissions
- Verify container is running: `docker ps | grep ifcconvert`

### Empty or invalid SVG
- Check log file for errors
- Verify IFC file is valid
- Try with `--verbose` flag for more details

### Container not found
- Start the worker: `docker compose up -d ifcconvert-worker`
- Check logs: `docker logs ifcpipeline-ifcconvert-worker-1`

### Poor quality or wrong view
- Adjust `--bounds` for larger canvas
- Change `--scale` for proper sizing
- Use `--center` to adjust positioning
- Add `--auto-section` or `--auto-elevation` for different views

## Example: Custom SVG Export

Create a custom script for your specific needs:

```bash
#!/bin/bash

# Custom SVG export for architectural floor plans

INPUT_FILE="/uploads/my-building.ifc"
OUTPUT_FILE="/output/converted/my-building-plan.svg"

docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert \
  -y \
  -j 8 \
  --log-format plain \
  --log-file "${OUTPUT_FILE%.svg}.log" \
  --exclude entities IfcOpeningElement IfcSpace IfcFurnishingElement \
  --bounds 3000x2000 \
  --scale 1:50 \
  --center 0.5x0.5 \
  --print-space-names \
  --print-space-areas \
  --door-arcs \
  --draw-storey-heights left \
  --svg-poly \
  "$INPUT_FILE" \
  "$OUTPUT_FILE"
```

## Performance Tips

- Use more threads (`-j`) for large files
- Exclude unnecessary elements to speed up conversion
- Use smaller bounds for faster rendering
- Consider `--svg-poly` for faster HLR algorithm

## References

- [IfcConvert Documentation](https://docs.ifcopenshell.org/ifcconvert/usage.html)
- [ARGUMENTS.md](./ifcconvert-worker/ARGUMENTS.md) - Complete parameter reference
- [EXAMPLES.md](./ifcconvert-worker/EXAMPLES.md) - More usage examples


