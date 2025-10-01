"""
Test script to enqueue jobs to the IFC Pipeline RQ workers and monitor their completion.

Prerequisites:
1. Docker environment running (`docker-compose up -d`).
2. Local Python environment with `redis`, `rq`, and `psycopg2` installed (`pip install redis rq psycopg2-binary`).
3. Sample IFC files (`sample_a.ifc`, `sample_b.ifc`) in `./shared/uploads/`.
4. Optionally, clean `./shared/output/` before running for clearer results.

Note on database verification:
- The ifcclash test includes verification that results are saved to the PostgreSQL database.
- The test will only pass if the worker successfully performs clash detection AND saves results to the database.
- This ensures that the database connectivity is working properly.
"""

import redis
import rq
import time
import os
import uuid
import pprint
import logging
import argparse
import psycopg2

# --- Configuration ---
REDIS_HOST = 'localhost'
REDIS_PORT = 6379
REDIS_DB = 0

# Paths relative to this script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SHARED_UPLOADS_DIR = os.path.join(SCRIPT_DIR, 'shared', 'uploads')
SHARED_OUTPUT_DIR = os.path.join(SCRIPT_DIR, 'shared', 'output')

# Sample filenames (ensure these exist in ./shared/uploads)
SAMPLE_FILE_A = "A1_2b_BIM_XXX_0001_00.ifc"
SAMPLE_FILE_B = "S2_2b_BIM_XXX_0001_00.ifc"

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
    max_wait_time = min(WAIT_TIMEOUT, 300)  # Use a shorter timeout of 5 minutes max
    
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
             
        if elapsed_time > max_wait_time:
            logger.error(f"Job {job.id} timed out waiting for completion after {elapsed_time:.2f} seconds.")
            return False
            
        time.sleep(POLL_INTERVAL)

# --- Test Functions ---

def test_ifcconvert(queues):
    test_name = f"ConvertTest_{uuid.uuid4().hex[:8]}"
    job_data = {
        "input_filename": f"/uploads/{SAMPLE_FILE_A}",
        "output_filename": f"/output/converted/{SAMPLE_FILE_A}.obj",
        # Add other IfcConvertRequest options as needed
        "log_file": f"/output/converted/{SAMPLE_FILE_A}_convert_log.txt",
        "test_name": test_name  # Add test identifier
    }
    job_success = enqueue_and_wait(queues['ifcconvert'], 'tasks.run_ifcconvert', job_data, "IFC to OBJ Conversion")
    
    # Verify database insertion
    if job_success:
        logger.info("Checking database for conversion results...")
        try:
            # Get PostgreSQL connection details from environment
            db_host = os.environ.get("POSTGRES_HOST", "localhost")
            db_port = os.environ.get("POSTGRES_PORT", "5432")
            db_name = os.environ.get("POSTGRES_DB", "ifcpipeline")
            db_user = os.environ.get("POSTGRES_USER", "ifcpipeline")
            db_pass = os.environ.get("POSTGRES_PASSWORD", "")
            
            logger.info(f"Connecting to PostgreSQL at {db_host}:{db_port}, database: {db_name}")
            
            # Connect to PostgreSQL
            conn = psycopg2.connect(
                host=db_host,
                port=db_port,
                dbname=db_name,
                user=db_user,
                password=db_pass
            )
            
            # Query conversions table
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM conversions WHERE test_name = %s", 
                          (test_name,))
            count = cursor.fetchone()[0]
            
            # Check if at least one record exists with the test name
            if count > 0:
                logger.info(f"Database verification successful: Found {count} record(s) in conversions table for test: {test_name}")
                
                # Get the most recent record for detailed information
                cursor.execute("SELECT id, filename, output_format, created_at FROM conversions WHERE test_name = %s ORDER BY created_at DESC LIMIT 1", 
                             (test_name,))
                record = cursor.fetchone()
                if record:
                    logger.info(f"Latest record: ID={record[0]}, Filename={record[1]}, Format={record[2]}, Created at={record[3]}")
                
                conn.close()
                return True
            else:
                logger.error(f"Database verification failed: No records found in conversions table for test: {test_name}")
                conn.close()
                return False
                
        except Exception as e:
            logger.error(f"Database verification failed with error: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    return job_success

def test_ifcclash(queues):
    # Basic clash request structure (mimics IfcClashRequest)
    test_name = f"TestSet_A_vs_B_{uuid.uuid4().hex[:8]}"
    job_data = {
        "clash_sets": [
            {
                "name": test_name,
                "a": [{"file": SAMPLE_FILE_A, "mode": "a", "selector": None}],
                "b": [{"file": SAMPLE_FILE_B, "mode": "a", "selector": None}]
            }
        ],
        "tolerance": 0.05,
        "output_filename": f"clash_result_{uuid.uuid4().hex[:8]}.json",
        "mode": "intersection",  # Changed from "basic" to valid enum value
        "clearance": 0.1, # Only used if mode is clearance
        "check_all": False,
        "allow_touching": False,
        "smart_grouping": False,
        "max_cluster_distance": 1.0
    }
    # Paths within job_data need to be relative to /uploads
    job_success = enqueue_and_wait(queues['ifcclash'], 'tasks.run_ifcclash_detection', job_data, "IFC Clash Detection")
    
    # Verify database insertion
    if job_success:
        logger.info("Checking database for clash results...")
        try:
            # Get PostgreSQL connection details from environment
            db_host = os.environ.get("POSTGRES_HOST", "localhost")
            db_port = os.environ.get("POSTGRES_PORT", "5432")
            db_name = os.environ.get("POSTGRES_DB", "ifcpipeline")
            db_user = os.environ.get("POSTGRES_USER", "ifcpipeline")
            db_pass = os.environ.get("POSTGRES_PASSWORD", "")
            
            logger.info(f"Connecting to PostgreSQL at {db_host}:{db_port}, database: {db_name}")
            
            # Connect to PostgreSQL
            conn = psycopg2.connect(
                host=db_host,
                port=db_port,
                dbname=db_name,
                user=db_user,
                password=db_pass
            )
            
            # Query clash_results table
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM clash_results WHERE clash_set_name LIKE %s", 
                          (f"%{test_name}%",))
            count = cursor.fetchone()[0]
            
            # Check if at least one record exists with the test clash set name
            if count > 0:
                logger.info(f"Database verification successful: Found {count} record(s) in clash_results table for test set: {test_name}")
                
                # Get the most recent record for detailed information
                cursor.execute("SELECT id, clash_count, created_at FROM clash_results WHERE clash_set_name LIKE %s ORDER BY created_at DESC LIMIT 1", 
                             (f"%{test_name}%",))
                record = cursor.fetchone()
                if record:
                    logger.info(f"Latest record: ID={record[0]}, Clash Count={record[1]}, Created at={record[2]}")
                
                conn.close()
                return True
            else:
                logger.error(f"Database verification failed: No records found in clash_results table for test set: {test_name}")
                conn.close()
                return False
                
        except Exception as e:
            logger.error(f"Database verification failed with error: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    return job_success

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
    # Get list of available IDS files
    ids_files = [f for f in os.listdir(SHARED_UPLOADS_DIR) if f.endswith('.ids')]
    
    if not ids_files:
        logger.error("No IDS files found in uploads directory. Skipping ifctester test.")
        return False
    
    ids_file = ids_files[0]  # Use the first available IDS file
    logger.info(f"Using IDS file: {ids_file}")
    
    test_name = f"TestIFC_{uuid.uuid4().hex[:8]}"
    job_data = {
        "ifc_filename": SAMPLE_FILE_A,  # Relative to /uploads
        "ids_filename": ids_file,  # Relative to /uploads
        "output_filename": f"{SAMPLE_FILE_A}_test_report.json",  # Relative to /output/test
        "report_type": "json",
        "test_name": test_name  # Add test identifier
    }
    
    job_success = enqueue_and_wait(queues['ifctester'], 'tasks.run_ifctester_validation', job_data, "IFC Tester Execution")
    
    # Verify database insertion
    if job_success:
        logger.info("Checking database for validation results...")
        try:
            # Get PostgreSQL connection details from environment
            db_host = os.environ.get("POSTGRES_HOST", "localhost")
            db_port = os.environ.get("POSTGRES_PORT", "5432")
            db_name = os.environ.get("POSTGRES_DB", "ifcpipeline")
            db_user = os.environ.get("POSTGRES_USER", "ifcpipeline")
            db_pass = os.environ.get("POSTGRES_PASSWORD", "")
            
            logger.info(f"Connecting to PostgreSQL at {db_host}:{db_port}, database: {db_name}")
            
            # Connect to PostgreSQL
            conn = psycopg2.connect(
                host=db_host,
                port=db_port,
                dbname=db_name,
                user=db_user,
                password=db_pass
            )
            
            # Query validations table
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM validations WHERE test_name = %s", 
                          (test_name,))
            count = cursor.fetchone()[0]
            
            # Check if at least one record exists with the test name
            if count > 0:
                logger.info(f"Database verification successful: Found {count} record(s) in validations table for test: {test_name}")
                
                # Get the most recent record for detailed information
                cursor.execute("SELECT id, pass_count, fail_count, created_at FROM validations WHERE test_name = %s ORDER BY created_at DESC LIMIT 1", 
                             (test_name,))
                record = cursor.fetchone()
                if record:
                    logger.info(f"Latest record: ID={record[0]}, Pass Count={record[1]}, Fail Count={record[2]}, Created at={record[3]}")
                
                conn.close()
                return True
            else:
                logger.error(f"Database verification failed: No records found in validations table for test: {test_name}")
                conn.close()
                return False
                
        except Exception as e:
            logger.error(f"Database verification failed with error: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    return job_success
    
def test_ifcdiff(queues):
    test_name = f"DiffTest_{uuid.uuid4().hex[:8]}"
    job_data = {
        "old_file": SAMPLE_FILE_A, # Relative to /uploads
        "new_file": SAMPLE_FILE_B, # Relative to /uploads
        "output_file": "diff_a_vs_b.json", # Relative to /output/diff
        "relationships": None,  # Changed from True to None to match expected List[str] type
        "is_shallow": False,
        "filter_elements": None,
        "test_name": test_name  # Add test identifier
    }
    job_success = enqueue_and_wait(queues['ifcdiff'], 'tasks.run_ifcdiff', job_data, "IFC Diff Comparison")
    
    # Verify database insertion
    if job_success:
        logger.info("Checking database for diff results...")
        try:
            # Get PostgreSQL connection details from environment
            db_host = os.environ.get("POSTGRES_HOST", "localhost")
            db_port = os.environ.get("POSTGRES_PORT", "5432")
            db_name = os.environ.get("POSTGRES_DB", "ifcpipeline")
            db_user = os.environ.get("POSTGRES_USER", "ifcpipeline")
            db_pass = os.environ.get("POSTGRES_PASSWORD", "")
            
            logger.info(f"Connecting to PostgreSQL at {db_host}:{db_port}, database: {db_name}")
            
            # Connect to PostgreSQL
            conn = psycopg2.connect(
                host=db_host,
                port=db_port,
                dbname=db_name,
                user=db_user,
                password=db_pass
            )
            
            # Query diff_results table
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM diff_results WHERE test_name = %s", 
                          (test_name,))
            count = cursor.fetchone()[0]
            
            # Check if at least one record exists with the test name
            if count > 0:
                logger.info(f"Database verification successful: Found {count} record(s) in diff_results table for test: {test_name}")
                
                # Get the most recent record for detailed information
                cursor.execute("SELECT id, added_count, modified_count, deleted_count, created_at FROM diff_results WHERE test_name = %s ORDER BY created_at DESC LIMIT 1", 
                             (test_name,))
                record = cursor.fetchone()
                if record:
                    logger.info(f"Latest record: ID={record[0]}, Added={record[1]}, Modified={record[2]}, Deleted={record[3]}, Created at={record[4]}")
                
                conn.close()
                return True
            else:
                logger.error(f"Database verification failed: No records found in diff_results table for test: {test_name}")
                conn.close()
                return False
                
        except Exception as e:
            logger.error(f"Database verification failed with error: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    return job_success
    
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

def clean_test_data_from_db():
    """Cleans test data from the database by removing test records created by the test script."""
    try:
        # Get PostgreSQL connection details from environment
        db_host = os.environ.get("POSTGRES_HOST", "localhost")
        db_port = os.environ.get("POSTGRES_PORT", "5432")
        db_name = os.environ.get("POSTGRES_DB", "ifcpipeline")
        db_user = os.environ.get("POSTGRES_USER", "ifcpipeline")
        db_pass = os.environ.get("POSTGRES_PASSWORD", "")
        
        logger.info(f"Connecting to PostgreSQL at {db_host}:{db_port} to clean test data")
        
        # Connect to PostgreSQL
        conn = psycopg2.connect(
            host=db_host,
            port=db_port,
            dbname=db_name,
            user=db_user,
            password=db_pass
        )
        
        cursor = conn.cursor()
        deleted_counts = {}
        
        # Delete test records from clash_results table
        cursor.execute("DELETE FROM clash_results WHERE clash_set_name LIKE %s", 
                      ("TestSet_A_vs_B_%",))
        deleted_counts['clash_results'] = cursor.rowcount
        
        # Delete test records from conversions table
        cursor.execute("DELETE FROM conversions WHERE test_name LIKE %s", 
                      ("ConvertTest_%",))
        deleted_counts['conversions'] = cursor.rowcount
        
        # Delete test records from validations table
        cursor.execute("DELETE FROM validations WHERE test_name LIKE %s", 
                      ("TestIFC_%",))
        deleted_counts['validations'] = cursor.rowcount
        
        # Delete test records from diff_results table
        cursor.execute("DELETE FROM diff_results WHERE test_name LIKE %s", 
                      ("DiffTest_%",))
        deleted_counts['diff_results'] = cursor.rowcount
        
        conn.commit()
        
        logger.info("Database cleanup summary:")
        for table, count in deleted_counts.items():
            logger.info(f"  - Deleted {count} test record(s) from {table} table")
        
        conn.close()
        return True
            
    except Exception as e:
        logger.error(f"Database cleanup failed with error: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return False

# --- Main Execution ---

def main():
    parser = argparse.ArgumentParser(description='Test IFC Pipeline workers')
    parser.add_argument('--workers', type=str, nargs='+', choices=[
        'ifcconvert', 'ifcclash', 'ifccsv_export', 'ifctester', 'ifcdiff', 'ifc5d', 'ifc2json', 'all'
    ], default=['all'], help='Workers to test')
    parser.add_argument('--clean-db', action='store_true', help='Clean test data from database after tests')
    
    args = parser.parse_args()
    workers = args.workers
    clean_db = args.clean_db
    
    logger.info("Starting Worker Test Script...")
    
    if not check_sample_files():
        logger.error("Prerequisite sample files missing. Exiting.")
        exit(1)
        
    redis_conn = connect_redis()
    queues = get_queues(redis_conn)
    
    results = {}
    
    # Determine which tests to run
    all_tests = 'all' in workers
    
    # Run selected tests
    if all_tests or 'ifcconvert' in workers:
        results["ifcconvert"] = test_ifcconvert(queues)
    
    if all_tests or 'ifcclash' in workers:
        logger.info("Running ifcclash test with database verification - test will only pass if data is saved to PostgreSQL")
        results["ifcclash"] = test_ifcclash(queues)
    
    if all_tests or 'ifccsv_export' in workers:
        results["ifccsv_export"] = test_ifccsv_export(queues)
    
    # if all_tests or 'ifccsv_import' in workers:
    #     results["ifccsv_import"] = test_ifccsv_import(queues)
    
    if all_tests or 'ifctester' in workers:
        results["ifctester"] = test_ifctester(queues)
    
    if all_tests or 'ifcdiff' in workers:
        results["ifcdiff"] = test_ifcdiff(queues)
    
    if all_tests or 'ifc5d' in workers:
        results["ifc5d"] = test_ifc5d(queues)
    
    if all_tests or 'ifc2json' in workers:
        results["ifc2json"] = test_ifc2json(queues)
    
    # Clean test data from database if requested
    if clean_db:
        logger.info("Cleaning test data from database...")
        clean_result = clean_test_data_from_db()
        if clean_result:
            logger.info("Database cleanup completed successfully")
        else:
            logger.error("Database cleanup failed")
    
    logger.info("--- Test Summary ---")
    all_passed = True
    for test_name, success in results.items():
        status = "PASSED" if success else "FAILED"
        if test_name == "ifcclash" and not success:
            logger.error(f"Test {test_name}: {status} - Including database insertion verification")
        else:
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