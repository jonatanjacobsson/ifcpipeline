# PostgreSQL Integration for IFC Pipeline

This directory contains configuration and utilities for the PostgreSQL database used by the IFC Pipeline workers and n8n workflow automation.

## Overview

The PostgreSQL instance hosts two separate databases:

### 1. IFC Pipeline Database (`ifcpipeline`)
Stores results from various IFC processing workers:
- Clash detection results
- Conversion results
- Tester results
- Diff results

### 2. n8n Database (`n8n`)
Stores n8n workflow automation data:
- Workflow definitions
- Credentials (encrypted)
- Execution history
- Webhooks and triggers
- Tags and settings

## Structure

- `init/` - Contains initialization scripts that run when the container is first started
  - `01-init.sql` - Creates IFC Pipeline database schema (tables for clash, diff, tester, conversion results)
  - `02-n8n-init.sql` - Creates n8n database and grants permissions
- `backup.sh` - Script for backing up the PostgreSQL database
- `maintenance.sh` - Script for performing database maintenance

## Configuration

Database configuration is set via environment variables in your `.env` file:

```bash
# PostgreSQL Server Configuration
POSTGRES_USER=ifcpipeline
POSTGRES_PASSWORD=<insert your password>
POSTGRES_DB=ifcpipeline
POSTGRES_HOST=postgres
POSTGRES_PORT=5432

# n8n Database Configuration
DB_TYPE=postgresdb
N8N_POSTGRES_DB=n8n
```

Both databases use the same PostgreSQL user (`ifcpipeline`) with appropriate permissions.

## Backup

### Backup All Databases

To manually backup all databases:

```bash
./postgres/backup.sh
```

Backups are stored in `/backups/postgres/` by default. You may need to create this directory or adjust the path in the script.

### Backup Individual Databases

**IFC Pipeline database:**
```bash
docker compose exec postgres pg_dump -U ifcpipeline -d ifcpipeline -F c -f /tmp/ifcpipeline_backup.dump
docker compose cp postgres:/tmp/ifcpipeline_backup.dump ./ifcpipeline_backup_$(date +%Y%m%d).dump
```

**n8n database:**
```bash
docker compose exec postgres pg_dump -U ifcpipeline -d n8n -F c -f /tmp/n8n_backup.dump
docker compose cp postgres:/tmp/n8n_backup.dump ./n8n_backup_$(date +%Y%m%d).dump
```

### Restore Databases

**Restore IFC Pipeline database:**
```bash
docker compose cp ifcpipeline_backup.dump postgres:/tmp/
docker compose exec postgres pg_restore -U ifcpipeline -d ifcpipeline -c /tmp/ifcpipeline_backup.dump
```

**Restore n8n database:**
```bash
docker compose cp n8n_backup.dump postgres:/tmp/
docker compose exec postgres pg_restore -U ifcpipeline -d n8n -c /tmp/n8n_backup.dump
```

## Maintenance

To run database maintenance:

```bash
./postgres/maintenance.sh
```

## Automated Scheduling

You can schedule backups and maintenance using cron:

```bash
# Add these lines to crontab with 'crontab -e'
0 2 * * * /path/to/postgres/backup.sh
0 3 * * 0 /path/to/postgres/maintenance.sh
```

## Database Access

### Web Interface (PgWeb)

Access the database through PgWeb at: **http://localhost:8081**

### Command Line

**Connect to IFC Pipeline database:**
```bash
docker compose exec postgres psql -U ifcpipeline -d ifcpipeline
```

**Connect to n8n database:**
```bash
docker compose exec postgres psql -U ifcpipeline -d n8n
```

**List all databases:**
```bash
docker compose exec postgres psql -U ifcpipeline -l
```

## Verify n8n PostgreSQL Integration

Run the test script to verify n8n is using PostgreSQL:

```bash
./test_n8n_postgres.sh
```

This will check:
- n8n database configuration
- Network connectivity
- Database existence
- n8n tables creation
- No SQLite database present

## Troubleshooting

### Check database sizes

```bash
docker compose exec postgres psql -U ifcpipeline -c "
SELECT 
    datname as database,
    pg_size_pretty(pg_database_size(datname)) as size
FROM pg_database
WHERE datname IN ('ifcpipeline', 'n8n')
ORDER BY datname;
"
```

### Check table counts

**IFC Pipeline tables:**
```bash
docker compose exec postgres psql -U ifcpipeline -d ifcpipeline -c "\dt"
```

**n8n tables:**
```bash
docker compose exec postgres psql -U ifcpipeline -d n8n -c "\dt"
```

### View connections

```bash
docker compose exec postgres psql -U ifcpipeline -c "
SELECT datname, count(*) as connections 
FROM pg_stat_activity 
GROUP BY datname;
"
```

## Additional Resources

- [n8n PostgreSQL Configuration Guide](../N8N_POSTGRES_QUICKSTART.md)
- [Detailed n8n PostgreSQL Analysis](../N8N_POSTGRES_CONFIGURATION_ANALYSIS.md)
- [PostgreSQL Official Documentation](https://www.postgresql.org/docs/14/) 