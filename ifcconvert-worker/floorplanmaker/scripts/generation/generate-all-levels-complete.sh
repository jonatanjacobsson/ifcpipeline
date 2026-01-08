#!/bin/bash
# Generate floor plans for ALL 8 building levels

echo "=================================================================="
echo " Generating Floor Plans for ALL 8 Building Levels"
echo "=================================================================="
echo ""

# All storeys detected from IFC model
declare -a STOREYS=(
    "000 Sea Level|1.20"
    "010 Quay Level +1.90m|3.10"
    "020 Mezzanine +5.40m|6.60"
    "030 Slussen Level +8.90m|10.10"
    "040 First Floor +14.23m|15.43"
    "050 Second Floor +19.56m|20.76"
    "060 Third Floor +24,89m|26.09"
    "070 Roof +29.50m|30.70"
)

TOTAL=${#STOREYS[@]}
CURRENT=0
SUCCESS=0
FAILED=0

for storey_data in "${STOREYS[@]}"; do
    CURRENT=$((CURRENT + 1))
    
    # Split storey name and section height
    IFS='|' read -r STOREY_NAME SECTION_HEIGHT <<< "$storey_data"
    
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "[$CURRENT/$TOTAL] $STOREY_NAME"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Section height: ${SECTION_HEIGHT}m"
    echo ""
    
    # Generate floor plan
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    "$SCRIPT_DIR/../processing/svg-floorplan-complete.sh" "$STOREY_NAME" "$SECTION_HEIGHT"
    
    EXIT_CODE=$?
    
    if [ $EXIT_CODE -eq 0 ]; then
        echo ""
        echo "✓ Completed: $STOREY_NAME"
        SUCCESS=$((SUCCESS + 1))
    else
        echo ""
        echo "✗ Failed: $STOREY_NAME (exit code: $EXIT_CODE)"
        FAILED=$((FAILED + 1))
    fi
    
    echo ""
done

echo "=================================================================="
echo "✓ Batch Generation Complete!"
echo "=================================================================="
echo ""
echo "Summary:"
echo "  Total:   $TOTAL levels"
echo "  Success: $SUCCESS levels"
echo "  Failed:  $FAILED levels"
echo ""
echo "Output directory: /output/converted/"
echo ""
echo "Generated floor plans:"
ls -lh /home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/floorplan-*.svg 2>/dev/null | \
  grep -v -- "-geometry.svg" | \
  grep -v -- "-spaces.svg" | \
  grep -v -- "-combined.svg" | \
  awk '{printf "  %-50s %6s\n", substr($9, length($9)-49), $5}' | \
  sed 's|/home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/||'
echo ""

