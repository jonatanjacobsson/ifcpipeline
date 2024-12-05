from pydantic import BaseModel, Field
from typing import List, Optional
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
    output_filename: str

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
