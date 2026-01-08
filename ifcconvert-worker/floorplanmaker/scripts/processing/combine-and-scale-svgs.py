#!/usr/bin/env python3
"""
Combine geometry and spaces SVGs with coordinate scaling.
Scales raw meter coordinates to millimeters for correct font sizing.
"""

import sys
import re
import xml.etree.ElementTree as ET

def scale_coordinates(element, scale_factor, ns):
    """
    Recursively scale all coordinates in SVG elements.
    This scales the actual coordinate values, not via transform.
    """
    
    # Scale path data
    if element.tag == '{http://www.w3.org/2000/svg}path':
        d = element.get('d', '')
        if d:
            # Scale all numeric values in path data
            def scale_number(match):
                return str(float(match.group(0)) * scale_factor)
            d_scaled = re.sub(r'-?\d+\.?\d*', scale_number, d)
            element.set('d', d_scaled)
    
    # Scale text positions and font sizes
    elif element.tag == '{http://www.w3.org/2000/svg}text':
        for attr in ['x', 'y']:
            val = element.get(attr)
            if val:
                element.set(attr, str(float(val) * scale_factor))
        
        # Scale font-size if specified as attribute
        font_size = element.get('font-size', '')
        if font_size:
            # Extract numeric value and unit
            match = re.match(r'(\d+\.?\d*)(\w*)', font_size)
            if match:
                size_val = float(match.group(1)) * scale_factor
                unit = match.group(2) or 'px'
                element.set('font-size', f'{size_val}{unit}')
    
    # Scale line endpoints
    elif element.tag == '{http://www.w3.org/2000/svg}line':
        for attr in ['x1', 'y1', 'x2', 'y2']:
            val = element.get(attr)
            if val:
                element.set(attr, str(float(val) * scale_factor))
    
    # Scale polyline/polygon points
    elif element.tag in ['{http://www.w3.org/2000/svg}polyline', '{http://www.w3.org/2000/svg}polygon']:
        points = element.get('points', '')
        if points:
            def scale_point(match):
                x, y = match.groups()
                return f'{float(x)*scale_factor},{float(y)*scale_factor}'
            points_scaled = re.sub(r'(-?\d+\.?\d*),(-?\d+\.?\d*)', scale_point, points)
            element.set('points', points_scaled)
    
    # Scale transform matrices (translate values only, not scale factors)
    transform = element.get('transform', '')
    if transform and 'translate' in transform:
        def scale_translate(match):
            x, y = match.groups()
            return f'translate({float(x)*scale_factor},{float(y)*scale_factor})'
        transform_scaled = re.sub(r'translate\((-?\d+\.?\d*),(-?\d+\.?\d*)\)', scale_translate, transform)
        element.set('transform', transform_scaled)
    
    # Recursively process children
    for child in element:
        scale_coordinates(child, scale_factor, ns)

def combine_and_scale(geometry_path, spaces_path, output_path, viewbox, width, height, scale=1000):
    """
    Combine two SVGs and apply coordinate scaling.
    
    Args:
        geometry_path: Path to geometry SVG
        spaces_path: Path to spaces SVG
        output_path: Path to output combined SVG
        viewbox: ViewBox string (e.g., "-64 -44 140 96")
        width: Canvas width
        height: Canvas height
        scale: Coordinate scale factor (default 1000 for m→mm)
    """
    print(f"Loading geometry: {geometry_path}")
    geo_tree = ET.parse(geometry_path)
    geo_root = geo_tree.getroot()
    
    print(f"Loading spaces: {spaces_path}")
    spaces_tree = ET.parse(spaces_path)
    spaces_root = spaces_tree.getroot()
    
    ns = {'svg': 'http://www.w3.org/2000/svg'}
    ET.register_namespace('', 'http://www.w3.org/2000/svg')
    ET.register_namespace('xlink', 'http://www.w3.org/1999/xlink')
    
    # Create new SVG root with viewBox and dimensions
    combined = ET.Element('{http://www.w3.org/2000/svg}svg')
    combined.set('width', f'{width}')
    combined.set('height', f'{height}')
    combined.set('viewBox', viewbox)
    
    # Merge <defs> sections
    combined_defs = ET.SubElement(combined, '{http://www.w3.org/2000/svg}defs')
    for defs in [geo_root.find('svg:defs', ns), spaces_root.find('svg:defs', ns)]:
        if defs is not None:
            for child in defs:
                combined_defs.append(child)
    
    # Merge <style> sections and scale font sizes
    combined_style_content = []
    for style in [geo_root.find('svg:style', ns), spaces_root.find('svg:style', ns)]:
        if style is not None and style.text:
            css = style.text
            # Scale font-size in CSS (e.g., "font-size: 8pt" → "font-size: 8000pt")
            def scale_font_size(match):
                size_val = float(match.group(1)) * scale
                unit = match.group(2)
                return f'font-size: {size_val}{unit}'
            css = re.sub(r'font-size:\s*(\d+\.?\d*)(\w+)', scale_font_size, css)
            
            # Scale stroke-width in CSS if needed (but be careful - we want to preserve small values)
            # For now, DON'T scale stroke-width - keep lines crisp
            
            combined_style_content.append(css)
    
    if combined_style_content:
        style_elem = ET.SubElement(combined, '{http://www.w3.org/2000/svg}style')
        style_elem.set('type', 'text/css')
        style_elem.text = '\n\n'.join(combined_style_content)
    
    # Add geometry content (skip defs and style) and scale coordinates
    print(f"  Scaling geometry coordinates by {scale}x...")
    for child in geo_root:
        if child.tag not in ['{http://www.w3.org/2000/svg}defs', '{http://www.w3.org/2000/svg}style']:
            # Scale all coordinates in-place (meters → millimeters)
            scale_coordinates(child, scale, ns)
            combined.append(child)
    
    # Add spaces content (skip defs and style) and scale coordinates
    print(f"  Scaling spaces coordinates by {scale}x...")
    for child in spaces_root:
        if child.tag not in ['{http://www.w3.org/2000/svg}defs', '{http://www.w3.org/2000/svg}style']:
            # Scale all coordinates in-place (meters → millimeters)
            scale_coordinates(child, scale, ns)
            combined.append(child)
    
    # Write output
    print(f"Writing combined SVG: {output_path}")
    tree = ET.ElementTree(combined)
    ET.indent(tree, space='    ')
    tree.write(output_path, encoding='utf-8', xml_declaration=True)
    
    print(f"✓ Combined SVG created with coordinate scaling")
    print(f"  Coordinates scaled: {scale}x (meters → millimeters)")
    print(f"  ViewBox: {viewbox}")
    print(f"  Canvas: {width}x{height}")
    print(f"  Stroke widths preserved (no transform scaling)")
    
    return output_path

if __name__ == '__main__':
    if len(sys.argv) != 8:
        print("Usage: combine-and-scale-svgs.py <geometry.svg> <spaces.svg> <output.svg> <viewbox> <width> <height> <scale>")
        print("Example: combine-and-scale-svgs.py geo.svg spaces.svg out.svg '-64 -44 140 96' 2816 2048 1000")
        sys.exit(1)
    
    geometry = sys.argv[1]
    spaces = sys.argv[2]
    output = sys.argv[3]
    viewbox = sys.argv[4]
    width = sys.argv[5]
    height = sys.argv[6]
    scale = float(sys.argv[7])
    
    combine_and_scale(geometry, spaces, output, viewbox, width, height, scale)

