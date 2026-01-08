#!/bin/bash
# =============================================================================
# MEP Floor Plan Generator (Following complete pattern)
# =============================================================================
# Generates MEP floor plans with architectural underlay using the same
# workflow as svg-floorplan-complete.sh
#
# Usage: ./generate-mep-floorplan.sh <discipline> <storey_name> <section_height>
# =============================================================================

set -e

if [ $# -lt 3 ]; then
    echo "Usage: $0 <discipline> <storey_name> <section_height_meters>"
    echo ""
    echo "Disciplines: electrical, mechanical, plumbing, structural"
    exit 1
fi

DISCIPLINE="$1"
STOREY_NAME="$2"
SECTION_HEIGHT_M="$3"

# Convert to mm for MEP models
SECTION_HEIGHT_MM=$(echo "$SECTION_HEIGHT_M * 1000" | bc)

echo "==========================================="
echo " MEP Floor Plan Generator"
echo "==========================================="
echo ""
echo "Discipline: $DISCIPLINE"
echo "Storey: $STOREY_NAME"
echo "Section Height: ${SECTION_HEIGHT_M}m (MEP: ${SECTION_HEIGHT_MM}mm)"
echo ""

# Model paths
ARCH_GEOM="/uploads/A1_2b_BIM_XXX_0001_00.v24.0.ifc"
ELECTRICAL="/uploads/E1_2b_BIM_XXX_600_00.v183.0.ifc"
MECHANICAL="/uploads/M1_2b_BIM_XXX_5700_00.v12.0.ifc"
PLUMBING="/uploads/P1_2b_BIM_XXX_5000_00.v12.0.ifc"
STRUCTURAL="/uploads/S2_2B_BIM_XXX_0001_00.v12.0.ifc"

# Sanitize storey name
STOREY_SLUG=$(echo "$STOREY_NAME" | tr ' +.,()' '_' | tr '[:upper:]' '[:lower:]' | sed 's/__*/_/g' | sed 's/_$//')

# Output paths
OUTPUT_DIR="/output/converted/floorplans"
OUTPUT_FILE="${OUTPUT_DIR}/${DISCIPLINE}_${STOREY_SLUG}.svg"

TEMP_DIR="/output/converted/temp"
ARCH_SVG="${TEMP_DIR}/arch_${STOREY_SLUG}.svg"
MEP_SVG="${TEMP_DIR}/mep_${DISCIPLINE}_${STOREY_SLUG}.svg"
COMBINED_SVG="${TEMP_DIR}/combined_${DISCIPLINE}_${STOREY_SLUG}.svg"

mkdir -p "/home/bimbot-ubuntu/apps/ifcpipeline/shared${OUTPUT_DIR}"
mkdir -p "/home/bimbot-ubuntu/apps/ifcpipeline/shared${TEMP_DIR}"

# Select MEP model and elements based on discipline
case "$DISCIPLINE" in
    electrical)
        MEP_FILE="$ELECTRICAL"
        # IFC2X3 classes for electrical
        MEP_ELEMENTS="IfcFlowSegment IfcFlowFitting IfcElectricDistributionPoint IfcFlowTerminal"
        COLOR="#FF6600"
        ;;
    mechanical)
        MEP_FILE="$MECHANICAL"
        # IFC2X3 classes for mechanical
        MEP_ELEMENTS="IfcFlowSegment IfcFlowFitting IfcFlowTerminal IfcFlowTreatmentDevice IfcEnergyConversionDevice"
        COLOR="#0099FF"
        ;;
    plumbing)
        MEP_FILE="$PLUMBING"
        # IFC2X3 classes for plumbing
        MEP_ELEMENTS="IfcFlowSegment IfcFlowFitting IfcFlowTerminal IfcFlowStorageDevice"
        COLOR="#00CC66"
        ;;
    structural)
        MEP_FILE="$STRUCTURAL"
        MEP_ELEMENTS="IfcColumn IfcBeam IfcSlab IfcFooting IfcPile"
        COLOR="#0066CC"
        ;;
    *)
        echo "Error: Unknown discipline"
        exit 1
        ;;
esac

echo "Files:"
echo "  Architecture: $ARCH_GEOM"
echo "  MEP/Struct:   $MEP_FILE"
echo "  Output:       $OUTPUT_FILE"
echo ""

# Step 1: Calculate bounds from architecture (MEP will use same model-offset)
echo "Step 1: Calculating bounding box from architecture..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CALC_BOUNDS="$SCRIPT_DIR/../utilities/calculate-bounds.py"

BOUNDS_OUTPUT=$(python3 "$CALC_BOUNDS" \
    "/home/bimbot-ubuntu/apps/ifcpipeline/shared${ARCH_GEOM}" \
    "$SECTION_HEIGHT_M" \
    IfcWall IfcDoor IfcWindow IfcStair IfcColumn IfcBeam 2>&1 | tail -1)

read MIN_X MIN_Y MAX_X MAX_Y CENTER_X CENTER_Y WIDTH HEIGHT <<< "$BOUNDS_OUTPUT"

MODEL_OFFSET="-${CENTER_X};-${CENTER_Y};0"
BOUNDS="${WIDTH%.*}x${HEIGHT%.*}"

echo "  Center: ($CENTER_X, $CENTER_Y)"
echo "  Model offset: $MODEL_OFFSET"
echo ""

# Step 2: Export architecture underlay (NO model-offset!)
echo "Step 2: Exporting architectural underlay..."
docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert \
  -y -j 8 -q \
  --model \
  --section-height "$SECTION_HEIGHT_M" \
  --include entities IfcWall IfcDoor IfcWindow \
  --no-progress \
  "$ARCH_GEOM" \
  "$ARCH_SVG" 2>&1 | grep -E "(Done|Conversion took)" || true

echo "  Note: Using raw coordinates for federated BIM coordinate system"

# Step 3: Export MEP layer (NO model-offset - use raw coordinates!)
echo "Step 3: Exporting $DISCIPLINE layer..."
docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert \
  -y -j 8 -q \
  --model \
  --section-height "$SECTION_HEIGHT_M" \
  --include entities $MEP_ELEMENTS \
  --no-progress \
  "$MEP_FILE" \
  "$MEP_SVG" 2>&1 | grep -E "(Done|Conversion took|Creating)" || true

echo "  Note: Using section height ${SECTION_HEIGHT_M}m (geometry uses meters, not mm)"

# Step 4: Scale coordinates and combine
echo "Step 4: Scaling coordinates to 1:50 and combining..."

ARCH_LOCAL="/home/bimbot-ubuntu/apps/ifcpipeline/shared${ARCH_SVG}"
MEP_LOCAL="/home/bimbot-ubuntu/apps/ifcpipeline/shared${MEP_SVG}"
OUTPUT_LOCAL="/home/bimbot-ubuntu/apps/ifcpipeline/shared${OUTPUT_FILE}"

python3 << COMBINE_SCRIPT
import xml.etree.ElementTree as ET
import re
import sys

ARCH_FILE = "$ARCH_LOCAL"
MEP_FILE = "$MEP_LOCAL"
OUTPUT_FILE = "$OUTPUT_LOCAL"
SCALE_FACTOR = 20  # 1:50 scale
COLOR = "$COLOR"
DISCIPLINE = "$DISCIPLINE"

ET.register_namespace('', 'http://www.w3.org/2000/svg')
ET.register_namespace('xlink', 'http://www.w3.org/1999/xlink')

def scale_coordinates(element, scale):
    """Scale all coordinates in an SVG element"""
    # Scale path data
    if element.tag == '{http://www.w3.org/2000/svg}path':
        d = element.get('d', '')
        if d:
            def scale_number(match):
                return str(float(match.group(0)) * scale)
            d_scaled = re.sub(r'-?\d+\.?\d*', scale_number, d)
            element.set('d', d_scaled)
    
    # Scale text/tspan positions
    elif element.tag in ['{http://www.w3.org/2000/svg}text', '{http://www.w3.org/2000/svg}tspan']:
        for attr in ['x', 'y']:
            val = element.get(attr)
            if val:
                try:
                    element.set(attr, str(float(val) * scale))
                except:
                    pass
    
    # Recursively process children
    for child in element:
        scale_coordinates(child, scale)

try:
    # Load architecture
    print("  Reading architecture SVG...")
    arch_tree = ET.parse(ARCH_FILE)
    arch_root = arch_tree.getroot()
    
    # Load MEP
    print("  Reading MEP SVG...")
    mep_tree = ET.parse(MEP_FILE)
    mep_root = mep_tree.getroot()
    
    # Scale both
    print(f"  Scaling coordinates by {SCALE_FACTOR}x (1:50)...")
    scale_coordinates(arch_root, SCALE_FACTOR)
    scale_coordinates(mep_root, SCALE_FACTOR)
    
    # Create combined SVG
    combined = arch_root  # Use arch as base
    
    ns = {'svg': 'http://www.w3.org/2000/svg'}
    
    # Add MEP groups with color styling
    print(f"  Merging {DISCIPLINE} layer...")
    for group in mep_root.findall('svg:g', ns):
        # Apply discipline color to all paths
        for path in group.findall('.//svg:path', ns):
            path.set('stroke', COLOR)
            path.set('stroke-width', '1.5px')
        
        # Mark as MEP layer
        group.set('class', f'{DISCIPLINE}-layer')
        combined.append(group)
    
    # Merge styles
    arch_style = arch_root.find('svg:style', ns)
    mep_style = mep_root.find('svg:style', ns)
    
    if arch_style is not None and mep_style is not None and mep_style.text:
        arch_style.text += '\n' + mep_style.text
    
    # Add MEP-specific CSS
    mep_css = f"""
        .{DISCIPLINE}-layer path {{
            stroke: {COLOR} !important;
            stroke-width: 1.5px;
            fill: none;
        }}
        
        /* Underlay styling */
        .cut path {{
            opacity: 0.3;
        }}
    """
    
    if arch_style is not None:
        arch_style.text += '\n' + mep_css
    
    # Calculate viewBox from scaled coordinates
    print("  Calculating viewBox...")
    all_coords = []
    for elem in combined.iter():
        d = elem.get('d', '')
        if d:
            coords = re.findall(r'(-?\d+\.?\d*),(-?\d+\.?\d*)', d)
            all_coords.extend([(float(x), float(y)) for x, y in coords])
    
    if all_coords:
        xs = [c[0] for c in all_coords]
        ys = [c[1] for c in all_coords]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        
        # Add padding
        width = max_x - min_x
        height = max_y - min_y
        padding_x = width * 0.1
        padding_y = height * 0.1
        
        viewbox_x = min_x - padding_x
        viewbox_y = min_y - padding_y
        viewbox_w = width + 2 * padding_x
        viewbox_h = height + 2 * padding_y
        
        combined.set('viewBox', f'{viewbox_x:.2f} {viewbox_y:.2f} {viewbox_w:.2f} {viewbox_h:.2f}')
        
        # Set canvas size
        aspect = viewbox_w / viewbox_h
        if aspect >= 1:
            canvas_w = 2048
            canvas_h = int(2048 / aspect)
        else:
            canvas_h = 2048
            canvas_w = int(2048 * aspect)
        
        combined.set('width', str(canvas_w))
        combined.set('height', str(canvas_h))
        
        print(f"  ViewBox: {viewbox_x:.1f} {viewbox_y:.1f} {viewbox_w:.1f} {viewbox_h:.1f} mm")
        print(f"  Canvas: {canvas_w} × {canvas_h} px")
    
    # Write output
    print("  Writing combined SVG...")
    tree = ET.ElementTree(combined)
    tree.write(OUTPUT_FILE, encoding='utf-8', xml_declaration=True)
    
    print("  ✓ Combined and scaled successfully")

except Exception as e:
    print(f"  ✗ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

COMBINE_SCRIPT

echo ""
echo "==========================================="
echo "✓ MEP Floor Plan Generated"
echo "==========================================="
echo ""
echo "Output: $OUTPUT_FILE"

if [ -f "/home/bimbot-ubuntu/apps/ifcpipeline/shared${OUTPUT_FILE}" ]; then
    SIZE=$(ls -lh "/home/bimbot-ubuntu/apps/ifcpipeline/shared${OUTPUT_FILE}" | awk '{print $5}')
    echo "  Size: $SIZE"
    echo "  Discipline: $DISCIPLINE"
    echo "  Scale: 1:50"
fi

echo ""
