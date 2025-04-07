import os
import logging
import ifcopenshell
from ifcdiff import IfcDiff
import redis
from rq import Queue, Connection

# Configure logging
logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'))
logger = logging.getLogger(__name__)

# Redis connection (assuming worker is started with 'rq worker -u redis://redis:6379 ifcdiff-tasks')
# The connection details are usually handled by how the worker is invoked,
# but defining the queue name here is good practice.
QUEUE_NAME = "ifcdiff-tasks"

# Environment setup
MODELS_DIR = "/uploads"
OUTPUT_DIR = "/output/diff"

def perform_ifc_diff(old_file_rel, new_file_rel, output_file_rel, relationships, is_shallow, filter_elements):
    """
    Core function to perform the IFC diff operation.
    This function will be executed by the RQ worker.

    Args:
        old_file_rel (str): Relative path of the old IFC file within MODELS_DIR.
        new_file_rel (str): Relative path of the new IFC file within MODELS_DIR.
        output_file_rel (str): Relative path for the output diff file within OUTPUT_DIR.
        relationships (bool): Include relationship data in the diff.
        is_shallow (bool): Perform a shallow diff.
        filter_elements (list or None): List of elements to filter.

    Returns:
        str: The relative path to the generated diff output file.

    Raises:
        FileNotFoundError: If input files are not found.
        Exception: For any other errors during the diff process.
    """
    old_file_path = os.path.join(MODELS_DIR, old_file_rel)
    new_file_path = os.path.join(MODELS_DIR, new_file_rel)
    # Ensure output directory exists
    output_dir_abs = os.path.join(OUTPUT_DIR)
    os.makedirs(output_dir_abs, exist_ok=True)
    output_path_abs = os.path.join(output_dir_abs, output_file_rel)
    output_path_rel = os.path.join("diff", output_file_rel) # Relative path for the result

    logger.info(f"Starting ifcdiff task. Old: '{old_file_path}', New: '{new_file_path}', Output: '{output_path_abs}'")
    logger.info(f"Parameters: relationships={relationships}, is_shallow={is_shallow}, filter_elements='{filter_elements}'")

    if not os.path.exists(old_file_path):
        logger.error(f"Old file not found: {old_file_path}")
        raise FileNotFoundError(f"Old file {old_file_rel} not found at {old_file_path}")
    if not os.path.exists(new_file_path):
        logger.error(f"New file not found: {new_file_path}")
        raise FileNotFoundError(f"New file {new_file_rel} not found at {new_file_path}")

    logger.info("Input files found.")

    try:
        logger.info(f"Opening IFC files: old='{old_file_path}', new='{new_file_path}'")
        old_ifc_file = ifcopenshell.open(old_file_path)
        new_ifc_file = ifcopenshell.open(new_file_path)
        logger.info("IFC files opened successfully.")

        logger.info(f"Initializing IfcDiff...")
        ifc_diff = IfcDiff(
            old_ifc_file,
            new_ifc_file,
            relationships=relationships,
            is_shallow=is_shallow,
            filter_elements=filter_elements
        )

        logger.info("Starting IfcDiff.diff() process...")
        ifc_diff.diff()
        logger.info("IfcDiff.diff() process completed.")

        logger.info(f"Saving diff results to {output_path_abs}...")
        ifc_diff.export(output_path_abs)
        logger.info("Diff results saved successfully.")

        # Return the relative path within the shared /output volume
        return output_path_rel

    except Exception as e:
        logger.error(f"Error occurred during IFC diff process: {str(e)}", exc_info=True)
        # Re-raise the exception so RQ marks the job as failed
        raise

# Note: No FastAPI app needed here anymore.
# The RQ worker will import this file and execute the 'perform_ifc_diff' function when a job arrives.
# To run the worker, use the command specified in docker-compose.yml:
# rq worker -u redis://redis:6379 ifcdiff-tasks