#!/bin/bash
#
# Simple Floor Plan Export - Clean floor plans without room labels
# Fastest option for basic architectural floor plans
#

set -e

echo "========================================="
echo "Simple Floor Plan Generator"
echo "========================================="
echo ""

# Configuration
INPUT_FILE="/uploads/A1_2b_BIM_XXX_0003_00.ifc"
OUTPUT_FILE="/output/converted/A1-spaces-floorplan.svg"
LOG_FILE="/output/converted/A1-spaces-floorplan.log"

echo "Input:  $INPUT_FILE"
echo "Output: $OUTPUT_FILE"
echo ""
echo "Configuration: Floor plans with room labels"
echo "Elements: Walls, Doors, Windows, Stairs, Railings, Spaces"
echo ""

START_TIME=$(date +%s)

docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert \
  -y \
  -j 4 \
  --log-format plain \
  --log-file "$LOG_FILE" \
  --model \
  --section-height-from-storeys \
  --bounds 4096x3072 \
  --scale 1:50 \
  --include entities IfcWall IfcDoor IfcWindow IfcStair IfcRailing IfcSpace \
  --door-arcs \
  --print-space-names \
  --print-space-areas \
  "$INPUT_FILE" \
  "$OUTPUT_FILE"

EXIT_CODE=$?
END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

echo ""

if [ $EXIT_CODE -eq 0 ]; then
    LOCAL_SVG="/home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/A1-floorplan-simple.svg"
    
    if [ -f "$LOCAL_SVG" ]; then
        SIZE=$(stat -c%s "$LOCAL_SVG")
        SIZE_MB=$(echo "scale=2; $SIZE / 1024 / 1024" | bc)
        PATHS=$(grep -c "<path\|<polyline\|<polygon" "$LOCAL_SVG" 2>/dev/null || echo "0")
        STOREYS=$(grep -c 'class="IfcBuildingStorey"' "$LOCAL_SVG" 2>/dev/null || echo "0")
        
        echo "✓✓ Simple Floor Plan Generated!"
        echo ""
        echo "  File: $LOCAL_SVG"
        echo "  Size: ${SIZE_MB}MB"
        echo "  Storeys: $STOREYS"
        echo "  Elements: $PATHS"
        echo "  Duration: ${DURATION}s"
        echo ""
        echo "View: firefox $LOCAL_SVG"
        
    else
        echo "✗ Output file not found"
    fi
else
    echo "✗ Conversion failed with exit code: $EXIT_CODE"
fi

echo ""
echo "========================================="


