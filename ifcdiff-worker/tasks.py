import logging
import os
import json
import ifcopenshell
from ifcdiff import IfcDiff
from shared.classes import IfcDiffRequest
from shared.db_client import save_diff_result
from collections.abc import Set, Sequence

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class IfcDiffJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder to handle IFC objects that are not natively JSON serializable."""
    
    def default(self, obj):
        # Handle OrderedSet objects (from orderly_set package used by ifcdiff)
        if hasattr(obj, '__class__') and ('OrderedSet' in str(type(obj)) or 'SetOrdered' in str(type(obj))):
            try:
                return list(obj)
            except (TypeError, AttributeError):
                return str(obj)
        
        # Handle IFC entity instances
        if hasattr(obj, '__class__') and hasattr(obj, 'is_a'):
            try:
                # Try to convert IFC entity to a dictionary representation
                return {
                    'type': obj.is_a(),
                    'id': getattr(obj, 'id', None),
                    'GlobalId': getattr(obj, 'GlobalId', None) if hasattr(obj, 'GlobalId') else None
                }
            except (TypeError, AttributeError):
                return str(obj)
        
        # Handle numpy arrays and other numeric types
        if hasattr(obj, '__class__') and 'numpy' in str(type(obj)):
            try:
                return obj.tolist()
            except (TypeError, AttributeError):
                return str(obj)
        
        # Handle sets and other iterables (but not strings or bytes)
        if isinstance(obj, Set) and not isinstance(obj, (str, bytes)):
            try:
                return list(obj)
            except (TypeError, AttributeError):
                return str(obj)
        
        # Handle sequences that aren't strings or bytes
        if isinstance(obj, Sequence) and not isinstance(obj, (str, bytes)):
            try:
                return list(obj)
            except (TypeError, AttributeError):
                return str(obj)
        
        # Handle DeepDiff objects (from deepdiff package)
        if hasattr(obj, '__class__') and 'DeepDiff' in str(type(obj)):
            try:
                # Convert DeepDiff to a dictionary representation
                return dict(obj)
            except (TypeError, AttributeError):
                return str(obj)
        
        # Handle other objects by converting to string
        try:
            return str(obj)
        except (TypeError, AttributeError):
            return f"<{type(obj).__name__} object>"

def safe_json_export(diff_instance, output_path):
    """Safely export diff results with custom JSON handling."""
    try:
        # First, try the standard export method
        diff_instance.export(output_path)
        logger.info("Standard export successful")
        return True
    except (TypeError, ValueError) as e:
        if "not JSON serializable" in str(e):
            logger.warning(f"Standard export failed due to JSON serialization: {str(e)}")
            logger.info("Attempting custom JSON serialization...")
            
            try:
                # Manually construct the diff data structure like the original export method
                # Based on IfcOpenShell source: https://raw.githubusercontent.com/IfcOpenShell/IfcOpenShell/refs/heads/v0.8.0/src/ifcdiff/ifcdiff.py
                added_elements = getattr(diff_instance, 'added_elements', set())
                deleted_elements = getattr(diff_instance, 'deleted_elements', set())
                change_register = getattr(diff_instance, 'change_register', {})
                
                logger.info(f"Preparing diff data: {len(added_elements)} added, {len(deleted_elements)} deleted, {len(change_register)} changed")
                
                diff_data = {
                    "added": list(added_elements),
                    "deleted": list(deleted_elements),
                    "changed": change_register
                }
                
                # Use our custom encoder to serialize the data
                with open(output_path, 'w') as f:
                    json.dump(diff_data, f, cls=IfcDiffJSONEncoder, indent=4)
                
                logger.info("Custom JSON serialization successful")
                return True
            except Exception as custom_e:
                logger.error(f"Custom JSON serialization failed: {str(custom_e)}")
                logger.error(f"Exception type: {type(custom_e).__name__}")
                
                # Try to identify the problematic object
                try:
                    added_elements = getattr(diff_instance, 'added_elements', set())
                    deleted_elements = getattr(diff_instance, 'deleted_elements', set())
                    change_register = getattr(diff_instance, 'change_register', {})
                    
                    # Test each part separately to identify the issue
                    logger.info("Testing serialization of individual components...")
                    
                    # Test added elements
                    try:
                        json.dumps(list(added_elements), cls=IfcDiffJSONEncoder)
                        logger.info("Added elements serialization: OK")
                    except Exception as e:
                        logger.error(f"Added elements serialization failed: {str(e)}")
                        logger.error(f"Sample added element types: {[type(elem).__name__ for elem in list(added_elements)[:3]]}")
                    
                    # Test deleted elements
                    try:
                        json.dumps(list(deleted_elements), cls=IfcDiffJSONEncoder)
                        logger.info("Deleted elements serialization: OK")
                    except Exception as e:
                        logger.error(f"Deleted elements serialization failed: {str(e)}")
                        logger.error(f"Sample deleted element types: {[type(elem).__name__ for elem in list(deleted_elements)[:3]]}")
                    
                    # Test change register
                    try:
                        json.dumps(change_register, cls=IfcDiffJSONEncoder)
                        logger.info("Change register serialization: OK")
                    except Exception as e:
                        logger.error(f"Change register serialization failed: {str(e)}")
                        if change_register:
                            sample_key = list(change_register.keys())[0]
                            sample_value = change_register[sample_key]
                            logger.error(f"Sample change register entry: {sample_key} -> {type(sample_value).__name__}")
                            logger.error(f"Sample change register value: {str(sample_value)[:200]}")
                    
                except Exception as debug_e:
                    logger.error(f"Debug analysis failed: {str(debug_e)}")
                
                # Final fallback: create a simplified version with actual counts
                try:
                    added_elements = getattr(diff_instance, 'added_elements', set())
                    deleted_elements = getattr(diff_instance, 'deleted_elements', set())
                    change_register = getattr(diff_instance, 'change_register', {})
                    
                    simplified_data = {
                        "error": "Complex diff data could not be fully serialized",
                        "summary": {
                            "added": len(added_elements),
                            "deleted": len(deleted_elements),
                            "changed": len(change_register)
                        },
                        "added_elements": [str(elem) for elem in list(added_elements)[:10]],  # First 10 as strings
                        "deleted_elements": [str(elem) for elem in list(deleted_elements)[:10]],  # First 10 as strings
                        "changed_elements": list(change_register.keys())[:10] if change_register else [],  # First 10 GlobalIds
                        "original_error": str(e),
                        "custom_serialization_error": str(custom_e)
                    }
                    
                    with open(output_path, 'w') as f:
                        json.dump(simplified_data, f, indent=2)
                    
                    logger.warning("Created simplified diff result due to serialization issues")
                    return True
                except Exception as final_e:
                    logger.error(f"All export methods failed: {str(final_e)}")
                    return False
        else:
            # Re-raise if it's not a JSON serialization error
            raise

def run_ifcdiff(job_data: dict) -> dict:
    """
    Compare two IFC files and generate a diff report using the ifcdiff library.
    
    Args:
        job_data: Dictionary containing job parameters conforming to IfcDiffRequest.
        
    Returns:
        Dictionary containing the diff results.
    """
    try:
        request = IfcDiffRequest(**job_data)
        logger.info(f"Starting ifcdiff job: old='{request.old_file}', new='{request.new_file}', output='{request.output_file}'")
        
        models_dir = "/uploads" # Standard mount point
        output_dir = "/output/diff" # Standard diff output directory
        old_file_path = os.path.join(models_dir, request.old_file)
        new_file_path = os.path.join(models_dir, request.new_file)
        output_path = os.path.join(output_dir, request.output_file)
        
        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)

        # Validate input file existence
        logger.info(f"Checking existence: old='{old_file_path}', new='{new_file_path}'")
        if not os.path.exists(old_file_path):
            logger.error(f"Old IFC file not found: {old_file_path}")
            raise FileNotFoundError(f"Old IFC file {request.old_file} not found")
        if not os.path.exists(new_file_path):
            logger.error(f"New IFC file not found: {new_file_path}")
            raise FileNotFoundError(f"New IFC file {request.new_file} not found")
        logger.info("Input files found.")

        # Open IFC files
        logger.info("Opening IFC files...")
        old_ifc_file = ifcopenshell.open(old_file_path)
        new_ifc_file = ifcopenshell.open(new_file_path)
        logger.info("IFC files opened successfully.")

        # Initialize IfcDiff
        logger.info(f"Initializing IfcDiff: relationships={request.relationships}, shallow={request.is_shallow}, filter='{request.filter_elements}'")
        ifc_diff_instance = IfcDiff(
            old_ifc_file, 
            new_ifc_file, 
            relationships=request.relationships, 
            is_shallow=request.is_shallow,
            filter_elements=request.filter_elements
        )

        # Perform the diff operation
        logger.info("Running diff comparison...")
        ifc_diff_instance.diff()
        logger.info("Diff comparison completed.")

        # Export the results using safe JSON export
        logger.info(f"Exporting diff results to {output_path}...")
        export_success = safe_json_export(ifc_diff_instance, output_path)
        if not export_success:
            raise Exception("Failed to export diff results after multiple attempts")
        logger.info("Diff results exported successfully.")
        
        # Load the diff data from the output file
        diff_data = {}
        try:
            with open(output_path, 'r') as json_file:
                diff_data = json.load(json_file)
        except Exception as e:
            logger.error(f"Error reading diff result file: {str(e)}")
            logger.warning("Continuing with empty diff data for database storage")
        
        # Save to database
        logger.info("Saving diff results to database...")
        db_id = save_diff_result(
            old_file=request.old_file,
            new_file=request.new_file,
            output_filename=output_path,
            diff_data=diff_data
        )
        
        # The ifcdiff library writes the file directly, return success and path
        result = {
            "success": True,
            "message": f"IFC diff completed. Results saved to {output_path}",
            "output_path": output_path
        }
        
        # Add database ID if available
        if db_id:
            result["db_id"] = db_id
            
        return result

    except FileNotFoundError as e:
        logger.error(f"File not found error during IFC diff: {str(e)}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Error during IFC diff: {str(e)}", exc_info=True)
        raise # Re-raise for RQ failure 