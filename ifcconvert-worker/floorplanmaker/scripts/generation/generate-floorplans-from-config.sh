#!/bin/bash
# =============================================================================
# Floor Plan Generator from Configuration
# =============================================================================
# Generates multiple floor plan view templates from a YAML configuration file
#
# Usage: ./generate-floorplans-from-config.sh floorplan-config.yaml
# =============================================================================

set -e

if [ $# -lt 1 ]; then
    echo "Usage: $0 <config.yaml>"
    exit 1
fi

CONFIG_FILE="$1"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "Error: Config file not found: $CONFIG_FILE"
    exit 1
fi

echo "============================================================================="
echo " Floor Plan Generator from View Template Configuration"
echo "============================================================================="
echo ""
echo "Configuration: $CONFIG_FILE"
echo ""

# Install yq for YAML parsing if not present
if ! command -v yq &> /dev/null; then
    echo "Installing yq for YAML parsing..."
    wget -qO /usr/local/bin/yq https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64
    chmod +x /usr/local/bin/yq
fi

# Parse basic project info
PROJECT_NAME=$(yq '.project.name' "$CONFIG_FILE")
OUTPUT_DIR=$(yq '.project.output_dir' "$CONFIG_FILE")
SCALE=$(yq '.project.scale' "$CONFIG_FILE")

echo "Project: $PROJECT_NAME"
echo "Output: $OUTPUT_DIR"
echo "Scale: $SCALE"
echo ""

# Create output directory
mkdir -p "/home/bimbot-ubuntu/apps/ifcpipeline/shared${OUTPUT_DIR}"

# Create summary file
SUMMARY_FILE="/home/bimbot-ubuntu/apps/ifcpipeline/shared${OUTPUT_DIR}/generation_summary.md"
cat > "$SUMMARY_FILE" << SUMMARY
# Floor Plan Generation Summary

**Project:** $PROJECT_NAME
**Scale:** $SCALE
**Generated:** $(date)
**Configuration:** $CONFIG_FILE

---

## View Templates

SUMMARY

# Get storeys
STOREY_COUNT=$(yq '.storeys | length' "$CONFIG_FILE")
echo "Building Storeys: $STOREY_COUNT"

# Get view templates
VIEW_TEMPLATE_COUNT=$(yq '.view_templates | length' "$CONFIG_FILE")
echo "View Templates: $VIEW_TEMPLATE_COUNT"
echo ""
echo "============================================================================="
echo ""

# Iterate through view templates
for ((vt=0; vt<VIEW_TEMPLATE_COUNT; vt++)); do
    VT_NAME=$(yq ".view_templates[$vt].name" "$CONFIG_FILE")
    VT_DESC=$(yq ".view_templates[$vt].description" "$CONFIG_FILE")
    VT_PREFIX=$(yq ".view_templates[$vt].output_prefix" "$CONFIG_FILE")
    VT_ENABLED=$(yq ".view_templates[$vt].enabled" "$CONFIG_FILE")
    
    if [ "$VT_ENABLED" != "true" ]; then
        echo "⊘ Skipping view template: $VT_NAME (disabled)"
        continue
    fi
    
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "📋 View Template: $VT_NAME"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Description: $VT_DESC"
    echo "Output Prefix: $VT_PREFIX"
    echo ""
    
    # Add to summary
    cat >> "$SUMMARY_FILE" << VTSUM

### $VT_NAME
**Description:** $VT_DESC  
**Prefix:** \`$VT_PREFIX\`

VTSUM
    
    # Get layer count
    LAYER_COUNT=$(yq ".view_templates[$vt].layers | length" "$CONFIG_FILE")
    echo "Layers: $LAYER_COUNT"
    
    # List layers
    for ((layer=0; layer<LAYER_COUNT; layer++)); do
        LAYER_MODEL=$(yq ".view_templates[$vt].layers[$layer].model" "$CONFIG_FILE")
        LAYER_TYPE=$(yq ".view_templates[$vt].layers[$layer].layer_type // \"main\"" "$CONFIG_FILE")
        LAYER_ELEMENTS=$(yq ".view_templates[$vt].layers[$layer].elements | join(\", \")" "$CONFIG_FILE")
        
        echo "  ├─ Layer $((layer+1)): $LAYER_MODEL [$LAYER_TYPE]"
        echo "  │  Elements: $LAYER_ELEMENTS"
    done
    echo ""
    
    # Generate for each storey
    for ((st=0; st<STOREY_COUNT; st++)); do
        STOREY_NAME=$(yq ".storeys[$st].name" "$CONFIG_FILE")
        STOREY_ELEV=$(yq ".storeys[$st].elevation" "$CONFIG_FILE")
        SECTION_HEIGHT=$(yq ".storeys[$st].section_height" "$CONFIG_FILE")
        
        echo "  📐 Generating: $STOREY_NAME (elevation: ${STOREY_ELEV}m, section: ${SECTION_HEIGHT}m)"
        
        # Create sanitized storey name for filename
        STOREY_FILENAME=$(echo "$STOREY_NAME" | tr ' +.,()' '_' | tr '[:upper:]' '[:lower:]' | sed 's/__*/_/g' | sed 's/_$//')
        OUTPUT_FILENAME="${VT_PREFIX}_${STOREY_FILENAME}.svg"
        
        echo "     Output: $OUTPUT_FILENAME"
        echo ""
        
        # TODO: Call the multi-layer floor plan generator
        # For now, just create a placeholder
        echo "     [Generator would be called here]"
        echo ""
    done
    
    echo ""
done

echo "============================================================================="
echo "✓ Floor Plan Generation Complete"
echo "============================================================================="
echo ""
echo "Summary: $SUMMARY_FILE"
echo ""

