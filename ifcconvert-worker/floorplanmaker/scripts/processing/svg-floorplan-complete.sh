#!/bin/bash
#
# Complete Floor Plan Generator - Single Storey
# Combines geometry + spaces with professional CSS styling
#
# Usage: ./svg-floorplan-complete.sh "010 Quay Level +1.90m"
#        ./svg-floorplan-complete.sh (exports all storeys)
#

set -e

# Configuration
GEOMETRY_FILE="/uploads/A1_2b_BIM_XXX_0001_00.v24.0.ifc"
SPACES_FILE="/uploads/A1_2b_BIM_XXX_0003_00.ifc"
OUTPUT_DIR="/output/converted"
STOREY_NAME="${1:-}"  # Optional: specific storey name

echo "========================================="
echo "Complete Floor Plan Generator"
echo "========================================="
echo ""

if [ -z "$STOREY_NAME" ]; then
    echo "Mode: Generate all storeys at their elevations"
    echo ""
    echo "Detecting building storeys and elevations..."
    
    # Extract storey names and heights from IFC file
    # Format: "010 Quay Level +1.90m" -> extract 1.90
    STOREY_INFO=$(docker exec ifcpipeline-ifcconvert-worker-1 grep -i "IFCBUILDINGSTOREY" "$GEOMETRY_FILE" 2>/dev/null | \
      grep -oP "(?<=')[^']*(?=')" | \
      grep -E "Level|Floor|Storey|Mezzanine|Roof|Quay|Slussen" | \
      sort -u)
    
    STOREY_COUNT=$(echo "$STOREY_INFO" | wc -l)
    echo "Found $STOREY_COUNT building storeys"
    echo ""
    
    # Process each storey
    STOREY_NUM=0
    while IFS= read -r STOREY; do
        if [ -n "$STOREY" ]; then
            STOREY_NUM=$((STOREY_NUM + 1))
            
            # Extract height from storey name (e.g., "+1.90m" -> "1.90")
            HEIGHT=$(echo "$STOREY" | grep -oP '[\+\-]?\d+[\.,]\d+' | tr ',' '.' | tr -d '+' | head -1)
            
            if [ -z "$HEIGHT" ]; then
                # If no height in name, use index * 3.5m as default floor height
                HEIGHT=$(echo "$STOREY_NUM * 3.5 - 2.0" | bc)
            fi
            
            # Add default section height offset (1.2m above floor)
            SECTION_HEIGHT=$(echo "$HEIGHT + 1.2" | bc)
            
            echo "========================================="
            echo "Processing $STOREY_NUM/$STOREY_COUNT: $STOREY"
            echo "  Floor elevation: ${HEIGHT}m"
            echo "  Section height: ${SECTION_HEIGHT}m"
            echo "========================================="
            echo ""
            
            # Call this script recursively with storey name and height
            "$0" "$STOREY" "$SECTION_HEIGHT"
            
            echo ""
        fi
    done <<< "$STOREY_INFO"
    
    echo ""
    echo "========================================="
    echo "✓✓✓ All $STOREY_NUM Storeys Completed!"
    echo "========================================="
    echo ""
    echo "Generated floor plans:"
    ls -lh /home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/floorplan-*.svg 2>/dev/null | tail -15
    echo ""
    exit 0
else
    SECTION_HEIGHT="${2:-1.5}"  # Default to 1.5m if not provided
    echo "Mode: Single storey at section height ${SECTION_HEIGHT}m"
    echo "Storey: $STOREY_NAME"
    OUTPUT_BASE="floorplan-$(echo "$STOREY_NAME" | tr ' +.,()' '_' | tr '[:upper:]' '[:lower:]' | sed 's/__*/_/g' | sed 's/_$//')"
fi

GEOMETRY_SVG="${OUTPUT_DIR}/${OUTPUT_BASE}-geometry.svg"
SPACES_SVG="${OUTPUT_DIR}/${OUTPUT_BASE}-spaces.svg"
COMBINED_SVG="${OUTPUT_DIR}/${OUTPUT_BASE}-combined.svg"
FINAL_SVG="${OUTPUT_DIR}/${OUTPUT_BASE}.svg"

echo ""
echo "Files:"
echo "  Geometry: $GEOMETRY_FILE"
echo "  Spaces:   $SPACES_FILE"
echo "  Output:   $FINAL_SVG"
echo ""

# Step 1: Calculate bounding box from geometry elements
echo "Step 1: Calculating bounding box from geometry elements..."
GEOMETRY_FILE_LOCAL="/home/bimbot-ubuntu/apps/ifcpipeline/shared${GEOMETRY_FILE}"

# Run Python script to calculate bounds
BOUNDS_OUTPUT=$(python3 /home/bimbot-ubuntu/apps/ifcpipeline/calculate-bounds.py \
  "$GEOMETRY_FILE_LOCAL" \
  "$SECTION_HEIGHT" \
  IfcWall IfcDoor IfcWindow IfcStair IfcRailing 2>&1)

# Parse bounds output (last line is the actual data)
BOUNDS_LINE=$(echo "$BOUNDS_OUTPUT" | tail -1)
read MIN_X MIN_Y MAX_X MAX_Y CENTER_X CENTER_Y WIDTH HEIGHT <<< "$BOUNDS_LINE"

echo "  Bounding box: (${MIN_X}, ${MIN_Y}) to (${MAX_X}, ${MAX_Y})"
echo "  Center: (${CENTER_X}, ${CENTER_Y})"
echo "  Size: ${WIDTH} x ${HEIGHT} meters"

# Calculate model offset to center the drawing
# Need to negate the center coordinates to center the model at origin
NEG_CENTER_X=$(echo "-1 * $CENTER_X" | bc -l)
NEG_CENTER_Y=$(echo "-1 * $CENTER_Y" | bc -l)
MODEL_OFFSET="${NEG_CENTER_X};${NEG_CENTER_Y};0"
echo "  Model offset: $MODEL_OFFSET"

# Calculate appropriate pixel bounds based on model size
# Use 20mm per meter (1:50 scale) and ensure bounds fit the model
PIXEL_WIDTH=$(echo "$WIDTH * 20 * 1.2" | bc -l | xargs printf "%.0f")
PIXEL_HEIGHT=$(echo "$HEIGHT * 20 * 1.2" | bc -l | xargs printf "%.0f")
# Round up to nearest 256 for cleaner numbers
PIXEL_WIDTH=$(( ((PIXEL_WIDTH + 255) / 256) * 256 ))
PIXEL_HEIGHT=$(( ((PIXEL_HEIGHT + 255) / 256) * 256 ))
BOUNDS="${PIXEL_WIDTH}x${PIXEL_HEIGHT}"
echo "  SVG bounds: $BOUNDS (derived from model size)"

# Step 2: Export geometry in RAW meter coordinates (NO --bounds, NO --scale)
echo "Step 2: Exporting geometry in raw meter coordinates..."
docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert \
  -y -j 8 -q \
  --log-format plain \
  --model \
  --section-height "$SECTION_HEIGHT" \
  --model-offset "$MODEL_OFFSET" \
  --include entities IfcWall IfcDoor IfcWindow IfcStair IfcRailing \
  --door-arcs \
  "$GEOMETRY_FILE" \
  "$GEOMETRY_SVG" 2>&1 | grep -E "(Done|Conversion took)" || true

# Step 3: Export spaces in RAW meter coordinates (NO --bounds, NO --scale)
echo "Step 3: Exporting spaces in raw meter coordinates..."
docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert \
  -y -j 8 -q \
  --log-format plain \
  --model \
  --section-height "$SECTION_HEIGHT" \
  --model-offset "$MODEL_OFFSET" \
  --include entities IfcSpace \
  --print-space-names \
  --print-space-areas \
  "$SPACES_FILE" \
  "$SPACES_SVG" 2>&1 | grep -E "(Done|Conversion took)" || true

echo "  ✓ Both exports in raw meter coordinates (will be scaled to 1:50)"

# Step 4: Scale coordinates and combine SVGs
echo "Step 4: Scaling coordinates to 1:50 scale and combining..."

GEOMETRY_LOCAL="/home/bimbot-ubuntu/apps/ifcpipeline/shared${GEOMETRY_SVG}"
SPACES_LOCAL="/home/bimbot-ubuntu/apps/ifcpipeline/shared${SPACES_SVG}"
COMBINED_LOCAL="/home/bimbot-ubuntu/apps/ifcpipeline/shared${COMBINED_SVG}"

python3 << COMBINE_SCRIPT
import xml.etree.ElementTree as ET
import re

geometry_file = "$GEOMETRY_LOCAL"
spaces_file = "$SPACES_LOCAL"
output_file = "$COMBINED_LOCAL"
SCALE_FACTOR = 20  # 1:50 scale: 1 meter = 20mm on drawing

# Register namespaces
ET.register_namespace('', 'http://www.w3.org/2000/svg')
ET.register_namespace('xlink', 'http://www.w3.org/1999/xlink')

def scale_coordinates(element, scale):
    """Scale all coordinates in an SVG element tree (meters → millimeters)"""
    
    # Scale path data
    if element.tag == '{http://www.w3.org/2000/svg}path':
        d = element.get('d', '')
        if d:
            # Scale all numeric values in path data
            def scale_number(match):
                return str(float(match.group(0)) * scale)
            d_scaled = re.sub(r'-?\d+\.?\d*', scale_number, d)
            element.set('d', d_scaled)
    
    # Scale text positions
    elif element.tag == '{http://www.w3.org/2000/svg}text':
        for attr in ['x', 'y']:
            val = element.get(attr)
            if val:
                element.set(attr, str(float(val) * scale))
    
    # Scale tspan positions
    elif element.tag == '{http://www.w3.org/2000/svg}tspan':
        for attr in ['x', 'y', 'dx', 'dy']:
            val = element.get(attr)
            if val:
                try:
                    element.set(attr, str(float(val) * scale))
                except ValueError:
                    pass
    
    # Scale line endpoints
    elif element.tag == '{http://www.w3.org/2000/svg}line':
        for attr in ['x1', 'y1', 'x2', 'y2']:
            val = element.get(attr)
            if val:
                element.set(attr, str(float(val) * scale))
    
    # Scale polyline/polygon points
    elif element.tag in ['{http://www.w3.org/2000/svg}polyline', '{http://www.w3.org/2000/svg}polygon']:
        points = element.get('points', '')
        if points:
            def scale_point(match):
                x, y = match.groups()
                return f'{float(x)*scale},{float(y)*scale}'
            points_scaled = re.sub(r'(-?\d+\.?\d*),(-?\d+\.?\d*)', scale_point, points)
            element.set('points', points_scaled)
    
    # Recursively process children
    for child in element:
        scale_coordinates(child, scale)

# Parse both SVGs
print("  Reading geometry SVG...")
geo_tree = ET.parse(geometry_file)
geo_root = geo_tree.getroot()

print("  Reading spaces SVG...")
space_tree = ET.parse(spaces_file)
space_root = space_tree.getroot()

# Scale coordinates for 1:50 scale
print(f"  Scaling geometry coordinates by {SCALE_FACTOR}x (1:50 scale)...")
scale_coordinates(geo_root, SCALE_FACTOR)

print(f"  Scaling spaces coordinates by {SCALE_FACTOR}x (1:50 scale)...")
scale_coordinates(space_root, SCALE_FACTOR)

# Create combined SVG based on geometry
combined_root = geo_root

# Find or create main group for content
ns = {'svg': 'http://www.w3.org/2000/svg'}
existing_groups = list(geo_root.findall('svg:g', ns))

# Extract all groups from spaces SVG and add to combined
space_groups = list(space_root.findall('svg:g', ns))
print(f"  Merging {len(space_groups)} space groups...")

for group in space_groups:
    combined_root.append(group)

# Add combined styles
print("  Merging stylesheets...")
geo_style = geo_root.find('svg:style', ns)
space_style = space_root.find('svg:style', ns)

if geo_style is not None and space_style is not None:
    # Combine CSS from both
    geo_css = geo_style.text if geo_style.text else ""
    space_css = space_style.text if space_style.text else ""
    
    # Remove duplicate CSS declarations
    combined_css = geo_css + "\n" + space_css
    geo_style.text = combined_css

# Calculate viewBox from scaled coordinates
print("  Calculating viewBox from scaled coordinates...")
all_coords = []
for elem in combined_root.iter():
    # Extract from path d attribute
    d = elem.get('d', '')
    if d:
        coords = re.findall(r'(-?\d+\.?\d*),(-?\d+\.?\d*)', d)
        all_coords.extend([(float(x), float(y)) for x, y in coords])
    # Extract from text/tspan x,y
    if elem.tag in ['{http://www.w3.org/2000/svg}text', '{http://www.w3.org/2000/svg}tspan']:
        x, y = elem.get('x'), elem.get('y')
        if x and y:
            try:
                all_coords.append((float(x), float(y)))
            except ValueError:
                pass

if all_coords:
    xs = [c[0] for c in all_coords]
    ys = [c[1] for c in all_coords]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    
    # Add 10% padding
    width = max_x - min_x
    height = max_y - min_y
    padding_x = width * 0.1
    padding_y = height * 0.1
    
    viewbox_x = min_x - padding_x
    viewbox_y = min_y - padding_y
    viewbox_w = width + 2 * padding_x
    viewbox_h = height + 2 * padding_y
    
    # Set viewBox (in mm at 1:50 scale)
    combined_root.set('viewBox', f'{viewbox_x:.2f} {viewbox_y:.2f} {viewbox_w:.2f} {viewbox_h:.2f}')
    
    # Set canvas dimensions (reasonable pixel size for display)
    # Target ~2000px for the larger dimension
    aspect = viewbox_w / viewbox_h
    if aspect >= 1:
        canvas_w = 2048
        canvas_h = int(2048 / aspect)
    else:
        canvas_h = 2048
        canvas_w = int(2048 * aspect)
    
    combined_root.set('width', str(canvas_w))
    combined_root.set('height', str(canvas_h))
    
    print(f"  ViewBox: {viewbox_x:.1f} {viewbox_y:.1f} {viewbox_w:.1f} {viewbox_h:.1f} mm (1:50 scale)")
    print(f"  Canvas: {canvas_w} × {canvas_h} px")

# Write combined SVG
print("  Writing combined SVG...")
combined_tree = ET.ElementTree(combined_root)
combined_tree.write(output_file, encoding='utf-8', xml_declaration=True)

print("  ✓ Combined SVG at 1:50 scale")
COMBINE_SCRIPT

# Step 5: Apply professional CSS styling
echo "Step 5: Applying architectural CSS styling..."
python3 /home/bimbot-ubuntu/apps/ifcpipeline/svg-style-rooms.py \
  "/home/bimbot-ubuntu/apps/ifcpipeline/shared${COMBINED_SVG}" \
  "/home/bimbot-ubuntu/apps/ifcpipeline/shared${FINAL_SVG}" | grep -E "(✓|Modified|Applied)"

# Clean up intermediate files
rm -f "/home/bimbot-ubuntu/apps/ifcpipeline/shared${GEOMETRY_SVG}"
rm -f "/home/bimbot-ubuntu/apps/ifcpipeline/shared${SPACES_SVG}"
rm -f "/home/bimbot-ubuntu/apps/ifcpipeline/shared${COMBINED_SVG}"

# Summary
echo ""
echo "========================================="
echo "✓✓ Complete Floor Plan Generated!"
echo "========================================="
echo ""

FINAL_FILE="/home/bimbot-ubuntu/apps/ifcpipeline/shared${FINAL_SVG}"
if [ -f "$FINAL_FILE" ]; then
    SIZE=$(stat -c%s "$FINAL_FILE")
    SIZE_KB=$((SIZE / 1024))
    PATHS=$(grep -c "<path\|<polyline\|<polygon" "$FINAL_FILE" 2>/dev/null || echo "0")
    TEXTS=$(grep -c "<text" "$FINAL_FILE" 2>/dev/null || echo "0")
    STOREYS=$(grep -c 'class="IfcBuildingStorey"' "$FINAL_FILE" 2>/dev/null || echo "0")
    
    echo "Output: $FINAL_SVG"
    echo "  Size: ${SIZE_KB}KB"
    echo "  Storeys: $STOREYS"
    echo "  Geometry elements: $PATHS"
    echo "  Room labels: $TEXTS"
    echo ""
    echo "Includes:"
    echo "  ✓ Walls, doors, windows, stairs, railings"
    echo "  ✓ Room boundaries and labels"
    echo "  ✓ Room names, numbers, and areas"
    echo "  ✓ Professional CSS styling (white text, uppercase)"
    echo "  ✓ Scale: 1:50"
    echo ""
    echo "View: firefox $FINAL_FILE"
else
    echo "Error: Output file not created"
    exit 1
fi

echo ""
echo "========================================="

