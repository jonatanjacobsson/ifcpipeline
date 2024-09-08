from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from typing import List, Dict, Optional, ForwardRef
import ipaddress
import json
import os
import requests
import logging
import ifcopenshell
from ifctester import ids, reporter
from ifccsv import IfcCsv
from ifcclash.ifcclash import Clasher, ClashSettings

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Define the load_config function
def load_config():
    config_path = '/app/config.json'
    default_config = {
        'api_keys': [],
        'allowed_ip_ranges': ['127.0.0.1/32']  # Default to localhost only
    }
    
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        logger.info(f"Loaded configuration: {config}")
        return config
    except FileNotFoundError:
        logger.warning(f"Config file not found at {config_path}. Using default configuration.")
        return default_config
    except json.JSONDecodeError:
        logger.error(f"Error decoding {config_path}. Using default configuration.")
        return default_config

# Load configuration
config = load_config()
API_KEYS = config.get('api_keys', [])
ALLOWED_IP_RANGES = [ipaddress.ip_network(cidr) for cidr in config.get('allowed_ip_ranges', [])]

# Initialize FastAPI app
app = FastAPI(swagger_ui_parameters={"tryItOutEnabled": True})

# Set up API key header
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Define the verify_access function
async def verify_access(request: Request, api_key: str = Depends(api_key_header)):
    client_ip = ipaddress.ip_address(request.client.host)
    logger.info(f"Access attempt from IP: {client_ip}")
    
    if any(client_ip in ip_range for ip_range in ALLOWED_IP_RANGES):
        logger.info(f"Access granted to {client_ip} (IP in allowed range)")
        return True
    
    if not api_key:
        logger.warning(f"Access denied to {client_ip} (No API key provided)")
        raise HTTPException(status_code=403, detail="API key required")
    
    if api_key not in API_KEYS:
        logger.warning(f"Access denied to {client_ip} (Invalid API key)")
        raise HTTPException(status_code=403, detail="Invalid API key")
    
    logger.info(f"Access granted to {client_ip} (Valid API key)")
    return True

# Define model classes
class ProcessRequest(BaseModel):
    filename: str
    operation: str

class IfcCsvRequest(BaseModel):
    filename: str
    output_filename: str = Field(..., alias="spreadsheet")
    format: str = "csv"
    delimiter: str = ","
    null_value: str = Field("-", alias="null")
    query: str = "IfcProduct"
    attributes: List[str] = ["Name", "Description"]

class IfcCsvImportRequest(BaseModel):
    ifc_filename: str
    csv_filename: str
    output_filename: str

class DownloadIFCRequest(BaseModel):
    output_filename: str
    url: str

class ClashFile(BaseModel):
    file: str
    selector: Optional[str] = None
    mode: Optional[str] = 'e'

class ClashSet(BaseModel):
    name: str
    a: List[ClashFile]
    b: List[ClashFile]

class IfcClashRequest(BaseModel):
    clash_sets: List[ClashSet]
    output_filename: str
    tolerance: float = 0.01

class IfcTesterRequest(BaseModel):
    ifc_filename: str
    ids_filename: str
    output_filename: str

class CustomClashSettings(ClashSettings):
    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__)

class CustomClasher(Clasher):
    def __init__(self, settings):
        super().__init__(settings)
        self.logger = logging.getLogger(__name__)
        if not hasattr(self.settings, 'logger') or self.settings.logger is None:
            self.settings.logger = self.logger

# Define route handlers
@app.get("/health")
async def health_check():
    return {"status": "healthy"}

@app.get("/list_models")
async def list_models():
    models_dir = "/app/models"
    try:
        files = [f for f in os.listdir(models_dir) if f != '.gitkeep']
        return {"files": files}
    except Exception as e:
        return {"error": str(e)}

@app.post("/ifccsv")
async def api_ifccsv(request: IfcCsvRequest, _: str = Depends(verify_access)):
    models_dir = "/app/models"
    output_dir = "/app/output/csv"
    file_path = os.path.join(models_dir, request.filename)
    output_path = os.path.join(output_dir, request.output_filename)

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"File {request.filename} not found")

    try:
        model = ifcopenshell.open(file_path)
        elements = ifcopenshell.util.selector.filter_elements(model, request.query)

        ifc_csv = IfcCsv()
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


@app.post("/ifcclash")
async def api_ifcclash(request: IfcClashRequest, _: str = Depends(verify_access)):
    models_dir = "/app/models"
    output_dir = "/app/output/clash"
    output_path = os.path.join(output_dir, request.output_filename)

    logger.info(f"Starting clash detection for {len(request.clash_sets)} clash sets")

    # Validate that all specified files exist
    for clash_set in request.clash_sets:
        for file in clash_set.a + clash_set.b:
            file_path = os.path.join(models_dir, file.file)
            if not os.path.exists(file_path):
                logger.error(f"File not found: {file_path}")
                raise HTTPException(status_code=404, detail=f"File {file.file} not found")

    try:
        settings = CustomClashSettings()  # Use CustomClashSettings instead of ClashSettings
        settings.output = output_path

        logger.info(f"Clash output will be saved to: {output_path}")

        clasher = CustomClasher(settings)  # Use CustomClasher instead of Clasher

        for clash_set in request.clash_sets:
            clasher_set = {
                "name": clash_set.name,
                "a": [],
                "b": [],
                "tolerance": request.tolerance,
                "mode": "intersection",
                "check_all": False,
                "allow_touching": False,
                "clearance": 0.0
            }

            for side in ['a', 'b']:
                for file in getattr(clash_set, side):
                    file_path = os.path.join(models_dir, file.file)
                    logger.info(f"Adding file to clash set: {file_path}")
                    clasher_set[side].append({
                        "file": file_path,
                        "mode": file.mode,
                        "selector": file.selector
                    })

            clasher_set[side].append({
                "file": file_path,
                "mode": file.mode,
                "selector": file.selector
            })


            clasher.clash_sets.append(clasher_set)

        logger.info("Starting clash detection")
        clasher.clash()
        logger.info("Clash detection completed")

        logger.info("Smart clashes....")
        preprocessed_clash_sets = preprocess_clash_data(clasher.clash_sets)
        smart_groups = clasher.smart_group_clashes(preprocessed_clash_sets, 10)

        logger.info("Exporting clash results")
        clasher.export()
        logger.info("Clash results exported")

        # Read the JSON result from the output file
        with open(output_path, 'r') as json_file:
            clash_results = json.load(json_file)

        clash_count = sum(len(clash_set["clashes"]) for clash_set in clash_results)
        logger.info(f"Total clashes found: {clash_count}")

        return {
            "success": True,
            "clash_count": clash_count,
            "result": clash_results
        }
    except Exception as e:
        logger.error(f"Error during clash detection: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    
def preprocess_clash_data(clash_sets):
    for clash_set in clash_sets:
        clashes = clash_set["clashes"]
        for clash in clashes.values():
            p1 = clash["p1"]
            p2 = clash["p2"]
            # Calculate the midpoint and add it as the "position" key
            clash["position"] = [(p1[i] + p2[i]) / 2 for i in range(3)]
    return clash_sets

@app.post("/ifctester")
async def ifctester(request: IfcTesterRequest, _: str = Depends(verify_access)):
    models_dir = "/app/models"
    ids_dir = "/app/ids"  # Assuming the IDS files are in the /ids directory
    output_dir = "/app/output/ids"
    
    ifc_path = os.path.join(models_dir, request.ifc_filename)
    ids_path = os.path.join(ids_dir, request.ids_filename)
    output_path = os.path.join(output_dir, request.output_filename)


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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.post("/download_ifc")
async def download_ifc(request: DownloadIFCRequest, _: str = Depends(verify_access)):
    models_dir = "/app/models"
    output_path = os.path.join(models_dir, request.output_filename)

    headers = {
        "accept": "application/vnd.api+json"
    }

    try:
        response = requests.get(request.url, headers=headers)
        response.raise_for_status()

        with open(output_path, 'wb') as f:
            f.write(response.content)

        return {"success": True, "message": f"{request.output_filename}"}
    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Failed to download file: {str(e)}")
    except IOError as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")

