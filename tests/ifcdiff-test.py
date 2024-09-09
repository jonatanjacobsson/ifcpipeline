import ifcopenshell
from ifcdiff import IfcDiff
import logging
import json
import os
import argparse

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def perform_ifc_diff(old_file, new_file, output_file):
    """
    Compare two IFC files and generate a diff report.
    
    Args:
        old_file (str): Path to the old IFC file.
        new_file (str): Path to the new IFC file.
        output_file (str): Path to save the output JSON file.
    
    Returns:
        dict: A dictionary containing the diff results and success status.
    """
    if not os.path.exists(old_file):
        raise FileNotFoundError(f"Old file {old_file} not found")
    if not os.path.exists(new_file):
        raise FileNotFoundError(f"New file {new_file} not found")

    try:

        ifc_diff = IfcDiff("/path/to/old.ifc", "/path/to/new.ifc", "/path/to/diff.json")
        ifc_diff.diff()
        print(ifc_diff.change_register)
        ifc_diff.export()

    except Exception as e:
        logger.error(f"Error during IFC diff: {str(e)}", exc_info=True)
        raise

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compare two IFC files and generate a diff report.")
    parser.add_argument("old_file", help="Path to the old IFC file")
    parser.add_argument("new_file", help="Path to the new IFC file")
    parser.add_argument("output_file", help="Path to save the output JSON file")
    
    args = parser.parse_args()

    try:
        result = perform_ifc_diff(args.old_file, args.new_file, args.output_file)
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"Error: {str(e)}")