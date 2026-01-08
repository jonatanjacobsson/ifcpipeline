#!/bin/bash
# =============================================================================
# Batch Generator for All MEP and Structural Floor Plans
# =============================================================================

set -e

echo "============================================================================="
echo " Batch MEP + Structural Floor Plan Generator"
echo "============================================================================="
echo ""

# All storeys
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

# Disciplines
DISCIPLINES=("electrical" "mechanical" "plumbing" "structural")

TOTAL_STOREYS=${#STOREYS[@]}
TOTAL_DISCIPLINES=${#DISCIPLINES[@]}
TOTAL=$((TOTAL_STOREYS * TOTAL_DISCIPLINES))

echo "Configuration:"
echo "  Storeys: $TOTAL_STOREYS"
echo "  Disciplines: $TOTAL_DISCIPLINES (${DISCIPLINES[*]})"
echo "  Total Plans: $TOTAL"
echo ""
echo "============================================================================="
echo ""

CURRENT=0
SUCCESS=0
FAILED=0

for discipline in "${DISCIPLINES[@]}"; do
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "📋 DISCIPLINE: ${discipline^^}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    
    for storey_data in "${STOREYS[@]}"; do
        CURRENT=$((CURRENT + 1))
        
        IFS='|' read -r STOREY_NAME SECTION_HEIGHT <<< "$storey_data"
        
        echo "[$CURRENT/$TOTAL] $discipline - $STOREY_NAME"
        
        if ./generate-mep-floorplan.sh "$discipline" "$STOREY_NAME" "$SECTION_HEIGHT" 2>&1 | grep -q "✓ Combined and scaled successfully"; then
            SUCCESS=$((SUCCESS + 1))
            echo "  ✓ Success"
        else
            FAILED=$((FAILED + 1))
            echo "  ✗ Failed"
        fi
        
        echo ""
    done
done

echo "============================================================================="
echo "✓ Batch Generation Complete!"
echo "============================================================================="
echo ""
echo "Summary:"
echo "  Total:   $TOTAL floor plans"
echo "  Success: $SUCCESS"
echo "  Failed:  $FAILED"
echo ""
echo "Output directory: /output/converted/floorplans/"
echo ""

ls -lh /home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/floorplans/*.svg 2>/dev/null | wc -l | xargs echo "Total files generated:"

echo ""

