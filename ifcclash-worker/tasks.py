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

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

        # Validate that all specified files exist
        for clash_set in request.clash_sets:
            for file in clash_set.a + clash_set.b:
                file_path = os.path.join(models_dir, file.file)
                if not os.path.exists(file_path):
                    logger.error(f"File not found: {file_path}")
                    raise FileNotFoundError(f"File {file.file} not found")

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