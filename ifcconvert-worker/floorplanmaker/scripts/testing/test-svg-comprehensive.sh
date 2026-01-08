#!/bin/bash

# Comprehensive SVG Export Test Script
# Tests multiple approaches to generate SVG floor plans

echo "======================================================================="
echo "Comprehensive SVG Export Test for IfcConvert"
echo "======================================================================="
echo ""

# Configuration
INPUT_FILE="/uploads/A1_2b_BIM_XXX_0001_00.v24.0.ifc"
OUTPUT_DIR="/output/converted"
LOG_DIR="/output/converted"

echo "IfcConvert Version:"
docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert --version
echo ""

echo "Input file: $INPUT_FILE"
echo "Testing different SVG export approaches..."
echo ""

# Function to test conversion
test_svg() {
    local TEST_NAME=$1
    shift
    local ARGS="$@"
    local OUTPUT_FILE="${OUTPUT_DIR}/svg-test-${TEST_NAME}.svg"
    local LOG_FILE="${LOG_DIR}/svg-test-${TEST_NAME}.log"
    
    echo "-----------------------------------------------------------------------"
    echo "Test: $TEST_NAME"
    echo "Args: $ARGS"
    echo "-----------------------------------------------------------------------"
    
    docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert \
        -y \
        --log-format plain \
        --log-file "$LOG_FILE" \
        $ARGS \
        "$INPUT_FILE" \
        "$OUTPUT_FILE" 2>&1 | tail -5
    
    EXIT_CODE=${PIPESTATUS[0]}
    
    if [ $EXIT_CODE -eq 0 ]; then
        LOCAL_SVG="/home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/svg-test-${TEST_NAME}.svg"
        if [ -f "$LOCAL_SVG" ]; then
            SIZE=$(stat -c%s "$LOCAL_SVG")
            LINES=$(wc -l < "$LOCAL_SVG")
            
            # Check if SVG has actual paths/geometry
            PATH_COUNT=$(grep -c "<path\|<polygon\|<polyline\|<rect\|<circle\|<line\|<g " "$LOCAL_SVG" || echo "0")
            
            echo "✓ Created: ${SIZE} bytes, ${LINES} lines, ${PATH_COUNT} geometry elements"
            
            if [ $PATH_COUNT -gt 10 ]; then
                echo "  ✓✓ SUCCESS - Has actual geometry!"
            elif [ $PATH_COUNT -gt 0 ]; then
                echo "  ⚠ Partial - Some geometry but may be incomplete"
            else
                echo "  ✗ Empty - No geometry found"
            fi
        else
            echo "✗ File not created"
        fi
    else
        echo "✗ Conversion failed (exit code: $EXIT_CODE)"
        tail -10 "/home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/svg-test-${TEST_NAME}.log" 2>/dev/null | head -5
    fi
    echo ""
}

# Test 1: Minimal (baseline)
test_svg "01-minimal" ""

# Test 2: With bounds only
test_svg "02-bounds" "--bounds 2048x1536"

# Test 3: With exclude only
test_svg "03-exclude" "--exclude entities IfcOpeningElement IfcSpace"

# Test 4: With bounds and exclude
test_svg "04-bounds-exclude" "--bounds 2048x1536 --exclude entities IfcOpeningElement"

# Test 5: With --plan flag (for curves/axis)
test_svg "05-plan" "--plan --bounds 2048x1536"

# Test 6: Just model (default, but explicit)
test_svg "06-model-only" "--model --bounds 2048x1536"

# Test 7: Both plan and model
test_svg "07-plan-model" "--plan --model --bounds 2048x1536"

# Test 8: With section height
test_svg "08-section-1.5m" "--section-height 1.5 --bounds 2048x1536"

# Test 9: Section height from storeys
test_svg "09-section-storeys" "--section-height-from-storeys --bounds 2048x1536"

# Test 10: Auto section
test_svg "10-auto-section" "--auto-section --bounds 2048x1536"

# Test 11: Auto elevation
test_svg "11-auto-elevation" "--auto-elevation --bounds 2048x1536"

# Test 12: Both auto section and elevation
test_svg "12-auto-both" "--auto-section --auto-elevation --bounds 2048x1536"

# Test 13: Kitchen sink (all options)
test_svg "13-kitchen-sink" \
    "-j 4 --plan --model --bounds 3000x2000 \
     --exclude entities IfcOpeningElement \
     --print-space-names --print-space-areas \
     --door-arcs --auto-section"

# Test 14: Simple with space info
test_svg "14-space-info" \
    "--bounds 2048x1536 --print-space-names --print-space-areas"

# Test 15: With SVG-specific options
test_svg "15-svg-options" \
    "--bounds 2048x1536 --svg-poly --svg-prefilter"

echo "======================================================================="
echo "Testing Complete!"
echo "======================================================================="
echo ""
echo "Results Summary:"
echo "----------------"

cd /home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/
for file in svg-test-*.svg; do
    if [ -f "$file" ]; then
        SIZE=$(stat -c%s "$file")
        PATH_COUNT=$(grep -c "<path\|<polygon\|<polyline\|<rect\|<circle\|<line\|<g " "$file" 2>/dev/null || echo "0")
        
        if [ $PATH_COUNT -gt 10 ]; then
            STATUS="✓✓ HAS GEOMETRY"
        elif [ $PATH_COUNT -gt 0 ]; then
            STATUS="⚠  Some geometry"
        else
            STATUS="✗  Empty"
        fi
        
        printf "%-30s %10s bytes, %4d elements  %s\n" "$file" "$SIZE" "$PATH_COUNT" "$STATUS"
    fi
done

echo ""
echo "Files location: /home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/"
echo ""
echo "Check successful files with:"
echo "  ls -lh shared/output/converted/svg-test-*.svg"
echo "  firefox shared/output/converted/svg-test-XX-name.svg"
echo ""


