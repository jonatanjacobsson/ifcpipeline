from shared.classes import IfcClashRequest, ClashSet, ClashFile, ClashMode
import logging
import json
import os
import time
import ifcopenshell
import ifcopenshell.util.selector
import ifcopenshell.geom
import multiprocessing
from ifcclash.ifcclash import Clasher, ClashSettings
from shared.db_client import save_clash_result
from typing import Tuple, List, Dict, Any

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class IFCValidationError(Exception):
    """Custom exception for IFC validation errors with detailed information."""
    def __init__(self, message: str, file_path: str, error_type: str, details: str = None):
        self.file_path = file_path
        self.error_type = error_type
        self.details = details
        super().__init__(f"{error_type}: {message}")


def validate_ifc_file(file_path: str) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Validate an IFC file before processing.
    
    Performs two levels of validation:
    1. Header check - file exists and contains ISO-10303-21
    2. Deep validation - can be opened and geometry iterator created
    
    Args:
        file_path: Full path to the IFC file
        
    Returns:
        Tuple of (is_valid, error_message, metadata)
        - is_valid: True if file passed all validation
        - error_message: Description of the error if invalid, empty string if valid
        - metadata: Dictionary with file info (schema, element_count, etc.)
    """
    metadata = {
        "file_path": file_path,
        "file_name": os.path.basename(file_path),
        "validated": False,
        "schema": None,
        "element_count": None,
    }
    
    # Level 1: Check file exists and has valid IFC header
    if not os.path.exists(file_path):
        return False, f"File not found: '{metadata['file_name']}'", metadata
    
    try:
        with open(file_path, 'rb') as f:
            # Read first 200 bytes to check header
            header = f.read(200).decode('utf-8', errors='ignore')
            if 'ISO-10303-21' not in header:
                return False, f"Invalid IFC file: '{metadata['file_name']}' does not contain a valid IFC header (ISO-10303-21). The file may be corrupted or not a valid IFC file.", metadata
    except Exception as e:
        return False, f"Cannot read file '{metadata['file_name']}': {str(e)}", metadata
    
    # Level 2: Deep validation - try to open and create geometry iterator
    try:
        logger.info(f"Deep validating IFC file: {metadata['file_name']}")
        ifc = ifcopenshell.open(file_path)
        metadata["schema"] = ifc.schema
        
        # Count elements
        try:
            elements = ifc.by_type("IfcProduct")
            metadata["element_count"] = len(elements)
        except:
            metadata["element_count"] = "unknown"
        
        # Test geometry iterator creation - this catches IFC4X3 issues
        logger.info(f"Testing geometry iterator for: {metadata['file_name']}")
        settings = ifcopenshell.geom.settings()
        
        # Try to create an iterator with a small subset to test
        try:
            # Get a few elements to test with
            test_elements = ifc.by_type("IfcProduct")[:5] if ifc.by_type("IfcProduct") else []
            if test_elements:
                iterator = ifcopenshell.geom.iterator(settings, ifc, include=test_elements)
                # Just initialize, don't iterate
                logger.info(f"Geometry iterator created successfully for: {metadata['file_name']}")
        except TypeError as e:
            error_msg = str(e)
            if "AGGREGATE OF STRING" in error_msg:
                return False, f"IFC schema compatibility issue with '{metadata['file_name']}' (schema: {metadata['schema']}): The file uses IFC attributes that are incompatible with the current geometry processor. This is a known issue with some IFC4X3 files. Error: {error_msg}", metadata
            else:
                # Other TypeError - might still be processable, log warning but continue
                logger.warning(f"Geometry iterator warning for {metadata['file_name']}: {error_msg}")
        except Exception as e:
            # Log but don't fail - some files might still work for clash detection
            logger.warning(f"Geometry iterator test warning for {metadata['file_name']}: {str(e)}")
        
        metadata["validated"] = True
        logger.info(f"Validation passed for: {metadata['file_name']} (schema: {metadata['schema']}, elements: {metadata['element_count']})")
        return True, "", metadata
        
    except ifcopenshell.Error as e:
        error_msg = str(e)
        if "Unable to parse IFC SPF header" in error_msg:
            return False, f"Corrupted or invalid IFC file: '{metadata['file_name']}' cannot be parsed. The file header is malformed or the file is not a valid IFC.", metadata
        return False, f"IFC parsing error for '{metadata['file_name']}': {error_msg}", metadata
    except Exception as e:
        return False, f"Failed to validate IFC file '{metadata['file_name']}': {str(e)}", metadata


def validate_all_clash_files(clash_sets: List, models_dir: str) -> Tuple[bool, List[str], List[Dict]]:
    """
    Validate all IFC files in the clash sets before processing.
    
    Args:
        clash_sets: List of ClashSet objects
        models_dir: Base directory for model files
        
    Returns:
        Tuple of (all_valid, error_messages, file_metadata)
    """
    errors = []
    metadata_list = []
    validated_files = set()  # Avoid re-validating the same file
    
    for clash_set in clash_sets:
        for file in clash_set.a + clash_set.b:
            file_path = os.path.join(models_dir, file.file)
            
            # Skip if already validated
            if file_path in validated_files:
                continue
            validated_files.add(file_path)
            
            is_valid, error_msg, metadata = validate_ifc_file(file_path)
            metadata_list.append(metadata)
            
            if not is_valid:
                errors.append(error_msg)
                logger.error(f"Validation failed: {error_msg}")
    
    return len(errors) == 0, errors, metadata_list

# Define a custom clasher class for better logging
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

# Function for preprocessing clash data (used for smart grouping)
def preprocess_clash_data(clash_sets):
    for clash_set in clash_sets:
        clashes = clash_set["clashes"]
        for clash in clashes.values():
            p1 = clash["p1"]
            p2 = clash["p2"]
            # Calculate the midpoint and add it as the "position" key
            clash["position"] = [(p1[i] + p2[i]) / 2 for i in range(3)]
    return clash_sets

def run_ifcclash_detection(job_data: dict) -> dict:
    """
    Process an IFC clash detection job
    
    Args:
        job_data: Dictionary containing the job parameters
        
    Returns:
        Dictionary containing the job results
    """
    try:
        # Parse the request from the job data
        request = IfcClashRequest(**job_data)
        models_dir = "/uploads"
        output_dir = "/output/clash"
        output_path = os.path.join(output_dir, request.output_filename)
        
        # Create output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)

        logger.info(f"Starting clash detection for {len(request.clash_sets)} clash sets")

        # === PRE-VALIDATION STEP ===
        # Validate all IFC files before processing to fail fast with clear error messages
        logger.info("Validating all IFC files before clash detection...")
        all_valid, validation_errors, file_metadata = validate_all_clash_files(
            request.clash_sets, models_dir
        )
        
        if not all_valid:
            # Create a detailed error message
            error_summary = f"Validation failed for {len(validation_errors)} file(s):\n"
            for i, err in enumerate(validation_errors, 1):
                error_summary += f"  {i}. {err}\n"
            
            logger.error(error_summary)
            
            # Raise a clear validation error
            raise IFCValidationError(
                message=f"{len(validation_errors)} file(s) failed validation",
                file_path=", ".join([m["file_name"] for m in file_metadata if not m["validated"]]),
                error_type="IFC Validation Error",
                details=error_summary
            )
        
        # Log successful validation
        validated_count = len([m for m in file_metadata if m["validated"]])
        schemas_found = set(m["schema"] for m in file_metadata if m["schema"])
        logger.info(f"All {validated_count} IFC file(s) validated successfully. Schemas: {', '.join(schemas_found)}")

        # Create a settings object
        settings = CustomClashSettings()
        settings.output = output_path
        logger.info(f"Clash output will be saved to: {output_path}")

        # Create a clasher object
        clasher = CustomClasher(settings)

        # Set up clash sets
        for clash_set in request.clash_sets:
            clasher_set = {
                "name": clash_set.name,
                "a": [],
                "b": [],
                "tolerance": request.tolerance,
                "mode": request.mode.value,
                "check_all": request.check_all,
                "allow_touching": request.allow_touching,
                "clearance": request.clearance
            }

            logger.info(f"Setting up clash set '{clash_set.name}' with mode: {request.mode.value}")

            # Validate mode-specific parameters
            if request.mode == ClashMode.CLEARANCE and request.clearance <= 0:
                raise ValueError("Clearance value must be greater than 0 when using clearance mode")

            # Add files to clash set
            for side in ['a', 'b']:
                for file in getattr(clash_set, side):
                    file_path = os.path.join(models_dir, file.file)
                    logger.info(f"Adding file to clash set: {file_path}")
                    clasher_set[side].append({
                        "file": file_path,
                        "mode": file.mode,
                        "selector": file.selector
                    })

            # Add clash set to clasher
            clasher.clash_sets.append(clasher_set)

        # Start clash detection
        start_time = time.time()
        logger.info("Starting clash detection")
        clasher.clash()

        # Handle smart grouping if requested
        logger.info(f"Smart grouping? {request.smart_grouping}")
        if request.smart_grouping:
            logger.info("Starting Smart Clashes....")
            try:
                # Preprocess clash sets for smart grouping
                preprocessed_clash_sets = preprocess_clash_data(clasher.clash_sets)
                # Call smart_group_clashes with both parameters to handle different function signatures
                smart_groups = clasher.smart_group_clashes(preprocessed_clash_sets, request.max_cluster_distance)
            except Exception as e:
                logger.error(f"Error during smart grouping: {str(e)}")
                logger.info("Continuing without smart grouping")
        else:
            logger.info("Skipping Smart Clashes (disabled)")

        # Export the results
        logger.info("Exporting clash results")
        try:
            # Try the export method from the Clasher class
            clasher.export()
        except AttributeError:
            # If that doesn't work, try the export_json method
            logger.info("Using export_json instead of export")
            clasher.export_json(output_path)
            
        # Calculate execution time
        end_time = time.time()
        execution_time = end_time - start_time
        logger.info(f"Clash detection and export completed in {execution_time:.2f} seconds")

        # Read the results from the output file
        try:
            with open(output_path, 'r') as json_file:
                clash_results = json.load(json_file)
            
            # Count clashes
            clash_count = 0
            clash_set_names = []
            for clash_set in clash_results:
                clash_count += len(clash_set.get("clashes", {}))
                clash_set_names.append(clash_set.get("name", "Unnamed"))
            
            # Create a comma-separated string of clash set names
            clash_set_name = ", ".join(clash_set_names)
            
            # Save to PostgreSQL
            logger.info("Saving clash result to PostgreSQL database")
            db_id = save_clash_result(
                clash_set_name=clash_set_name,
                output_filename=output_path,
                clash_count=clash_count,
                clash_data=clash_results,
                original_clash_id=None  # Set to None for new clash sets
            )
            
            # Return the results (include db_id if available)
            result = {
                "success": True,
                "result": clash_results,
                "clash_count": clash_count,
                "output_path": output_path
            }
            
            # Add database ID if available
            if db_id:
                result["db_id"] = db_id
            
            return result
        except Exception as e:
            logger.error(f"Error reading result file: {str(e)}")
            return {
                "success": True,
                "message": "Clash detection completed but result file could not be read",
                "output_path": output_path
            }
            
    except Exception as e:
        logger.error(f"Error during clash detection: {str(e)}", exc_info=True)
        # Re-raise the exception to mark the job as failed
        raise