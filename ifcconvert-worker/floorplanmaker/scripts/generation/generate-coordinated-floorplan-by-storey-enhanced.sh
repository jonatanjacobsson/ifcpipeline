#!/bin/bash
# =============================================================================
# Generate Coordinated Floor Plans (All Disciplines) - By Storey Name (Enhanced)
# =============================================================================
# Enhanced version with more comprehensive entity includes
#
# Usage:
#   ./generate-coordinated-floorplan-by-storey-enhanced.sh "020 Mezzanine +5.40m"
#
# =============================================================================

set -e  # Exit on error

# Check arguments
if [ $# -lt 1 ]; then
    echo "Usage: $0 <storey_name>"
    echo ""
    echo "Example: $0 '020 Mezzanine +5.40m'"
    exit 1
fi

STOREY_NAME="$1"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "==========================================="
echo "COORDINATED FLOOR PLAN GENERATION (ENHANCED)"
echo "==========================================="
echo ""
echo "Storey: $STOREY_NAME"
echo "Selection method: Include elements by storey assignment"
echo "Entity includes: Enhanced (more comprehensive)"
echo ""

# Generate safe filename
STOREY_SAFE=$(echo "$STOREY_NAME" | tr '[:upper:]' '[:lower:]' | tr ' +.,' '_' | sed 's/__*/_/g' | sed 's/_$//')

# Output paths
TEMP_DIR="/output/converted/temp"
OUTPUT_DIR="/output/converted/floorplans"
OUTPUT_FILE="${OUTPUT_DIR}/coord_all_enhanced_${STOREY_SAFE}.svg"

# Temporary layer files
ARCH_SVG="${TEMP_DIR}/coord_arch_enh_${STOREY_SAFE}.svg"
STRUCT_SVG="${TEMP_DIR}/coord_struct_enh_${STOREY_SAFE}.svg"
PLUMB_SVG="${TEMP_DIR}/coord_plumb_enh_${STOREY_SAFE}.svg"
MECH_SVG="${TEMP_DIR}/coord_mech_enh_${STOREY_SAFE}.svg"
ELEC_SVG="${TEMP_DIR}/coord_elec_enh_${STOREY_SAFE}.svg"
SPACES_SVG="${TEMP_DIR}/coord_spaces_enh_${STOREY_SAFE}.svg"
FURNITURE_SVG="${TEMP_DIR}/coord_furniture_enh_${STOREY_SAFE}.svg"

# Step 1: Export all layers using storey-based selection with enhanced includes
echo "Step 1: Exporting layers by storey assignment (enhanced)..."

# Layer 1: Architecture (underlay) - ENHANCED
echo "  [1/7] Exporting architecture underlay..."
docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert \
  -y -j 8 -q \
  --model \
  --plan \
  --include+ attribute Name "$STOREY_NAME" \
  --door-arcs \
  --no-progress \
  --svg-no-css \
  --surface-colour \
  --wkt-use-section \
  /uploads/A1_2b_BIM_XXX_0001_00.v24.0.ifc \
  "$ARCH_SVG" 2>&1 | grep -E "(Done|Creating)" || true

# Layer 2: Structural - ENHANCED
echo "  [2/7] Exporting structural..."
docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert \
  -y -j 8 -q \
  --model \
  --include+ attribute Name "$STOREY_NAME" \
  --no-progress \
  --svg-no-css \
  /uploads/S2_2B_BIM_XXX_0001_00.v12.0.ifc \
  "$STRUCT_SVG" 2>&1 | grep -E "(Done|Creating)" || true

# Layer 3: Plumbing - ENHANCED
echo "  [3/7] Exporting plumbing..."
docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert \
  -y -j 8 -q \
  --model \
  --include+ attribute Name "$STOREY_NAME" \
  --no-progress \
  --svg-no-css \
  /uploads/P1_2b_BIM_XXX_5000_00.v12.0.ifc \
  "$PLUMB_SVG" 2>&1 | grep -E "(Done|Creating)" || true

# Layer 4: Mechanical - ENHANCED
echo "  [4/7] Exporting mechanical..."
docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert \
  -y -j 8 -q \
  --model \
  --plan \
  --include+ attribute Name "$STOREY_NAME" \
  --no-progress \
  --svg-no-css \
  /uploads/M1_2b_BIM_XXX_5700_00.v12.0.ifc \
  "$MECH_SVG" 2>&1 | grep -E "(Done|Creating)" || true

# Layer 5: Electrical - ENHANCED
echo "  [5/7] Exporting electrical..."
docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert \
  -y -j 8 -q \
  --model \
  --include+ attribute Name "$STOREY_NAME" \
  --no-progress \
  --svg-no-css \
  /uploads/E1_2b_BIM_XXX_600_00.v183.0.ifc \
  "$ELEC_SVG" 2>&1 | grep -E "(Done|Creating)" || true

# Layer 6: Furniture & Equipment - NEW
echo "  [6/7] Exporting furniture and equipment..."
docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert \
  -y -j 8 -q \
  --model \
  --include+ attribute Name "$STOREY_NAME" \
  --no-progress \
  --svg-no-css \
  /uploads/A1_2b_BIM_XXX_0001_00.v24.0.ifc \
  "$FURNITURE_SVG" 2>&1 | grep -E "(Done|Creating)" || true

# Layer 7: Spaces (labels)
echo "  [7/7] Exporting spaces..."
docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert \
  -y -j 8 -q \
  --model \
  --include+ attribute Name "$STOREY_NAME" \
  --print-space-names \
  --no-progress \
  --svg-no-css \
  /uploads/A1_2b_BIM_XXX_0003_00.ifc \
  "$SPACES_SVG" 2>&1 | grep -E "(Done|Creating)" || true

echo ""

# Step 2: Combine all layers with Python
echo "Step 2: Combining 7 layers with proper opacities and scaling..."

# Calculate CSS file path
CSS_FILE="$SCRIPT_DIR/../../config/styles/floorplan-styles.css"

ARCH_LOCAL="/home/bimbot-ubuntu/apps/ifcpipeline/shared${ARCH_SVG}"
STRUCT_LOCAL="/home/bimbot-ubuntu/apps/ifcpipeline/shared${STRUCT_SVG}"
PLUMB_LOCAL="/home/bimbot-ubuntu/apps/ifcpipeline/shared${PLUMB_SVG}"
MECH_LOCAL="/home/bimbot-ubuntu/apps/ifcpipeline/shared${MECH_SVG}"
ELEC_LOCAL="/home/bimbot-ubuntu/apps/ifcpipeline/shared${ELEC_SVG}"
FURNITURE_LOCAL="/home/bimbot-ubuntu/apps/ifcpipeline/shared${FURNITURE_SVG}"
SPACES_LOCAL="/home/bimbot-ubuntu/apps/ifcpipeline/shared${SPACES_SVG}"
OUTPUT_LOCAL="/home/bimbot-ubuntu/apps/ifcpipeline/shared${OUTPUT_FILE}"

python3 << COMBINE_SCRIPT
import xml.etree.ElementTree as ET
import re
import sys
import os

# Input files
ARCH_FILE = "$ARCH_LOCAL"
STRUCT_FILE = "$STRUCT_LOCAL"
PLUMB_FILE = "$PLUMB_LOCAL"
MECH_FILE = "$MECH_LOCAL"
ELEC_FILE = "$ELEC_LOCAL"
FURNITURE_FILE = "$FURNITURE_LOCAL"
SPACES_FILE = "$SPACES_LOCAL"
OUTPUT_FILE = "$OUTPUT_LOCAL"

SCALE_FACTOR = 20  # 1:50 scale

ET.register_namespace('', 'http://www.w3.org/2000/svg')
ET.register_namespace('xlink', 'http://www.w3.org/1999/xlink')

def scale_coordinates(element, scale):
    """Scale all coordinates in an SVG element, preserving arc flags"""
    # Scale path data
    if element.tag == '{http://www.w3.org/2000/svg}path':
        d = element.get('d', '')
        if d:
            # Split path into tokens
            tokens = re.findall(r'[A-Za-z]|[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?', d)
            
            scaled_tokens = []
            i = 0
            while i < len(tokens):
                token = tokens[i]
                
                # If it's an arc command, handle specially
                if token.upper() == 'A':
                    scaled_tokens.append(token)
                    i += 1
                    
                    # Arc has 7 parameters: rx ry x-axis-rotation large-arc-flag sweep-flag x y
                    # Scale: rx, ry, x, y (params 0, 1, 5, 6)
                    # Don't scale: x-axis-rotation, large-arc-flag, sweep-flag (params 2, 3, 4)
                    for param_idx in range(7):
                        if i < len(tokens):
                            param = tokens[i]
                            if param_idx in [0, 1, 5, 6]:  # Scale rx, ry, x, y
                                scaled_tokens.append(str(float(param) * scale))
                            elif param_idx in [3, 4]:  # Arc flags - keep as integers
                                # Ensure flags are integers 0 or 1
                                scaled_tokens.append(str(int(float(param))))
                            else:  # x-axis-rotation - don't scale
                                scaled_tokens.append(param)
                            i += 1
                else:
                    # Command letter - keep as is
                    if token.isalpha():
                        scaled_tokens.append(token)
                    # Number - scale it
                    else:
                        scaled_tokens.append(str(float(token) * scale))
                    i += 1
            
            # Reconstruct path with proper spacing
            d_scaled = ' '.join(scaled_tokens)
            # Clean up spacing around commas
            d_scaled = re.sub(r'\s*,\s*', ',', d_scaled)
            element.set('d', d_scaled)
    
    # Scale text/tspan positions
    elif element.tag in ['{http://www.w3.org/2000/svg}text', '{http://www.w3.org/2000/svg}tspan']:
        for attr in ['x', 'y', 'dx', 'dy']:
            val = element.get(attr)
            if val:
                try:
                    element.set(attr, str(float(val) * scale))
                except:
                    pass
    
    # Scale line elements
    elif element.tag == '{http://www.w3.org/2000/svg}line':
        for attr in ['x1', 'y1', 'x2', 'y2']:
            val = element.get(attr)
            if val:
                try:
                    element.set(attr, str(float(val) * scale))
                except:
                    pass
    
    # Scale polyline/polygon points
    elif element.tag in ['{http://www.w3.org/2000/svg}polyline', '{http://www.w3.org/2000/svg}polygon']:
        points = element.get('points', '')
        if points:
            def scale_point(match):
                x, y = match.groups()
                return f'{float(x)*scale},{float(y)*scale}'
            points_scaled = re.sub(r'(-?\d+\.?\d*)[,\s]+(-?\d+\.?\d*)', scale_point, points)
            element.set('points', points_scaled)
    
    # Recursively process children
    for child in element:
        scale_coordinates(child, scale)

def apply_layer_style(element, layer_name, opacity, stroke_color, stroke_width='0.5px'):
    """Apply styling to a layer's groups"""
    ns = {'svg': 'http://www.w3.org/2000/svg'}
    
    # Add class to all groups
    for group in element.findall('svg:g', ns):
        group.set('class', f'{layer_name}-layer')
        group.set('opacity', str(opacity))

try:
    print("  Loading layers...")
    layers = []
    
    # Load all 7 layers with metadata
    layer_config = [
        ('architecture', ARCH_FILE, 0.4, '#CCCCCC', '0.3px'),
        ('structural', STRUCT_FILE, 0.7, '#0066CC', '0.4px'),
        ('plumbing', PLUMB_FILE, 0.85, '#0099CC', '0.6px'),
        ('mechanical', MECH_FILE, 0.85, '#00CC66', '0.6px'),
        ('electrical', ELEC_FILE, 0.85, '#FF6600', '0.6px'),
        ('furniture', FURNITURE_FILE, 0.6, '#996633', '0.3px'),
        ('spaces', SPACES_FILE, 1.0, '#888888', '0.2px'),
    ]
    
    for name, file_path, opacity, color, width in layer_config:
        if os.path.exists(file_path):
            print(f"    Loading {name}...")
            tree = ET.parse(file_path)
            root = tree.getroot()
            
            # Scale coordinates
            scale_coordinates(root, SCALE_FACTOR)
            
            # Apply layer styling
            apply_layer_style(root, name, opacity, color, width)
            
            layers.append((name, root))
        else:
            print(f"    Warning: {name} file not found, skipping", file=sys.stderr)
    
    if not layers:
        print("Error: No layers loaded!", file=sys.stderr)
        sys.exit(1)
    
    # Create combined SVG from first layer
    print("  Merging layers...")
    combined_root = layers[0][1]
    
    ns = {'svg': 'http://www.w3.org/2000/svg'}
    
    # Merge all other layers into first
    for i in range(1, len(layers)):
        layer_name, layer_root = layers[i]
        
        # Copy all groups from this layer
        for group in layer_root.findall('svg:g', ns):
            combined_root.append(group)
    
    # Add coordinated view CSS from external file
    print("  Applying coordinated view CSS...")
    combined_style = combined_root.find('svg:style', ns)
    
    # Create style element if it doesn't exist
    if combined_style is None:
        print("    Creating new style element...")
        combined_style = ET.Element('{http://www.w3.org/2000/svg}style')
        combined_style.set('type', 'text/css')
        # Insert style element as first child after any existing defs
        defs = combined_root.find('svg:defs', ns)
        if defs is not None:
            # Insert after defs
            defs_index = list(combined_root).index(defs)
            combined_root.insert(defs_index + 1, combined_style)
        else:
            # Insert as first child
            combined_root.insert(0, combined_style)
    
    # Read CSS from external file
    css_file = "$CSS_FILE"
    coord_css = ""
    
    try:
        with open(css_file, 'r') as f:
            coord_css = f.read()
        print(f"    ✓ Loaded CSS from {css_file} ({len(coord_css)} chars)")
    except FileNotFoundError:
        print(f"    ✗ ERROR: CSS file {css_file} not found!", file=sys.stderr)
        sys.exit(1)
    
    # Apply CSS to style element
    if combined_style is not None:
        if combined_style.text:
            combined_style.text += '\n' + coord_css
        else:
            combined_style.text = coord_css
        print(f"    ✓ Applied CSS to style element")
    
    # Uppercase all text content
    for text_elem in combined_root.iter('{http://www.w3.org/2000/svg}text'):
        for tspan in text_elem.iter('{http://www.w3.org/2000/svg}tspan'):
            if tspan.text:
                tspan.text = tspan.text.upper()
    
    # Calculate viewBox from all scaled coordinates (excluding defs/markers)
    print("  Calculating viewBox from all layers...")
    all_coords = []
    
    # Only iterate through content groups (not defs)
    ns = {'svg': 'http://www.w3.org/2000/svg'}
    content_groups = combined_root.findall('svg:g', ns)
    
    for group in content_groups:
        for elem in group.iter():
            d = elem.get('d', '')
            if d:
                # Only extract coordinates from M (move) and L (line) commands, not A (arc) radii
                coords = re.findall(r'[ML]\s*(-?\d+\.?\d*),\s*(-?\d+\.?\d*)', d)
                all_coords.extend([(float(x), float(y)) for x, y in coords])
            
            # Text positions
            if elem.tag in ['{http://www.w3.org/2000/svg}text', '{http://www.w3.org/2000/svg}tspan']:
                x, y = elem.get('x'), elem.get('y')
                if x and y:
                    try:
                        all_coords.append((float(x), float(y)))
                    except:
                        pass
    
    if all_coords:
        xs = [c[0] for c in all_coords]
        ys = [c[1] for c in all_coords]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        
        width = max_x - min_x
        height = max_y - min_y
        padding_x = width * 0.1
        padding_y = height * 0.1
        
        viewbox_x = min_x - padding_x
        viewbox_y = min_y - padding_y
        viewbox_w = width + 2 * padding_x
        viewbox_h = height + 2 * padding_y
        
        combined_root.set('viewBox', f'{viewbox_x:.2f} {viewbox_y:.2f} {viewbox_w:.2f} {viewbox_h:.2f}')
        
        # Set canvas dimensions
        aspect = viewbox_w / viewbox_h
        if aspect >= 1:
            canvas_w = 2048
            canvas_h = int(2048 / aspect)
        else:
            canvas_h = 2048
            canvas_w = int(2048 * aspect)
        
        combined_root.set('width', str(canvas_w))
        combined_root.set('height', str(canvas_h))
        
        print(f"  ViewBox: {viewbox_x:.1f} {viewbox_y:.1f} {viewbox_w:.1f} {viewbox_h:.1f} mm")
        print(f"  Canvas: {canvas_w} × {canvas_h} px")
    
    print("  Writing combined SVG...")
    tree = ET.ElementTree(combined_root)
    tree.write(OUTPUT_FILE, encoding='utf-8', xml_declaration=True)
    
    print("  ✓ Combined successfully")

except Exception as e:
    print(f"  ✗ Error: {e}", file=sys.stderr)
    import traceback
    traceback.print_exc()
    sys.exit(1)
COMBINE_SCRIPT

echo ""
echo "==========================================="
echo "✓ Coordinated Floor Plan Generated (Enhanced)"
echo "==========================================="
echo ""
echo "Output: $OUTPUT_FILE"

if [ -f "/home/bimbot-ubuntu/apps/ifcpipeline/shared${OUTPUT_FILE}" ]; then
    SIZE=$(ls -lh "/home/bimbot-ubuntu/apps/ifcpipeline/shared${OUTPUT_FILE}" | awk '{print $5}')
    echo "  Size: $SIZE"
    echo "  Layers: 7 (Architecture, Structural, MEP, Furniture, Spaces)"
    echo "    - Selection method: By storey assignment"
    echo "    - Storey: $STOREY_NAME"
    echo "    - Entity includes: Enhanced/Comprehensive"
    echo "  Scale: 1:50"
    
    # Clean up intermediate files (optional)
    # rm -f "$ARCH_LOCAL" "$STRUCT_LOCAL" "$PLUMB_LOCAL" "$MECH_LOCAL" "$ELEC_LOCAL" "$FURNITURE_LOCAL" "$SPACES_LOCAL"
fi

echo ""

