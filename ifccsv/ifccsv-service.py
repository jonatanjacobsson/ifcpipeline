from fastapi import FastAPI, HTTPException, Depends
from shared.classes import IfcCsvRequest
import ifcopenshell
import ifccsv
import os

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
    models_dir = "/app/uploads"
    output_dir = "/app/output/csv"
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


@app.get("/health")
async def health_check():
    return {"status": "healthy"}