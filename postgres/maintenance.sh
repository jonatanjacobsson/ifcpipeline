#!/bin/bash
set -euo pipefail

CONTAINER_NAME="${POSTGRES_CONTAINER:-ifc_pipeline_postgres_objectstorage}"
DB_USER="${POSTGRES_USER:-ifcpipeline}"
DB_NAME="${POSTGRES_DB:-ifcpipeline}"

# Run VACUUM ANALYZE to optimize the database
docker exec "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -c "VACUUM ANALYZE;"

# Log database statistics
docker exec "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -c "SELECT pg_size_pretty(pg_database_size(current_database()));"
