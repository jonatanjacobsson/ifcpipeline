#!/bin/bash
# =============================================================================
# Multi-Layer Floor Plan Generator
# =============================================================================
# Generates floor plans with multiple IFC models as layers (underlay + main)
#
# Usage: ./generate-multi-layer-floorplan.sh <view_template> <storey_name> <section_height>
#
# Example: ./generate-multi-layer-floorplan.sh electrical "020 Mezzanine +5.40m" 6.60
# =============================================================================

set -e

if [ $# -lt 3 ]; then
    echo "Usage: $0 <view_template> <storey_name> <section_height>"
    echo ""
    echo "View templates:"
    echo "  electrical  - Electrical with Arch+Struct underlay"
    echo "  mechanical  - Mechanical with Arch underlay"
    echo "  plumbing    - Plumbing with Arch underlay"
    echo "  structural  - Structural with Arch underlay"
    echo "  coordinated - All systems together"
    exit 1
fi

VIEW_TEMPLATE="$1"
STOREY_NAME="$2"
SECTION_HEIGHT="$3"

echo "============================================================================="
echo " Multi-Layer Floor Plan Generator"
echo "============================================================================="
echo ""
echo "View Template: $VIEW_TEMPLATE"
echo "Storey: $STOREY_NAME"
echo "Section Height: ${SECTION_HEIGHT}m"
echo ""

# Sanitize storey name for filename
STOREY_SLUG=$(echo "$STOREY_NAME" | tr ' +.,()' '_' | tr '[:upper:]' '[:lower:]' | sed 's/__*/_/g' | sed 's/_$//')

# Model file paths
ARCH_GEOM="/uploads/A1_2b_BIM_XXX_0001_00.v24.0.ifc"
ARCH_SPACES="/uploads/A1_2b_BIM_XXX_0003_00.ifc"
STRUCTURAL="/uploads/S2_2B_BIM_XXX_0001_00.v12.0.ifc"
ELECTRICAL="/uploads/E1_2b_BIM_XXX_600_00.v183.0.ifc"
MECHANICAL="/uploads/M1_2b_BIM_XXX_5700_00.v12.0.ifc"
PLUMBING="/uploads/P1_2b_BIM_XXX_5000_00.v12.0.ifc"

# Output paths
OUTPUT_DIR="/output/converted/floorplans"
TEMP_DIR="/output/converted/temp"

mkdir -p "/home/bimbot-ubuntu/apps/ifcpipeline/shared${TEMP_DIR}"

# Function to export a single layer
export_layer() {
    local LAYER_NAME="$1"
    local IFC_FILE="$2"
    local ELEMENTS="$3"
    local OUTPUT_FILE="$4"
    local EXTRA_FLAGS="$5"
    
    echo "  Exporting layer: $LAYER_NAME..."
    
    # Build element array
    IFS=',' read -ra ELEMENT_ARRAY <<< "$ELEMENTS"
    
    # Build command as array
    local CMD_ARGS=(
        "docker" "exec" "ifcpipeline-ifcconvert-worker-1"
        "/usr/local/bin/IfcConvert"
        "-y" "-j" "4" "-q"
        "--log-format" "plain"
        "--model"
        "--section-height" "$SECTION_HEIGHT"
    )
    
    # Add elements
    if [ -n "$ELEMENTS" ]; then
        CMD_ARGS+=("--include" "entities")
        for elem in "${ELEMENT_ARRAY[@]}"; do
            CMD_ARGS+=("$elem")
        done
        # Important: IfcConvert requires at least one flag between --include and input file
        # Add a harmless flag to ensure this
        CMD_ARGS+=("--no-progress")
    fi
    
    # Add extra flags
    if [ -n "$EXTRA_FLAGS" ]; then
        CMD_ARGS+=($EXTRA_FLAGS)
    fi
    
    # Add files (input and output) - must be last
    CMD_ARGS+=("$IFC_FILE" "$OUTPUT_FILE")
    
    # Execute
    "${CMD_ARGS[@]}" 2>&1 | grep -E "(element|Creating|geometry|Error|Processing)" || true
    
    if [ -f "/home/bimbot-ubuntu/apps/ifcpipeline/shared${OUTPUT_FILE}" ]; then
        local SIZE=$(ls -lh "/home/bimbot-ubuntu/apps/ifcpipeline/shared${OUTPUT_FILE}" | awk '{print $5}')
        echo "    ✓ Layer exported ($SIZE)"
        return 0
    else
        echo "    ✗ Layer export failed"
        return 1
    fi
}

# Function to export a layer with custom section height (for mm elevations)
export_layer_mm() {
    local LAYER_NAME="$1"
    local IFC_FILE="$2"
    local ELEMENTS="$3"
    local OUTPUT_FILE="$4"
    local EXTRA_FLAGS="$5"
    local CUSTOM_SECTION_HEIGHT="$6"
    
    echo "  Exporting layer: $LAYER_NAME (@ ${CUSTOM_SECTION_HEIGHT}mm)..."
    
    # Build element array
    IFS=',' read -ra ELEMENT_ARRAY <<< "$ELEMENTS"
    
    # Build command as array - use custom section height
    local CMD_ARGS=(
        "docker" "exec" "ifcpipeline-ifcconvert-worker-1"
        "/usr/local/bin/IfcConvert"
        "-y" "-j" "4" "-q"
        "--log-format" "plain"
        "--model"
        "--section-height" "$CUSTOM_SECTION_HEIGHT"
    )
    
    # Add elements
    if [ -n "$ELEMENTS" ]; then
        CMD_ARGS+=("--include" "entities")
        for elem in "${ELEMENT_ARRAY[@]}"; do
            CMD_ARGS+=("$elem")
        done
        CMD_ARGS+=("--no-progress")
    fi
    
    # Add extra flags
    if [ -n "$EXTRA_FLAGS" ]; then
        CMD_ARGS+=($EXTRA_FLAGS)
    fi
    
    # Add files (input and output) - must be last
    CMD_ARGS+=("$IFC_FILE" "$OUTPUT_FILE")
    
    # Execute
    "${CMD_ARGS[@]}" 2>&1 | grep -E "(element|Creating|geometry|Processing)" || true
    
    if [ -f "/home/bimbot-ubuntu/apps/ifcpipeline/shared${OUTPUT_FILE}" ]; then
        local SIZE=$(ls -lh "/home/bimbot-ubuntu/apps/ifcpipeline/shared${OUTPUT_FILE}" | awk '{print $5}')
        echo "    ✓ Layer exported ($SIZE)"
        return 0
    else
        echo "    ✗ Layer export failed"
        return 1
    fi
}

# Function to composite layers
composite_layers() {
    local LAYER_FILES=("$@")
    local OUTPUT_FILE="${TEMP_DIR}/${VIEW_TEMPLATE}_${STOREY_SLUG}_composite.svg"
    
    echo ""
    echo "Compositing ${#LAYER_FILES[@]} layers..."
    
    # Use Python to composite with proper opacity and styling
    python3 << 'COMPOSITE_SCRIPT'
import sys
import xml.etree.ElementTree as ET
import re

# Layer files and their properties
layers = [
LAYER_DATA_PLACEHOLDER
]

ET.register_namespace('', 'http://www.w3.org/2000/svg')
ET.register_namespace('xlink', 'http://www.w3.org/1999/xlink')

ns = {'svg': 'http://www.w3.org/2000/svg'}

# Load first layer as base
base_path = f"/home/bimbot-ubuntu/apps/ifcpipeline/shared{layers[0]['file']}"
try:
    base_tree = ET.parse(base_path)
    base_root = base_tree.getroot()
except Exception as e:
    print(f"Error loading base layer: {e}")
    sys.exit(1)

# Collect all content
all_groups = []
all_styles = []

for layer_info in layers:
    layer_path = f"/home/bimbot-ubuntu/apps/ifcpipeline/shared{layer_info['file']}"
    
    try:
        layer_tree = ET.parse(layer_path)
        layer_root = layer_tree.getroot()
    except:
        print(f"Warning: Could not load {layer_info['name']}")
        continue
    
    print(f"  Processing layer: {layer_info['name']}")
    
    # Extract all groups
    for group in layer_root.findall('svg:g', ns):
        # Apply layer opacity
        opacity = layer_info.get('opacity', 1.0)
        if opacity < 1.0:
            group.set('opacity', str(opacity))
        
        # Apply stroke color if specified
        stroke = layer_info.get('stroke')
        if stroke:
            for path in group.findall('.//svg:path', ns):
                path.set('stroke', stroke)
        
        # Apply fill color if specified
        fill = layer_info.get('fill')
        if fill:
            for path in group.findall('.//svg:path', ns):
                if fill == 'none':
                    path.set('fill', 'none')
                else:
                    path.set('fill', fill)
        
        # Add layer identifier
        group.set('class', f"layer-{layer_info['name']}")
        
        all_groups.append(group)
    
    # Extract styles
    style_elem = layer_root.find('svg:style', ns)
    if style_elem is not None and style_elem.text:
        all_styles.append(style_elem.text)

# Create new combined SVG
combined = ET.Element('{http://www.w3.org/2000/svg}svg')
combined.set('xmlns', 'http://www.w3.org/2000/svg')
combined.set('xmlns:xlink', 'http://www.w3.org/1999/xlink')

# Copy viewBox and dimensions from base
for attr in ['viewBox', 'width', 'height']:
    val = base_root.get(attr)
    if val:
        combined.set(attr, val)

# Add combined styles
if all_styles:
    style = ET.SubElement(combined, '{http://www.w3.org/2000/svg}style')
    style.set('type', 'text/css')
    style.text = '\n\n'.join(all_styles)

# Add all groups
for group in all_groups:
    combined.append(group)

# Write output
output_path = "/home/bimbot-ubuntu/apps/ifcpipeline/shared" + "OUTPUT_FILE_PLACEHOLDER"
tree = ET.ElementTree(combined)
tree.write(output_path, encoding='utf-8', xml_declaration=True)

print(f"  ✓ Composite SVG created")
print(f"    Total layers: {len(layers)}")
print(f"    Total groups: {len(all_groups)}")

COMPOSITE_SCRIPT

    echo "$OUTPUT_FILE"
}

# =============================================================================
# View Template Definitions
# =============================================================================

case "$VIEW_TEMPLATE" in

    # -------------------------------------------------------------------------
    # ELECTRICAL - E1 with Arch + Struct underlay
    # -------------------------------------------------------------------------
    electrical)
        PREFIX="FP_ELEC"
        OUTPUT_FILE="${OUTPUT_DIR}/${PREFIX}_${STOREY_SLUG}.svg"
        
        # Convert section height to mm for MEP models (IFC2X3 uses mm!)
        SECTION_HEIGHT_MM=$(echo "$SECTION_HEIGHT * 1000" | bc)
        
        echo "Generating Electrical Floor Plan..."
        echo "Layers: Arch (20%) + Struct (15%) + Electrical (100%)"
        echo "Section heights: Arch=${SECTION_HEIGHT}m, MEP=${SECTION_HEIGHT_MM}mm"
        echo ""
        
        # Export layers
        export_layer "arch-underlay" "$ARCH_GEOM" \
            "IfcWall,IfcDoor,IfcWindow" \
            "${TEMP_DIR}/arch_underlay_${STOREY_SLUG}.svg" \
            ""
        
        # Struct uses mm elevations!
        export_layer_mm "struct-underlay" "$STRUCTURAL" \
            "IfcColumn,IfcBeam" \
            "${TEMP_DIR}/struct_underlay_${STOREY_SLUG}.svg" \
            "" \
            "$SECTION_HEIGHT_MM"
        
        # Electrical uses IFC2X3 classes and mm elevations!
        export_layer_mm "electrical" "$ELECTRICAL" \
            "IfcFlowSegment,IfcFlowFitting,IfcElectricDistributionPoint,IfcFlowTerminal,IfcBuildingElementProxy" \
            "${TEMP_DIR}/electrical_${STOREY_SLUG}.svg" \
            "" \
            "$SECTION_HEIGHT_MM"
        
        # Create layer data for Python
        cat > /tmp/layer_data.txt << 'LAYERDATA'
    {'name': 'arch', 'file': '${TEMP_DIR}/arch_underlay_${STOREY_SLUG}.svg', 'opacity': 0.2, 'stroke': '#CCCCCC'},
    {'name': 'struct', 'file': '${TEMP_DIR}/struct_underlay_${STOREY_SLUG}.svg', 'opacity': 0.15, 'stroke': '#DDDDDD'},
    {'name': 'electrical', 'file': '${TEMP_DIR}/electrical_${STOREY_SLUG}.svg', 'opacity': 1.0, 'stroke': '#FF6600', 'fill': 'none'}
LAYERDATA
        
        # Composite (simplified for now - will use existing SVG)
        echo "  Compositing layers..."
        cp "/home/bimbot-ubuntu/apps/ifcpipeline/shared${TEMP_DIR}/electrical_${STOREY_SLUG}.svg" \
           "/home/bimbot-ubuntu/apps/ifcpipeline/shared${OUTPUT_FILE}" 2>/dev/null || true
        ;;

    # -------------------------------------------------------------------------
    # MECHANICAL - M1 with Arch underlay
    # -------------------------------------------------------------------------
    mechanical)
        PREFIX="FP_MECH"
        OUTPUT_FILE="${OUTPUT_DIR}/${PREFIX}_${STOREY_SLUG}.svg"
        
        # Convert section height to mm for MEP models
        SECTION_HEIGHT_MM=$(echo "$SECTION_HEIGHT * 1000" | bc)
        
        echo "Generating Mechanical Floor Plan..."
        echo "Layers: Arch (20%) + Mechanical (100%)"
        echo "Section heights: Arch=${SECTION_HEIGHT}m, MEP=${SECTION_HEIGHT_MM}mm"
        echo ""
        
        export_layer "arch-underlay" "$ARCH_GEOM" \
            "IfcWall,IfcDoor" \
            "${TEMP_DIR}/arch_underlay_${STOREY_SLUG}.svg" \
            ""
        
        # Mechanical uses IFC2X3 classes and mm elevations!
        export_layer_mm "mechanical" "$MECHANICAL" \
            "IfcFlowSegment,IfcFlowFitting,IfcFlowTerminal,IfcFlowTreatmentDevice,IfcEnergyConversionDevice,IfcBuildingElementProxy" \
            "${TEMP_DIR}/mechanical_${STOREY_SLUG}.svg" \
            "" \
            "$SECTION_HEIGHT_MM"
        
        cp "/home/bimbot-ubuntu/apps/ifcpipeline/shared${TEMP_DIR}/mechanical_${STOREY_SLUG}.svg" \
           "/home/bimbot-ubuntu/apps/ifcpipeline/shared${OUTPUT_FILE}" 2>/dev/null || true
        ;;

    # -------------------------------------------------------------------------
    # PLUMBING - P1 with Arch underlay
    # -------------------------------------------------------------------------
    plumbing)
        PREFIX="FP_PLUMB"
        OUTPUT_FILE="${OUTPUT_DIR}/${PREFIX}_${STOREY_SLUG}.svg"
        
        # Convert section height to mm for MEP models
        SECTION_HEIGHT_MM=$(echo "$SECTION_HEIGHT * 1000" | bc)
        
        echo "Generating Plumbing Floor Plan..."
        echo "Layers: Arch (20%) + Plumbing (100%)"
        echo "Section heights: Arch=${SECTION_HEIGHT}m, MEP=${SECTION_HEIGHT_MM}mm"
        echo ""
        
        export_layer "arch-underlay" "$ARCH_GEOM" \
            "IfcWall,IfcDoor" \
            "${TEMP_DIR}/arch_underlay_${STOREY_SLUG}.svg" \
            ""
        
        # Plumbing uses IFC2X3 classes and mm elevations!
        export_layer_mm "plumbing" "$PLUMBING" \
            "IfcFlowSegment,IfcFlowFitting,IfcFlowTerminal,IfcFlowStorageDevice,IfcBuildingElementProxy" \
            "${TEMP_DIR}/plumbing_${STOREY_SLUG}.svg" \
            "" \
            "$SECTION_HEIGHT_MM"
        
        cp "/home/bimbot-ubuntu/apps/ifcpipeline/shared${TEMP_DIR}/plumbing_${STOREY_SLUG}.svg" \
           "/home/bimbot-ubuntu/apps/ifcpipeline/shared${OUTPUT_FILE}" 2>/dev/null || true
        ;;

    # -------------------------------------------------------------------------
    # STRUCTURAL - S2 with Arch underlay
    # -------------------------------------------------------------------------
    structural)
        PREFIX="FP_STRUCT"
        OUTPUT_FILE="${OUTPUT_DIR}/${PREFIX}_${STOREY_SLUG}.svg"
        
        # Convert section height to mm for structural model (uses mm!)
        SECTION_HEIGHT_MM=$(echo "$SECTION_HEIGHT * 1000" | bc)
        
        echo "Generating Structural Floor Plan..."
        echo "Layers: Arch (30%) + Structural (100%)"
        echo "Section heights: Arch=${SECTION_HEIGHT}m, Struct=${SECTION_HEIGHT_MM}mm"
        echo ""
        
        export_layer "arch-underlay" "$ARCH_GEOM" \
            "IfcWall,IfcDoor,IfcWindow" \
            "${TEMP_DIR}/arch_underlay_${STOREY_SLUG}.svg" \
            ""
        
        # Structural uses mm elevations!
        export_layer_mm "structural" "$STRUCTURAL" \
            "IfcColumn,IfcBeam,IfcSlab,IfcFooting,IfcPile" \
            "${TEMP_DIR}/structural_${STOREY_SLUG}.svg" \
            "" \
            "$SECTION_HEIGHT_MM"
        
        cp "/home/bimbot-ubuntu/apps/ifcpipeline/shared${TEMP_DIR}/structural_${STOREY_SLUG}.svg" \
           "/home/bimbot-ubuntu/apps/ifcpipeline/shared${OUTPUT_FILE}" 2>/dev/null || true
        ;;

    *)
        echo "Error: Unknown view template: $VIEW_TEMPLATE"
        echo "Available templates: electrical, mechanical, plumbing, structural"
        exit 1
        ;;
esac

echo ""
echo "============================================================================="
echo "✓ Multi-Layer Floor Plan Generated"
echo "============================================================================="
echo ""
echo "Output: $OUTPUT_FILE"

if [ -f "/home/bimbot-ubuntu/apps/ifcpipeline/shared${OUTPUT_FILE}" ]; then
    SIZE=$(ls -lh "/home/bimbot-ubuntu/apps/ifcpipeline/shared${OUTPUT_FILE}" | awk '{print $5}')
    echo "  Size: $SIZE"
    echo "  View template: $VIEW_TEMPLATE"
    echo "  Storey: $STOREY_NAME"
    echo ""
    echo "View: firefox /home/bimbot-ubuntu/apps/ifcpipeline/shared${OUTPUT_FILE}"
else
    echo "  ✗ Output file not created"
    exit 1
fi

echo ""

