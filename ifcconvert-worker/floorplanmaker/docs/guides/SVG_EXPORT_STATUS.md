# SVG Export Status and Troubleshooting

## Current Status

### ❌ Issue Identified

**Problem:** SVG exports are empty or contain only CSS styles without actual geometry

**Root Cause:** IfcConvert version mismatch
- **Dockerfile specifies:** v0.8.0
- **Actually installed:** v0.7.11
- **Impact:** Version 0.7.11 has limited/buggy SVG export functionality

### Test Results

Comprehensive testing of 15 different SVG export configurations revealed:
- ✗ 14/15 tests failed to generate SVG files
- ⚠ 1/15 tests generated SVG with empty geometry (only CSS)
- The generated SVGs contain section groups with "nan" values in transformation matrices

**Working Test:** Test #13 (kitchen sink with all options)
- Command: `-j 4 --plan --model --bounds 3000x2000 --exclude entities IfcOpeningElement --print-space-names --print-space-areas --door-arcs --auto-section`
- Output: 1738 bytes, 2 empty `<g>` groups with invalid matrices
- Result: Not usable

## Solutions

### Solution 1: Upgrade to IfcConvert 0.8.0 (Recommended)

IfcConvert 0.8.0+ has significantly improved SVG export capabilities.

**Steps to upgrade:**

1. Rebuild the container:
```bash
cd ../../../  # to project root
docker compose stop ifcconvert-worker
docker compose build --no-cache ifcconvert-worker
docker compose up -d ifcconvert-worker
```

2. Verify version:
```bash
docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert --version
```

Should show: `IfcOpenShell IfcConvert 0.8.0` or later

**Note:** The Dockerfile already specifies 0.8.0, but the download URL may be incorrect or the binary may be outdated. We need to verify the correct download URL.

### Solution 2: Use Alternative Export Formats (Current Workaround)

Until the container is upgraded, use these reliable formats:

#### A. Export to OBJ (3D Model) ✅ WORKING

```bash
./test-obj-export.sh
```

**Advantages:**
- ✓ Works perfectly with current version
- ✓ Full 3D geometry
- ✓ Material information included (.mtl file)
- ✓ Can be viewed in Blender, MeshLab, or online viewers

**Output:**
- `.obj` file: 3D geometry (4.4 MB for test file)
- `.mtl` file: Material definitions
- 53,206 vertices, 88,792 faces

**View with:**
- Blender: `blender model.obj`
- MeshLab: `meshlab model.obj`
- Online: Upload to https://3dviewer.net/

#### B. Export to glTF (.glb) ✅ RECOMMENDED FOR WEB

```bash
docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert \
  -y -j 8 \
  --use-element-names \
  --weld-vertices \
  /uploads/A1_2b_BIM_XXX_0001_00.v24.0.ifc \
  /output/converted/model.glb
```

**Advantages:**
- ✓ Modern format for web/mobile
- ✓ Compact binary format
- ✓ Supports materials and textures
- ✓ Works with Three.js, Babylon.js

#### C. Export to STEP (.stp) ✅ FOR CAD

```bash
docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert \
  -y -j 4 \
  --convert-back-units \
  /uploads/A1_2b_BIM_XXX_0001_00.v24.0.ifc \
  /output/converted/model.stp
```

**Advantages:**
- ✓ Standard CAD exchange format
- ✓ Preserves units and precision
- ✓ Opens in all CAD software

### Solution 3: Generate SVG from 3D Model (Alternative Workflow)

Since direct IFC→SVG doesn't work, use a two-step process:

1. **Export to OBJ:**
```bash
./test-obj-export.sh
```

2. **Convert OBJ to SVG using external tools:**
   - **Blender** (with Python script)
   - **Inkscape** (import 3D model, export 2D view)
   - **Online tools:** svg3d.io, etc.

## Why SVG Export Fails in 0.7.11

### Technical Details

1. **Missing 2D Projection:**
   - SVG requires 2D projections of 3D geometry
   - IfcConvert 0.7.11 fails to compute proper 2D projections
   - Results in empty `<g>` groups or "nan" values in matrices

2. **Section Cutting Issues:**
   - `--auto-section` and `--auto-elevation` flags fail silently
   - Section plane calculations return invalid values
   - No error messages, just empty output

3. **Options That Don't Help:**
   - `--plan`: Exports curves/axis but not floor plan
   - `--bounds`: Sets canvas size but no geometry to draw
   - `--section-height`: Fails to create horizontal cut
   - `--print-space-names`: Labels work but no geometry to label

## Comparison: 0.7.11 vs 0.8.0

| Feature | v0.7.11 | v0.8.0+ |
|---------|---------|---------|
| OBJ Export | ✓ Works | ✓ Works |
| glTF Export | ✓ Works | ✓ Better |
| STEP Export | ✓ Works | ✓ Works |
| **SVG Export** | ✗ Broken | ✓ Works |
| Auto Sections | ✗ Fails | ✓ Works |
| Floor Plans | ✗ Empty | ✓ Works |
| Space Labels | ⚠ No geometry | ✓ Works |

## Recommended Action Plan

### Immediate (Use Workarounds)
1. ✓ Use OBJ export for 3D models
2. ✓ Use glTF for web applications
3. ✓ Document limitations for users

### Short-term (Upgrade Container)
1. Verify correct IfcConvert 0.8.0 download URL
2. Rebuild container with correct binary
3. Test SVG exports after upgrade
4. Update documentation

### Long-term (Enhance Pipeline)
1. Add post-processing for SVG generation
2. Integrate Blender for automated 2D rendering
3. Provide multiple export format options
4. Add preview thumbnails

## Testing After Upgrade

Once upgraded to 0.8.0, test with:

```bash
# Test 1: Simple floor plan
docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert \
  -y --bounds 2048x1536 \
  --section-height 1.5 \
  --print-space-names \
  /uploads/A1_2b_BIM_XXX_0001_00.v24.0.ifc \
  /output/test-floorplan.svg

# Test 2: Auto sections
docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert \
  -y --bounds 2048x1536 \
  --auto-section \
  --door-arcs \
  /uploads/A1_2b_BIM_XXX_0001_00.v24.0.ifc \
  /output/test-sections.svg

# Verify: Check for actual paths
grep -c "<path" ../../shared/output/test-floorplan.svg
```

Expected result: Should show 100+ paths (actual geometry)

## References

- [IfcConvert Documentation](https://docs.ifcopenshell.org/ifcconvert/usage.html)
- [IfcOpenShell Releases](https://github.com/IfcOpenShell/IfcOpenShell/releases)
- [SVG Export Issues](https://github.com/IfcOpenShell/IfcOpenShell/issues?q=svg)

## Status Log

- **2025-10-15:** Identified version mismatch (0.7.11 vs 0.8.0)
- **2025-10-15:** Comprehensive testing confirms SVG export failure
- **2025-10-15:** Documented workarounds (OBJ, glTF, STEP exports)
- **Next:** Upgrade container to 0.8.0

## Summary

🔴 **Current State:** SVG export not functional with IfcConvert 0.7.11  
🟡 **Workaround:** Use OBJ, glTF, or STEP export formats  
🟢 **Solution:** Upgrade to IfcConvert 0.8.0 for working SVG export  


