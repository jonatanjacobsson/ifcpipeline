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

Backups are stored in `/backups/postgres/` by default. You may need to create this directory or adjust the path in the script.

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