#!/bin/bash
#
# Multi-Height Floor Plan Export - Tests different section heights
# to find the best one for showing room labels
#

set -e

echo "========================================="
echo "Multi-Height Floor Plan Test"
echo "========================================="
echo ""
echo "Testing different section heights to find spaces..."
echo ""

# Configuration
INPUT_FILE="/uploads/A1_2b_BIM_XXX_0001_00.v24.0.ifc"
OUTPUT_DIR="/output/converted"
BASE_NAME="A1-height"

# Section heights to test (in meters)
HEIGHTS=(0.8 1.0 1.2 1.5 1.8 2.0 2.5 3.0)

for HEIGHT in "${HEIGHTS[@]}"; do
    OUTPUT_FILE="${OUTPUT_DIR}/${BASE_NAME}-${HEIGHT}m.svg"
    LOG_FILE="${OUTPUT_DIR}/${BASE_NAME}-${HEIGHT}m.log"
    
    echo "Testing section height: ${HEIGHT}m"
    
    docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert \
      -y -q \
      -j 4 \
      --log-format plain \
      --log-file "$LOG_FILE" \
      --model \
      --section-height "$HEIGHT" \
      --bounds 4096x3072 \
      --scale 1:50 \
      --include entities IfcWall IfcDoor IfcWindow IfcStair IfcRailing IfcSpace \
      --door-arcs \
      --print-space-names \
      --print-space-areas \
      "$INPUT_FILE" \
      "$OUTPUT_FILE" 2>&1 | grep -E "(Done|Conversion took)" || true
    
    # Check results
    LOCAL_FILE="/home/bimbot-ubuntu/apps/ifcpipeline/shared${OUTPUT_FILE}"
    if [ -f "$LOCAL_FILE" ]; then
        SIZE=$(stat -c%s "$LOCAL_FILE")
        SIZE_KB=$((SIZE / 1024))
        PATHS=$(grep -c "<path\|<polyline\|<polygon" "$LOCAL_FILE" 2>/dev/null || echo "0")
        TEXTS=$(grep -c "<text" "$LOCAL_FILE" 2>/dev/null || echo "0")
        SPACES=$(grep -c 'class="IfcSpace"' "$LOCAL_FILE" 2>/dev/null || echo "0")
        
        if [ $TEXTS -gt 0 ]; then
            echo "  ✓✓ Height ${HEIGHT}m: ${SIZE_KB}KB, ${PATHS} paths, ${TEXTS} text labels, ${SPACES} spaces [FOUND LABELS!]"
        elif [ $SPACES -gt 5 ]; then
            echo "  ✓  Height ${HEIGHT}m: ${SIZE_KB}KB, ${PATHS} paths, ${SPACES} spaces [HAS SPACES]"
        else
            echo "  -  Height ${HEIGHT}m: ${SIZE_KB}KB, ${PATHS} paths, ${SPACES} spaces"
        fi
    else
        echo "  ✗  Height ${HEIGHT}m: Failed"
    fi
done

echo ""
echo "========================================="
echo "Results Summary"
echo "========================================="
echo ""

# Find the best results
cd /home/bimbot-ubuntu/apps/ifcpipeline/shared${OUTPUT_DIR}

echo "Files with text labels (room names):"
for svg in ${BASE_NAME}-*.svg; do
    if [ -f "$svg" ]; then
        TEXTS=$(grep -c "<text" "$svg" 2>/dev/null || echo "0")
        if [ $TEXTS -gt 0 ]; then
            echo "  ✓✓ $svg - $TEXTS labels"
        fi
    fi
done

echo ""
echo "Files with most space geometry:"
for svg in ${BASE_NAME}-*.svg; do
    if [ -f "$svg" ]; then
        SPACES=$(grep -c 'class="IfcSpace"' "$svg" 2>/dev/null || echo "0")
        if [ $SPACES -gt 5 ]; then
            echo "  ✓ $svg - $SPACES space elements"
        fi
    fi
done

echo ""
echo "All generated files:"
ls -lh ${BASE_NAME}-*.svg 2>/dev/null | awk '{print "  "$9" - "$5}'

echo ""
echo "Files location: /home/bimbot-ubuntu/apps/ifcpipeline/shared${OUTPUT_DIR}/"
echo ""
echo "View with: firefox shared/output/converted/${BASE_NAME}-1.5m.svg"
echo ""
echo "========================================="

