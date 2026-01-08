#!/bin/bash
# Diagnostic script to understand SVG alignment issues

GEOMETRY_SVG="/home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/floorplan-020_mezzanine_5_40m-geometry.svg"
SPACES_SVG="/home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/floorplan-020_mezzanine_5_40m-spaces.svg"
FINAL_SVG="/home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/floorplan-020_mezzanine_5_40m.svg"

echo "================================================================="
echo "VERBOSE SVG ALIGNMENT DIAGNOSTIC"
echo "================================================================="
echo ""

if [ ! -f "$GEOMETRY_SVG" ]; then
    echo "ERROR: Intermediate files not found. Running generation..."
    cd /home/bimbot-ubuntu/apps/ifcpipeline
    ./svg-floorplan-complete.sh "020 Mezzanine +5.40m" 6.60 > /dev/null 2>&1
fi

echo "1. SVG ROOT ELEMENTS"
echo "================================================================="
echo ""
echo "GEOMETRY SVG:"
head -2 "$GEOMETRY_SVG" | tail -1
echo ""
echo "SPACES SVG:"
head -2 "$SPACES_SVG" | tail -1
echo ""
echo "FINAL SVG:"
sed -n '2p' "$FINAL_SVG"
echo ""

echo "2. TRANSFORMATION MATRICES"
echo "================================================================="
echo ""
echo "GEOMETRY matrices:"
grep -oP 'data-matrix3="[^"]*"' "$GEOMETRY_SVG" | sort -u
echo ""
echo "SPACES matrices:"
grep -oP 'data-matrix3="[^"]*"' "$SPACES_SVG" | sort -u
echo ""
echo "FINAL matrices:"
grep -oP 'data-matrix3="[^"]*"' "$FINAL_SVG" | sort -u
echo ""

echo "3. SAMPLE COORDINATES FROM GEOMETRY"
echo "================================================================="
echo ""
echo "First door coordinate:"
grep -A1 'class="IfcDoor"' "$GEOMETRY_SVG" | grep '<path' | head -1 | grep -oP 'M[0-9.,]+ [0-9.,]+'
echo ""
echo "First wall coordinate:"
grep -A1 'class="IfcWall"' "$GEOMETRY_SVG" | grep '<path' | head -1 | grep -oP 'M[0-9.,]+ [0-9.,]+'
echo ""

echo "4. SAMPLE COORDINATES FROM SPACES"
echo "================================================================="
echo ""
echo "First space boundary coordinate:"
grep -A1 'class="IfcSpace"' "$SPACES_SVG" | grep '<path' | head -1 | grep -oP 'M[0-9.,]+ [0-9.,]+'
echo ""
echo "First text position:"
grep '<text' "$SPACES_SVG" | head -1 | grep -oP 'x="[0-9.]+" y="[0-9.]+"'
echo ""

echo "5. COORDINATE RANGE ANALYSIS"
echo "================================================================="
echo ""

python3 << 'ANALYZE'
import re

geo_file = "/home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/floorplan-020_mezzanine_5_40m-geometry.svg"
spaces_file = "/home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/floorplan-020_mezzanine_5_40m-spaces.svg"

with open(geo_file, 'r') as f:
    geo_content = f.read()
    
with open(spaces_file, 'r') as f:
    spaces_content = f.read()

# Extract path coordinates
geo_paths = re.findall(r'<path d="M([0-9.]+),([0-9.]+)', geo_content)
space_paths = re.findall(r'<path d="M([0-9.]+),([0-9.]+)', spaces_content)
text_coords = re.findall(r'<text[^>]*x="([0-9.]+)" y="([0-9.]+)"', spaces_content)

if geo_paths:
    geo_x = [float(p[0]) for p in geo_paths]
    geo_y = [float(p[1]) for p in geo_paths]
    print(f"GEOMETRY coordinate range:")
    print(f"  X: {min(geo_x):.2f} to {max(geo_x):.2f} (span: {max(geo_x)-min(geo_x):.2f})")
    print(f"  Y: {min(geo_y):.2f} to {max(geo_y):.2f} (span: {max(geo_y)-min(geo_y):.2f})")
    print(f"  Center: ({(min(geo_x)+max(geo_x))/2:.2f}, {(min(geo_y)+max(geo_y))/2:.2f})")
    print()

if space_paths:
    space_x = [float(p[0]) for p in space_paths]
    space_y = [float(p[1]) for p in space_paths]
    print(f"SPACES coordinate range:")
    print(f"  X: {min(space_x):.2f} to {max(space_x):.2f} (span: {max(space_x)-min(space_x):.2f})")
    print(f"  Y: {min(space_y):.2f} to {max(space_y):.2f} (span: {max(space_y)-min(space_y):.2f})")
    print(f"  Center: ({(min(space_x)+max(space_x))/2:.2f}, {(min(space_y)+max(space_y))/2:.2f})")
    print()

if text_coords:
    text_x = [float(t[0]) for t in text_coords]
    text_y = [float(t[1]) for t in text_coords]
    print(f"TEXT coordinate range:")
    print(f"  X: {min(text_x):.2f} to {max(text_x):.2f}")
    print(f"  Y: {min(text_y):.2f} to {max(text_y):.2f}")
    print()

# Compare if they're in the same coordinate system
if geo_paths and space_paths:
    geo_center_x = (min(geo_x) + max(geo_x)) / 2
    geo_center_y = (min(geo_y) + max(geo_y)) / 2
    space_center_x = (min(space_x) + max(space_x)) / 2
    space_center_y = (min(space_y) + max(space_y)) / 2
    
    offset_x = space_center_x - geo_center_x
    offset_y = space_center_y - geo_center_y
    
    print(f"ALIGNMENT CHECK:")
    print(f"  Geometry center: ({geo_center_x:.2f}, {geo_center_y:.2f})")
    print(f"  Spaces center: ({space_center_x:.2f}, {space_center_y:.2f})")
    print(f"  Offset: ({offset_x:.2f}, {offset_y:.2f})")
    
    if abs(offset_x) < 50 and abs(offset_y) < 50:
        print(f"  ✓ WELL ALIGNED (offset < 50 units)")
    elif abs(offset_x) < 200 and abs(offset_y) < 200:
        print(f"  ⚠ SLIGHT MISALIGNMENT (offset < 200 units)")
    else:
        print(f"  ✗ MAJOR MISALIGNMENT (offset > 200 units)")

ANALYZE

echo ""
echo "6. MATRIX DECOMPOSITION"
echo "================================================================="
echo ""

python3 << 'DECOMPOSE'
import re

geo_file = "/home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/floorplan-020_mezzanine_5_40m-geometry.svg"
spaces_file = "/home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/floorplan-020_mezzanine_5_40m-spaces.svg"

for name, filename in [("GEOMETRY", geo_file), ("SPACES", spaces_file)]:
    with open(filename, 'r') as f:
        content = f.read()
        matrix_match = re.search(r'data-matrix3="\[\[([^]]+)\],\[([^]]+)\],\[([^]]+)\]\]"', content)
        
        if matrix_match:
            row1 = matrix_match.group(1).split(',')
            row2 = matrix_match.group(2).split(',')
            row3 = matrix_match.group(3).split(',')
            
            scale_x = float(row1[0])
            scale_y = float(row2[1])
            translate_x = float(row1[2])
            translate_y = float(row2[2])
            
            print(f"{name} matrix transformation:")
            print(f"  Scale: ({scale_x:.6f}, {scale_y:.6f})")
            print(f"  Translation: ({translate_x:.6f}, {translate_y:.6f})")
            print()

DECOMPOSE

echo "================================================================="
echo "END OF DIAGNOSTIC"
echo "================================================================="

