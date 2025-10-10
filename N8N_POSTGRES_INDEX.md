# n8n PostgreSQL Configuration - Documentation Index

## 📖 Overview

This directory contains comprehensive documentation for configuring n8n to use PostgreSQL instead of SQLite from the very first startup in the IFC Pipeline project.

## 🎯 Quick Start

**New to this configuration?** Start here:

1. **Read:** [N8N_POSTGRES_QUICKSTART.md](N8N_POSTGRES_QUICKSTART.md) (10 minutes)
2. **Follow:** Step-by-step setup instructions
3. **Run:** `./test_n8n_postgres.sh` to verify
4. **Done:** Access n8n at http://localhost:5678

## 📚 Documentation Guide

### For Different Audiences

#### 🚀 **I want to get started quickly**
→ [N8N_POSTGRES_QUICKSTART.md](N8N_POSTGRES_QUICKSTART.md)
- Step-by-step setup guide
- Copy-paste commands
- Quick troubleshooting

#### 🔍 **I want to understand the technical details**
→ [N8N_POSTGRES_CONFIGURATION_ANALYSIS.md](N8N_POSTGRES_CONFIGURATION_ANALYSIS.md)
- Complete technical analysis
- Decision points and trade-offs
- Advanced configuration options
- Architecture details

#### 📊 **I want a summary of what was implemented**
→ [N8N_POSTGRES_IMPLEMENTATION_SUMMARY.md](N8N_POSTGRES_IMPLEMENTATION_SUMMARY.md)
- Executive summary
- Files changed/created
- Success criteria
- Testing procedures

#### 🏗️ **I want to see the architecture**
→ [N8N_POSTGRES_ARCHITECTURE.md](N8N_POSTGRES_ARCHITECTURE.md)
- System diagrams
- Data flow diagrams
- Network architecture
- Integration details

#### 🆚 **I want to compare SQLite vs PostgreSQL**
→ [N8N_POSTGRES_COMPARISON.md](N8N_POSTGRES_COMPARISON.md)
- Detailed comparison tables
- Performance comparisons
- Use case recommendations
- Migration guide

#### ✅ **I want a checklist to follow**
→ [N8N_POSTGRES_CHECKLIST.md](N8N_POSTGRES_CHECKLIST.md)
- Pre-installation checklist
- Installation steps
- Verification steps
- Production readiness checklist

---

## 📄 Document Details

### [N8N_POSTGRES_QUICKSTART.md](N8N_POSTGRES_QUICKSTART.md)
**Purpose:** Get n8n with PostgreSQL running quickly  
**Length:** ~10 pages  
**Reading Time:** 10-15 minutes  
**Best For:** First-time setup, quick reference

**Contents:**
- Prerequisites
- Step-by-step setup (8 steps)
- Verification procedures
- Access points
- Troubleshooting
- Database management commands

---

### [N8N_POSTGRES_CONFIGURATION_ANALYSIS.md](N8N_POSTGRES_CONFIGURATION_ANALYSIS.md)
**Purpose:** Comprehensive technical analysis  
**Length:** ~25 pages  
**Reading Time:** 30-45 minutes  
**Best For:** Understanding implementation details, advanced users

**Contents:**
- Current state analysis
- Required changes detailed
- Implementation decision points
- Complete implementation checklist
- Common issues and solutions
- Advanced configuration
- Backup strategies
- Performance tuning

---

### [N8N_POSTGRES_IMPLEMENTATION_SUMMARY.md](N8N_POSTGRES_IMPLEMENTATION_SUMMARY.md)
**Purpose:** Executive summary of implementation  
**Length:** ~15 pages  
**Reading Time:** 15-20 minutes  
**Best For:** Understanding what was done, quick reference

**Contents:**
- Files modified and created
- What this achieves
- Environment variables explained
- Database structure
- Implementation flow
- Key benefits
- Troubleshooting quick reference
- Testing checklist

---

### [N8N_POSTGRES_ARCHITECTURE.md](N8N_POSTGRES_ARCHITECTURE.md)
**Purpose:** Visual and technical architecture documentation  
**Length:** ~20 pages  
**Reading Time:** 20-30 minutes  
**Best For:** Understanding system design, architectural decisions

**Contents:**
- System architecture diagrams
- Environment variables flow
- Database initialization flow
- Data flow diagrams
- Integration with IFC Pipeline
- Network communication
- File system layout
- Security architecture
- Scalability architecture
- Backup architecture

---

### [N8N_POSTGRES_COMPARISON.md](N8N_POSTGRES_COMPARISON.md)
**Purpose:** Detailed comparison of SQLite vs PostgreSQL  
**Length:** ~20 pages  
**Reading Time:** 25-35 minutes  
**Best For:** Decision making, understanding trade-offs

**Contents:**
- Quick comparison table
- Detailed feature comparison
- Performance comparisons
- Scalability scenarios
- Use case recommendations
- Migration paths
- Real-world scenarios
- Cost-benefit analysis
- Final recommendations

---

### [N8N_POSTGRES_CHECKLIST.md](N8N_POSTGRES_CHECKLIST.md)
**Purpose:** Comprehensive checklist for setup and maintenance  
**Length:** ~15 pages  
**Reading Time:** Use as reference  
**Best For:** Following setup process, ensuring nothing is missed

**Contents:**
- Pre-installation checklist
- Configuration files checklist
- Installation checklist
- Verification checklist
- Initial setup checklist
- Functional testing checklist
- Access points checklist
- Security checklist
- Backup setup checklist
- Documentation checklist
- Integration checklist
- Monitoring setup checklist
- Production readiness checklist
- Maintenance checklist (ongoing)

---

## 🛠️ Implementation Files

### Configuration Files Created

1. **`postgres/init/02-n8n-init.sql`**
   - PostgreSQL initialization script
   - Creates n8n database
   - Sets up permissions
   - Runs automatically on first PostgreSQL start

2. **`test_n8n_postgres.sh`**
   - Comprehensive test script
   - Verifies configuration
   - Checks connectivity
   - Validates setup
   - Executable: `chmod +x test_n8n_postgres.sh`

### Configuration Files Modified

1. **`docker-compose.yml`**
   - Added n8n database environment variables
   - 7 new environment variables in n8n service

2. **`.env.example`**
   - Added n8n database configuration section
   - `DB_TYPE=postgresdb`
   - `N8N_POSTGRES_DB=n8n`

3. **`postgres/README.md`**
   - Updated to document n8n database
   - Added n8n-specific commands
   - Added troubleshooting section

---

## 🎓 Learning Path

### Beginner Path
1. Read: Quick Start Guide
2. Follow: Setup instructions
3. Run: Test script
4. Read: Comparison document (understand why PostgreSQL)

### Intermediate Path
1. Read: Implementation Summary
2. Read: Configuration Analysis
3. Review: Architecture diagrams
4. Use: Checklist for setup

### Advanced Path
1. Read: Configuration Analysis (full)
2. Read: Architecture document (full)
3. Review: docker-compose.yml changes
4. Review: PostgreSQL init scripts
5. Customize: Connection pooling, performance tuning

---

## 🔑 Key Concepts

### What This Implementation Does
✅ Configures n8n to use PostgreSQL from first startup  
✅ No SQLite database is created  
✅ All n8n data stored in PostgreSQL  
✅ Integrates with existing PostgreSQL infrastructure  
✅ Production-ready configuration  

### What You Need to Do
1. Create `.env` file with database credentials
2. Ensure configuration files are in place (already done)
3. Run `docker compose up --build -d`
4. Verify with `./test_n8n_postgres.sh`
5. Access n8n and complete setup

### Environment Variables Required
```bash
POSTGRES_PASSWORD=your-secure-password
DB_TYPE=postgresdb
N8N_POSTGRES_DB=n8n
```

---

## 🔧 Quick Commands Reference

### Setup Commands
```bash
# Clone repository
git clone https://github.com/jonatanjacobsson/ifcpipeline.git
cd ifcpipeline

# Create environment file
cp .env.example .env
# Edit .env and set POSTGRES_PASSWORD

# Start all services
docker compose up --build -d

# Test configuration
./test_n8n_postgres.sh
```

### Management Commands
```bash
# View logs
docker compose logs n8n -f
docker compose logs postgres -f

# Restart n8n
docker compose restart n8n

# Access PostgreSQL
docker compose exec postgres psql -U ifcpipeline -d n8n

# Backup n8n database
docker compose exec postgres pg_dump -U ifcpipeline -d n8n -F c -f /tmp/backup.dump

# Check service status
docker compose ps
docker stats
```

### Verification Commands
```bash
# Check environment variables
docker compose exec n8n env | grep DB_

# Check database exists
docker compose exec postgres psql -U ifcpipeline -l | grep n8n

# Check n8n tables
docker compose exec postgres psql -U ifcpipeline -d n8n -c "\dt"

# Verify no SQLite
docker compose exec n8n ls /home/node/.n8n/ | grep -q database.sqlite && echo "ERROR: SQLite found" || echo "OK: Using PostgreSQL"
```

---

## 🎯 Success Criteria

Your setup is successful when:

✅ Test script passes all checks  
✅ n8n accessible at http://localhost:5678  
✅ No `database.sqlite` file exists  
✅ PostgreSQL `n8n` database contains 30+ tables  
✅ Workflows persist after `docker compose restart n8n`  
✅ No database errors in n8n logs  

---

## 🐛 Troubleshooting Guide

### Problem: SQLite database still created
**Solution:** Check environment variables in docker-compose.yml  
**See:** [N8N_POSTGRES_QUICKSTART.md](N8N_POSTGRES_QUICKSTART.md#troubleshooting)

### Problem: Can't connect to PostgreSQL
**Solution:** Ensure PostgreSQL is running first  
**See:** [N8N_POSTGRES_CONFIGURATION_ANALYSIS.md](N8N_POSTGRES_CONFIGURATION_ANALYSIS.md#common-issues-and-solutions)

### Problem: Permission errors
**Solution:** Check database permissions  
**See:** [N8N_POSTGRES_CONFIGURATION_ANALYSIS.md](N8N_POSTGRES_CONFIGURATION_ANALYSIS.md#issue-2-permission-denied-errors)

### All Other Issues
**See:** Troubleshooting sections in:
- [N8N_POSTGRES_QUICKSTART.md](N8N_POSTGRES_QUICKSTART.md#troubleshooting)
- [N8N_POSTGRES_CONFIGURATION_ANALYSIS.md](N8N_POSTGRES_CONFIGURATION_ANALYSIS.md#common-issues-and-solutions)
- [N8N_POSTGRES_COMPARISON.md](N8N_POSTGRES_COMPARISON.md#troubleshooting-quick-reference)

---

## 📊 Database Structure

```
PostgreSQL Server (postgres:14)
│
├── Database: ifcpipeline (IFC Pipeline workers)
│   ├── clash_results
│   ├── conversion_results
│   ├── tester_results
│   └── diff_results
│
└── Database: n8n (n8n workflows)
    ├── workflow_entity
    ├── credentials_entity
    ├── execution_entity
    ├── tag_entity
    ├── webhook_entity
    └── 25+ more tables
```

---

## 🔒 Security Notes

- PostgreSQL uses password authentication
- n8n credentials are encrypted in database
- Encryption key stored in `/home/node/.n8n/` volume
- Database isolated on Docker internal network
- SSL disabled for internal network (secure by isolation)

**For Production:**
- Use strong passwords
- Enable PostgreSQL SSL/TLS if needed
- Restrict network access
- Regular security updates

---

## 💾 Backup Strategy

### What to Backup
1. **PostgreSQL n8n database** (workflows, credentials, executions)
2. **n8n data directory** (encryption key - CRITICAL!)

### How to Backup
```bash
# Backup database
docker compose exec postgres pg_dump -U ifcpipeline -d n8n -F c -f /tmp/backup.dump
docker compose cp postgres:/tmp/backup.dump ./n8n_backup_$(date +%Y%m%d).dump

# Backup data directory
tar -czf n8n-data_$(date +%Y%m%d).tar.gz ./n8n-data/
```

**See:** [postgres/README.md](postgres/README.md) for detailed backup procedures

---

## 🚀 Next Steps After Setup

1. **Install n8n Community Nodes**
   - Open n8n > Settings > Community Nodes
   - Install: `n8n-nodes-ifcpipeline`

2. **Configure IFC Pipeline Credentials**
   - Add IFC Pipeline API credential
   - Use API key from `.env` file

3. **Create First Workflow**
   - Use IFC Pipeline nodes
   - Test with example files

4. **Set Up Backups**
   - Schedule automated backups
   - Test restore procedure

5. **Monitor System**
   - Check PgWeb: http://localhost:8081
   - Check RQ Dashboard: http://localhost:9181

---

## 📞 Support Resources

### Documentation
- All `.md` files in `/workspace/`
- Test script: `./test_n8n_postgres.sh`
- Postgres README: [postgres/README.md](postgres/README.md)

### External Resources
- [n8n Official Docs](https://docs.n8n.io/)
- [PostgreSQL Docs](https://www.postgresql.org/docs/14/)
- [IFC Pipeline GitHub](https://github.com/jonatanjacobsson/ifcpipeline)

### Getting Help
1. Run test script: `./test_n8n_postgres.sh`
2. Check logs: `docker compose logs n8n -f`
3. Review troubleshooting sections
4. Open GitHub issue if needed

---

## 📈 Implementation Status

**Status:** ✅ Complete and Ready

**What's Included:**
- ✅ All configuration files created/modified
- ✅ PostgreSQL initialization script
- ✅ Test and verification script
- ✅ Comprehensive documentation (6 documents)
- ✅ Architecture diagrams
- ✅ Comparison analysis
- ✅ Setup checklist
- ✅ Troubleshooting guides

**What You Need to Do:**
1. Create `.env` file with credentials
2. Run `docker compose up --build -d`
3. Run `./test_n8n_postgres.sh`
4. Access n8n at http://localhost:5678

**Time Required:** 15-30 minutes for first-time setup

---

## 📝 Document Revision History

| Date | Document | Version | Changes |
|------|----------|---------|---------|
| 2025-10-08 | All | 1.0 | Initial comprehensive documentation |

---

## ✅ Final Checklist

Before you start:
- [ ] Read this index document
- [ ] Choose appropriate documentation path (beginner/intermediate/advanced)
- [ ] Have Docker and Docker Compose installed
- [ ] Have 30 minutes for setup

Ready to begin?
→ Start with [N8N_POSTGRES_QUICKSTART.md](N8N_POSTGRES_QUICKSTART.md)

---

**This comprehensive documentation ensures n8n uses PostgreSQL from day one, providing a production-ready, scalable, and maintainable solution integrated with your IFC Pipeline infrastructure.**
