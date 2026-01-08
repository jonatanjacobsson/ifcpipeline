#!/bin/bash
# Generate floor plans for all building levels

echo "=========================================="
echo " Generating Floor Plans for ALL Levels"
echo "=========================================="
echo ""

# Define all storeys (from config)
declare -a STOREYS=(
    "000 Sea Level|1.20"
    "010 Quay Level +1.90m|3.10"
    "020 Mezzanine +5.40m|6.60"
    "030 Slussen Level +8.90m|10.10"
)

TOTAL=${#STOREYS[@]}
CURRENT=0

for storey_data in "${STOREYS[@]}"; do
    CURRENT=$((CURRENT + 1))
    
    # Split storey name and section height
    IFS='|' read -r STOREY_NAME SECTION_HEIGHT <<< "$storey_data"
    
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "[$CURRENT/$TOTAL] $STOREY_NAME"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Section height: ${SECTION_HEIGHT}m"
    echo ""
    
    # Generate floor plan
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    "$SCRIPT_DIR/../processing/svg-floorplan-complete.sh" "$STOREY_NAME" "$SECTION_HEIGHT"
    
    EXIT_CODE=$?
    
    if [ $EXIT_CODE -eq 0 ]; then
        echo ""
        echo "✓ Completed: $STOREY_NAME"
    else
        echo ""
        echo "✗ Failed: $STOREY_NAME (exit code: $EXIT_CODE)"
    fi
    
    echo ""
done

echo "=========================================="
echo "✓ All floor plans generated!"
echo "=========================================="
echo ""
echo "Output directory: /output/converted/"
ls -lh /home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/floorplan-*.svg 2>/dev/null | awk '{print "  " $9 " (" $5 ")"}'
echo ""

