# n8n PostgreSQL Quick Start Guide

This guide will help you configure n8n to use PostgreSQL from the very beginning when starting your project from scratch.

## Prerequisites

- Docker and Docker Compose installed
- Git (to clone the repository)

## Step-by-Step Setup

### 1. Clone and Navigate to Project

```bash
git clone https://github.com/jonatanjacobsson/ifcpipeline.git
cd ifcpipeline
```

### 2. Create Environment File

Copy the example environment file:

```bash
cp .env.example .env
```

### 3. Edit the .env File

Open `.env` in your text editor and set the required variables:

```bash
# REQUIRED: Set a strong PostgreSQL password
POSTGRES_PASSWORD=your-secure-password-here

# REQUIRED: Set your API key
IFC_PIPELINE_API_KEY=your-api-key-here

# OPTIONAL: Customize other settings as needed
IFC_PIPELINE_EXTERNAL_URL=https://your-domain.com
N8N_WEBHOOK_URL=https://your-n8n-webhooks.com
```

**Important:** The following n8n database settings are already configured in `.env.example`:
- `DB_TYPE=postgresdb` - Tells n8n to use PostgreSQL
- `N8N_POSTGRES_DB=n8n` - Creates a dedicated database for n8n

### 4. Verify Configuration Files

The repository already includes the necessary configuration:

✅ `docker-compose.yml` - n8n service configured with PostgreSQL environment variables
✅ `postgres/init/02-n8n-init.sql` - Automatically creates n8n database on first start
✅ `.env.example` - Includes n8n database configuration

### 5. Start All Services

Build and start all services:

```bash
docker compose up --build -d
```

This will:
- Start PostgreSQL and create the n8n database (via init script)
- Start n8n and automatically connect to PostgreSQL
- n8n will create all required tables on first startup
- Start all other IFC Pipeline services

### 6. Verify n8n PostgreSQL Integration

Run the test script to verify everything is configured correctly:

```bash
./test_n8n_postgres.sh
```

Expected output:
```
✓ n8n service is running
✓ PostgreSQL service is running
✓ DB_TYPE is set to postgresdb
✓ DB_POSTGRESDB_HOST is set to postgres
✓ Found 30+ n8n tables in PostgreSQL
✓ No SQLite database found - n8n is using PostgreSQL
```

### 7. Access n8n

Open your browser and navigate to:

```
http://localhost:5678
```

### 8. Complete n8n Setup

On first access, you'll see the n8n setup wizard:

1. Create your owner account (username, password, email)
2. Skip or complete the questionnaire
3. You're ready to create workflows!

### 9. Verify Data Persistence

Test that n8n data persists in PostgreSQL:

```bash
# Create a test workflow in n8n web interface
# Then restart n8n
docker compose restart n8n

# Wait a moment for n8n to start
sleep 10

# Access n8n again at http://localhost:5678
# Your workflow should still be there
```

## What Just Happened?

### Database Setup
- PostgreSQL created a dedicated `n8n` database
- n8n automatically created ~30+ tables for workflows, credentials, executions, etc.
- All n8n data is now stored in PostgreSQL instead of SQLite

### Tables Created by n8n
- `workflow_entity` - Your workflow definitions
- `credentials_entity` - Encrypted credentials
- `execution_entity` - Workflow execution history
- `tag_entity` - Workflow tags
- `webhook_entity` - Webhook configurations
- And 25+ more tables for n8n operations

## Access Points

Once everything is running, you can access:

| Service | URL | Purpose |
|---------|-----|---------|
| n8n | http://localhost:5678 | Workflow automation |
| API Gateway | http://localhost:8000 | IFC Pipeline API |
| API Docs | http://localhost:8000/docs | Interactive API documentation |
| IFC Viewer | http://localhost:8001 | 3D IFC viewer |
| PgWeb | http://localhost:8081 | PostgreSQL web interface |
| RQ Dashboard | http://localhost:9181 | Job queue monitoring |

## Troubleshooting

### n8n still creates SQLite database

**Problem:** You find a `database.sqlite` file in `./n8n-data/`

**Solution:**
```bash
# Stop n8n
docker compose stop n8n

# Remove the SQLite database
docker compose exec n8n rm /home/node/.n8n/database.sqlite

# Verify environment variables
docker compose exec n8n env | grep DB_

# Restart n8n
docker compose start n8n
```

### Cannot connect to PostgreSQL

**Problem:** n8n logs show connection errors

**Solution:**
```bash
# Check PostgreSQL is running
docker compose ps postgres

# Check PostgreSQL logs
docker compose logs postgres

# Verify database exists
docker compose exec postgres psql -U ifcpipeline -l

# Test connection from n8n container
docker compose exec n8n nc -zv postgres 5432
```

### Permission errors

**Problem:** n8n shows "permission denied" errors

**Solution:**
```bash
# Grant permissions to the database user
docker compose exec postgres psql -U ifcpipeline -d n8n -c "
GRANT ALL ON SCHEMA public TO ifcpipeline;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO ifcpipeline;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO ifcpipeline;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO ifcpipeline;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO ifcpipeline;
"
```

### Check n8n logs

View detailed n8n logs:
```bash
docker compose logs n8n -f
```

## Database Management

### View n8n Tables

```bash
# Connect to PostgreSQL
docker compose exec postgres psql -U ifcpipeline -d n8n

# List all tables
\dt

# Check workflow count
SELECT COUNT(*) FROM workflow_entity;

# Exit
\q
```

### Backup n8n Database

```bash
# Create backup
docker compose exec postgres pg_dump -U ifcpipeline -d n8n -F c -f /tmp/n8n_backup.dump

# Copy backup to host
docker compose cp postgres:/tmp/n8n_backup.dump ./n8n_backup_$(date +%Y%m%d).dump

# Compress backup
gzip n8n_backup_$(date +%Y%m%d).dump
```

### Restore n8n Database

```bash
# Copy backup to container
docker compose cp n8n_backup.dump postgres:/tmp/

# Restore backup
docker compose exec postgres pg_restore -U ifcpipeline -d n8n -c /tmp/n8n_backup.dump
```

## Advanced Configuration

### Using Same Database, Different Schema

If you prefer to use the same `ifcpipeline` database with a separate schema:

1. Edit `.env`:
   ```bash
   N8N_POSTGRES_DB=ifcpipeline
   ```

2. Edit `postgres/init/02-n8n-init.sql`:
   ```sql
   -- Use existing database, create schema
   \c ifcpipeline;
   CREATE SCHEMA IF NOT EXISTS n8n;
   GRANT ALL ON SCHEMA n8n TO ifcpipeline;
   ```

3. Edit `docker-compose.yml` - add schema environment variable:
   ```yaml
   - DB_POSTGRESDB_SCHEMA=n8n
   ```

### Connection Pooling

For production, configure connection pooling in `docker-compose.yml`:

```yaml
environment:
  - DB_POSTGRESDB_POOL_SIZE=10
  - DB_POSTGRESDB_POOL_IDLE_TIMEOUT_MILLIS=30000
```

## Next Steps

1. **Install n8n Community Nodes**
   - Open n8n at http://localhost:5678
   - Go to Settings > Community Nodes
   - Search and install: `n8n-nodes-ifcpipeline`

2. **Configure IFC Pipeline Credentials**
   - In n8n, go to Credentials
   - Add new credential for IFC Pipeline
   - Enter your API key from `.env` file

3. **Create Your First Workflow**
   - Use the IFC Pipeline nodes
   - Test with the example files in `/shared/examples/`

4. **Set Up Backups**
   - Schedule regular PostgreSQL backups
   - Backup `./n8n-data/` volume (encryption keys)

## Security Recommendations

- Change default passwords in `.env`
- Use strong, unique passwords
- Restrict network access to PostgreSQL port (5432)
- Enable SSL/TLS for production environments
- Regularly backup both PostgreSQL and n8n-data volume
- Keep n8n and PostgreSQL images updated

## Summary

✅ n8n uses PostgreSQL from first start
✅ No SQLite database created
✅ All workflows and data persist in PostgreSQL
✅ Integrated with existing IFC Pipeline infrastructure
✅ Ready for production use

## Need Help?

- View detailed analysis: [N8N_POSTGRES_CONFIGURATION_ANALYSIS.md](N8N_POSTGRES_CONFIGURATION_ANALYSIS.md)
- Check n8n logs: `docker compose logs n8n -f`
- Run test script: `./test_n8n_postgres.sh`
- Open an issue on GitHub

---

**Congratulations!** Your n8n instance is now using PostgreSQL and ready for workflow automation with IFC Pipeline.
