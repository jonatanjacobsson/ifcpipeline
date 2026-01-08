#!/usr/bin/env python3
"""
Split multi-storey SVG into individual storey files
"""

import sys
import os
import xml.etree.ElementTree as ET
import re

def split_svg_by_storeys(input_file, output_dir):
    print("========================================")
    print("Split SVG by Building Storeys")
    print("========================================")
    print()
    print(f"Input: {input_file}")
    print(f"Output dir: {output_dir}")
    print()
    
    if not os.path.exists(input_file):
        print(f"Error: Input file not found: {input_file}")
        sys.exit(1)
    
    os.makedirs(output_dir, exist_ok=True)
    
    # Register namespaces
    ET.register_namespace('', 'http://www.w3.org/2000/svg')
    ET.register_namespace('xlink', 'http://www.w3.org/1999/xlink')
    
    print("Parsing SVG...")
    tree = ET.parse(input_file)
    root = tree.getroot()
    
    ns = {'svg': 'http://www.w3.org/2000/svg'}
    
    # Find all IfcBuildingStorey groups
    storey_groups = root.findall(".//svg:g[@class='IfcBuildingStorey']", ns)
    
    print(f"Found {len(storey_groups)} building storeys")
    print()
    
    # Extract common elements (style, defs, etc.)
    style_elem = root.find('svg:style', ns)
    defs_elem = root.find('svg:defs', ns)
    viewBox = root.get('viewBox')
    width = root.get('width')
    height = root.get('height')
    
    created_files = []
    
    for idx, storey_group in enumerate(storey_groups, 1):
        storey_name = storey_group.get('data-name', f'Storey_{idx}')
        storey_guid = storey_group.get('data-guid', '')
        
        # Create safe filename
        safe_name = re.sub(r'[^\w\s-]', '', storey_name)
        safe_name = re.sub(r'[-\s]+', '-', safe_name).strip('-').lower()
        output_file = os.path.join(output_dir, f"floorplan-{safe_name}.svg")
        
        print(f"  {idx}. {storey_name}")
        print(f"     -> {os.path.basename(output_file)}")
        
        # Create new SVG for this storey
        new_root = ET.Element('{http://www.w3.org/2000/svg}svg')
        new_root.set('xmlns', 'http://www.w3.org/2000/svg')
        new_root.set('xmlns:xlink', 'http://www.w3.org/1999/xlink')
        
        if width:
            new_root.set('width', width)
        if height:
            new_root.set('height', height)
        if viewBox:
            new_root.set('viewBox', viewBox)
        
        # Add defs if present
        if defs_elem is not None:
            new_root.append(defs_elem)
        
        # Add style if present
        if style_elem is not None:
            new_root.append(style_elem)
        
        # Add this storey's group
        new_root.append(storey_group)
        
        # Write individual SVG
        new_tree = ET.ElementTree(new_root)
        new_tree.write(output_file, encoding='utf-8', xml_declaration=True)
        
        # Get file size
        size = os.path.getsize(output_file)
        created_files.append((storey_name, output_file, size))
    
    print()
    print("========================================")
    print(f"✓ Created {len(created_files)} storey floor plans")
    print("========================================")
    print()
    
    for name, filepath, size in created_files:
        size_kb = size // 1024
        print(f"  {name:30s} {size_kb:4d}KB  {os.path.basename(filepath)}")
    
    print()
    print(f"Location: {output_dir}/")
    print()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        input_svg = sys.argv[1]
        output_directory = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(input_svg)
    else:
        print("Usage: svg-split-storeys.py input.svg [output_dir]")
        sys.exit(1)
    
    split_svg_by_storeys(input_svg, output_directory)

