from fastapi import FastAPI, HTTPException, Depends
from shared.classes import IfcTesterRequest
import ifcopenshell
from ifctester import ids, reporter
import os

app = FastAPI()


@app.post("/ifctester", summary="Validate IFC against IDS", tags=["Validation"])
async def ifctester(request: IfcTesterRequest):
    """
    Validate an IFC file against an IDS (Information Delivery Specification) file.
    
    Args:
        request (IfcTesterRequest): The request body containing validation parameters.
    
    Returns:
        dict: A dictionary containing the validation results and success status.
    """
    models_dir = "/app/uploads"
    ids_dir = "/app/uploads"
    output_dir = "/app/output/ids"
    
    ifc_path = os.path.join(models_dir, request.ifc_filename)
    ids_path = os.path.join(ids_dir, request.ids_filename)
    output_path = os.path.join(output_dir, request.output_filename)
    report_type = request.report_type


    if not os.path.exists(ifc_path):
        raise HTTPException(status_code=404, detail=f"IFC file {request.ifc_filename} not found")
    if not os.path.exists(ids_path):
        raise HTTPException(status_code=404, detail=f"IDS file {request.ids_filename} not found")

    try:
        # Load the IDS file
        my_ids = ids.open(ids_path)

        # Open the IFC file
        my_ifc = ifcopenshell.open(ifc_path)

        # Validate IFC model against IDS requirements
        my_ids.validate(my_ifc)

        if report_type == "json":
            # Generate JSON report
            json_reporter = reporter.Json(my_ids)
            json_reporter.report()
            json_reporter.to_file(output_path)

            # Get a summary of the results
            total_specs = len(my_ids.specifications)
            passed_specs = sum(1 for spec in my_ids.specifications if spec.status)
            failed_specs = total_specs - passed_specs

            return {
                "success": True,
                "total_specifications": total_specs,
                "passed_specifications": passed_specs,
                "failed_specifications": failed_specs,
                "report": json_reporter.to_string()
            }
        
        if report_type == "html":
            # Generate JSON report
            html_reporter = reporter.Html(my_ids)
            html_reporter.report()
            html_reporter.to_file(output_path)


            return {
                "success": True,
                "report": html_reporter.to_string()
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    return {"status": "healthy"}