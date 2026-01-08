#!/bin/bash

# Advanced IfcConvert SVG Test Script
# Demonstrates various SVG export options

echo "========================================"
echo "IfcConvert SVG Export - Advanced Test"
echo "========================================"
echo ""

# Configuration
INPUT_FILE="/uploads/Building-Architecture.ifc"
OUTPUT_DIR="/output/converted"

# Function to run conversion with specific settings
run_conversion() {
    local VARIANT_NAME=$1
    local OUTPUT_FILE="${OUTPUT_DIR}/Building-Architecture-${VARIANT_NAME}.svg"
    local LOG_FILE="${OUTPUT_DIR}/Building-Architecture-${VARIANT_NAME}.log"
    
    shift  # Remove first argument
    local EXTRA_ARGS="$@"
    
    echo "----------------------------------------"
    echo "Generating: $VARIANT_NAME"
    echo "Arguments: $EXTRA_ARGS"
    echo "----------------------------------------"
    
    docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert \
        -y \
        --log-format plain \
        --log-file "$LOG_FILE" \
        $EXTRA_ARGS \
        "$INPUT_FILE" \
        "$OUTPUT_FILE"
    
    if [ $? -eq 0 ]; then
        SIZE=$(docker exec ifcpipeline-ifcconvert-worker-1 stat -c%s "$OUTPUT_FILE")
        echo "✓ Success! Size: $SIZE bytes"
        echo "  Output: $OUTPUT_FILE"
    else
        echo "✗ Failed!"
        docker exec ifcpipeline-ifcconvert-worker-1 cat "$LOG_FILE" 2>/dev/null
    fi
    echo ""
}

# Variant 1: Basic floor plan (minimal)
run_conversion "basic" \
    "-j 4" \
    "--exclude entities IfcOpeningElement IfcSpace"

# Variant 2: Floor plan with labels
run_conversion "with-labels" \
    "-j 4" \
    "--exclude entities IfcOpeningElement IfcSpace" \
    "--bounds 2048x1536" \
    "--print-space-names" \
    "--print-space-areas"

# Variant 3: Floor plan with door arcs
run_conversion "with-doors" \
    "-j 4" \
    "--exclude entities IfcOpeningElement IfcSpace" \
    "--bounds 2048x1536" \
    "--door-arcs" \
    "--print-space-names"

# Variant 4: Scaled floor plan (1:100)
run_conversion "scaled" \
    "-j 4" \
    "--exclude entities IfcOpeningElement IfcSpace" \
    "--bounds 1024x768" \
    "--scale 1:100" \
    "--center 0.5x0.5" \
    "--print-space-names" \
    "--print-space-areas" \
    "--door-arcs"

# Variant 5: Auto sections and elevations
run_conversion "sections" \
    "-j 4" \
    "--exclude entities IfcOpeningElement" \
    "--auto-section" \
    "--auto-elevation" \
    "--draw-storey-heights full"

# Variant 6: Include everything (no exclusions)
run_conversion "complete" \
    "-j 4" \
    "--bounds 2048x1536" \
    "--print-space-names" \
    "--print-space-areas" \
    "--door-arcs"

echo "========================================"
echo "All variants generated!"
echo "========================================"
echo ""
echo "Local files location:"
echo "  /home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/"
echo ""
echo "Generated files:"
ls -lh /home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/Building-Architecture-*.svg 2>/dev/null | awk '{print "  " $9 " (" $5 ")"}'
echo ""
echo "View them with:"
echo "  firefox /home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/Building-Architecture-*.svg"
echo ""


