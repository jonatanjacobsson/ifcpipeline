# n8n Database Configuration: SQLite vs PostgreSQL Comparison

## Quick Comparison Table

| Feature | SQLite (Default) | PostgreSQL (Implemented) |
|---------|------------------|--------------------------|
| **Database Type** | File-based | Client-server |
| **Data Location** | `/home/node/.n8n/database.sqlite` | PostgreSQL server, `n8n` database |
| **Setup Complexity** | Zero config needed | Requires environment variables |
| **Production Ready** | ⚠️ Not recommended | ✅ Recommended |
| **Concurrent Access** | Limited | Excellent |
| **Scalability** | Limited | Excellent |
| **Backup Method** | Copy SQLite file | pg_dump / pg_restore |
| **Data Integrity** | Good | Excellent |
| **Performance** | Good for small scale | Better for production |
| **Transaction Support** | Basic | Advanced |
| **Query Optimization** | Limited | Advanced |
| **Monitoring** | Difficult | Easy (PgWeb, psql) |
| **Recovery Options** | Limited | Advanced |
| **Multi-instance** | ❌ Not possible | ✅ Possible |
| **Replication** | ❌ Not supported | ✅ Supported |
| **Connection Pooling** | ❌ N/A | ✅ Yes |

## Detailed Comparison

### 1. Installation and Setup

#### SQLite (Default)
```yaml
# docker-compose.yml
n8n:
  image: docker.n8n.io/n8nio/n8n
  volumes:
    - ./n8n-data:/home/node/.n8n
  # No database configuration needed
```

**Result:** SQLite file created automatically at `/home/node/.n8n/database.sqlite`

#### PostgreSQL (Implemented)
```yaml
# docker-compose.yml
n8n:
  image: docker.n8n.io/n8nio/n8n
  environment:
    - DB_TYPE=postgresdb
    - DB_POSTGRESDB_DATABASE=n8n
    - DB_POSTGRESDB_HOST=postgres
    - DB_POSTGRESDB_PORT=5432
    - DB_POSTGRESDB_USER=ifcpipeline
    - DB_POSTGRESDB_PASSWORD=${POSTGRES_PASSWORD}
  depends_on:
    - postgres
```

**Result:** n8n connects to PostgreSQL server, uses dedicated `n8n` database

---

### 2. Data Storage

#### SQLite
```
./n8n-data/
├── database.sqlite       ← All n8n data in one file
├── .n8n_encryption_key
└── config/
```

- Single file contains all data
- File can grow to several GB
- Must backup entire file

#### PostgreSQL
```
PostgreSQL Server
├── Database: n8n
│   ├── workflow_entity (table)
│   ├── credentials_entity (table)
│   ├── execution_entity (table)
│   └── 30+ more tables

./n8n-data/
├── .n8n_encryption_key   ← Still needed for credential encryption
└── config/
```

- Data distributed across tables
- Efficient indexing
- Selective backup possible

---

### 3. Performance Comparison

#### Workflow Execution Performance

| Metric | SQLite | PostgreSQL |
|--------|--------|------------|
| Single workflow execution | Fast | Fast |
| Concurrent workflows (5) | Good | Excellent |
| Concurrent workflows (20+) | Degraded | Excellent |
| Large execution history | Slow queries | Fast with indexes |
| Workflow list with 1000+ | Slow | Fast |

#### Database Size Impact

| Database Size | SQLite Performance | PostgreSQL Performance |
|---------------|-------------------|----------------------|
| < 100 MB | Excellent | Excellent |
| 100-500 MB | Good | Excellent |
| 500 MB - 1 GB | Degraded | Excellent |
| > 1 GB | Poor | Excellent |

---

### 4. Backup and Recovery

#### SQLite Backup

**Backup Process:**
```bash
# Must stop n8n first to ensure consistency
docker compose stop n8n

# Copy SQLite file
cp ./n8n-data/database.sqlite ./backup/database_backup.sqlite

# Restart n8n
docker compose start n8n
```

**Limitations:**
- Requires n8n downtime
- All-or-nothing backup
- No point-in-time recovery
- File locking issues if not stopped

#### PostgreSQL Backup

**Backup Process:**
```bash
# No downtime needed
docker compose exec postgres pg_dump -U ifcpipeline -d n8n -F c -f /tmp/backup.dump

# Can backup while n8n is running
# Transactionally consistent
# Can restore to specific point in time
```

**Advantages:**
- ✅ No downtime required
- ✅ Transactionally consistent
- ✅ Point-in-time recovery possible
- ✅ Incremental backups possible
- ✅ Selective table backup/restore

---

### 5. Monitoring and Management

#### SQLite

**Monitoring:**
```bash
# Check file size
ls -lh ./n8n-data/database.sqlite

# Query database (requires stopping n8n)
docker compose stop n8n
sqlite3 ./n8n-data/database.sqlite "SELECT COUNT(*) FROM workflow_entity;"
docker compose start n8n
```

**Limitations:**
- No web interface
- Must stop n8n to query safely
- Limited query tools
- No built-in monitoring

#### PostgreSQL

**Monitoring:**
```bash
# Web interface (PgWeb)
# Visit: http://localhost:8081

# Command line (while n8n is running)
docker compose exec postgres psql -U ifcpipeline -d n8n

# Check database size
SELECT pg_size_pretty(pg_database_size('n8n'));

# Check table sizes
SELECT 
  tablename,
  pg_size_pretty(pg_total_relation_size(tablename::text)) as size
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(tablename::text) DESC;

# Check active connections
SELECT count(*) FROM pg_stat_activity WHERE datname = 'n8n';
```

**Advantages:**
- ✅ Web interface available
- ✅ Real-time monitoring
- ✅ Query without stopping n8n
- ✅ Built-in performance stats
- ✅ Connection tracking

---

### 6. Scalability Scenarios

#### Scenario 1: Growing Execution History

**SQLite:**
```
Workflows: 100
Executions: 10,000
Database Size: 500 MB
Query Time: 2-5 seconds (degrading)
```

**PostgreSQL:**
```
Workflows: 100
Executions: 10,000
Database Size: 500 MB
Query Time: <100 ms (consistent)
```

#### Scenario 2: Many Concurrent Workflows

**SQLite:**
```
Concurrent Executions: 10
Performance: Good initially, degrades over time
File Locking: Can become bottleneck
```

**PostgreSQL:**
```
Concurrent Executions: 50+
Performance: Consistent
Connection Pooling: Handles high concurrency
```

#### Scenario 3: Multiple n8n Instances

**SQLite:**
```
Multiple Instances: ❌ NOT POSSIBLE
Reason: File locking conflicts
Solution: Only one n8n per SQLite file
```

**PostgreSQL:**
```
Multiple Instances: ✅ SUPPORTED
Setup: Multiple n8n containers → Same PostgreSQL
Load Balancing: Possible
High Availability: Possible
```

---

### 7. Data Integrity

#### SQLite

**Pros:**
- ✅ ACID compliant
- ✅ Reliable for single user
- ✅ No network issues

**Cons:**
- ⚠️ File corruption possible if container crashes
- ⚠️ No automatic repair
- ⚠️ Limited integrity checks

#### PostgreSQL

**Pros:**
- ✅ ACID compliant
- ✅ Write-ahead logging (WAL)
- ✅ Automatic crash recovery
- ✅ Checksums for data corruption detection
- ✅ Foreign key constraints
- ✅ Advanced constraint checking

**Cons:**
- ⚠️ Network dependency (mitigated by Docker network)

---

### 8. Migration Path

#### From SQLite to PostgreSQL

If you already have n8n running with SQLite:

**Option 1: Export/Import Workflows**
```bash
# 1. Export workflows from SQLite n8n (via UI)
# 2. Stop n8n
docker compose stop n8n

# 3. Remove SQLite database
rm ./n8n-data/database.sqlite

# 4. Apply PostgreSQL configuration
# (environment variables in docker-compose.yml)

# 5. Start n8n with PostgreSQL
docker compose start n8n

# 6. Import workflows (via UI)
```

**Option 2: Database Migration (Advanced)**
```bash
# Use n8n export/import API
# Export all workflows to JSON
# Configure PostgreSQL
# Import all workflows from JSON

# Note: Execution history is lost
# Only workflows and credentials can be migrated
```

#### From PostgreSQL to SQLite (Not Recommended)

Only for testing/development:
```bash
# 1. Stop n8n
# 2. Remove DB_TYPE and DB_POSTGRESDB_* environment variables
# 3. Start n8n (will create new SQLite database)
# 4. Manually recreate workflows
```

---

### 9. Resource Usage

#### SQLite

**CPU:**
- Low overhead
- Single-threaded for writes

**Memory:**
- Database cache in n8n process
- No separate database process

**Disk I/O:**
- All I/O through n8n process
- File system dependent

**Total Resources:**
```
n8n container: 4 CPU, 6GB RAM
Total: 4 CPU, 6GB RAM
```

#### PostgreSQL

**CPU:**
- Separate process
- Multi-threaded queries
- Query optimization

**Memory:**
- Dedicated database cache
- Connection pooling
- Separate from n8n process

**Disk I/O:**
- Optimized I/O
- Write-ahead logging
- Better caching

**Total Resources:**
```
n8n container: 4 CPU, 6GB RAM
PostgreSQL container: 0.5 CPU, 512MB RAM
Total: 4.5 CPU, 6.5GB RAM
```

**Note:** Slightly higher resource usage, but better performance and scalability.

---

### 10. Use Case Recommendations

#### Use SQLite When:

1. ✅ **Testing/Development**
   - Quick setup
   - No production use
   - Short-lived environments

2. ✅ **Very Small Scale**
   - < 10 workflows
   - Minimal executions
   - Single user

3. ✅ **Standalone Demo**
   - No external dependencies
   - Portable setup
   - No production requirements

#### Use PostgreSQL When:

1. ✅ **Production Environment** ⭐
   - Business-critical workflows
   - Requires reliability
   - Need monitoring

2. ✅ **Growing Usage** ⭐
   - 10+ workflows
   - Regular execution schedule
   - Multiple users

3. ✅ **High Availability** ⭐
   - Need backup/recovery
   - Require uptime guarantees
   - Business continuity

4. ✅ **Integration with Other Services** ⭐
   - Already have PostgreSQL (like IFC Pipeline)
   - Centralized database management
   - Unified backup strategy

5. ✅ **Scalability Requirements** ⭐
   - Plan to grow
   - May need multiple instances
   - Performance is critical

6. ✅ **Compliance/Audit** ⭐
   - Need transaction logs
   - Require audit trail
   - Data retention policies

---

### 11. Real-World Scenarios

#### Scenario A: Small Team (2-5 Users)

**With SQLite:**
```
Month 1: Working fine
Month 3: Slow queries appearing
Month 6: Database file 800MB, noticeable lag
Month 12: Migration to PostgreSQL needed
```

**With PostgreSQL:**
```
Month 1: Working fine
Month 3: Working fine
Month 6: Working fine, 800MB database, no lag
Month 12: Still working fine, easy to scale
```

#### Scenario B: IFC Pipeline Integration (Your Use Case)

**With SQLite:**
```
- Separate database technology from IFC Pipeline
- Different backup procedures
- Different monitoring tools
- Two database systems to maintain
```

**With PostgreSQL:**
```
✅ Same PostgreSQL instance for both
✅ Unified backup strategy
✅ Single monitoring interface (PgWeb)
✅ Consistent DBA practices
✅ Better resource utilization
```

#### Scenario C: Automated Workflow Execution

**With SQLite:**
```
10 workflows × 24 executions/day = 240 executions/day
After 30 days: 7,200 executions
Database size: ~500MB
Query performance: Degrading
```

**With PostgreSQL:**
```
10 workflows × 24 executions/day = 240 executions/day
After 30 days: 7,200 executions
Database size: ~500MB
Query performance: Consistent
Can handle 10x more without issues
```

---

## Migration Checklist: SQLite → PostgreSQL

### Pre-Migration

- [ ] Current n8n version noted
- [ ] All workflows exported via UI/API
- [ ] Credentials documented
- [ ] Execution history not needed (cannot be migrated)
- [ ] Downtime window planned
- [ ] PostgreSQL configured and tested
- [ ] Backup of current SQLite database

### Migration Steps

- [ ] Stop n8n: `docker compose stop n8n`
- [ ] Backup SQLite: `cp ./n8n-data/database.sqlite ./backup/`
- [ ] Apply PostgreSQL configuration to docker-compose.yml
- [ ] Add environment variables to .env
- [ ] Ensure postgres/init/02-n8n-init.sql exists
- [ ] Start PostgreSQL: `docker compose up -d postgres`
- [ ] Verify database created: `docker compose exec postgres psql -U ifcpipeline -l`
- [ ] Remove SQLite: `rm ./n8n-data/database.sqlite`
- [ ] Start n8n: `docker compose up -d n8n`
- [ ] Verify PostgreSQL connection: `./test_n8n_postgres.sh`
- [ ] Access n8n UI: http://localhost:5678
- [ ] Complete setup wizard
- [ ] Import workflows
- [ ] Recreate credentials
- [ ] Test workflow executions

### Post-Migration

- [ ] All workflows imported
- [ ] All credentials configured
- [ ] Test workflow executions successful
- [ ] Verify data persists after restart
- [ ] Set up backup schedule
- [ ] Monitor performance
- [ ] Update documentation
- [ ] Archive old SQLite backup

---

## Cost-Benefit Analysis

### SQLite

**Costs:**
- 👎 Limited scalability
- 👎 Poor concurrent performance at scale
- 👎 Difficult to monitor
- 👎 Backup requires downtime
- 👎 No multi-instance support
- 👎 Migration pain later

**Benefits:**
- 👍 Zero configuration
- 👍 No additional resources
- 👍 Simple backup (copy file)
- 👍 Good for development

**Total Cost of Ownership:**
```
Short-term: Low
Long-term: High (migration cost + limited features)
```

### PostgreSQL

**Costs:**
- 👎 Initial configuration effort (1-2 hours)
- 👎 Slightly more resources (512MB RAM)
- 👎 Requires understanding of PostgreSQL basics

**Benefits:**
- 👍 Production-ready from day 1
- 👍 Excellent scalability
- 👍 Easy monitoring
- 👍 No-downtime backups
- 👍 Multi-instance capable
- 👍 Better performance at scale
- 👍 Integration with existing PostgreSQL

**Total Cost of Ownership:**
```
Short-term: Medium (setup time)
Long-term: Low (no migration needed, scales naturally)
```

---

## Final Recommendation

### For IFC Pipeline Project: **Use PostgreSQL** ⭐

**Reasons:**

1. ✅ **Already have PostgreSQL** - No additional infrastructure
2. ✅ **Production system** - IFC Pipeline is production-grade
3. ✅ **Integration benefits** - Unified database management
4. ✅ **Scalability** - Room to grow
5. ✅ **Consistency** - Same database technology throughout
6. ✅ **Monitoring** - PgWeb already available
7. ✅ **Backup** - Unified backup strategy
8. ✅ **Professional** - Production best practices

**Implementation Effort:** Low (1-2 hours with provided configuration)

**Long-term Benefits:** High (avoids future migration, better performance, scalable)

---

## Summary

| Aspect | SQLite | PostgreSQL | Winner |
|--------|--------|------------|--------|
| Setup Ease | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | SQLite |
| Production Ready | ⭐⭐ | ⭐⭐⭐⭐⭐ | PostgreSQL |
| Performance (Scale) | ⭐⭐ | ⭐⭐⭐⭐⭐ | PostgreSQL |
| Scalability | ⭐ | ⭐⭐⭐⭐⭐ | PostgreSQL |
| Monitoring | ⭐ | ⭐⭐⭐⭐⭐ | PostgreSQL |
| Backup | ⭐⭐ | ⭐⭐⭐⭐⭐ | PostgreSQL |
| Concurrent Access | ⭐⭐ | ⭐⭐⭐⭐⭐ | PostgreSQL |
| Resource Usage | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | SQLite |
| IFC Pipeline Fit | ⭐⭐ | ⭐⭐⭐⭐⭐ | PostgreSQL |

**Overall Winner for Production Use: PostgreSQL** 🏆

---

**With the provided configuration, you get PostgreSQL benefits with minimal setup effort!**
