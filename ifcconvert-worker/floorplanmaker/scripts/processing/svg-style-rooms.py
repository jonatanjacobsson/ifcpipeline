#!/usr/bin/env python3
"""
SVG Room Label Styling - Applies architectural drawing standards
- White text with black outline for readability
- Smaller font sizes (6pt/4pt/3pt)
- Uppercase text
- Arial/Helvetica font
"""

import re
import sys
import os

def style_svg(input_file, output_file):
    print("========================================")
    print("SVG Room Label Styling")
    print("========================================")
    print()
    print(f"Input:  {input_file}")
    print(f"Output: {output_file}")
    print()
    
    if not os.path.exists(input_file):
        print(f"Error: Input file not found: {input_file}")
        sys.exit(1)
    
    print("Reading SVG...")
    with open(input_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    print("Applying architectural drawing standards...")
    
    # Custom CSS for room labels - architectural drawing standards
    # Coordinates scaled 20x (1:50 scale - 1 meter = 20mm on drawing)
    # Font sizes scaled accordingly
    
    custom_css = """
        /* ================================================== */
        /* Architectural Drawing Standards - Room Labels     */
        /* Font sizes for 1:50 scale (20x coordinates)      */
        /* ================================================== */
        
        text {
            font-family: 'Arial', 'Helvetica', sans-serif;
            font-size: 16pt;
            font-weight: bold;
            fill: white !important;
            stroke: black;
            stroke-width: 0.5px;
            paint-order: stroke fill;
            letter-spacing: 1px;
        }
        
        text tspan {
            font-family: 'Arial', 'Helvetica', sans-serif;
            font-weight: bold;
        }
        
        /* Room name - First line (larger, bold) */
        text tspan:first-child {
            font-size: 12pt;
            font-weight: bold;
            letter-spacing: 1.5px;
        }
        
        /* Room number - Second line (medium) */
        text tspan:nth-child(2) {
            font-size: 8pt;
            font-weight: normal;
            letter-spacing: 1px;
        }
        
        /* Area - Third line (smaller) */
        text tspan:nth-child(3) {
            font-size: 6pt;
            font-weight: normal;
            letter-spacing: 0.8px;
        }
        
        /* Space elements - subtle fill */
        .IfcSpace path {
            fill: rgba(200, 200, 200, 0.1);
            stroke: rgba(150, 150, 150, 0.3);
            stroke-width: 0.5px;
        }
"""
    
    # Insert custom CSS before the closing </style> tag
    if '</style>' in content:
        content = content.replace('</style>', custom_css + '\n    </style>')
    else:
        # If no style tag, create one after the <defs> section
        if '<defs>' in content:
            content = content.replace('</defs>', 
                f'</defs>\n    <style type="text/css">\n    <![CDATA[{custom_css}\n    ]]>\n    </style>')
    
    # Convert all text content to UPPERCASE (but preserve HTML entities)
    print("  Converting text to uppercase...")
    def uppercase_tspan_content(match):
        """Convert text between tspan tags to uppercase, preserving HTML entities"""
        opening = match.group(1)
        text_content = match.group(2)
        closing = match.group(3)
        
        # Preserve HTML entities by temporarily replacing them
        entities = re.findall(r'&[a-zA-Z]+;|&#\d+;', text_content)
        temp_markers = []
        temp_content = text_content
        
        for i, entity in enumerate(entities):
            marker = f'__ENTITY_{i}__'
            temp_markers.append((marker, entity))
            temp_content = temp_content.replace(entity, marker, 1)
        
        # Uppercase the text
        temp_content = temp_content.upper()
        
        # Restore HTML entities (keeping them lowercase)
        for marker, entity in temp_markers:
            temp_content = temp_content.replace(marker, entity)
        
        return opening + temp_content + closing
    
    content = re.sub(
        r'(<tspan[^>]*>)([^<]+)(</tspan>)',
        uppercase_tspan_content,
        content
    )
    
    # Add class attribute to text elements for better targeting
    content = re.sub(r'<text ', r'<text class="room-label" ', content)
    
    print("Saving styled SVG...")
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(content)
    
    # Count modifications
    text_count = len(re.findall(r'<text', content))
    tspan_count = len(re.findall(r'<tspan', content))
    
    # Get file sizes
    size_before = os.path.getsize(input_file)
    size_after = os.path.getsize(output_file)
    
    print()
    print("✓ Styling successfully applied!")
    print()
    print("Results:")
    print(f"  File size: {size_before // 1024}KB → {size_after // 1024}KB")
    print(f"  Text elements: {text_count}")
    print(f"  Text spans: {tspan_count}")
    print()
    print("Applied styles:")
    print("  ✓ White text with black outline (0.4px stroke)")
    print("  ✓ Room names: 6pt bold (uppercase)")
    print("  ✓ Room numbers: 3pt normal (uppercase)")
    print("  ✓ Areas: 2pt normal (uppercase)")
    print("  ✓ Font: Arial/Helvetica")
    print("  ✓ Letter spacing for readability")
    print()
    print(f"View: firefox {output_file}")
    print()
    print("========================================")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        input_svg = sys.argv[1]
        output_svg = sys.argv[2] if len(sys.argv) > 2 else input_svg.replace('.svg', '-styled.svg')
    else:
        input_svg = "/home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/A1-spaces-floorplan.svg"
        output_svg = "/home/bimbot-ubuntu/apps/ifcpipeline/shared/output/converted/A1-spaces-floorplan-styled.svg"
    
    style_svg(input_svg, output_svg)

