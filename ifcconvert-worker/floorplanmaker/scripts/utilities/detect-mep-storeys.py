#!/usr/bin/env python3
"""Detect building storeys from all IFC models"""

import ifcopenshell
import sys

models = {
    "Architecture (A1)": "/home/bimbot-ubuntu/apps/ifcpipeline/shared/uploads/A1_2b_BIM_XXX_0001_00.v24.0.ifc",
    "Electrical (E1)": "/home/bimbot-ubuntu/apps/ifcpipeline/shared/uploads/E1_2b_BIM_XXX_600_00.v183.0.ifc",
    "Mechanical (M1)": "/home/bimbot-ubuntu/apps/ifcpipeline/shared/uploads/M1_2b_BIM_XXX_5700_00.v12.0.ifc",
    "Plumbing (P1)": "/home/bimbot-ubuntu/apps/ifcpipeline/shared/uploads/P1_2b_BIM_XXX_5000_00.v12.0.ifc",
    "Structural (S2)": "/home/bimbot-ubuntu/apps/ifcpipeline/shared/uploads/S2_2B_BIM_XXX_0001_00.v12.0.ifc",
}

print("="*80)
print(" BUILDING STOREYS IN ALL IFC MODELS")
print("="*80)
print()

for model_name, model_path in models.items():
    try:
        print(f"📐 {model_name}")
        print(f"   {model_path.split('/')[-1]}")
        print()
        
        ifc_file = ifcopenshell.open(model_path)
        storeys = ifc_file.by_type('IfcBuildingStorey')
        
        if not storeys:
            print(f"   ⚠️  No IfcBuildingStorey elements found")
            print()
            continue
        
        storey_data = []
        for storey in storeys:
            name = storey.Name if storey.Name else "Unnamed"
            
            # Get elevation
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
        
        print(f"   Found {len(storey_data)} storeys:")
        for name, elev in storey_data:
            section_height = elev + 1.2
            print(f"      • {name:40s} @ {elev:6.2f}m  (section: {section_height:.2f}m)")
        
        print()
        
    except Exception as e:
        print(f"   ❌ Error loading model: {e}")
        print()

print("="*80)
