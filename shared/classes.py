import os
import re
from typing import Annotated, Any, Dict, List, Optional
from pydantic import AliasChoices, BaseModel, Field, field_validator
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


def _validate_original_upload_basename(v: str) -> str:
    """Accept human upload names (spaces, Unicode); reject path traversal and shell metacharacters."""
    name = os.path.basename(v.strip())
    if not name:
        raise ValueError("Filename cannot be empty")
    if ".." in name or "/" in name or "\\" in name or "\x00" in name:
        raise ValueError("Invalid filename")
    for bad in (";", "|", "`", "$", "&"):
        if bad in name:
            raise ValueError("Filename contains invalid characters")
    if len(name) > 512:
        raise ValueError("Filename too long")
    return name


def _validate_safe_path(v: str) -> str:
    if not v:
        raise ValueError("Path cannot be empty")
    if ".." in v or ";" in v or "`" in v or "|" in v or "$" in v or "&" in v:
        raise ValueError("Path contains invalid or dangerous characters")

    # Accept the canonical `s3://<bucket>/<key>` URI that gateway endpoints
    # (`/upload/*`, `/download-from-url`) and worker outputs now return.
    # Strip `s3://<bucket>/` before per-segment validation so the rest of
    # the key is checked the same way a legacy `uploads/foo.ifc` path is.
    # The bucket segment is still required to match SAFE_FILENAME_PATTERN
    # so a crafted URI cannot smuggle unsafe characters past the scheme.
    # `shared.object_storage.normalize_input_key` strips the same prefix
    # before handing the key to S3, keeping gateway validation and worker
    # resolution in lockstep.
    path_part = v
    if path_part.startswith("s3://"):
        rest = path_part[len("s3://"):]
        bucket, sep, key = rest.partition("/")
        if not sep or not bucket or not key:
            raise ValueError("Invalid s3 URI: expected s3://<bucket>/<key>")
        if not SAFE_FILENAME_PATTERN.match(bucket):
            raise ValueError("s3 URI bucket contains invalid characters")
        path_part = key

    for part in path_part.split("/"):
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


class VersionPinOptional(BaseModel):
    """Optional MinIO S3 VersionId / audit pinning (n8n `applyVersionPins`).

    CDE and workers resolve these via `shared.object_storage.pin_for`.
    For two-input jobs use `input_version_ids` (per object key) or explicit
    per-side fields (e.g. ifcdiff); a single `input_audit_id` cannot pin two files.
    """

    input_version_id: Optional[str] = None
    input_version_ids: Optional[Dict[str, str]] = None
    input_audit_id: Optional[int] = None


class IfcConvertRequest(BaseModel):
    """Mirror of the flags that ifcconvert-worker's tasks.py looks at.

    Every option the worker reads is listed here with a safe default so that
    `request.<flag>` attribute access does not explode when callers only
    provide `input_filename`/`output_filename`.

    The IfcConvert ``--validate`` flag is exposed as ``validate_geometry`` on
    the Python side (with JSON alias ``validate``) to avoid shadowing the
    deprecated ``BaseModel.validate`` classmethod, which otherwise emits a
    ``UserWarning`` at every worker import.
    """

    input_filename: str
    output_filename: str
    # MinIO/S3 VersionId pinning (same semantics as VersionPinOptional).
    input_version_id: Optional[str] = None
    input_version_ids: Optional[Dict[str, str]] = None
    input_audit_id: Optional[int] = None

    # --- command-line options ------------------------------------------------
    verbose: bool = False
    quiet: bool = False
    cache: bool = False
    cache_file: Optional[str] = None
    stderr_progress: bool = False
    yes: bool = False
    no_progress: bool = False
    log_format: Optional[str] = None
    log_file: Optional[str] = None

    # --- geometry options ----------------------------------------------------
    kernel: Optional[str] = None
    threads: Optional[int] = None
    center_model: bool = False
    center_model_geometry: bool = False

    include: Optional[List[str]] = None
    include_type: Optional[str] = None
    include_plus: Optional[List[str]] = None
    include_plus_type: Optional[str] = None
    exclude: Optional[List[str]] = None
    exclude_type: Optional[str] = None
    exclude_plus: Optional[List[str]] = None
    exclude_plus_type: Optional[str] = None
    filter_file: Optional[str] = None

    default_material_file: Optional[str] = None
    exterior_only: Optional[str] = None
    apply_default_materials: bool = False
    use_material_names: bool = False
    surface_colour: bool = False

    plan: bool = False
    model: bool = True
    dimensionality: Optional[int] = None

    mesher_linear_deflection: Optional[float] = None
    mesher_angular_deflection: Optional[float] = None
    reorient_shells: bool = False

    length_unit: Optional[float] = None
    angle_unit: Optional[float] = None
    precision: Optional[float] = None
    precision_factor: Optional[float] = None
    convert_back_units: bool = False

    layerset_first: bool = False
    enable_layerset_slicing: bool = False

    disable_boolean_result: bool = False
    disable_opening_subtractions: bool = False
    merge_boolean_operands: bool = False
    boolean_attempt_2d: bool = False
    debug: bool = False

    no_wire_intersection_check: bool = False
    no_wire_intersection_tolerance: Optional[float] = None
    edge_arrows: bool = False

    weld_vertices: bool = False
    unify_shapes: bool = False

    use_world_coords: bool = False
    building_local_placement: bool = False
    site_local_placement: bool = False
    model_offset: Optional[str] = None
    model_rotation: Optional[str] = None

    context_ids: Optional[List[str]] = None
    iterator_output: Optional[int] = None

    no_normals: bool = False
    generate_uvs: bool = False
    # Renamed from ``validate`` so the field no longer shadows
    # ``BaseModel.validate`` (deprecated Pydantic v2 classmethod). JSON callers
    # still send ``"validate": true`` via the validation alias; ``model_dump()``
    # in api-gateway will emit the field name, which is also accepted on the
    # worker side via ``AliasChoices``.
    validate_geometry: bool = Field(
        default=False,
        validation_alias=AliasChoices("validate_geometry", "validate"),
    )
    element_hierarchy: bool = False

    force_space_transparency: Optional[float] = None
    keep_bounding_boxes: bool = False
    circle_segments: Optional[int] = None

    function_step_type: Optional[int] = None
    function_step_param: Optional[float] = None

    no_parallel_mapping: bool = False
    sew_shells: bool = False
    triangulation_type: Optional[int] = None

    # --- serialization (SVG) options ----------------------------------------
    bounds: Optional[str] = None
    scale: Optional[str] = None
    center: Optional[str] = None
    section_ref: Optional[str] = None
    elevation_ref: Optional[str] = None
    elevation_ref_guid: Optional[List[str]] = None
    auto_section: bool = False
    auto_elevation: bool = False
    draw_storey_heights: Optional[str] = None
    storey_height_line_length: Optional[float] = None
    svg_xmlns: bool = False
    svg_poly: bool = False
    svg_prefilter: bool = False
    svg_segment_projection: bool = False
    svg_write_poly: bool = False
    svg_project: bool = False
    svg_without_storeys: bool = False
    svg_no_css: bool = False
    door_arcs: bool = False
    section_height: Optional[float] = None
    section_height_from_storeys: bool = False
    print_space_names: bool = False
    print_space_areas: bool = False
    space_name_transform: Optional[str] = None

    # --- naming & coordinate format -----------------------------------------
    use_element_names: bool = False
    use_element_guids: bool = False
    use_element_step_ids: bool = False
    use_element_types: bool = False

    y_up: bool = False
    ecef: bool = False
    digits: Optional[int] = None
    base_uri: Optional[str] = None
    wkt_use_section: bool = False

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


class IfcCsvRequest(VersionPinOptional):
    filename: str
    output_filename: str
    format: str = "csv"
    delimiter: str = ","
    null_value: str = Field("-", alias="null")
    query: str = "IfcElement"
    attributes: List[str] = ["Name", "Description"]
    # Optional ifccsv IfcCsv.export() extras (see IfcOpenshell/src/ifccsv/ifccsv.py).
    headers: Optional[List[Optional[str]]] = None
    groups: Optional[List[Dict[str, Any]]] = None
    sort: Optional[List[Dict[str, Any]]] = None
    summaries: Optional[List[Dict[str, Any]]] = None
    formatting: Optional[List[Dict[str, Any]]] = None
    include_global_id: bool = True

    @field_validator("filename", "output_filename")
    @classmethod
    def validate_filenames(cls, v: str) -> str:
        return _validate_safe_path(v)


class IfcCsvImportRequest(VersionPinOptional):
    ifc_filename: str
    csv_filename: str
    output_filename: str = None

    @field_validator("ifc_filename", "csv_filename", "output_filename")
    @classmethod
    def validate_filenames(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return _validate_safe_path(v)


class IfcFastRequest(VersionPinOptional):
    """Native ``ifcfast`` (Rust) operations — see ``shared/ifcfast_ops.py``."""

    filename: str
    operation: str = "export_products"
    output_filename: Optional[str] = None
    output_format: str = "csv"
    delimiter: str = ","
    null_value: str = Field("-", alias="null")
    # export_products / filter_products
    query: str = "IfcProduct"
    attributes: List[str] = ["Name", "Description"]
    headers: Optional[List[Optional[str]]] = None
    include_global_id: bool = True
    mmap: bool = True  # kept for API compat; native parser always mmap-based
    # export_layer / extract_all
    layer: Optional[str] = None
    layers: Optional[List[str]] = None
    output_prefix: Optional[str] = None
    # traverse
    traverse: Optional[str] = None
    guid: Optional[str] = None
    # model.filter
    filter_entity: Optional[str] = None
    filter_mode: Optional[str] = None
    filter_storey_guid: Optional[str] = None
    # preview
    preview_table: Optional[str] = None
    preview_n: int = 5
    # diff
    other_filename: Optional[str] = None
    diff_sample: int = 5
    # type_bank / type_summary
    sample_guids: int = 3
    # geometry
    point_cloud_per_m2: float = 1000.0
    point_cloud_seed: int = 42
    mesh_unit: str = "m"
    # by_type
    entity_type: Optional[str] = None

    @field_validator("filename", "output_filename", "other_filename", "output_prefix")
    @classmethod
    def validate_filenames(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return _validate_safe_path(v)


class ClashFile(BaseModel):
    file: str
    selector: Optional[str] = None
    mode: Optional[str] = "a"

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

class IfcClashRequest(VersionPinOptional):
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


class IfcTesterRequest(VersionPinOptional):
    ifc_filename: str
    ids_filename: str
    output_filename: str
    report_type: str = "json"

    @field_validator("ifc_filename", "ids_filename", "output_filename")
    @classmethod
    def validate_filenames(cls, v: str) -> str:
        return _validate_safe_path(v)


# IfcGherkinRequest + BeastPdfGherkinRequest moved to cde/shared/classes.py
# along with their workers (2026-05). Re-add here only if a future ifcpipeline
# service grows a need for the same Pydantic shapes.


class IfcDiffRequest(VersionPinOptional):
    old_file: str
    new_file: str
    output_file: str = "diff.json"
    relationships: Optional[List[str]] = None
    is_shallow: bool = True
    filter_elements: Optional[str] = None
    # Same S3 key, two VersionIds (n8n IfcDiff explicit pins; precedence in worker).
    old_version_id: Optional[str] = None
    new_version_id: Optional[str] = None

    @field_validator("old_file", "new_file", "output_file")
    @classmethod
    def validate_files(cls, v: str) -> str:
        return _validate_safe_path(v)


class IFC2JSONRequest(VersionPinOptional):
    filename: str
    output_filename: str

    @field_validator("filename", "output_filename")
    @classmethod
    def validate_filenames(cls, v: str) -> str:
        return _validate_safe_path(v)


class FragmentsRequest(VersionPinOptional):
    """IFC → ThatOpen ``.frag`` pre-bake (ifcfrag-worker)."""

    input_filename: str
    output_filename: Optional[str] = None

    @field_validator("input_filename")
    @classmethod
    def validate_input(cls, v: str) -> str:
        return _validate_safe_path(v)

    @field_validator("output_filename")
    @classmethod
    def validate_output(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
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

class IfcQtoRequest(VersionPinOptional):
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
    # n8n IfcPipeline `downloadFromUrl` sends `source_etag`; also accept JSON `versionId`.
    source_etag: Annotated[
        Optional[str],
        Field(
            default=None,
            validation_alias=AliasChoices("source_etag", "versionId"),
            description="Opaque upstream version token (e.g. ACC version id) for audit short-circuit.",
        ),
    ] = None

    @field_validator("output_filename")
    @classmethod
    def validate_output_filename(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return _validate_original_upload_basename(v)


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
class IfcPatchRequest(VersionPinOptional):
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
                    "script_path": "\\\\bim-host.example.internal\\Client\\INTERAXO\\Project-Phase\\Discipline\\Batch\\Run-RTVXporterBatch.ps1",
                    "batch_file": "\\\\bim-host.example.internal\\Client\\INTERAXO\\Project-Phase\\Discipline\\Batch\\sample.rbxml",
                    "timeout_seconds": 7200
                }
            ]
        }

# IfcCoord Worker Classes
class IfcCoordRequest(BaseModel):
    """Request model for IfcCoord operations"""
    path_a: str = Field(..., description="First federated IFC filename under /uploads")
    path_b: str = Field(..., description="Second federated IFC filename under /uploads")
    mode: str = Field(default="propose_only", description="propose_only or propose_and_apply")
    policy_path: Optional[str] = Field(default=None, description="Optional path to policy JSON inside /uploads or scenarios")
    policy_inline: Optional[Dict[str, Any]] = Field(default=None, description="Optional inline policy JSON")
    clash_options: Optional[Dict[str, Any]] = Field(default=None, description="Optional custom clash_options dictionary override")
    max_rounds: int = Field(default=10, description="Maximum coordination/fixing rounds")
    max_auto_apply: Optional[int] = Field(default=None, description="Hard cap of auto-applied fixes")
    output_subdir: Optional[str] = Field(default=None, description="Optional custom output subdirectory name under /output/coord")
