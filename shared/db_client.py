import os
import json
import logging
import traceback
from datetime import datetime
from typing import Optional, Any, Dict

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DBClient:
    """Centralized database client for connecting to PostgreSQL"""
    
    def __init__(self):
        """Initialize database connection parameters from environment variables"""
        self.db_host = os.environ.get("POSTGRES_HOST", "postgres")
        self.db_port = os.environ.get("POSTGRES_PORT", "5432")
        self.db_name = os.environ.get("POSTGRES_DB", "ifcpipeline")
        self.db_user = os.environ.get("POSTGRES_USER", "ifcpipeline")
        # Don't store the password directly in the code
        self.db_pass = os.environ.get("POSTGRES_PASSWORD", "")
    
    def get_connection(self):
        """Get a connection to the PostgreSQL database"""
        try:
            # Log connection attempt
            logger.info(f"Attempting to connect to PostgreSQL: host={self.db_host}, port={self.db_port}, db={self.db_name}, user={self.db_user}")
            
            # Check if psycopg2 is available
            try:
                import psycopg2
                from psycopg2.extras import Json
                logger.info("Successfully imported psycopg2")
            except ImportError as e:
                logger.error(f"ERROR: psycopg2 module not found: {str(e)}")
                return None
                
            # Try to connect
            conn = psycopg2.connect(
                host=self.db_host,
                port=self.db_port,
                dbname=self.db_name,
                user=self.db_user,
                password=self.db_pass
            )
            logger.info("PostgreSQL connection successful")
            return conn
        except Exception as e:
            logger.error(f"Error connecting to PostgreSQL: {str(e)}")
            logger.error(traceback.format_exc())
            return None
    
    def save_clash_result(self, clash_set_name: str, output_filename: str, 
                         clash_count: int, clash_data: Dict[str, Any], 
                         original_clash_id: Optional[int] = None) -> Optional[int]:
        """
        Save clash detection results to PostgreSQL
        
        Args:
            clash_set_name: Name of the clash set
            output_filename: Path to output JSON file
            clash_count: Number of clashes detected
            clash_data: JSON data containing clash results
            original_clash_id: ID of the original clash result (for versioning)
            
        Returns:
            int: The ID of the newly inserted record or None if insert failed
        """
        logger.info(f"Attempting to save clash result to PostgreSQL: {clash_set_name}, {clash_count} clashes")
        
        # Import here to avoid module not found error
        try:
            import psycopg2
            from psycopg2.extras import Json
        except ImportError as e:
            logger.error(f"ERROR: Failed to import psycopg2 when saving clash result: {str(e)}")
            return None
        
        conn = self.get_connection()
        if not conn:
            logger.warning("Database connection not available. Skipping database storage.")
            return None
        
        try:
            cursor = conn.cursor()
            
            # Insert clash result into database
            query = """
            INSERT INTO clash_results 
            (clash_set_name, output_filename, clash_count, clash_data, original_clash_id) 
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id;
            """
            
            # Get current time for logging
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"[{now}] Executing SQL query: {query}")
            
            cursor.execute(
                query, 
                (
                    clash_set_name, 
                    output_filename, 
                    clash_count, 
                    Json(clash_data), 
                    original_clash_id
                )
            )
            
            result_id = cursor.fetchone()[0]
            conn.commit()
            
            logger.info(f"Successfully saved clash result to PostgreSQL with ID: {result_id}")
            return result_id
                
        except Exception as e:
            logger.error(f"Error saving clash result to PostgreSQL: {str(e)}")
            logger.error(traceback.format_exc())
            if conn:
                conn.rollback()
            return None
        finally:
            if conn:
                conn.close()
                
    def save_diff_result(self, old_file: str, new_file: str, 
                         output_filename: str, diff_data: Dict[str, Any]) -> Optional[int]:
        """
        Save IFC diff results to PostgreSQL
        
        Args:
            old_file: Path to old IFC file
            new_file: Path to new IFC file
            output_filename: Path to output JSON file
            diff_data: JSON data containing diff results
            
        Returns:
            int: The ID of the newly inserted record or None if insert failed
        """
        # Count the number of differences
        diff_count = 0
        try:
            if isinstance(diff_data, dict):
                for category in ['added', 'deleted', 'modified']:
                    if category in diff_data:
                        diff_count += len(diff_data[category])
        except Exception as e:
            logger.warning(f"Could not count differences: {str(e)}")
            # Continue anyway, setting a default count
            diff_count = -1  # Indicates count could not be determined
            
        logger.info(f"Attempting to save diff result to PostgreSQL: {old_file} -> {new_file}, {diff_count} differences")
        
        try:
            import psycopg2
            from psycopg2.extras import Json
        except ImportError as e:
            logger.error(f"ERROR: Failed to import psycopg2 when saving diff result: {str(e)}")
            return None
        
        conn = self.get_connection()
        if not conn:
            logger.warning("Database connection not available. Skipping database storage.")
            return None
        
        try:
            cursor = conn.cursor()
            
            # Insert diff result into database
            query = """
            INSERT INTO diff_results 
            (old_file, new_file, diff_count, diff_data) 
            VALUES (%s, %s, %s, %s)
            RETURNING id;
            """
            
            # Get current time for logging
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"[{now}] Executing SQL query: {query}")
            
            cursor.execute(
                query, 
                (
                    old_file, 
                    new_file, 
                    diff_count, 
                    Json(diff_data)
                )
            )
            
            result_id = cursor.fetchone()[0]
            conn.commit()
            
            logger.info(f"Successfully saved diff result to PostgreSQL with ID: {result_id}")
            return result_id
                
        except Exception as e:
            logger.error(f"Error saving diff result to PostgreSQL: {str(e)}")
            logger.error(traceback.format_exc())
            if conn:
                conn.rollback()
            return None
        finally:
            if conn:
                conn.close()
    
    def save_tester_result(self, ifc_filename: str, ids_filename: str, 
                           output_filename: str, test_results: Dict[str, Any],
                           pass_count: int, fail_count: int) -> Optional[int]:
        """
        Save IFC tester results to PostgreSQL
        
        Args:
            ifc_filename: Path to IFC file
            ids_filename: Path to IDS file
            output_filename: Path to output report file
            test_results: JSON data containing test results
            pass_count: Number of passing tests
            fail_count: Number of failing tests
            
        Returns:
            int: The ID of the newly inserted record or None if insert failed
        """
        logger.info(f"Attempting to save tester result to PostgreSQL: {ifc_filename}, pass={pass_count}, fail={fail_count}")
        
        try:
            import psycopg2
            from psycopg2.extras import Json
        except ImportError as e:
            logger.error(f"ERROR: Failed to import psycopg2 when saving tester result: {str(e)}")
            return None
        
        conn = self.get_connection()
        if not conn:
            logger.warning("Database connection not available. Skipping database storage.")
            return None
        
        try:
            cursor = conn.cursor()
            
            # Insert tester result into database
            query = """
            INSERT INTO tester_results 
            (ifc_filename, ids_filename, test_results, pass_count, fail_count) 
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id;
            """
            
            # Get current time for logging
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"[{now}] Executing SQL query: {query}")
            
            cursor.execute(
                query, 
                (
                    ifc_filename, 
                    ids_filename, 
                    Json(test_results), 
                    pass_count,
                    fail_count
                )
            )
            
            result_id = cursor.fetchone()[0]
            conn.commit()
            
            logger.info(f"Successfully saved tester result to PostgreSQL with ID: {result_id}")
            return result_id
                
        except Exception as e:
            logger.error(f"Error saving tester result to PostgreSQL: {str(e)}")
            logger.error(traceback.format_exc())
            if conn:
                conn.rollback()
            return None
        finally:
            if conn:
                conn.close()
    
    def save_conversion_result(self, input_filename: str, output_filename: str, 
                              conversion_options: Dict[str, Any]) -> Optional[int]:
        """
        Save IFC conversion results to PostgreSQL
        
        Args:
            input_filename: Path to input IFC file
            output_filename: Path to output file
            conversion_options: Dictionary of options used for conversion
            
        Returns:
            int: The ID of the newly inserted record or None if insert failed
        """
        logger.info(f"Attempting to save conversion result to PostgreSQL: {input_filename} -> {output_filename}")
        
        try:
            import psycopg2
            from psycopg2.extras import Json
        except ImportError as e:
            logger.error(f"ERROR: Failed to import psycopg2 when saving conversion result: {str(e)}")
            return None
        
        conn = self.get_connection()
        if not conn:
            logger.warning("Database connection not available. Skipping database storage.")
            return None
        
        try:
            cursor = conn.cursor()
            
            # Insert conversion result into database
            query = """
            INSERT INTO conversion_results 
            (input_filename, output_filename, conversion_options) 
            VALUES (%s, %s, %s)
            RETURNING id;
            """
            
            # Get current time for logging
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"[{now}] Executing SQL query: {query}")
            
            cursor.execute(
                query, 
                (
                    input_filename, 
                    output_filename, 
                    Json(conversion_options)
                )
            )
            
            result_id = cursor.fetchone()[0]
            conn.commit()
            
            logger.info(f"Successfully saved conversion result to PostgreSQL with ID: {result_id}")
            return result_id
                
        except Exception as e:
            logger.error(f"Error saving conversion result to PostgreSQL: {str(e)}")
            logger.error(traceback.format_exc())
            if conn:
                conn.rollback()
            return None
        finally:
            if conn:
                conn.close()

# Singleton pattern for db client
db_client = DBClient()

# Backward compatibility functions
def get_db_connection():
    """Get a connection to the PostgreSQL database (for backward compatibility)"""
    return db_client.get_connection()

def save_clash_result(clash_set_name, output_filename, clash_count, clash_data, original_clash_id=None):
    """Save clash detection results to PostgreSQL (for backward compatibility)"""
    return db_client.save_clash_result(clash_set_name, output_filename, clash_count, clash_data, original_clash_id)

def save_diff_result(old_file, new_file, output_filename, diff_data):
    """Save diff results to PostgreSQL (for backward compatibility)"""
    return db_client.save_diff_result(old_file, new_file, output_filename, diff_data)

def save_tester_result(ifc_filename, ids_filename, output_filename, test_results, pass_count, fail_count):
    """Save tester results to PostgreSQL (for backward compatibility)"""
    return db_client.save_tester_result(ifc_filename, ids_filename, output_filename, test_results, pass_count, fail_count)

def save_conversion_result(input_filename, output_filename, conversion_options):
    """Save conversion results to PostgreSQL (for backward compatibility)"""
    return db_client.save_conversion_result(input_filename, output_filename, conversion_options) 