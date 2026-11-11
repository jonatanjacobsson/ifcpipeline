#!/bin/bash
# Dump the ifcpipeline database to $BACKUP_DIR and keep $RETENTION_DAYS days.
# Exits non-zero (and leaves no file behind) if the dump fails or is empty.
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/backups/postgres}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
CONTAINER_NAME="${POSTGRES_CONTAINER:-ifc_pipeline_postgres_objectstorage}"
DB_USER="${POSTGRES_USER:-ifcpipeline}"
DB_NAME="${POSTGRES_DB:-ifcpipeline}"
# Space-separated table patterns to skip (pg_dump -T), e.g. a table whose
# extension library the postgres image lacks: PG_DUMP_EXCLUDE="public.some_table"
PG_DUMP_EXCLUDE="${PG_DUMP_EXCLUDE:-}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
TARGET="$BACKUP_DIR/ifcpipeline_backup_$TIMESTAMP.dump"

mkdir -p "$BACKUP_DIR"

if ! docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null | grep -qx true; then
    echo "backup.sh: container $CONTAINER_NAME is not running" >&2
    exit 1
fi

# read -a splits on spaces without glob-expanding patterns like public.log_*
read -r -a exclude_patterns <<< "$PG_DUMP_EXCLUDE"
exclude_args=()
for pattern in ${exclude_patterns[@]+"${exclude_patterns[@]}"}; do
    exclude_args+=(-T "$pattern")
done

# Custom format (-F c) is already compressed. Write to a temp name so a failed
# dump never looks like a good backup.
docker exec "$CONTAINER_NAME" pg_dump -U "$DB_USER" -d "$DB_NAME" -F c ${exclude_args[@]+"${exclude_args[@]}"} > "$TARGET.partial" \
    || { rm -f "$TARGET.partial"; echo "backup.sh: pg_dump failed" >&2; exit 1; }
if [ ! -s "$TARGET.partial" ]; then
    rm -f "$TARGET.partial"
    echo "backup.sh: pg_dump produced an empty file" >&2
    exit 1
fi
mv "$TARGET.partial" "$TARGET"
echo "backup.sh: wrote $TARGET ($(du -h "$TARGET" | cut -f1))"

# Prune only after a successful dump, so a run of failures never deletes the last good one.
find "$BACKUP_DIR" \( -name "ifcpipeline_backup_*.dump" -o -name "ifcpipeline_backup_*.dump.gz" \) -type f -mtime +"$RETENTION_DAYS" -delete
