from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict
from datetime import datetime
from enum import Enum

class ProcessRequest(BaseModel):
    filename: str
    operation: str
    
class IfcConvertRequest(BaseModel):
    input_filename: str
    output_filename: str
    verbose: bool = True
    plan: bool = False
    model: bool = True
    weld_vertices: bool = False
    use_world_coords: bool = False
    convert_back_units: bool = False
    sew_shells: bool = False
    merge_boolean_operands: bool = False
    disable_opening_subtractions: bool = False
    bounds: Optional[str] = None
    include: Optional[List[str]] = None
    exclude: Optional[List[str]] = None
    log_file: Optional[str] = None
    
class IfcCsvRequest(BaseModel):
    filename: str
    output_filename: str
    format: str = "csv"
    delimiter: str = ","
    null_value: str = Field("-", alias="null")
    query: str = "IfcProduct"
    attributes: List[str] = ["Name", "Description"]

class IfcCsvImportRequest(BaseModel):
    ifc_filename: str
    csv_filename: str
    output_filename: str = None

class ClashFile(BaseModel):
    file: str
    selector: Optional[str] = None
    mode: Optional[str] = 'e'

class ClashSet(BaseModel):
    name: str
    a: List[ClashFile]
    b: List[ClashFile]

class ClashMode(str, Enum):
    INTERSECTION = "intersection"
    COLLISION = "collision"
    CLEARANCE = "clearance"

class IfcClashRequest(BaseModel):
    clash_sets: List[ClashSet]
    output_filename: str
    tolerance: float = 0.01
    smart_grouping: bool = False
    max_cluster_distance: float = 5.0
    mode: ClashMode = ClashMode.INTERSECTION
    clearance: float = 0.0
    check_all: bool = False
    allow_touching: bool = False

class IfcTesterRequest(BaseModel):
    ifc_filename: str
    ids_filename: str
    output_filename: str
    report_type: str = "json"


class IfcDiffRequest(BaseModel):
    old_file: str
    new_file: str
    output_file: str = "diff.json"
    relationships: Optional[List[str]] = None
    is_shallow: bool = True
    filter_elements: Optional[str] = None

class IFC2JSONRequest(BaseModel):
    filename: str
    output_filename: str

class DownloadRequest(BaseModel):
    file_path: str

class DownloadLink(BaseModel):
    file_path: str
    token: str
    expiry: datetime

class IfcQtoRequest(BaseModel):
    input_file: str
    output_file: Optional[str] = None

class DownloadUrlRequest(BaseModel):
    url: str
    output_filename: Optional[str] = None  # Optional path to save the file to, if not provided it will use the filename from the URL

class IfcClassifyRequest(BaseModel):
    category: str
    family: str
    type: str
    manufacturer: str = ""
    description: str = ""

class IfcClassifyBatchRequest(BaseModel):
    elements: List[IfcClassifyRequest]

class IfcClassificationResult(BaseModel):
    ifc_class: str
    predefined_type: Optional[str] = None
    confidence: float
    element_id: Optional[str] = None

class IfcClassifyResponse(BaseModel):
    result: IfcClassificationResult
    processing_time_ms: float

class IfcClassifyBatchResponse(BaseModel):
    results: List[IfcClassificationResult]
    processing_time_ms: float
    total_elements: int

# IfcPatch Worker Classes
class IfcPatchRequest(BaseModel):
    """Request model for IfcPatch operations"""
    input_file: str = Field(..., description="Input IFC filename in /uploads")
    output_file: str = Field(..., description="Output IFC filename")
    recipe: str = Field(..., description="Recipe name (built-in or custom)")
    arguments: Optional[List[Any]] = Field(default=[], description="Recipe-specific arguments")
    use_custom: bool = Field(default=False, description="Whether to use custom recipe")
    
    class Config:
        json_schema_extra = {
            "example": {
                "input_file": "model.ifc",
                "output_file": "model_patched.ifc",
                "recipe": "ExtractElements",
                "arguments": [".IfcWall"],
                "use_custom": False
            }
        }

class IfcPatchListRecipesRequest(BaseModel):
    """Request to list available recipes"""
    include_custom: bool = Field(default=True, description="Include custom recipes")
    include_builtin: bool = Field(default=True, description="Include built-in recipes")

class RecipeInfo(BaseModel):
    """Information about a recipe"""
    name: str
    description: str
    is_custom: bool
    parameters: List[Dict[str, Any]]
    output_type: Optional[str] = None

class IfcPatchListRecipesResponse(BaseModel):
    """Response with available recipes"""
    recipes: List[RecipeInfo]
    total_count: int
    builtin_count: int
    custom_count: int


class RevitCommandType(str, Enum):
    PYREVIT = "pyrevit"
    RTV = "rtv"
    POWERSHELL = "powershell"

class RevitExecuteRequest(BaseModel):
    """Request to execute a Revit/PyRevit command on the Windows worker."""
    command_type: RevitCommandType = Field(..., description="Type of command: pyrevit, rtv, or powershell")
    script_path: str = Field(..., description="Path to the script, executable, or PS1 wrapper on the Windows machine")
    model_path: Optional[str] = Field(default=None, description="Path to the .rvt model (passed as positional arg for pyrevit)")
    revit_version: Optional[str] = Field(default=None, description="Revit year to launch, e.g. '2025'. Adds --revit=YYYY for pyrevit")
    batch_file: Optional[str] = Field(default=None, description="RTV batch file path (.rbxml). Passed as -BatchFile arg to the RTV wrapper script")
    arguments: Optional[List[str]] = Field(default=[], description="Additional command-line arguments")
    timeout_seconds: int = Field(default=3600, ge=10, le=86400, description="Max execution time in seconds")
    working_directory: Optional[str] = Field(default=None, description="Working directory (local or UNC path)")

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "command_type": "pyrevit",
                    "script_path": "C:\\Scripts\\test_detach_to_temp.py",
                    "model_path": "C:\\Models\\project.rvt",
                    "revit_version": "2025",
                    "timeout_seconds": 3600
                },
                {
                    "command_type": "rtv",
                    "script_path": "\\\\bim-wsp1.hogerklick.bim\\Trelleborg\\INTERAXO\\04. Etapp C Huvudbyggnad\\A\\Batch\\Run-RTVXporterBatch.ps1",
                    "batch_file": "\\\\bim-wsp1.hogerklick.bim\\Trelleborg\\INTERAXO\\04. Etapp C Huvudbyggnad\\A\\Batch\\Batch_A_007.rbxml",
                    "timeout_seconds": 7200
                }
            ]
        }