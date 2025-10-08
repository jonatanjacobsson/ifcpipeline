# n8n PostgreSQL Configuration Checklist

Use this checklist when setting up n8n with PostgreSQL from scratch.

## ✅ Pre-Installation Checklist

### System Requirements
- [ ] Docker installed (version 20.10+)
- [ ] Docker Compose installed (version 2.0+)
- [ ] Git installed
- [ ] Sufficient disk space (min 10GB free)
- [ ] Sufficient RAM (min 8GB total)

### Repository Setup
- [ ] Repository cloned: `git clone https://github.com/jonatanjacobsson/ifcpipeline.git`
- [ ] Changed to project directory: `cd ifcpipeline`
- [ ] Verified all configuration files present

---

## ✅ Configuration Files Checklist

### Environment File (.env)
- [ ] Created `.env` from `.env.example`: `cp .env.example .env`
- [ ] Set `POSTGRES_PASSWORD` (strong, unique password)
- [ ] Set `IFC_PIPELINE_API_KEY` (generate UUID or random string)
- [ ] Verified `DB_TYPE=postgresdb` is present
- [ ] Verified `N8N_POSTGRES_DB=n8n` is present
- [ ] Set `IFC_PIPELINE_EXTERNAL_URL` (your domain or http://localhost:8000)
- [ ] Set `N8N_WEBHOOK_URL` (your n8n webhook URL)

**Example `.env` must include:**
```bash
POSTGRES_PASSWORD=your-secure-password-here
IFC_PIPELINE_API_KEY=your-api-key-here
DB_TYPE=postgresdb
N8N_POSTGRES_DB=n8n
```

### Docker Compose File (docker-compose.yml)
- [ ] File contains n8n service with database environment variables
- [ ] n8n service has `DB_TYPE=${DB_TYPE:-postgresdb}`
- [ ] n8n service has `DB_POSTGRESDB_DATABASE=${N8N_POSTGRES_DB:-n8n}`
- [ ] n8n service has `DB_POSTGRESDB_HOST=postgres`
- [ ] n8n service has `DB_POSTGRESDB_PORT=5432`
- [ ] n8n service has `DB_POSTGRESDB_USER=${POSTGRES_USER:-ifcpipeline}`
- [ ] n8n service has `DB_POSTGRESDB_PASSWORD=${POSTGRES_PASSWORD}`
- [ ] n8n service has `depends_on: - postgres`

### PostgreSQL Init Scripts
- [ ] File `postgres/init/01-init.sql` exists (IFC Pipeline tables)
- [ ] File `postgres/init/02-n8n-init.sql` exists (n8n database)
- [ ] `02-n8n-init.sql` creates `n8n` database
- [ ] `02-n8n-init.sql` grants permissions to `ifcpipeline` user

---

## ✅ Installation Checklist

### First Start
- [ ] Run: `docker compose up --build -d`
- [ ] Wait for all services to start (1-2 minutes)
- [ ] No error messages in output

### Verify Services Started
```bash
docker compose ps
```
- [ ] postgres service is "Up" (healthy)
- [ ] n8n service is "Up" (healthy)
- [ ] api-gateway service is "Up"
- [ ] All worker services are "Up"

### Check Service Logs
```bash
# Check PostgreSQL
docker compose logs postgres | tail -20
```
- [ ] PostgreSQL started successfully
- [ ] No error messages
- [ ] Init scripts executed (look for "database system is ready")

```bash
# Check n8n
docker compose logs n8n | tail -20
```
- [ ] n8n started successfully
- [ ] No database connection errors
- [ ] Service ready on port 5678

---

## ✅ Verification Checklist

### Run Test Script
```bash
./test_n8n_postgres.sh
```
- [ ] ✓ n8n service is running
- [ ] ✓ PostgreSQL service is running
- [ ] ✓ DB_TYPE is set to postgresdb
- [ ] ✓ DB_POSTGRESDB_HOST is set to postgres
- [ ] ✓ DB_POSTGRESDB_DATABASE is set
- [ ] ✓ n8n can connect to PostgreSQL (port 5432)
- [ ] ✓ Database 'n8n' exists in PostgreSQL
- [ ] ✓ No SQLite database found
- [ ] ✓ n8n web interface accessible

### Manual Verification

**Check environment variables in n8n:**
```bash
docker compose exec n8n env | grep DB_
```
- [ ] `DB_TYPE=postgresdb` is shown
- [ ] `DB_POSTGRESDB_DATABASE=n8n` is shown
- [ ] `DB_POSTGRESDB_HOST=postgres` is shown
- [ ] `DB_POSTGRESDB_USER=ifcpipeline` is shown
- [ ] Other DB_POSTGRESDB_* variables are shown

**Check n8n database exists:**
```bash
docker compose exec postgres psql -U ifcpipeline -l
```
- [ ] `n8n` database is listed
- [ ] `ifcpipeline` database is listed

**Check for SQLite database (should NOT exist):**
```bash
docker compose exec n8n ls -la /home/node/.n8n/
```
- [ ] No `database.sqlite` file present
- [ ] `.n8n_encryption_key` file exists (this is normal)

---

## ✅ Initial Setup Checklist

### Access n8n Web Interface
- [ ] Open browser to http://localhost:5678
- [ ] n8n setup wizard appears
- [ ] No database connection errors

### Complete n8n Setup Wizard
- [ ] Create owner account (username, email, password)
- [ ] Password saved securely
- [ ] Email confirmed (if required)
- [ ] Optional questionnaire completed or skipped
- [ ] Reached n8n main interface

### Verify n8n Tables Created
```bash
docker compose exec postgres psql -U ifcpipeline -d n8n -c "\dt"
```
- [ ] Tables listed (should see 30+ tables)
- [ ] Tables include: `workflow_entity`
- [ ] Tables include: `credentials_entity`
- [ ] Tables include: `execution_entity`
- [ ] Tables include: `user`
- [ ] Tables include: `settings`

---

## ✅ Functional Testing Checklist

### Create Test Workflow
- [ ] Click "Add workflow" in n8n
- [ ] Add a simple node (e.g., "Schedule Trigger")
- [ ] Save workflow with name: "Test Workflow"
- [ ] Workflow saved successfully

### Verify Data Persistence
```bash
# Restart n8n
docker compose restart n8n

# Wait 10 seconds
sleep 10
```
- [ ] n8n restarted successfully
- [ ] Access n8n at http://localhost:5678
- [ ] "Test Workflow" still exists
- [ ] Can open and edit workflow

### Check Workflow in Database
```bash
docker compose exec postgres psql -U ifcpipeline -d n8n -c "SELECT id, name, active FROM workflow_entity;"
```
- [ ] "Test Workflow" is listed
- [ ] Data persisted in PostgreSQL

---

## ✅ Access Points Checklist

Verify all services are accessible:

### n8n
- [ ] http://localhost:5678 - n8n interface loads
- [ ] Can login with created account
- [ ] Dashboard accessible

### IFC Pipeline API
- [ ] http://localhost:8000 - API Gateway responds
- [ ] http://localhost:8000/docs - Swagger docs load

### IFC Viewer
- [ ] http://localhost:8001 - Viewer loads

### Database Admin (PgWeb)
- [ ] http://localhost:8081 - PgWeb loads
- [ ] Can connect to `n8n` database
- [ ] Can see n8n tables

### Job Queue Dashboard
- [ ] http://localhost:9181 - RQ Dashboard loads
- [ ] Shows worker queues

---

## ✅ Security Checklist

### Environment Variables
- [ ] `.env` file is NOT committed to git
- [ ] `.env` has strong password (min 16 chars)
- [ ] API key is random/secure (UUID format recommended)
- [ ] `.env.example` does NOT contain real credentials

### File Permissions
- [ ] `.env` file permissions: `chmod 600 .env` (optional but recommended)
- [ ] No sensitive data in docker-compose.yml
- [ ] All passwords use environment variables

### Network Security
- [ ] PostgreSQL port 5432 exposed only on localhost (or internal network)
- [ ] n8n port 5678 exposed appropriately for your use case
- [ ] Firewall configured if needed

---

## ✅ Backup Setup Checklist

### Database Backup
- [ ] Understand backup command:
  ```bash
  docker compose exec postgres pg_dump -U ifcpipeline -d n8n -F c -f /tmp/backup.dump
  ```
- [ ] Test backup creation
- [ ] Backup stored securely
- [ ] Backup includes both `n8n` and `ifcpipeline` databases

### Volume Backup
- [ ] Understand n8n-data backup:
  ```bash
  tar -czf n8n-data_backup.tar.gz ./n8n-data/
  ```
- [ ] Test backup creation
- [ ] `.n8n_encryption_key` included in backup (CRITICAL!)

### Automated Backup (Optional)
- [ ] Cron job created for automated backups
- [ ] Backup schedule documented
- [ ] Backup retention policy defined
- [ ] Backup restoration tested

---

## ✅ Documentation Checklist

### Read Documentation
- [ ] Read `N8N_POSTGRES_QUICKSTART.md`
- [ ] Read `N8N_POSTGRES_CONFIGURATION_ANALYSIS.md`
- [ ] Read `N8N_POSTGRES_IMPLEMENTATION_SUMMARY.md`
- [ ] Understand backup procedures in `postgres/README.md`

### Team Documentation
- [ ] Document credentials location
- [ ] Document access URLs
- [ ] Document backup procedures
- [ ] Document recovery procedures

---

## ✅ Integration Checklist

### n8n Community Nodes
- [ ] Open n8n Settings
- [ ] Go to "Community Nodes"
- [ ] Search for `n8n-nodes-ifcpipeline`
- [ ] Install package
- [ ] Restart n8n if required
- [ ] Verify IFC Pipeline nodes available

### IFC Pipeline Credentials
- [ ] In n8n, go to Credentials
- [ ] Add new credential: "IFC Pipeline API"
- [ ] Enter API URL: http://api-gateway or your external URL
- [ ] Enter API Key from `.env` file
- [ ] Test connection
- [ ] Save credential

### Test IFC Pipeline Integration
- [ ] Create workflow with IFC Pipeline node
- [ ] Upload test file
- [ ] Execute workflow
- [ ] Verify results

---

## ✅ Monitoring Setup Checklist

### Health Checks
- [ ] Understand health endpoint: http://localhost:8000/health
- [ ] Test health endpoint returns success
- [ ] Set up monitoring if required

### Log Monitoring
- [ ] Understand log commands:
  ```bash
  docker compose logs n8n -f
  docker compose logs postgres -f
  ```
- [ ] Know where to check logs
- [ ] Set up log aggregation if required

### Database Monitoring
- [ ] Access PgWeb: http://localhost:8081
- [ ] Can view `n8n` database size
- [ ] Can view table row counts
- [ ] Can view active connections

### Performance Monitoring
- [ ] Check resource usage:
  ```bash
  docker stats
  ```
- [ ] Verify n8n within resource limits
- [ ] Verify PostgreSQL within resource limits

---

## ✅ Troubleshooting Preparation Checklist

### Know Common Issues
- [ ] Read troubleshooting section in `N8N_POSTGRES_QUICKSTART.md`
- [ ] Know how to check logs
- [ ] Know how to restart services
- [ ] Have test script available: `./test_n8n_postgres.sh`

### Recovery Procedures
- [ ] Understand how to restore from backup
- [ ] Know how to reset n8n database
- [ ] Know how to check PostgreSQL connection
- [ ] Have support resources documented

---

## ✅ Production Readiness Checklist

### Performance
- [ ] Resource limits appropriate in docker-compose.yml
- [ ] Connection pooling configured if needed
- [ ] Execution history cleanup policy defined

### Security
- [ ] SSL/TLS configured if needed
- [ ] Firewall rules in place
- [ ] Access control configured
- [ ] Secrets management in place

### High Availability (Optional)
- [ ] Database replication configured if needed
- [ ] Backup/restore tested
- [ ] Disaster recovery plan documented
- [ ] Monitoring alerts configured

### Compliance (If Applicable)
- [ ] Data retention policy implemented
- [ ] Audit logging enabled
- [ ] Access logs reviewed
- [ ] Compliance requirements met

---

## ✅ Go-Live Checklist

### Final Verification
- [ ] All above checklists completed
- [ ] Test script passes: `./test_n8n_postgres.sh`
- [ ] Can create and execute workflows
- [ ] Data persists after restart
- [ ] Backups tested and working
- [ ] Team trained on system

### Production Deployment
- [ ] External URLs configured correctly
- [ ] DNS records set up
- [ ] SSL certificates installed if needed
- [ ] Firewall rules applied
- [ ] Monitoring active
- [ ] Backup automation running
- [ ] Documentation complete

### Post-Deployment
- [ ] System monitored for 24 hours
- [ ] No errors in logs
- [ ] Performance acceptable
- [ ] Users trained
- [ ] Support procedures documented

---

## ✅ Maintenance Checklist (Ongoing)

### Daily
- [ ] Check service status: `docker compose ps`
- [ ] Review error logs if any
- [ ] Monitor disk space

### Weekly
- [ ] Verify backups created successfully
- [ ] Review database size growth
- [ ] Check for failed workflows in n8n
- [ ] Review resource usage: `docker stats`

### Monthly
- [ ] Test backup restoration
- [ ] Review and clean old executions
- [ ] Update security patches
- [ ] Review performance metrics

### Quarterly
- [ ] Review and update documentation
- [ ] Review security settings
- [ ] Plan for scaling if needed
- [ ] Team training refresh

---

## Quick Reference Commands

### Start Services
```bash
docker compose up --build -d
```

### Stop Services
```bash
docker compose down
```

### View Logs
```bash
docker compose logs n8n -f
docker compose logs postgres -f
```

### Test Configuration
```bash
./test_n8n_postgres.sh
```

### Backup n8n Database
```bash
docker compose exec postgres pg_dump -U ifcpipeline -d n8n -F c -f /tmp/backup.dump
docker compose cp postgres:/tmp/backup.dump ./n8n_backup_$(date +%Y%m%d).dump
```

### Access PostgreSQL
```bash
docker compose exec postgres psql -U ifcpipeline -d n8n
```

### Check Service Status
```bash
docker compose ps
docker stats
```

---

## Completion Sign-Off

**Installation Date:** _________________

**Installed By:** _________________

**Verified By:** _________________

**Notes:**
_________________________________________________
_________________________________________________
_________________________________________________

---

## Support Resources

- **Documentation Directory:** `/workspace/`
  - `N8N_POSTGRES_QUICKSTART.md`
  - `N8N_POSTGRES_CONFIGURATION_ANALYSIS.md`
  - `N8N_POSTGRES_IMPLEMENTATION_SUMMARY.md`
  - `N8N_POSTGRES_ARCHITECTURE.md`
  - `N8N_POSTGRES_COMPARISON.md`

- **Test Script:** `./test_n8n_postgres.sh`

- **n8n Documentation:** https://docs.n8n.io/

- **PostgreSQL Documentation:** https://www.postgresql.org/docs/14/

- **IFC Pipeline GitHub:** https://github.com/jonatanjacobsson/ifcpipeline

---

**When all items are checked, your n8n PostgreSQL configuration is complete and production-ready!** ✅
