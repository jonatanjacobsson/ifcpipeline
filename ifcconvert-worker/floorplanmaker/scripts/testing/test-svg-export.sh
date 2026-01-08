#!/bin/bash

# Test script for IfcConvert SVG export
# Generates a floor plan SVG from Building-Architecture.ifc

echo "=================================="
echo "IfcConvert SVG Test Script"
echo "=================================="
echo ""

# Input and output paths (inside container)
INPUT_FILE="/uploads/A1_2b_BIM_XXX_0001_00.v24.0.ifc"
OUTPUT_FILE="/output/converted/A1_2b_BIM_XXX_0001_00-floorplan.svg"
LOG_FILE="/output/converted/A1_2b_BIM_XXX_0001_00-convert.log"

echo "Input:  $INPUT_FILE"
echo "Output: $OUTPUT_FILE"
echo "Log:    $LOG_FILE"
echo ""

# Run IfcConvert inside the container with good SVG defaults
echo "Running IfcConvert..."
docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert \
  -y \
  --log-format plain \
  --log-file "$LOG_FILE" \
  -j 4 \
  --plan \
  --exclude entities IfcOpeningElement \
  --bounds 2048x1536 \
  --print-space-names \
  --print-space-areas \
  --door-arcs \
  "$INPUT_FILE" \
  "$OUTPUT_FILE"

# Check exit code
EXIT_CODE=$?
echo ""

if [ $EXIT_CODE -eq 0 ]; then
    echo "✓ Conversion successful!"
    echo ""
    
    # Show output file info
    echo "Output file details:"
    docker exec ifcpipeline-ifcconvert-worker-1 ls -lh "$OUTPUT_FILE"
    
    # Copy to local machine for viewing
    LOCAL_OUTPUT="/home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/A1_2b_BIM_XXX_0001_00-floorplan.svg"
    if [ -f "$LOCAL_OUTPUT" ]; then
        FILE_SIZE=$(stat -c%s "$LOCAL_OUTPUT")
        echo ""
        echo "Local file: $LOCAL_OUTPUT"
        echo "Size: $FILE_SIZE bytes"
        echo ""
        echo "You can view it with:"
        echo "  firefox $LOCAL_OUTPUT"
        echo "  or"
        echo "  chromium $LOCAL_OUTPUT"
    fi
    
    # Show first few lines of log
    echo ""
    echo "Log file (first 10 lines):"
    echo "---"
    docker exec ifcpipeline-ifcconvert-worker-1 head -n 10 "$LOG_FILE"
    echo "---"
    echo ""
    echo "Full log: $LOCAL_OUTPUT/../Building-Architecture-convert.log"
    
else
    echo "✗ Conversion failed with exit code $EXIT_CODE"
    echo ""
    echo "Log file contents:"
    echo "---"
    docker exec ifcpipeline-ifcconvert-worker-1 cat "$LOG_FILE" 2>/dev/null || echo "Log file not found"
    echo "---"
    exit $EXIT_CODE
fi

echo ""
echo "=================================="
echo "Test complete!"
echo "=================================="

