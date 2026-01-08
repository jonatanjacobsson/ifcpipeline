#!/usr/bin/env python3
"""
Calculate bounding box from IFC geometry at a specific section height.
Uses IfcOpenShell to read IFC and calculate bounds from actual geometry.
"""

import sys
import ifcopenshell
import ifcopenshell.geom
import numpy as np
from pathlib import Path

def calculate_bounds(ifc_file_paths, section_height, element_types):
    """
    Calculate bounding box from IFC elements at a given section height.
    
    Args:
        ifc_file_paths: Single path or list of paths to IFC files
        section_height: Height in meters for the section plane
        element_types: List of IFC element types to include (e.g., ['IfcWall', 'IfcDoor'])
    
    Returns:
        Dictionary with bounds information
    """
    # Handle both single file and multiple files
    if isinstance(ifc_file_paths, str):
        ifc_file_paths = [ifc_file_paths]
    
    # Collect all vertices from all files
    all_points = []
    element_count = 0
    
    print(f"Section height: {section_height}m", file=sys.stderr)
    print(f"Element types: {', '.join(element_types)}", file=sys.stderr)
    
    # Create geometry settings for processing
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    
    for ifc_file_path in ifc_file_paths:
        print(f"Loading IFC file: {ifc_file_path}", file=sys.stderr)
        ifc_file = ifcopenshell.open(ifc_file_path)
        
        for element_type in element_types:
            elements = ifc_file.by_type(element_type)
            print(f"  Found {len(elements)} {element_type} elements in {Path(ifc_file_path).name}", file=sys.stderr)
            
            for element in elements:
                try:
                    # Get the geometry representation
                    shape = ifcopenshell.geom.create_shape(settings, element)
                    
                    # Get vertices from the shape
                    verts = shape.geometry.verts
                    
                    # Vertices are stored as flat list: [x1,y1,z1, x2,y2,z2, ...]
                    for i in range(0, len(verts), 3):
                        x, y, z = verts[i], verts[i+1], verts[i+2]
                        
                        # Only include points near the section height (within ±2m)
                        if abs(z - section_height) < 2.0:
                            all_points.append([x, y, z])
                    
                    element_count += 1
                    
                except Exception as e:
                    # Some elements may not have geometry
                    pass
    
    print(f"  Processed {element_count} elements with geometry from {len(ifc_file_paths)} file(s)", file=sys.stderr)
    print(f"  Collected {len(all_points)} vertices near section height", file=sys.stderr)
    
    if len(all_points) == 0:
        print("ERROR: No geometry found at section height!", file=sys.stderr)
        sys.exit(1)
    
    # Calculate bounding box
    points = np.array(all_points)
    min_x, min_y, min_z = np.min(points, axis=0)
    max_x, max_y, max_z = np.max(points, axis=0)
    
    # Calculate center and dimensions
    center_x = (min_x + max_x) / 2
    center_y = (min_y + max_y) / 2
    center_z = (min_z + max_z) / 2
    
    width = max_x - min_x
    height = max_y - min_y
    depth = max_z - min_z
    
    print(f"  Bounding box:", file=sys.stderr)
    print(f"    Min: ({min_x:.2f}, {min_y:.2f}, {min_z:.2f})", file=sys.stderr)
    print(f"    Max: ({max_x:.2f}, {max_y:.2f}, {max_z:.2f})", file=sys.stderr)
    print(f"    Center: ({center_x:.2f}, {center_y:.2f}, {center_z:.2f})", file=sys.stderr)
    print(f"    Size: {width:.2f} x {height:.2f} x {depth:.2f} meters", file=sys.stderr)
    
    # Output the results as a single line for shell script parsing
    # Format: min_x min_y max_x max_y center_x center_y width height
    print(f"{min_x} {min_y} {max_x} {max_y} {center_x} {center_y} {width} {height}")
    
    return {
        'min_x': min_x,
        'min_y': min_y,
        'max_x': max_x,
        'max_y': max_y,
        'center_x': center_x,
        'center_y': center_y,
        'width': width,
        'height': height
    }


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: calculate-bounds.py <ifc_file> <section_height> <element_types...>", file=sys.stderr)
        print("Example: calculate-bounds.py model.ifc 3.1 IfcWall IfcDoor IfcWindow", file=sys.stderr)
        sys.exit(1)
    
    ifc_file_path = sys.argv[1]
    section_height = float(sys.argv[2])
    element_types = sys.argv[3:]
    
    if not Path(ifc_file_path).exists():
        print(f"ERROR: IFC file not found: {ifc_file_path}", file=sys.stderr)
        sys.exit(1)
    
    calculate_bounds(ifc_file_path, section_height, element_types)

