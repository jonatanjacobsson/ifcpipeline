# PostgreSQL Integration for IFC Pipeline

This directory contains configuration and utilities for the PostgreSQL database used by the IFC Pipeline workers.

## Overview

The PostgreSQL database stores results from various IFC processing workers:

- Clash detection results
- Conversion results
- Tester results
- Diff results

## Structure

- `init/` - Contains initialization scripts that run when the container is first started
- `backup.sh` - Script for backing up the PostgreSQL database
- `maintenance.sh` - Script for performing database maintenance

## Configuration

Database configuration is set via environment variables in your `.env` file:

```
POSTGRES_USER=ifcpipeline
POSTGRES_PASSWORD=<insert your password>
POSTGRES_DB=ifcpipeline
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
```

## Backup

To manually backup the database:

```bash
./postgres/backup.sh
```

Backups are stored in `/backups/postgres/` by default. The script reads these environment variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `BACKUP_DIR` | `/backups/postgres` | Where dumps are written |
| `RETENTION_DAYS` | `7` | Dumps older than this are deleted after a successful run |
| `POSTGRES_CONTAINER` | `ifc_pipeline_postgres_objectstorage` | Container to dump from |
| `POSTGRES_USER` / `POSTGRES_DB` | `ifcpipeline` | Credentials and database |
| `PG_DUMP_EXCLUDE` | (empty) | Space-separated table patterns to skip, e.g. a table that uses an extension the postgres image does not ship |

It exits non-zero if the container is down or `pg_dump` fails or writes nothing, so cron mail or a monitor sees the failure. Restore with `docker exec -i ifc_pipeline_postgres_objectstorage pg_restore -U ifcpipeline -d ifcpipeline < <file>.dump`.

## Maintenance

To run database maintenance:

```bash
./postgres/maintenance.sh
```

## Automated Scheduling

You can schedule backups and maintenance using cron:

```
# Add these lines to crontab with 'crontab -e'
0 2 * * * /path/to/postgres/backup.sh
0 3 * * 0 /path/to/postgres/maintenance.sh
``` 