from typing import Dict, List
import ifcopenshell
from ifccsv import IfcCsv
import os
import json
import logging
from ifcclash.ifcclash import Clasher, ClashSettings
import subprocess

def process_ifccsv(file_path, output_path, query, attributes, format="csv", delimiter=",", null="-"):
    try:
        model = ifcopenshell.open(file_path)
        elements = ifcopenshell.util.selector.filter_elements(model, query)

        ifc_csv = IfcCsv()
        ifc_csv.export(model, elements, attributes)

        if format == "csv":
            ifc_csv.export_csv(output_path, delimiter=delimiter)
        elif format == "ods":
            ifc_csv.export_ods(output_path)
        elif format == "xlsx":
            ifc_csv.export_xlsx(output_path)
        else:
            raise ValueError(f"Unsupported format: {format}")

        # Return headers and results for API response
        return {
            "headers": ifc_csv.headers,
            "results": ifc_csv.results
        }
    except Exception as e:
        raise Exception(f"Error processing IFC CSV: {str(e)}")

def import_ifccsv(file_path, input_csv_path, output_path):
    try:
        model = ifcopenshell.open(file_path)
        ifc_csv = IfcCsv()
        ifc_csv.Import(model, input_csv_path)
        model.write(output_path)
        return {"message": "IFC file updated successfully"}
    except Exception as e:
        raise Exception(f"Error importing IFC CSV: {str(e)}")

def process_ifcclash(clash_sets: List[Dict], output_path: str, tolerance: float = 0.01):
    try:
        # Set up logging
        logging.basicConfig(level=logging.INFO)
        logger = logging.getLogger("ifcclash")

        settings = ClashSettings()
        settings.output = output_path
        settings.logger = logger  # Set the logger

        clasher = Clasher(settings)

        for clash_set in clash_sets:
            clasher_set = {
                "name": clash_set.get("name", "Unnamed Set"),
                "a": [],
                "b": [],
                "tolerance": tolerance,
                "mode": clash_set.get("mode", "intersection"),
                "check_all": clash_set.get("check_all", False),
                "allow_touching": clash_set.get("allow_touching", False),
                "clearance": clash_set.get("clearance", 0.0)
            }

            for a in clash_set['a']:
                clasher_set["a"].append({
                    "file": a['file'],
                    "mode": a.get('mode'),
                    "selector": a.get('selector')
                })

            for b in clash_set['b']:
                clasher_set["b"].append({
                    "file": b['file'],
                    "mode": b.get('mode'),
                    "selector": b.get('selector')
                })

            clasher.clash_sets.append(clasher_set)

        clasher.clash()
        clasher.export()

        # Read the results from the output file
        with open(output_path, 'r') as f:
            results = json.load(f)

        return {
            "clash_count": sum(len(clash_set["clashes"]) for clash_set in results),
            "clashes": results
        }
    except Exception as e:
        raise Exception(f"Error processing IFC Clash: {str(e)}")

def process_ifcdiff(old_file, new_file, output_file, relationships=None):
    command = ["python", "-m", "ifcdiff"]
    
    if relationships:
        command.extend(["-r", " ".join(relationships)])
    
    command.extend(["-o", output_file, old_file, new_file])
    
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        
        with open(output_file, 'r') as json_file:
            diff_results = json.load(json_file)
        
        return {
            "success": True,
            "message": f"IFC diff completed successfully. Results saved to {output_file}",
            "results": diff_results,
            "stdout": result.stdout,
            "stderr": result.stderr
        }
    except subprocess.CalledProcessError as e:
        return {
            "success": False,
            "message": f"IFC diff failed: {e.stderr}",
            "stdout": e.stdout,
            "stderr": e.stderr
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Error during IFC diff: {str(e)}"
        }