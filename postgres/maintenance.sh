#!/bin/bash
CONTAINER_NAME="ifc_pipeline_postgres"

# Run VACUUM ANALYZE to optimize the database
docker exec $CONTAINER_NAME psql -U ifcpipeline -d ifcpipeline -c "VACUUM ANALYZE;"

# Log database statistics
docker exec $CONTAINER_NAME psql -U ifcpipeline -d ifcpipeline -c "SELECT pg_database_size('ifcpipeline');" 