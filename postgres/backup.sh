#!/bin/bash
BACKUP_DIR="/backups/postgres"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
CONTAINER_NAME="ifc_pipeline_postgres"
mkdir -p $BACKUP_DIR

# Backup the entire database
docker exec $CONTAINER_NAME pg_dump -U ifcpipeline -d ifcpipeline -F c > $BACKUP_DIR/ifcpipeline_backup_$TIMESTAMP.dump

# Compress the backup
gzip $BACKUP_DIR/ifcpipeline_backup_$TIMESTAMP.dump

# Clean up old backups - keep only the last 7 days
find $BACKUP_DIR -name "ifcpipeline_backup_*.dump.gz" -type f -mtime +7 -delete 