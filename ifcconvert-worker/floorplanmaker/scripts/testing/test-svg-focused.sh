#!/bin/bash
#
# Focused SVG Export Tests - Only tests configurations most likely to work
# With better error filtering and cleaner output

set -e

echo "======================================================================="
echo "Focused SVG Export Tests for IfcConvert 0.8.3"
echo "======================================================================="
echo ""

# Configuration
INPUT_FILE="/uploads/A1_2b_BIM_XXX_0001_00.v24.0.ifc"
OUTPUT_DIR="/output/converted"
CONTAINER="ifcpipeline-ifcconvert-worker-1"

# Show version
echo "IfcConvert Version:"
docker exec $CONTAINER /usr/local/bin/IfcConvert --version
echo ""

echo "Input file: $INPUT_FILE"
echo ""
echo "Testing focused configurations (excluding known failures)..."
echo ""

# Function to run test with clean output
run_test() {
    local test_num=$1
    local test_name=$2
    shift 2
    local args=("$@")
    
    local output_file="$OUTPUT_DIR/svg-focused-${test_num}-${test_name}.svg"
    local log_file="$OUTPUT_DIR/svg-focused-${test_num}-${test_name}.log"
    local err_summary="$OUTPUT_DIR/svg-focused-${test_num}-${test_name}.err"
    
    echo "-----------------------------------------------------------------------"
    echo "Test $test_num: $test_name"
    echo "Args: ${args[@]}"
    echo "-----------------------------------------------------------------------"
    
    # Run conversion with timeout
    timeout 120s docker exec $CONTAINER /usr/local/bin/IfcConvert \
        -y \
        --log-format plain \
        --log-file "$log_file" \
        "${args[@]}" \
        "$INPUT_FILE" \
        "$output_file" >/dev/null 2>&1
    
    local exit_code=$?
    
    # Analyze results
    if [ $exit_code -eq 0 ]; then
        # Check if file exists locally
        local local_file="/home/bimbot-ubuntu/apps/ifcpipeline/shared${output_file}"
        if [ -f "$local_file" ]; then
            local size=$(stat -c%s "$local_file")
            local lines=$(wc -l < "$local_file")
            local paths=$(grep -c "<path\|<polyline\|<polygon\|<line\|<rect\|<circle" "$local_file" 2>/dev/null || echo "0")
            local groups=$(grep -c "<g " "$local_file" 2>/dev/null || echo "0")
            
            echo "✓ Conversion succeeded (exit code: 0)"
            echo "  File: $size bytes, $lines lines"
            echo "  Geometry: $paths paths, $groups groups"
            
            if [ $paths -gt 10 ]; then
                echo "  ✓✓ GOOD - Contains significant geometry!"
            elif [ $paths -gt 0 ]; then
                echo "  ⚠  PARTIAL - Contains some geometry"
            else
                echo "  ✗ EMPTY - No geometry found"
            fi
        else
            echo "⚠  Conversion reported success but file not found"
        fi
    elif [ $exit_code -eq 124 ]; then
        echo "✗ TIMEOUT - Conversion took >120 seconds"
    elif [ $exit_code -eq 137 ]; then
        echo "✗ KILLED - Process killed (likely out of memory)"
    else
        echo "✗ Conversion failed (exit code: $exit_code)"
    fi
    
    # Show unique errors from log
    local local_log="/home/bimbot-ubuntu/apps/ifcpipeline/shared${log_file}"
    if [ -f "$local_log" ]; then
        local total_errors=$(grep -c "\[Error\]" "$local_log" 2>/dev/null || echo "0")
        if [ $total_errors -gt 0 ]; then
            echo "  Errors: $total_errors total"
            # Get unique error messages (first 3)
            grep "\[Error\]" "$local_log" | sed 's/\[Error\] \[.*\] //' | sort -u | head -3 > "$err_summary" 2>/dev/null || true
            local unique_errors=$(cat "$err_summary" 2>/dev/null | wc -l)
            echo "  Unique error types: $unique_errors"
            if [ $unique_errors -gt 0 ]; then
                echo "  First unique error:"
                head -1 "$err_summary" | sed 's/^/    /'
            fi
        fi
    fi
    
    echo ""
}

# Test 1: Simple glTF export (baseline - should always work)
echo "═══════════════════════════════════════════════════════════════════════"
echo "BASELINE TEST: GLB Export (to verify geometry processing works)"
echo "═══════════════════════════════════════════════════════════════════════"
echo ""

timeout 60s docker exec $CONTAINER /usr/local/bin/IfcConvert \
    -y -j 4 \
    "$INPUT_FILE" \
    "$OUTPUT_DIR/baseline-test.glb" >/dev/null 2>&1

if [ $? -eq 0 ]; then
    glb_size=$(stat -c%s "/home/bimbot-ubuntu/apps/ifcpipeline/shared${OUTPUT_DIR}/baseline-test.glb" 2>/dev/null || echo "0")
    if [ $glb_size -gt 100000 ]; then
        echo "✓ GLB baseline test PASSED ($glb_size bytes)"
        echo "  → Geometry processing is working correctly"
    else
        echo "⚠  GLB baseline test produced small file"
    fi
else
    echo "✗ GLB baseline test FAILED"
    echo "  → Geometry processing may be broken"
fi

echo ""
echo "═══════════════════════════════════════════════════════════════════════"
echo "SVG EXPORT TESTS"
echo "═══════════════════════════════════════════════════════════════════════"
echo ""

# Test 2: OBJ with just plan representations (simplest 2D)
run_test "01" "plan-only" \
    --plan \
    --bounds 2048x1536

# Test 3: Model-only with auto-section
run_test "02" "model-section" \
    --model \
    --auto-section \
    --bounds 2048x1536

# Test 4: Plan + Model + Section (known to partially work in 0.7.11)
run_test "03" "plan-model-section" \
    -j 4 \
    --plan \
    --model \
    --auto-section \
    --bounds 2048x1536

# Test 5: With entity filtering to reduce complexity
run_test "04" "filtered-simple" \
    -j 4 \
    --plan \
    --model \
    --auto-section \
    --bounds 2048x1536 \
    --exclude entities IfcOpeningElement IfcSpace

# Test 6: With specific storey filtering
run_test "05" "storey-section" \
    -j 4 \
    --plan \
    --model \
    --section-height 1.5 \
    --bounds 2048x1536 \
    --exclude entities IfcOpeningElement

# Test 7: Full featured (if anything works, this should be best)
run_test "06" "full-featured" \
    -j 4 \
    --plan \
    --model \
    --auto-section \
    --bounds 3000x2000 \
    --exclude entities IfcOpeningElement \
    --print-space-names \
    --print-space-areas \
    --door-arcs

echo "======================================================================="
echo "Test Summary"
echo "======================================================================="
echo ""

# Count results
cd /home/bimbot-ubuntu/apps/ifcpipeline/shared${OUTPUT_DIR}
total_tests=6
successful=0
partial=0
failed=0

for svg in svg-focused-*.svg 2>/dev/null; do
    if [ -f "$svg" ]; then
        size=$(stat -c%s "$svg")
        paths=$(grep -c "<path\|<polyline\|<polygon\|<line" "$svg" 2>/dev/null || echo "0")
        
        if [ $paths -gt 10 ]; then
            ((successful++))
            echo "✓ $svg: $size bytes, $paths elements [GOOD]"
        elif [ $paths -gt 0 ]; then
            ((partial++))
            echo "⚠ $svg: $size bytes, $paths elements [PARTIAL]"
        else
            ((failed++))
        fi
    fi
done

echo ""
echo "Results:"
echo "  ✓ Successful (>10 paths): $successful"
echo "  ⚠ Partial (1-10 paths):   $partial"
echo "  ✗ Failed (0 paths):        $failed"
echo ""

if [ $successful -gt 0 ]; then
    echo "✓✓ Success! Some configurations produced good SVG output"
    echo ""
    echo "Best results:"
    ls -lh svg-focused-*.svg 2>/dev/null | head -3
elif [ $partial -gt 0 ]; then
    echo "⚠  Partial success - SVG files created but with limited geometry"
    echo "   Consider using OBJ or glTF export instead for this IFC file"
else
    echo "✗ No successful SVG exports"
    echo "   SVG export appears broken for this file in IfcConvert 0.8.3"
    echo "   Recommendation: Use OBJ or glTF export formats instead"
fi

echo ""
echo "Files location: /home/bimbot-ubuntu/apps/ifcpipeline/shared${OUTPUT_DIR}/"
echo "View logs: ls -lh svg-focused-*.log"
echo ""
echo "======================================================================="


