"""
Test script to enqueue jobs to the IFC Pipeline RQ workers and monitor their completion.

Prerequisites:
1. Docker environment running (`docker-compose up -d`).
2. Local Python environment with `redis` and `rq` installed (`pip install redis rq`).
3. Sample IFC files (`sample_a.ifc`, `sample_b.ifc`) in `./shared/uploads/`.
4. Optionally, clean `./shared/output/` before running for clearer results.
"""

import redis
import rq
import time
import os
import uuid
import pprint
import logging

# --- Configuration ---
REDIS_HOST = 'localhost'
REDIS_PORT = 6379
REDIS_DB = 0

# Paths relative to this script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SHARED_UPLOADS_DIR = os.path.join(SCRIPT_DIR, 'shared', 'uploads')
SHARED_OUTPUT_DIR = os.path.join(SCRIPT_DIR, 'shared', 'output')

# Sample filenames (ensure these exist in ./shared/uploads)
SAMPLE_FILE_A = "sample_a.ifc"
SAMPLE_FILE_B = "sample_b.ifc"

# Worker Queues
QUEUE_NAMES = [
    "ifcconvert",
    "ifcclash",
    "ifccsv",
    "ifctester",
    "ifcdiff",
    "ifc5d",
    "ifc2json",
    "default" # Keep default for potential fallback or general tasks
]

JOB_TIMEOUT = "2h" # Default timeout for jobs
WAIT_TIMEOUT = 7200 # Max seconds to wait for a job to finish (matches JOB_TIMEOUT)
POLL_INTERVAL = 5   # Seconds between checking job status

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Helper Functions ---

def connect_redis():
    """Establishes connection to Redis."""
    try:
        conn = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB)
        conn.ping()
        logger.info(f"Successfully connected to Redis at {REDIS_HOST}:{REDIS_PORT}")
        return conn
    except redis.exceptions.ConnectionError as e:
        logger.error(f"Failed to connect to Redis at {REDIS_HOST}:{REDIS_PORT}. Please ensure Redis is running and accessible.")
        logger.error(f"Error details: {e}")
        exit(1)

def get_queues(connection):
    """Gets RQ Queue objects for all defined queue names."""
    return {name: rq.Queue(name, connection=connection) for name in QUEUE_NAMES}

def check_sample_files():
    """Checks if required sample files exist."""
    path_a = os.path.join(SHARED_UPLOADS_DIR, SAMPLE_FILE_A)
    path_b = os.path.join(SHARED_UPLOADS_DIR, SAMPLE_FILE_B)
    if not os.path.exists(path_a):
        logger.error(f"Sample file A not found: {path_a}")
        return False
    if not os.path.exists(path_b):
        logger.error(f"Sample file B not found: {path_b}")
        return False
    logger.info("Required sample files found.")
    return True

def enqueue_and_wait(queue, func_string, job_data, description):
    """Enqueues a job, waits for completion, and reports status."""
    logger.info(f"--- Testing: {description} --- < Queue: {queue.name} > ---")
    logger.info(f"Enqueuing job: {func_string}")
    logger.info("Job Data:")
    pprint.pprint(job_data, indent=2)
    
    try:
        job = queue.enqueue(
            func_string,
            job_data,
            job_timeout=JOB_TIMEOUT,
            result_ttl=3600 # Keep result for 1 hour
        )
        logger.info(f"Job enqueued with ID: {job.id}")
    except Exception as e:
        logger.error(f"Failed to enqueue job: {e}")
        return False

    start_time = time.time()
    while True:
        job.refresh()
        status = job.get_status()
        elapsed_time = time.time() - start_time

        if status == 'finished':
            logger.info(f"Job {job.id} finished successfully in {elapsed_time:.2f} seconds.")
            logger.info("Result:")
            pprint.pprint(job.result, indent=2)
            # Basic check for success flag in result if it exists
            if isinstance(job.result, dict) and not job.result.get('success', True):
                 logger.warning(f"Job {job.id} reported success=False in result.")
                 return False
            return True
        elif status == 'failed':
            logger.error(f"Job {job.id} failed after {elapsed_time:.2f} seconds.")
            logger.error(f"Failure Reason: {job.exc_info}")
            return False
        elif status in ['queued', 'started', 'deferred']:
            logger.info(f"Job {job.id} status: {status} (Elapsed: {elapsed_time:.2f}s)")
        else:
             logger.warning(f"Job {job.id} has unexpected status: {status}")
             
        if elapsed_time > WAIT_TIMEOUT:
            logger.error(f"Job {job.id} timed out waiting for completion after {elapsed_time:.2f} seconds.")
            return False
            
        time.sleep(POLL_INTERVAL)

# --- Test Functions ---

def test_ifcconvert(queues):
    job_data = {
        "input_filename": f"/uploads/{SAMPLE_FILE_A}",
        "output_filename": f"/output/converted/{SAMPLE_FILE_A}.obj",
        # Add other IfcConvertRequest options as needed
        "log_file": f"/output/converted/{SAMPLE_FILE_A}_convert_log.txt"
    }
    return enqueue_and_wait(queues['ifcconvert'], 'tasks.run_ifcconvert', job_data, "IFC to OBJ Conversion")

def test_ifcclash(queues):
    # Basic clash request structure (mimics IfcClashRequest)
    job_data = {
        "clash_sets": [
            {
                "name": "TestSet_A_vs_B",
                "a": [{"file": SAMPLE_FILE_A, "mode": "a", "selector": None}],
                "b": [{"file": SAMPLE_FILE_B, "mode": "a", "selector": None}]
            }
        ],
        "tolerance": 0.05,
        "output_filename": "clash_result.json",
        "mode": "basic", # or "clearance"
        "clearance": 0.1, # Only used if mode is clearance
        "check_all": False,
        "allow_touching": False,
        "smart_grouping": False,
        "max_cluster_distance": 1.0
    }
    # Paths within job_data need to be relative to /uploads
    return enqueue_and_wait(queues['ifcclash'], 'tasks.run_ifcclash_detection', job_data, "IFC Clash Detection")

def test_ifccsv_export(queues):
    job_data = {
        "filename": SAMPLE_FILE_A,
        "output_filename": f"{SAMPLE_FILE_A}.csv",
        "format": "csv",
        "query": "IfcWall", # Example query
        "attributes": ["GlobalId", "Name", "ObjectType"], # Example attributes
        "delimiter": ","
    }
    success = enqueue_and_wait(queues['ifccsv'], 'tasks.run_ifc_to_csv_conversion', job_data, "IFC to CSV Export")
    # Could add tests for xlsx/ods here too
    return success
    
def test_ifccsv_import(queues):
    # NOTE: This test assumes 'sample_a.csv' exists from a previous export.
    # A more robust test would generate a suitable CSV or use a known good one.
    csv_to_import = f"{SAMPLE_FILE_A}.csv" 
    target_ifc = SAMPLE_FILE_B # Import into a different file
    output_ifc = f"{SAMPLE_FILE_B}_updated.ifc"
    
    # Ensure the source CSV exists in the expected output location for the worker
    # Path expected by the worker: /output/csv/sample_a.csv (or relevant format)
    source_csv_path_for_worker = f"csv/{csv_to_import}" # Relative to /output/

    job_data = {
        "ifc_filename": target_ifc, # Relative to /uploads
        "data_filename": source_csv_path_for_worker, # Relative to /output
        "output_filename": output_ifc # Relative to /output/ifc_updated
    }
    return enqueue_and_wait(queues['ifccsv'], 'tasks.run_csv_to_ifc_import', job_data, "CSV to IFC Import")
    
def test_ifctester(queues):
    # Assuming a simple request structure for ifctester-worker
    job_data = {
        "filename": SAMPLE_FILE_A, # Relative to /uploads
        "output_filename": f"{SAMPLE_FILE_A}_test_report.json" # Relative to /output/test (adjust if needed)
    }
    # The actual task function name might be different, assuming 'tasks.run_ifctester'
    # Check ifctester-worker/tasks.py if this fails.
    return enqueue_and_wait(queues['ifctester'], 'tasks.run_ifctester', job_data, "IFC Tester Execution")
    
def test_ifcdiff(queues):
    job_data = {
        "old_file": SAMPLE_FILE_A, # Relative to /uploads
        "new_file": SAMPLE_FILE_B, # Relative to /uploads
        "output_file": "diff_a_vs_b.json", # Relative to /output/diff
        "relationships": True, # Example options
        "is_shallow": False,
        "filter_elements": None
    }
    return enqueue_and_wait(queues['ifcdiff'], 'tasks.run_ifcdiff', job_data, "IFC Diff Comparison")
    
def test_ifc5d(queues):
    job_data = {
        "input_file": SAMPLE_FILE_A, # Relative to /uploads
        "output_file": f"{SAMPLE_FILE_A}_qto.ifc" # Relative to /output/qto
    }
    return enqueue_and_wait(queues['ifc5d'], 'tasks.run_qto_calculation', job_data, "IFC QTO Calculation")
    
def test_ifc2json(queues):
    job_data = {
        "filename": SAMPLE_FILE_A, # Relative to /uploads
        "output_filename": f"{SAMPLE_FILE_A}.json" # Relative to /output/json
    }
    return enqueue_and_wait(queues['ifc2json'], 'tasks.run_ifc_to_json_conversion', job_data, "IFC to JSON Conversion")

# --- Main Execution ---

def main():
    logger.info("Starting Worker Test Script...")
    
    if not check_sample_files():
        logger.error("Prerequisite sample files missing. Exiting.")
        exit(1)
        
    redis_conn = connect_redis()
    queues = get_queues(redis_conn)
    
    results = {}
    
    # Run tests sequentially
    results["ifcconvert"] = test_ifcconvert(queues)
    results["ifcclash"] = test_ifcclash(queues)
    results["ifccsv_export"] = test_ifccsv_export(queues)
    # results["ifccsv_import"] = test_ifccsv_import(queues) # Uncomment if CSV import setup is ready
    results["ifctester"] = test_ifctester(queues)
    results["ifcdiff"] = test_ifcdiff(queues)
    results["ifc5d"] = test_ifc5d(queues)
    results["ifc2json"] = test_ifc2json(queues)
    
    logger.info("--- Test Summary ---")
    all_passed = True
    for test_name, success in results.items():
        status = "PASSED" if success else "FAILED"
        logger.info(f"Test {test_name}: {status}")
        if not success:
            all_passed = False
            
    if all_passed:
        logger.info("All tests passed!")
    else:
        logger.error("Some tests failed.")
        exit(1)

if __name__ == "__main__":
    main() 