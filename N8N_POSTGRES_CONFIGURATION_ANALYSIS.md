# n8n PostgreSQL Configuration Analysis

## Executive Summary

This document provides a thorough analysis of what needs to be in place for n8n to use the PostgreSQL database in this project from the very beginning, instead of defaulting to SQLite.

## Current State Analysis

### Existing Infrastructure
Your project already has:
1. ✅ **PostgreSQL service** running in `docker-compose.yml` (lines 331-350)
2. ✅ **PostgreSQL credentials** defined in `.env.example`
3. ✅ **n8n service** configured in `docker-compose.yml` (lines 295-329)
4. ✅ **Volume mapping** for n8n data at `./n8n-data:/home/node/.n8n`

### What's Missing
The n8n service is **NOT configured to use PostgreSQL** - it will default to SQLite because the required database environment variables are not set.

---

## Required Changes

### 1. Environment Variables in `.env` File

Add the following n8n database configuration variables to your `.env` file:

```bash
# n8n Database Configuration (PostgreSQL)
DB_TYPE=postgresdb
DB_POSTGRESDB_DATABASE=n8n
DB_POSTGRESDB_HOST=postgres
DB_POSTGRESDB_PORT=5432
DB_POSTGRESDB_USER=${POSTGRES_USER:-ifcpipeline}
DB_POSTGRESDB_PASSWORD=${POSTGRES_PASSWORD}
DB_POSTGRESDB_SCHEMA=public
```

**Explanation:**
- `DB_TYPE=postgresdb` - Tells n8n to use PostgreSQL instead of SQLite
- `DB_POSTGRESDB_DATABASE=n8n` - Separate database name for n8n (or use same `ifcpipeline` DB)
- `DB_POSTGRESDB_HOST=postgres` - Docker service name for PostgreSQL
- `DB_POSTGRESDB_PORT=5432` - Standard PostgreSQL port
- `DB_POSTGRESDB_USER` - Database user (reuses your existing POSTGRES_USER)
- `DB_POSTGRESDB_PASSWORD` - Database password (reuses your existing POSTGRES_PASSWORD)
- `DB_POSTGRESDB_SCHEMA=public` - Default schema (optional but explicit)

### 2. Update `docker-compose.yml`

Modify the n8n service environment section (starting at line 307) to include database configuration:

```yaml
  n8n:
    image: docker.n8n.io/n8nio/n8n
    ports:
      - "5678:5678"
    networks:
      - default
      - remotely-net
    volumes:
      - ./n8n-data:/home/node/.n8n
      - ./shared/uploads:/uploads
      - ./shared/output:/output
      - ./shared/examples:/examples
    environment:
      - N8N_HOST=0.0.0.0
      - N8N_PORT=5678
      - N8N_PROTOCOL=http
      - IFC_PIPELINE_URL=http://api-gateway
      - WEBHOOK_URL=${N8N_WEBHOOK_URL}
      - REMOTELY_URL=http://remotely:5000
      - N8N_SECURE_COOKIE=false
      - GENERIC_TIMEZONE=Europe/Stockholm
      - N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS=true
      - N8N_COMMUNITY_PACKAGES_ENABLED=${N8N_COMMUNITY_PACKAGES_ENABLED}
      # PostgreSQL Database Configuration
      - DB_TYPE=postgresdb
      - DB_POSTGRESDB_DATABASE=${N8N_POSTGRES_DB:-n8n}
      - DB_POSTGRESDB_HOST=postgres
      - DB_POSTGRESDB_PORT=5432
      - DB_POSTGRESDB_USER=${POSTGRES_USER:-ifcpipeline}
      - DB_POSTGRESDB_PASSWORD=${POSTGRES_PASSWORD}
      - DB_POSTGRESDB_SCHEMA=public
      # PostgreSQL SSL settings
      - NODE_TLS_REJECT_UNAUTHORIZED=0
      - PGSSLMODE=disable
    depends_on:
      - api-gateway
      - postgres
    restart: unless-stopped
    deploy:
      resources:
        limits:
          cpus: '4'
          memory: 6000M
```

**Key changes:**
- Added 6 new `DB_*` environment variables
- Existing SSL settings are already appropriate
- `depends_on: postgres` already exists ✅

### 3. PostgreSQL Database Initialization

Add n8n database creation to `/workspace/postgres/init/01-init.sql`:

```sql
-- Create n8n database (if using separate database)
CREATE DATABASE n8n;

-- Grant permissions to the existing user
GRANT ALL PRIVILEGES ON DATABASE n8n TO ifcpipeline;

-- Connect to n8n database and create schema
\c n8n;
CREATE SCHEMA IF NOT EXISTS public;
GRANT ALL ON SCHEMA public TO ifcpipeline;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO ifcpipeline;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO ifcpipeline;
```

**Alternative approach:** Use the same `ifcpipeline` database with a separate schema:
- Set `DB_POSTGRESDB_DATABASE=ifcpipeline`
- Set `DB_POSTGRESDB_SCHEMA=n8n`
- Add schema creation in init script:

```sql
-- Create n8n schema in existing database
CREATE SCHEMA IF NOT EXISTS n8n;
GRANT ALL ON SCHEMA n8n TO ifcpipeline;
```

### 4. Update `.env.example`

Add the n8n database variables to `.env.example` for documentation:

```bash
# n8n Database Configuration
# Set to postgresdb to use PostgreSQL instead of SQLite
DB_TYPE=postgresdb
N8N_POSTGRES_DB=n8n
```

---

## Implementation Decision Points

### Option A: Separate Database for n8n (Recommended)
**Pros:**
- Clear separation of concerns
- Easier to backup/restore independently
- No risk of naming conflicts

**Cons:**
- Slightly more complex setup
- One additional database

**Configuration:**
```bash
DB_POSTGRESDB_DATABASE=n8n
DB_POSTGRESDB_SCHEMA=public
```

### Option B: Shared Database with Separate Schema
**Pros:**
- Simpler database management
- Single backup process

**Cons:**
- Potential naming conflicts
- Mixed concerns in same database

**Configuration:**
```bash
DB_POSTGRESDB_DATABASE=ifcpipeline
DB_POSTGRESDB_SCHEMA=n8n
```

### Option C: Shared Database and Schema (Not Recommended)
**Pros:**
- Simplest setup

**Cons:**
- High risk of table name conflicts
- n8n creates many tables that could collide with your existing tables
- Harder to maintain

---

## Complete Implementation Checklist

When starting from scratch, ensure the following order:

### 1. Before First `docker compose up`:

- [ ] Create `.env` file from `.env.example`
- [ ] Set strong `POSTGRES_PASSWORD` in `.env`
- [ ] Add n8n database environment variables to `.env`:
  ```bash
  DB_TYPE=postgresdb
  N8N_POSTGRES_DB=n8n
  ```
- [ ] Update `docker-compose.yml` with n8n database environment variables
- [ ] Update `/workspace/postgres/init/01-init.sql` to create n8n database

### 2. First Startup:

```bash
# Build and start all services
docker compose up --build -d

# Verify PostgreSQL is running
docker compose logs postgres

# Verify n8n connected to PostgreSQL (check logs for database connection)
docker compose logs n8n | grep -i postgres

# Check that n8n tables were created
docker compose exec postgres psql -U ifcpipeline -d n8n -c "\dt"
```

### 3. Verification:

- [ ] Access n8n at http://localhost:5678
- [ ] Complete n8n setup wizard
- [ ] Create a test workflow
- [ ] Verify data persists after restart:
  ```bash
  docker compose restart n8n
  # Access n8n again and verify workflow still exists
  ```

### 4. Database Inspection:

Connect to PostgreSQL and verify n8n tables:

```bash
docker compose exec postgres psql -U ifcpipeline -d n8n
```

Expected n8n tables include:
- `execution_entity`
- `workflow_entity`
- `credentials_entity`
- `tag_entity`
- `webhook_entity`
- And many more (n8n creates ~30+ tables)

---

## Common Issues and Solutions

### Issue 1: n8n still uses SQLite

**Symptom:** After configuration, n8n still creates `database.sqlite` in `/n8n-data/`

**Solution:**
1. Check environment variables are passed: `docker compose exec n8n env | grep DB_`
2. Ensure no `database.sqlite` exists before first start
3. Verify PostgreSQL connection: `docker compose exec postgres psql -U ifcpipeline -d n8n -c "SELECT 1;"`

### Issue 2: Permission denied errors

**Symptom:** n8n logs show "permission denied for schema" or "permission denied for table"

**Solution:**
```sql
-- Run in PostgreSQL
GRANT ALL ON SCHEMA public TO ifcpipeline;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO ifcpipeline;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO ifcpipeline;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO ifcpipeline;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO ifcpipeline;
```

### Issue 3: Connection refused

**Symptom:** n8n logs show "Connection refused" to PostgreSQL

**Solution:**
1. Verify PostgreSQL is running: `docker compose ps postgres`
2. Check depends_on is set correctly in docker-compose.yml
3. Verify network connectivity: `docker compose exec n8n ping postgres`

### Issue 4: SSL/TLS errors

**Symptom:** n8n shows SSL certificate errors

**Solution:**
Already configured in your docker-compose.yml:
```yaml
- NODE_TLS_REJECT_UNAUTHORIZED=0
- PGSSLMODE=disable
```

### Issue 5: Migration from existing SQLite

**Symptom:** You already have data in SQLite and want to migrate

**Solution:**
n8n doesn't provide automatic migration. You'll need to:
1. Export workflows from old n8n (via API or UI)
2. Configure PostgreSQL as above
3. Start fresh n8n with PostgreSQL
4. Import workflows back

---

## Testing PostgreSQL Integration

### Quick Test Script

```bash
#!/bin/bash
# test_n8n_postgres.sh

echo "Testing n8n PostgreSQL integration..."

# 1. Check environment variables
echo "1. Checking n8n environment variables..."
docker compose exec n8n env | grep -E "DB_TYPE|DB_POSTGRESDB"

# 2. Check PostgreSQL connection from n8n container
echo "2. Testing PostgreSQL connection..."
docker compose exec n8n sh -c "nc -zv postgres 5432"

# 3. Verify n8n database exists
echo "3. Checking n8n database exists..."
docker compose exec postgres psql -U ifcpipeline -lqt | grep -qw n8n && echo "✓ Database exists" || echo "✗ Database missing"

# 4. Check n8n tables
echo "4. Checking n8n tables created..."
TABLE_COUNT=$(docker compose exec postgres psql -U ifcpipeline -d n8n -t -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public';")
echo "Found $TABLE_COUNT n8n tables"

# 5. Check for SQLite file (should not exist)
echo "5. Checking for SQLite database (should not exist)..."
if docker compose exec n8n test -f /home/node/.n8n/database.sqlite; then
    echo "✗ WARNING: SQLite database found! n8n may not be using PostgreSQL"
else
    echo "✓ No SQLite database found"
fi

echo "Test complete!"
```

---

## Advanced Configuration

### Connection Pooling

For production environments, configure connection pooling:

```yaml
environment:
  - DB_POSTGRESDB_POOL_SIZE=5
  - DB_POSTGRESDB_POOL_IDLE_TIMEOUT_MILLIS=30000
```

### SSL/TLS (for production)

For secure PostgreSQL connections:

```yaml
environment:
  - DB_POSTGRESDB_SSL_ENABLED=true
  - DB_POSTGRESDB_SSL_CA=/path/to/ca.pem
  - DB_POSTGRESDB_SSL_CERT=/path/to/cert.pem
  - DB_POSTGRESDB_SSL_KEY=/path/to/key.pem
  - DB_POSTGRESDB_SSL_REJECT_UNAUTHORIZED=true
  - PGSSLMODE=require
```

### Performance Tuning

PostgreSQL configuration for n8n (in `postgresql.conf` or via environment):

```conf
# Recommended settings for n8n
max_connections = 100
shared_buffers = 256MB
effective_cache_size = 1GB
maintenance_work_mem = 64MB
checkpoint_completion_target = 0.9
wal_buffers = 16MB
default_statistics_target = 100
random_page_cost = 1.1
effective_io_concurrency = 200
work_mem = 2621kB
min_wal_size = 1GB
max_wal_size = 4GB
```

---

## Backup Considerations

### Database Backup

Include n8n database in your backup strategy:

```bash
#!/bin/bash
# backup_n8n_postgres.sh

BACKUP_DIR="/backups/postgres/n8n"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/n8n_backup_${TIMESTAMP}.sql"

mkdir -p $BACKUP_DIR

docker compose exec postgres pg_dump -U ifcpipeline -d n8n -F c -b -v -f "/tmp/backup.dump"
docker compose cp postgres:/tmp/backup.dump "$BACKUP_FILE"
docker compose exec postgres rm /tmp/backup.dump

# Compress backup
gzip "$BACKUP_FILE"

echo "Backup completed: ${BACKUP_FILE}.gz"
```

### Volume Backup

Also backup the n8n data volume (encryption keys, settings):

```bash
# Backup n8n data directory
tar -czf /backups/n8n-data_$(date +%Y%m%d_%H%M%S).tar.gz ./n8n-data/
```

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────┐
│                     Docker Network                       │
│                                                          │
│  ┌──────────────┐         ┌─────────────────────────┐  │
│  │     n8n      │         │      PostgreSQL         │  │
│  │              │◄────────┤   (postgres:14)         │  │
│  │  Port: 5678  │  DB     │                         │  │
│  │              │  Conn   │  Databases:             │  │
│  └──────────────┘         │  - ifcpipeline (main)   │  │
│         │                 │  - n8n (workflows)      │  │
│         │                 └─────────────────────────┘  │
│         │                            │                 │
│         │                            │                 │
│  ┌──────▼──────────────────┐        │                 │
│  │   Volume Mappings       │        │                 │
│  │  - ./n8n-data           │        │                 │
│  │  - ./shared/uploads     │        ▼                 │
│  │  - ./shared/output      │  ┌─────────────────┐    │
│  │  - ./shared/examples    │  │  postgres-data  │    │
│  └─────────────────────────┘  │    (volume)     │    │
│                                └─────────────────┘    │
└─────────────────────────────────────────────────────────┘

Environment Variables Flow:
.env file ──► docker-compose.yml ──► n8n container
  │                                        │
  ├─ POSTGRES_USER ────────────────────────┤
  ├─ POSTGRES_PASSWORD ────────────────────┤
  ├─ DB_TYPE=postgresdb ───────────────────┤
  └─ N8N_POSTGRES_DB=n8n ──────────────────┘
```

---

## Summary: What Must Be In Place

### Before First Start (Critical):

1. **Environment Variables in `.env`:**
   - `DB_TYPE=postgresdb`
   - `N8N_POSTGRES_DB=n8n`
   - `POSTGRES_USER=ifcpipeline`
   - `POSTGRES_PASSWORD=<secure-password>`

2. **Docker Compose Configuration:**
   - All 6 `DB_POSTGRESDB_*` environment variables in n8n service
   - `depends_on: postgres` (already present)

3. **PostgreSQL Initialization:**
   - Database creation script in `postgres/init/02-n8n-init.sql`
   - Proper user permissions

### On First Start (Automatic):

4. **n8n Behavior:**
   - n8n will connect to PostgreSQL
   - n8n will create all required tables automatically
   - n8n will store encryption keys in `/home/node/.n8n`

### Verification (Post-Start):

5. **Validation:**
   - No `database.sqlite` file exists
   - PostgreSQL `n8n` database contains tables
   - n8n web interface accessible and functional
   - Workflows persist after container restart

---

## Recommended Implementation Steps

1. **Create the database initialization script:**
   - File: `/workspace/postgres/init/02-n8n-init.sql`
   - Content: Database and user setup

2. **Update docker-compose.yml:**
   - Add database environment variables to n8n service

3. **Update .env.example:**
   - Add n8n database configuration variables

4. **Create .env file (if starting fresh):**
   - Copy from .env.example
   - Set secure passwords

5. **Start services:**
   ```bash
   docker compose up --build -d
   ```

6. **Verify setup:**
   - Check n8n logs: `docker compose logs n8n`
   - Check PostgreSQL: `docker compose exec postgres psql -U ifcpipeline -d n8n -c "\dt"`
   - Access n8n: http://localhost:5678

7. **Complete n8n setup:**
   - Create admin account
   - Configure credentials for IFC Pipeline
   - Test workflow creation and execution

---

## Final Notes

- **This configuration ensures n8n uses PostgreSQL from the very first start**
- **No migration from SQLite is needed if implemented before first run**
- **All n8n data (workflows, credentials, executions) will be stored in PostgreSQL**
- **The existing PostgreSQL infrastructure in your project can be reused**
- **Backup strategy should include both PostgreSQL database and n8n-data volume**
- **The configuration is production-ready with SSL disabled for internal Docker network**

This implementation follows n8n best practices and integrates seamlessly with your existing IFC Pipeline architecture.
