from fastapi import FastAPI, HTTPException, Depends
from shared.classes import IfcCsvRequest, IfcCsvImportRequest
import ifcopenshell
import ifccsv
import os
import pandas as pd
import openpyxl

app = FastAPI()

@app.post("/ifccsv", summary="Convert IFC to CSV", tags=["Conversion"])
async def api_ifccsv(request: IfcCsvRequest):
    """
    Convert an IFC file to CSV format.
    
    Args:
        request (IfcCsvRequest): The request body containing conversion parameters.
    
    Returns:
        dict: A dictionary containing the conversion results and success status.
    """
    models_dir = "/uploads"
    output_dir = "/output/csv"
    file_path = os.path.join(models_dir, request.filename)
    output_path = os.path.join(output_dir, request.output_filename)

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"File {request.filename} not found")

    try:
        model = ifcopenshell.open(file_path)
        elements = ifcopenshell.util.selector.filter_elements(model, request.query)

        ifc_csv = ifccsv.IfcCsv()
        ifc_csv.export(model, elements, request.attributes)

        if request.format == "csv":
            ifc_csv.export_csv(output_path, delimiter=request.delimiter)
        elif request.format == "ods":
            ifc_csv.export_ods(output_path)
        elif request.format == "xlsx":
            ifc_csv.export_xlsx(output_path)
        else:
            raise ValueError(f"Unsupported format: {request.format}")

        result = {
            "headers": ifc_csv.headers,
            "results": ifc_csv.results
        }

        return {"success": True, "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/ifccsv/import", summary="Import CSV changes to IFC", tags=["Conversion"])
async def patch_ifc_from_csv(request: IfcCsvImportRequest):
    """
    Import changes from a CSV file into an IFC model.
    
    Args:
        request (IfcCsvImportRequest): The request containing the IFC and CSV filenames.
    
    Returns:
        dict: A dictionary containing the success status and output file path.
    """
    models_dir = "/uploads"
    csv_dir = "/output/csv"
    output_dir = "/output/ifc"
    
    # Construct file paths
    ifc_path = os.path.join(models_dir, request.ifc_filename)
    csv_path = os.path.join(csv_dir, request.csv_filename)
    
    # Check if input files exist
    if not os.path.exists(ifc_path):
        raise HTTPException(status_code=404, detail=f"IFC file {request.ifc_filename} not found")
    if not os.path.exists(csv_path):
        raise HTTPException(status_code=404, detail=f"CSV file {request.csv_filename} not found")
    
    try:
        # Open the IFC model
        model = ifcopenshell.open(ifc_path)
        
        # Create IfcCsv instance and import changes
        ifc_csv = ifccsv.IfcCsv()
        ifc_csv.Import(model, csv_path)
        
        # Determine output path
        if request.output_filename:
            output_path = os.path.join(output_dir, request.output_filename)
        else:
            output_path = ifc_path  # Overwrite original file if no output filename provided
            
        # Ensure output directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        # Write the updated model
        model.write(output_path)
        
        return {
            "success": True,
            "message": "CSV changes successfully imported to IFC model",
            "output_path": output_path
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error importing CSV changes: {str(e)}")

@app.get("/health")
async def health_check():
    return {"status": "healthy"}