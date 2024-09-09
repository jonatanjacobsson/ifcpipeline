import os
from ifcpipeline.custom_scripts.ifc_operations import process_ifccsv, import_ifccsv, process_ifcclash, process_ifcdiff

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

def test_process_ifcdiff():
    old_file = "./A1_1_BIM_XXX_0001_00_old.ifc"
    new_file = "./A1_1_BIM_XXX_0001_00.ifc"
    output_file = "diff_results.json"
    relationships = ["property", "type"]
    
    # Check if files exist
    if not os.path.exists(old_file):
        print(f"Error: Old file '{old_file}' does not exist.")
        return
    if not os.path.exists(new_file):
        print(f"Error: New file '{new_file}' does not exist.")
        return
    
    # Print current working directory and list files in the models directory
    print(f"Current working directory: {os.getcwd()}")
    print("Files in models directory:")
    for file in os.listdir("./"):
        print(f"  {file}")
    
    result = process_ifcdiff(old_file, new_file, output_file, relationships)
    print("Process IFC Diff Result:", result)

if __name__ == "__main__":
    
    print("\nTesting process_ifcdiff:")
    test_process_ifcdiff()