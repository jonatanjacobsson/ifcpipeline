#!/bin/bash
# Detect all building storeys from IFC model

echo "Detecting all building storeys from A1 model..."
echo ""

# Use Python with ifcopenshell to properly parse storeys
python3 << 'PYTHON'
import ifcopenshell
import re

ifc_file = ifcopenshell.open('/home/bimbot-ubuntu/apps/ifcpipeline/shared/uploads/A1_2b_BIM_XXX_0001_00.v24.0.ifc')

storeys = ifc_file.by_type('IfcBuildingStorey')

print(f"Found {len(storeys)} building storeys:\n")
print("=" * 80)

storey_data = []

for storey in storeys:
    name = storey.Name if storey.Name else "Unnamed"
    
    # Get elevation from placement
    elevation = 0.0
    if storey.ObjectPlacement:
        if hasattr(storey.ObjectPlacement, 'RelativePlacement'):
            placement = storey.ObjectPlacement.RelativePlacement
            if hasattr(placement, 'Location'):
                location = placement.Location
                if hasattr(location, 'Coordinates'):
                    elevation = float(location.Coordinates[2])
    
    storey_data.append((name, elevation))

# Sort by elevation
storey_data.sort(key=lambda x: x[1])

for i, (name, elev) in enumerate(storey_data, 1):
    section_height = elev + 1.2  # Add 1.2m offset for section cut
    print(f"{i}. {name}")
    print(f"   Elevation: {elev:.2f}m")
    print(f"   Section height: {section_height:.2f}m")
    print()

print("=" * 80)
print("\nGenerate command array for bash:\n")

for name, elev in storey_data:
    section_height = elev + 1.2
    print(f'    "{name}|{section_height:.2f}"')

PYTHON

