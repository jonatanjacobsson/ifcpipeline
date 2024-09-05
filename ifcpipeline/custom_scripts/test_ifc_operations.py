import os
from ifc_operations import process_ifccsv, import_ifccsv, process_ifcclash

def test_process_ifccsv():
    file_path = "./models/A1_1_BIM_XXX_0001_00.ifc"
    output_path = "output.csv"
    query = "IfcWall"
    attributes = ["Name", "GlobalId"]
    
    result = process_ifccsv(file_path, output_path, query, attributes)
    print("Process IFC CSV Result:", result)


def test_process_ifcclash():
    clash_sets = [
        {
            "name": "Clash Set A",
            "a": [{"file": "./models/A1_1_BIM_XXX_0001_00.ifc"}],
            "b": [{"file": "./models/A1_1_BIM_XXX_0002_00.ifc"}]
        }
    ]
    output_path = "clash_results.txt"
    tolerance = 0.01
    
    result = process_ifcclash(clash_sets, output_path, tolerance)
    print("Process IFC Clash Result:", result)

if __name__ == "__main__":
    print("Testing process_ifccsv:")
    test_process_ifccsv()
        
    print("\nTesting process_ifcclash:")
    test_process_ifcclash()