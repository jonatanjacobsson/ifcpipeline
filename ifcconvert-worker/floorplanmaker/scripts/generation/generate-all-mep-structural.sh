#!/bin/bash
# =============================================================================
# Batch Generator for All MEP and Structural Floor Plans
# =============================================================================
# Generates Electrical, Mechanical, Plumbing, and Structural floor plans
# for all building levels
# =============================================================================

set -e

echo "============================================================================="
echo " Batch Generator: MEP + Structural Floor Plans"
echo "============================================================================="
echo ""

# All building storeys
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

# View templates to generate
VIEW_TEMPLATES=("electrical" "mechanical" "plumbing" "structural")

TOTAL_STOREYS=${#STOREYS[@]}
TOTAL_TEMPLATES=${#VIEW_TEMPLATES[@]}
TOTAL_PLANS=$((TOTAL_STOREYS * TOTAL_TEMPLATES))

echo "Configuration:"
echo "  Storeys: $TOTAL_STOREYS"
echo "  View Templates: $TOTAL_TEMPLATES (${VIEW_TEMPLATES[*]})"
echo "  Total Plans: $TOTAL_PLANS"
echo ""
echo "============================================================================="
echo ""

CURRENT=0
SUCCESS=0
FAILED=0

# Make script executable
chmod +x ./generate-multi-layer-floorplan.sh

# Generate for each view template and storey
for view_template in "${VIEW_TEMPLATES[@]}"; do
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "📋 VIEW TEMPLATE: ${view_template^^}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    
    for storey_data in "${STOREYS[@]}"; do
        CURRENT=$((CURRENT + 1))
        
        # Split storey name and section height
        IFS='|' read -r STOREY_NAME SECTION_HEIGHT <<< "$storey_data"
        
        echo "[$CURRENT/$TOTAL_PLANS] $view_template - $STOREY_NAME"
        echo ""
        
        # Generate floor plan
        if ./generate-multi-layer-floorplan.sh "$view_template" "$STOREY_NAME" "$SECTION_HEIGHT" 2>&1 | tail -20; then
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
echo "  Total:   $TOTAL_PLANS floor plans"
echo "  Success: $SUCCESS"
echo "  Failed:  $FAILED"
echo ""
echo "Output directory: /output/converted/floorplans/"
echo ""
echo "Generated floor plans by type:"
for view_template in "${VIEW_TEMPLATES[@]}"; do
    COUNT=$(ls -1 /home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/floorplans/FP_${view_template^^}*.svg 2>/dev/null | wc -l || echo "0")
    echo "  ${view_template}: $COUNT plans"
done
echo ""

echo "List all generated files:"
ls -lh /home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/floorplans/*.svg 2>/dev/null | \
    awk '{printf "  %-60s %6s\n", substr($9, length($9)-59), $5}' | \
    sed 's|/home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/floorplans/||' || echo "  No files found"

echo ""

