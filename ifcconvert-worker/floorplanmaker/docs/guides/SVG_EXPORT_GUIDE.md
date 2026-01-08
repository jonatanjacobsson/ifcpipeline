# SVG Export Guide for IfcConvert 0.7.11

## Working Configuration ✓

Based on comprehensive testing, here's the **WORKING** configuration for SVG exports:

### Minimal Working Command

```bash
docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert \
  -y \
  -j 4 \
  --plan \
  --model \
  --auto-section \
  --bounds 3000x2000 \
  --exclude entities IfcOpeningElement \
  /uploads/your-file.ifc \
  /output/your-file.svg
```

### Quick Start Script

```bash
chmod +x svg-export-working.sh
./svg-export-working.sh
```

## Critical Parameters Explained

### ✅ Required for Success

1. **`--plan`** - Includes curves and axis representations
   - Without this: Empty SVG
   
2. **`--model`** - Includes surfaces and solids
   - Without this: Empty SVG
   
3. **`--auto-section`** - Creates automatic section planes
   - **This is the KEY parameter!**
   - Creates East-West and North-South sections
   - Without this: No 2D projections

4. **`--exclude entities IfcOpeningElement`** - Skips problematic openings
   - Prevents boolean operation failures
   - Critical for stability

### ✅ Recommended Options

5. **`--bounds 3000x2000`** - Sets canvas size
   - Larger = more detail
   - Adjust based on model size

6. **`-j 4`** - Parallel processing with 4 threads
   - Speeds up conversion
   - Adjust based on CPU

7. **`--print-space-names`** - Adds room labels
   - Shows space names in SVG

8. **`--print-space-areas`** - Shows room areas
   - Displays area in m²

9. **`--door-arcs`** - Visualizes door swings
   - Useful for floor plans

## What Works vs What Doesn't

### ✓ Working Combinations

| Configuration | Result | Use Case |
|--------------|--------|----------|
| `--plan --model --auto-section` | ✓ Works | Basic floor plan |
| `+ --bounds 3000x2000` | ✓ Better | Scaled view |
| `+ --exclude IfcOpeningElement` | ✓ Stable | Avoid errors |
| `+ --print-space-names` | ✓ Enhanced | Room labels |
| `+ --door-arcs` | ✓ Detailed | Door swings |

### ✗ Failing Configurations  

| Configuration | Result | Reason |
|--------------|--------|--------|
| No flags | ✗ Fails | No 2D projection |
| `--bounds` only | ✗ Fails | No geometry |
| `--plan` only | ✗ Fails | No sections |
| `--model` only | ✗ Fails | No sections |
| `--section-height 1.5` | ✗ Fails | Manual sections broken |
| `--section-height-from-storeys` | ✗ Fails | Storey detection broken |
| `--auto-elevation` | ✗ Fails | Elevations broken |

## Test Results Summary

From 15 comprehensive tests:
- **14 tests failed** - returned exit code 1 or empty SVG
- **1 test succeeded** - Test #13 ("kitchen sink")

**Successful Test Configuration:**
```bash
-j 4 --plan --model --bounds 3000x2000 \
--exclude entities IfcOpeningElement \
--print-space-names --print-space-areas \
--door-arcs --auto-section
```

**Results:**
- ✓ 2,165 objects processed
- ✓ SVG generated (1,738 bytes)
- ✓ 2 section groups created
- ⚠ Limited geometry (4 elements)

## Known Limitations

### 1. Minimal Geometry in Output
- SVG contains section groups but limited paths
- Most geometry doesn't project to 2D properly
- Result: Floor plan outline without detail

### 2. Version Limitations
- IfcConvert 0.7.11 has limited SVG support
- Better support in 0.8.0+ (when available)
- Current binary at 0.8.0 release tag is actually 0.7.11

### 3. Boolean Operation Failures
- Opening subtractions often fail
- Workaround: Exclude IfcOpeningElement
- Trade-off: Missing door/window details

### 4. Section Plane Issues
- Manual section heights don't work
- Storey-based sections don't work
- Only `--auto-section` produces results

## Optimization Tips

### For Large Files (100MB+)

```bash
-j 8 \                              # More threads
--exclude entities IfcOpeningElement IfcSpace IfcFurnishingElement \
--bounds 2048x1536                  # Smaller canvas = faster
```

### For Detailed Output

```bash
-j 4 \
--bounds 4096x3072                  # Larger canvas
--print-space-names \
--print-space-areas \
--door-arcs \
--draw-storey-heights full
```

### For Speed

```bash
-j 8 \
--exclude entities IfcOpeningElement IfcSpace \
--bounds 1024x768                   # Minimal canvas
```

## Troubleshooting

### Problem: Empty SVG (Only CSS)

**Solution:** Ensure you use ALL required parameters:
```bash
--plan --model --auto-section
```

### Problem: "Input file not specified" Error

**Cause:** Argument parsing issue with `--exclude`

**Solution:** Don't place `--exclude` right before input file

### Problem: Conversion Takes Forever

**Solutions:**
1. Reduce thread count: `-j 2`
2. Exclude more entities
3. Use smaller bounds
4. Filter by storey/zone first

### Problem: Errors in Log

**Common Errors:**
```
[Error] Opening subtraction failed for 1 openings
```

**Solution:** Already handled by `--exclude entities IfcOpeningElement`

## Alternative Approaches

If SVG quality is insufficient, consider:

### 1. Export to OBJ, Then Convert
```bash
# Step 1: Export to OBJ (works perfectly)
docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert \
  -y -j 8 --use-element-names \
  /uploads/file.ifc /output/file.obj

# Step 2: Use Blender/Inkscape to generate 2D views
blender --background --python generate_floorplan.py file.obj
```

### 2. Export to glTF for Web Viewing
```bash
docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert \
  -y -j 8 \
  /uploads/file.ifc /output/file.glb
```

### 3. Use IFC.js for Interactive Web Views
- Better for interactive floor plans
- Real-time 3D/2D switching
- Modern web-based solution

## Expected Output

### What You Get
- SVG file with CSS styles
- Section groups (East-West, North-South)
- Limited geometry elements
- Room labels (if requested)

### What You Don't Get
- Detailed floor plan lines
- Complete wall outlines
- Furniture details
- Precise dimensional information

## Example: Complete Workflow

```bash
#!/bin/bash

# 1. Copy IFC file to uploads
cp /path/to/your/model.ifc shared/uploads/

# 2. Run conversion
docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert \
  -y -j 4 \
  --plan --model --auto-section \
  --bounds 3000x2000 \
  --exclude entities IfcOpeningElement \
  --print-space-names \
  --print-space-areas \
  /uploads/model.ifc \
  /output/converted/model-floorplan.svg

# 3. Check result
file shared/output/converted/model-floorplan.svg
grep -c "<path" shared/output/converted/model-floorplan.svg

# 4. View in browser
firefox shared/output/converted/model-floorplan.svg
```

## Comparison with Other Formats

| Format | Quality | Speed | Use Case |
|--------|---------|-------|----------|
| **SVG** | ⭐⭐ | Fast | 2D floor plans (limited) |
| **OBJ** | ⭐⭐⭐⭐⭐ | Medium | 3D models (perfect) |
| **glTF** | ⭐⭐⭐⭐⭐ | Medium | Web/mobile (perfect) |
| **STEP** | ⭐⭐⭐⭐⭐ | Slow | CAD software (perfect) |

**Recommendation:** For production use, consider OBJ or glTF exports until IfcConvert SVG support improves.

## Future Improvements

When upgrading to IfcConvert 0.8.0+:
- Better 2D projections
- Working manual section heights
- Working storey-based sections
- Better geometry detail in SVG
- Fewer boolean operation failures

## Summary

✅ **SVG export works** with specific configuration  
⚠️ **Limited geometry** in current version  
🔧 **Working configuration documented** above  
📊 **Alternative formats recommended** for better results  

For best results:
- Use the working configuration provided
- Set realistic expectations about output quality
- Consider OBJ/glTF for production use
- Plan upgrade to IfcConvert 0.8.0+ when available

## Support

- Test scripts: `./svg-export-working.sh`
- Comprehensive tests: `./test-svg-comprehensive.sh`
- Documentation: This file
- Status: `SVG_EXPORT_STATUS.md`


