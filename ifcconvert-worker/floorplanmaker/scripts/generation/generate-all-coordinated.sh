#!/bin/bash
# =============================================================================
# Generate Coordinated Floor Plans for All Storeys
# =============================================================================
# Batch generation of coordinated multi-discipline floor plans
#
# Usage:
#   ./generate-all-coordinated.sh
#
# =============================================================================

# Don't use set -e for batch scripts - we handle errors manually
# set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/../../config/templates/floorplan-config.yaml"
PARSER="$SCRIPT_DIR/../utilities/config_parser.py"
GENERATOR="$SCRIPT_DIR/generate-coordinated-floorplan.sh"

echo ""
echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║     BATCH GENERATION: COORDINATED FLOOR PLANS (ALL STOREYS)          ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""

# Read storeys from config
STOREYS=(
    "000 Sea Level:0.00"
    "010 Quay Level +1.90m:1.90"
    "020 Mezzanine +5.40m:5.40"
    "030 Slussen Level +8.90m:8.90"
    "040 First Floor +14.23m:14.23"
    "050 Second Floor +19.56m:19.56"
    "060 Third Floor +24,89m:24.89"
    "070 Roof +29.50m:29.50"
)

TOTAL=${#STOREYS[@]}
SUCCESS_COUNT=0
FAIL_COUNT=0
START_TIME=$(date +%s)

echo "Generating coordinated floor plans for $TOTAL storeys..."
echo ""

# Progress tracking
CURRENT=1

for storey_info in "${STOREYS[@]}"; do
    # Parse storey name and elevation
    IFS=':' read -r STOREY_NAME STOREY_ELEV <<< "$storey_info"
    
    # Get next storey elevation (for MEP section heights)
    NEXT_INDEX=$((CURRENT))
    if [ $NEXT_INDEX -lt $TOTAL ]; then
        NEXT_STOREY_INFO="${STOREYS[$NEXT_INDEX]}"
        IFS=':' read -r NEXT_NAME NEXT_ELEV <<< "$NEXT_STOREY_INFO"
    else
        # For roof level, use current + 3.5m
        NEXT_ELEV=$(echo "$STOREY_ELEV + 3.5" | bc)
    fi
    
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "[$CURRENT/$TOTAL] $STOREY_NAME (@ ${STOREY_ELEV}m → MEP @ ${NEXT_ELEV}m)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    # Generate floor plan with next level elevation for MEP
    if $GENERATOR "$STOREY_NAME" "$STOREY_ELEV" "$NEXT_ELEV" 2>&1 | grep -E "(Section heights|Exporting|Loading|Merging|ViewBox|Success|Error)" || true; then
        # Check if output file was created
        OUTPUT_SAFE=$(echo "$STOREY_NAME" | tr '[:upper:]' '[:lower:]' | tr ' +.,' '_' | sed 's/__*/_/g' | sed 's/_$//')
        OUTPUT_FILE="/home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/floorplans/coord_all_${OUTPUT_SAFE}.svg"
        if [ -f "$OUTPUT_FILE" ] && [ -s "$OUTPUT_FILE" ]; then
            echo "  ✓ Success"
            ((SUCCESS_COUNT++))
        else
            echo "  ✗ Failed (output not created)"
            ((FAIL_COUNT++))
        fi
    else
        echo "  ✗ Failed"
        ((FAIL_COUNT++))
    fi
    
    echo ""
    ((CURRENT++))
done

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))
MINUTES=$((DURATION / 60))
SECONDS=$((DURATION % 60))

echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║                        BATCH GENERATION COMPLETE                      ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""
echo "Summary:"
echo "  Total:      $TOTAL floor plans"
echo "  Success:    $SUCCESS_COUNT"
echo "  Failed:     $FAIL_COUNT"
echo "  Duration:   ${MINUTES}m ${SECONDS}s"
echo ""
echo "Output directory:"
echo "  /output/converted/floorplans/coord_all_*.svg"
echo ""

# List generated files
echo "Generated files:"
ls -lh /home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/floorplans/coord_all_*.svg 2>/dev/null | awk '{print "  " $9 " (" $5 ")"}'

echo ""

