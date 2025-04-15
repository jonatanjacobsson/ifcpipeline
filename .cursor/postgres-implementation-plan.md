# Simple PostgreSQL Integration for IFC Pipeline Workers

This plan integrates a standalone PostgreSQL database with JSONB support directly into your existing Docker setup, providing a simpler alternative to the full Supabase implementation.

## 1. Adding PostgreSQL to Your Docker Setup

### 1.1. Update Docker Compose File

Add PostgreSQL service to your `docker-compose.yml`:

```yaml
services:
  # Your existing services...
  
  postgres:
    image: postgres:14
    container_name: ifc_pipeline_postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-ifcpipeline}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB:-ifcpipeline}
    ports:
      - "5432:5432"
    volumes:
      - postgres-data:/var/lib/postgresql/data
      - ./postgres/init:/docker-entrypoint-initdb.d
    networks:
      - default
    deploy:
      resources:
        limits:
          cpus: '0.5'
          memory: 512M

volumes:
  # Your existing volumes...
  postgres-data:
```

### 1.2. Create Initial Database Scripts

Create a directory structure for initialization scripts:

```bash
mkdir -p postgres/init
```

Create a file `postgres/init/01-init.sql` to set up tables:

```sql
-- Create clash results table
CREATE TABLE IF NOT EXISTS clash_results (
    id SERIAL PRIMARY KEY,
    original_clash_id INTEGER REFERENCES clash_results(id),
    clash_set_name TEXT NOT NULL,
    output_filename TEXT NOT NULL,
    clash_count INTEGER NOT NULL,
    clash_data JSONB NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Create indexes for faster searching
CREATE INDEX IF NOT EXISTS idx_clash_results_original_clash_id ON clash_results(original_clash_id);
CREATE INDEX IF NOT EXISTS idx_clash_results_clash_set_name ON clash_results(clash_set_name);
CREATE INDEX IF NOT EXISTS idx_clash_results_created_at ON clash_results(created_at);
CREATE INDEX IF NOT EXISTS idx_clash_data ON clash_results USING gin (clash_data);

-- Create conversion results table
CREATE TABLE IF NOT EXISTS conversion_results (
    id SERIAL PRIMARY KEY,
    input_filename TEXT NOT NULL,
    output_filename TEXT NOT NULL,
    conversion_options JSONB NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Create tester results table
CREATE TABLE IF NOT EXISTS tester_results (
    id SERIAL PRIMARY KEY,
    ifc_filename TEXT NOT NULL,
    ids_filename TEXT NOT NULL,
    test_results JSONB NOT NULL,
    pass_count INTEGER NOT NULL,
    fail_count INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Create diff results table
CREATE TABLE IF NOT EXISTS diff_results (
    id SERIAL PRIMARY KEY,
    old_file TEXT NOT NULL,
    new_file TEXT NOT NULL,
    diff_count INTEGER NOT NULL,
    diff_data JSONB NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

### 1.3. Set Environment Variables

Add database credentials to your `.env` file:

```
# PostgreSQL Configuration
POSTGRES_USER=ifcpipeline
POSTGRES_PASSWORD=your-password-here
POSTGRES_DB=ifcpipeline
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
```

## 2. Update ifcclash-worker to Connect to PostgreSQL

### 2.1. Update ifcclash-worker Dockerfile

Modify the Dockerfile to include the PostgreSQL client:

```dockerfile
# Add to existing ifcclash-worker/Dockerfile
RUN pip install psycopg2-binary
```

### 2.2. Create Database Client Module

Create a new file `ifcclash-worker/db_client.py`:

```python
import os
import json
import logging
import psycopg2
from psycopg2.extras import Json

logger = logging.getLogger(__name__)

# PostgreSQL connection details from environment variables
DB_HOST = os.environ.get("POSTGRES_HOST", "postgres")
DB_PORT = os.environ.get("POSTGRES_PORT", "5432")
DB_NAME = os.environ.get("POSTGRES_DB", "ifcpipeline")
DB_USER = os.environ.get("POSTGRES_USER", "ifcpipeline")
DB_PASS = os.environ.get("POSTGRES_PASSWORD", "")

def get_db_connection():
    """Get a connection to the PostgreSQL database"""
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASS
        )
        return conn
    except Exception as e:
        logger.error(f"Error connecting to PostgreSQL: {str(e)}")
        return None

def save_clash_result(clash_set_name, output_filename, clash_count, clash_data, original_clash_id=None):
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
    conn = get_db_connection()
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
        logger.error(f"Error saving clash result to PostgreSQL: {str(e)}", exc_info=True)
        if conn:
            conn.rollback()
        return None
    finally:
        if conn:
            conn.close()
```

### 2.3. Update Tasks Implementation

Modify `ifcclash-worker/tasks.py` to save results to PostgreSQL before returning:

```python
# Add this import at the top of the file
from db_client import save_clash_result

# Modify the try/except block near the end of run_ifcclash_detection to save results before returning
# Around line 150 in the current implementation

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
```

### 2.4. Update Docker Compose Configuration

Add PostgreSQL environment variables to the ifcclash-worker service in your `docker-compose.yml`:

```yaml
ifcclash-worker:
  build:
    context: .
    dockerfile: ifcclash-worker/Dockerfile
  volumes:
    - ./shared/uploads:/uploads
    - ./shared/output:/output
    - ./shared/examples:/examples
  environment:
    - PYTHONUNBUFFERED=1
    - LOG_LEVEL=DEBUG
    - REDIS_URL=redis://redis:6379/0
    - POSTGRES_HOST=postgres
    - POSTGRES_PORT=5432
    - POSTGRES_DB=ifcpipeline
    - POSTGRES_USER=ifcpipeline
    - POSTGRES_PASSWORD=${POSTGRES_PASSWORD:}
  depends_on:
    - redis
    - postgres
  restart: unless-stopped
  deploy:
    replicas: 1
    resources:
      limits:
        cpus: '4.0'
        memory: '6G'
```

## 3. Implementation for Other Workers

### 3.1. For ifcconvert-worker

Create a client for the ifcconvert-worker to save conversion results:

```python
def save_conversion_result(input_filename, output_filename, conversion_options):
    """Save conversion results to PostgreSQL"""
    conn = get_db_connection()
    if not conn:
        return None
    
    try:
        cursor = conn.cursor()
        
        query = """
        INSERT INTO conversion_results 
        (input_filename, output_filename, conversion_options) 
        VALUES (%s, %s, %s)
        RETURNING id;
        """
        
        cursor.execute(
            query, 
            (input_filename, output_filename, Json(conversion_options))
        )
        
        result_id = cursor.fetchone()[0]
        conn.commit()
        
        return result_id
            
    except Exception as e:
        logger.error(f"Error saving conversion result: {str(e)}")
        if conn:
            conn.rollback()
        return None
    finally:
        if conn:
            conn.close()
```

### 3.2. For ifctester-worker

Create a client for the ifctester-worker to save test results:

```python
def save_test_result(ifc_filename, ids_filename, test_results, pass_count, fail_count):
    """Save test results to PostgreSQL"""
    conn = get_db_connection()
    if not conn:
        return None
    
    try:
        cursor = conn.cursor()
        
        query = """
        INSERT INTO tester_results 
        (ifc_filename, ids_filename, test_results, pass_count, fail_count) 
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id;
        """
        
        cursor.execute(
            query, 
            (ifc_filename, ids_filename, Json(test_results), pass_count, fail_count)
        )
        
        result_id = cursor.fetchone()[0]
        conn.commit()
        
        return result_id
            
    except Exception as e:
        logger.error(f"Error saving test result: {str(e)}")
        if conn:
            conn.rollback()
        return None
    finally:
        if conn:
            conn.close()
```

## 4. Backup and Maintenance

### 4.1. Backup Strategy

Create a simple backup script in `postgres/backup.sh`:

```bash
#!/bin/bash
BACKUP_DIR="/path/to/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
CONTAINER_NAME="ifc_pipeline_postgres"
mkdir -p $BACKUP_DIR

# Backup the entire database
docker exec $CONTAINER_NAME pg_dump -U ifcpipeline -d ifcpipeline -F c > $BACKUP_DIR/ifcpipeline_backup_$TIMESTAMP.dump

# Compress the backup
gzip $BACKUP_DIR/ifcpipeline_backup_$TIMESTAMP.dump

# Clean up old backups - keep only the last 7 days
find $BACKUP_DIR -name "ifcpipeline_backup_*.dump.gz" -type f -mtime +7 -delete
```

Make the script executable and add to crontab:

```bash
chmod +x postgres/backup.sh
crontab -e
# Add the following line:
# 0 2 * * * /path/to/postgres/backup.sh
```

### 4.2. Basic Maintenance

Create a simple maintenance script in `postgres/maintenance.sh`:

```bash
#!/bin/bash
CONTAINER_NAME="ifc_pipeline_postgres"

# Run VACUUM ANALYZE to optimize the database
docker exec $CONTAINER_NAME psql -U ifcpipeline -d ifcpipeline -c "VACUUM ANALYZE;"

# Log database statistics
docker exec $CONTAINER_NAME psql -U ifcpipeline -d ifcpipeline -c "SELECT pg_database_size('ifcpipeline');"
```

Make the script executable and add to crontab:

```bash
chmod +x postgres/maintenance.sh
crontab -e
# Add the following line:
# 0 3 * * 0 /path/to/postgres/maintenance.sh
```

## 5. Integration with n8n

Since your project uses n8n for workflow automation, you can add a PostgreSQL connection to query and visualize clash data:

1. Use the PostgreSQL node in n8n to connect to your database
2. Create workflows to query clash detection results
3. Set up notifications for new clash detections
4. Create reports by combining data from multiple tables

## 6. Benefits of This Approach

1. **Simplicity**: Just one additional container (PostgreSQL) in your stack
2. **Direct Access**: Workers connect directly to the database without any intermediary
3. **Performance**: Native PostgreSQL JSONB provides excellent performance for JSON data
4. **Compatibility**: PostgreSQL is widely supported by many tools and libraries
5. **Lightweight**: Lower resource requirements compared to full Supabase
6. **Integration**: Easy to integrate with existing components like n8n

## 7. Future Enhancements

1. **Connection Pooling**: Add PgBouncer for connection pooling if needed
2. **High Availability**: Configure PostgreSQL replication for high availability
3. **Query UI**: Add pgAdmin or similar tool for database management
4. **Custom Functions**: Create PostgreSQL functions for common queries
5. **Data Archiving**: Implement time-based partitioning for archiving old data 