from typing import Dict, List
import ifcopenshell
from ifccsv import IfcCsv
import os
import json
import logging
from ifcclash.ifcclash import Clasher, ClashSettings

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