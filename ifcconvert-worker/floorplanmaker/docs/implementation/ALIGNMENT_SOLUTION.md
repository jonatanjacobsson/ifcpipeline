# SVG Alignment Solution

## The Problem
Floor plans generated from separate IFC files (geometry + spaces) were misaligned by ~256 pixels, even when using the same `--model-offset` and `--bounds` parameters.

## Root Cause
The `--bounds` parameter in IfcConvert **applies automatic scaling** to fit content within the specified pixel dimensions. Since the geometry file and spaces file have different content extents, IfcConvert was applying **different scale factors** to each, causing misalignment.

```
--bounds arg    Specifies the bounding rectangle, for example 512x512, 
                to which the output **will be scaled**.
```

## The Solution
**Remove both `--bounds` and `--scale`** parameters entirely and use only:
1. `--model-offset` to center content at origin
2. Manual `viewBox` attribute to define the coordinate system
3. Manual `width` and `height` for canvas dimensions

### Implementation Steps

1. **Calculate unified bounds from BOTH IFC files**:
   ```bash
   python3 calculate-bounds.py \
     geometry.ifc \
     spaces.ifc \
     6.60 \
     IfcWall IfcDoor IfcWindow IfcStair IfcRailing IfcSpace
   ```

2. **Export with only `--model-offset` (NO --bounds, NO --scale)**:
   ```bash
   IfcConvert \
     --model-offset "-827.25;-779.12;0" \
     --include entities IfcWall IfcDoor IfcWindow \
     geometry.ifc output.svg
   ```

3. **Manually set SVG viewBox and dimensions**:
   ```bash
   sed -i 's|<svg xmlns=...|<svg ... width="2816" height="2048" viewBox="-63.93 -43.81 139.50 95.61">|'
   ```

## Results

### Before (with --bounds)
- Different transformation matrices per file
- Coordinates in pixel space (0-2814)
- Misalignment: ~256 pixels

### After (without --bounds/--scale)
- Identity transformation matrix: `[[1,0,0],[0,1,0],[0,0,1]]`
- Coordinates in raw IFC meter space (-58 to +58)
- Perfect alignment: content centered at (0, 0)

## Key Learnings

1. **`--bounds` is NOT just a canvas size** - it applies scaling
2. **`--scale` centers content** - different content = different centering
3. **Raw coordinates + manual viewBox = predictable alignment**
4. **Calculate bounds from ALL input files** - not just one

## Files Modified
- `calculate-bounds.py` - Now accepts multiple IFC files
- `svg-floorplan-complete.sh` - Removed --bounds, added manual viewBox
- `diagnose-alignment.sh` - Added coordinate range analysis

## Testing
```bash
./svg-floorplan-complete.sh "020 Mezzanine +5.40m" 6.60
./diagnose-alignment.sh
```

Expected output:
```
✓ Transformation matrices match perfectly!
Overall coordinate range:
  X: -58.13 to 58.13 (center: -0.00)
  Y: -39.84 to 39.84 (center: -0.00)
✓ All content is within the viewBox!
```
