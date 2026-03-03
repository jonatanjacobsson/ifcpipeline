import re
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Any, Dict
from datetime import datetime
from enum import Enum

# Safe filename: alphanumeric, dot, underscore, hyphen only (no path traversal, shell metacharacters, or injection)
SAFE_FILENAME_PATTERN = re.compile(r"^[a-zA-Z0-9._-]+$")
# Safe path: like filename but allows / for path segments; rejects ..
SAFE_PATH_PATTERN = re.compile(r"^(?!.*\.\.)[a-zA-Z0-9._/-]+$")


def _validate_safe_filename(v: str) -> str:
    if not v or not SAFE_FILENAME_PATTERN.match(v):
        raise ValueError(
            "Invalid filename: only letters, numbers, dots, underscores, and hyphens allowed"
        )
    return v


def _validate_safe_path(v: str) -> str:
    if not v:
        raise ValueError("Path cannot be empty")
    if ".." in v or ";" in v or "`" in v or "|" in v or "$" in v or "&" in v:
        raise ValueError("Path contains invalid or dangerous characters")
    for part in v.split("/"):
        if part and not SAFE_FILENAME_PATTERN.match(part):
            raise ValueError("Path contains invalid characters")
    return v


class ProcessRequest(BaseModel):
    filename: str
    operation: str

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, v: str) -> str:
        return _validate_safe_path(v)


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

    @field_validator("input_filename", "output_filename")
    @classmethod
    def validate_filenames(cls, v: str) -> str:
        return _validate_safe_path(v)

    @field_validator("log_file")
    @classmethod
    def validate_log_file(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return _validate_safe_path(v)


class IfcCsvRequest(BaseModel):
    filename: str
    output_filename: str
    format: str = "csv"
    delimiter: str = ","
    null_value: str = Field("-", alias="null")
    query: str = "IfcProduct"
    attributes: List[str] = ["Name", "Description"]

    @field_validator("filename", "output_filename")
    @classmethod
    def validate_filenames(cls, v: str) -> str:
        return _validate_safe_path(v)


class IfcCsvImportRequest(BaseModel):
    ifc_filename: str
    csv_filename: str
    output_filename: str = None

    @field_validator("ifc_filename", "csv_filename", "output_filename")
    @classmethod
    def validate_filenames(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return _validate_safe_path(v)


class ClashFile(BaseModel):
    file: str
    selector: Optional[str] = None
    mode: Optional[str] = "e"

    @field_validator("file")
    @classmethod
    def validate_file(cls, v: str) -> str:
        return _validate_safe_path(v)


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

    @field_validator("output_filename")
    @classmethod
    def validate_output_filename(cls, v: str) -> str:
        return _validate_safe_path(v)


class IfcTesterRequest(BaseModel):
    ifc_filename: str
    ids_filename: str
    output_filename: str
    report_type: str = "json"

    @field_validator("ifc_filename", "ids_filename", "output_filename")
    @classmethod
    def validate_filenames(cls, v: str) -> str:
        return _validate_safe_path(v)


class IfcDiffRequest(BaseModel):
    old_file: str
    new_file: str
    output_file: str = "diff.json"
    relationships: Optional[List[str]] = None
    is_shallow: bool = True
    filter_elements: Optional[str] = None

    @field_validator("old_file", "new_file", "output_file")
    @classmethod
    def validate_files(cls, v: str) -> str:
        return _validate_safe_path(v)


class IFC2JSONRequest(BaseModel):
    filename: str
    output_filename: str

    @field_validator("filename", "output_filename")
    @classmethod
    def validate_filenames(cls, v: str) -> str:
        return _validate_safe_path(v)


class DownloadRequest(BaseModel):
    file_path: str

    @field_validator("file_path")
    @classmethod
    def validate_file_path(cls, v: str) -> str:
        return _validate_safe_path(v)


class DownloadLink(BaseModel):
    file_path: str
    token: str
    expiry: datetime

class IfcQtoRequest(BaseModel):
    input_file: str
    output_file: Optional[str] = None

    @field_validator("input_file", "output_file")
    @classmethod
    def validate_files(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return _validate_safe_path(v)


class DownloadUrlRequest(BaseModel):
    url: str
    output_filename: Optional[str] = None  # Optional path to save the file to, if not provided it will use the filename from the URL

    @field_validator("output_filename")
    @classmethod
    def validate_output_filename(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return _validate_safe_path(v)


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

    @field_validator("input_file", "output_file")
    @classmethod
    def validate_paths(cls, v: str) -> str:
        return _validate_safe_path(v)

    @field_validator("recipe")
    @classmethod
    def validate_recipe(cls, v: str) -> str:
        return _validate_safe_filename(v)

    @field_validator("arguments")
    @classmethod
    def validate_arguments(cls, v: Optional[List[Any]]) -> Optional[List[Any]]:
        if v is None:
            return v
        # Block shell metacharacters; allow natural text including & for international content
        dangerous = set(";`$|")
        for i, arg in enumerate(v):
            if isinstance(arg, str) and any(c in arg for c in dangerous):
                raise ValueError(
                    f"Argument at index {i} contains invalid or dangerous characters"
                )
        return v

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