#!/bin/bash
#
# SVG Room Label Styling - Applies architectural drawing standards
# White text, smaller size, uppercase
#

set -e

INPUT_SVG="${1:-/home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/A1-spaces-floorplan.svg}"
OUTPUT_SVG="${INPUT_SVG%.svg}-styled.svg"

if [ ! -f "$INPUT_SVG" ]; then
    echo "Error: Input SVG not found: $INPUT_SVG"
    exit 1
fi

echo "========================================="
echo "SVG Room Label Styling"
echo "========================================="
echo ""
echo "Input:  $INPUT_SVG"
echo "Output: $OUTPUT_SVG"
echo ""

# Create styled version using Python
python3 << 'EOF'
import re
import sys

input_file = sys.argv[1]
output_file = sys.argv[2]

print("Reading SVG...")
with open(input_file, 'r', encoding='utf-8') as f:
    content = f.read()

print("Applying architectural drawing standards...")

# 1. Add custom CSS for room labels
custom_css = """
        /* Architectural Drawing Standards - Room Labels */
        text {
            font-family: 'Arial', 'Helvetica', sans-serif;
            font-size: 8pt;
            font-weight: bold;
            fill: white;
            stroke: black;
            stroke-width: 0.3px;
            paint-order: stroke fill;
            text-transform: uppercase;
        }
        
        text tspan {
            font-family: 'Arial', 'Helvetica', sans-serif;
            font-size: 8pt;
            font-weight: bold;
        }
        
        /* Room name - larger */
        text tspan:first-child {
            font-size: 6pt;
            font-weight: bold;
        }
        
        /* Room number - medium */
        text tspan:nth-child(2) {
            font-size: 3pt;
            font-weight: normal;
        }
        
        /* Area - smaller */
        text tspan:nth-child(3) {
            font-size: 2pt;
            font-weight: normal;
        }
"""

# Insert custom CSS before the closing </style> tag
content = content.replace('</style>', custom_css + '\n    </style>')

# 2. Convert all text content to uppercase
def uppercase_text(match):
    return match.group(0).upper()

# Find all text between > and < in tspan elements
content = re.sub(r'(<tspan[^>]*>)([^<]+)(</tspan>)', 
                 lambda m: m.group(1) + m.group(2).upper() + m.group(3), 
                 content)

# 3. Add explicit styling attributes to text elements for better compatibility
# Add class to text elements
content = re.sub(r'<text ', r'<text class="room-label" ', content)

print("Saving styled SVG...")
with open(output_file, 'w', encoding='utf-8') as f:
    f.write(content)

print(f"✓ Styled SVG created")

# Count modifications
text_count = len(re.findall(r'<text', content))
print(f"  Modified {text_count} text labels")

EOF

python3 -c "
import sys
sys.argv = ['', '$INPUT_SVG', '$OUTPUT_SVG']
$(cat << 'SCRIPT'
import re
import sys

input_file = sys.argv[1]
output_file = sys.argv[2]

with open(input_file, 'r', encoding='utf-8') as f:
    content = f.read()

# Add custom CSS for room labels
custom_css = '''
        /* Architectural Drawing Standards - Room Labels */
        text {
            font-family: Arial, Helvetica, sans-serif;
            font-size: 8pt;
            font-weight: bold;
            fill: white;
            stroke: black;
            stroke-width: 0.3px;
            paint-order: stroke fill;
            text-transform: uppercase;
        }
        
        text tspan {
            font-family: Arial, Helvetica, sans-serif;
            font-size: 8pt;
            font-weight: bold;
        }
        
        /* Room name - larger */
        text tspan:first-child {
            font-size: 6pt;
            font-weight: bold;
        }
        
        /* Room number - medium */
        text tspan:nth-child(2) {
            font-size: 3pt;
            font-weight: normal;
        }
        
        /* Area - smaller */
        text tspan:nth-child(3) {
            font-size: 2pt;
            font-weight: normal;
        }
'''

# Insert custom CSS before the closing </style> tag
content = content.replace('</style>', custom_css + '\n    </style>')

# Convert all text content to uppercase
content = re.sub(r'(<tspan[^>]*>)([^<]+)(</tspan>)', 
                 lambda m: m.group(1) + m.group(2).upper() + m.group(3), 
                 content)

# Add class to text elements
content = re.sub(r'<text ', r'<text class=\"room-label\" ', content)

with open(output_file, 'w', encoding='utf-8') as f:
    f.write(content)

# Count modifications
text_count = len(re.findall(r'<text', content))
print(f'  Modified {text_count} text labels')
SCRIPT
)
"

if [ $? -eq 0 ]; then
    SIZE_BEFORE=$(stat -c%s "$INPUT_SVG")
    SIZE_AFTER=$(stat -c%s "$OUTPUT_SVG")
    SIZE_BEFORE_KB=$((SIZE_BEFORE / 1024))
    SIZE_AFTER_KB=$((SIZE_AFTER / 1024))
    
    echo ""
    echo "Results:"
    echo "  Before: ${SIZE_BEFORE_KB}KB"
    echo "  After:  ${SIZE_AFTER_KB}KB"
    echo ""
    echo "Styling applied:"
    echo "  ✓ White text with black outline"
    echo "  ✓ Room names: 6pt (uppercase)"
    echo "  ✓ Room numbers: 3pt (uppercase)"
    echo "  ✓ Areas: 2pt (uppercase)"
    echo "  ✓ Font: Arial/Helvetica (standard)"
    echo ""
    echo "View: firefox $OUTPUT_SVG"
else
    echo "Error: Failed to apply styling"
    exit 1
fi

echo ""
echo "========================================="

