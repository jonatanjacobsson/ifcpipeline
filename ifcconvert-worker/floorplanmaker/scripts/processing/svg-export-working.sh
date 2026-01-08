#!/bin/bash

# Working SVG Export Script for IfcConvert 0.7.11
# Based on successful test configuration

echo "========================================="
echo "SVG Export for IFC Files"
echo "========================================="
echo ""

# Configuration
INPUT_FILE="/uploads/A1_2b_BIM_XXX_0001_00.v24.0.ifc"
OUTPUT_FILE="/output/converted/A1-floorplan.svg"
LOG_FILE="/output/converted/A1-floorplan.log"

echo "Input:  $INPUT_FILE"
echo "Output: $OUTPUT_FILE"
echo ""

# The working combination for IfcConvert 0.7.11:
# Key factors:
# 1. --plan --model together (includes both curves and geometry)
# 2. --auto-section (creates section planes)
# 3. --exclude IfcOpeningElement (avoids boolean operation failures)
# 4. --bounds for proper sizing
# 5. -j for parallel processing

echo "Running IfcConvert with working configuration..."
echo "(This may take 1-2 minutes for large files)"
echo ""

docker exec ifcpipeline-ifcconvert-worker-1 /usr/local/bin/IfcConvert \
  -y \
  -j 4 \
  --log-format plain \
  --log-file "$LOG_FILE" \
  --plan \
  --model \
  --auto-section \
  --bounds 3000x2000 \
  --exclude entities IfcOpeningElement \
  --print-space-names \
  --print-space-areas \
  --door-arcs \
  "$INPUT_FILE" \
  "$OUTPUT_FILE"

EXIT_CODE=$?
echo ""

if [ $EXIT_CODE -eq 0 ]; then
    LOCAL_SVG="/home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/A1-floorplan.svg"
    
    if [ -f "$LOCAL_SVG" ]; then
        SIZE=$(stat -c%s "$LOCAL_SVG")
        LINES=$(wc -l < "$LOCAL_SVG")
        PATHS=$(grep -c "<path\|<polyline\|<polygon" "$LOCAL_SVG" 2>/dev/null || echo "0")
        GROUPS=$(grep -c "<g " "$LOCAL_SVG" 2>/dev/null || echo "0")
        
        echo "✓ SVG Export Successful!"
        echo ""
        echo "Output Details:"
        echo "  File: $LOCAL_SVG"
        echo "  Size: $SIZE bytes"
        echo "  Lines: $LINES"
        echo "  Paths: $PATHS"
        echo "  Groups: $GROUPS"
        echo ""
        
        if [ $PATHS -gt 0 ]; then
            echo "✓✓ SVG contains geometry!"
        else
            echo "⚠  SVG created but may be empty - check the file"
        fi
        
        echo ""
        echo "View with:"
        echo "  firefox $LOCAL_SVG"
        echo "  chromium $LOCAL_SVG"
        echo "  inkscape $LOCAL_SVG"
        
        # Show any errors from log
        if [ -f "/home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/A1-floorplan.log" ]; then
            ERROR_COUNT=$(grep -c "\[Error\]" "/home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/A1-floorplan.log" 2>/dev/null || echo "0")
            if [ $ERROR_COUNT -gt 0 ]; then
                echo ""
                echo "⚠  Note: $ERROR_COUNT errors in log (this is normal for complex files)"
                echo "  Common errors: Opening subtractions (handled by excluding IfcOpeningElement)"
            fi
        fi
        
    else
        echo "✗ Output file not found"
    fi
else
    echo "✗ Conversion failed with exit code: $EXIT_CODE"
    echo ""
    echo "Log contents:"
    cat "/home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/A1-floorplan.log" 2>/dev/null | tail -20
fi

echo ""
echo "========================================="


