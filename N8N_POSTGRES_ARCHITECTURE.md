# n8n PostgreSQL Architecture

## System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Docker Compose Network                          │
│                                                                          │
│  ┌────────────────┐                    ┌──────────────────────────┐   │
│  │     n8n        │                    │     PostgreSQL 14        │   │
│  │                │                    │   (postgres service)      │   │
│  │  Image: n8n    │◄──────────────────►│                          │   │
│  │  Port: 5678    │   Database         │   Port: 5432             │   │
│  │                │   Connection       │   User: ifcpipeline      │   │
│  │  Environment:  │                    │                          │   │
│  │  DB_TYPE=      │                    │   Databases:             │   │
│  │   postgresdb   │                    │   ┌──────────────────┐   │   │
│  │                │                    │   │  1. ifcpipeline  │   │   │
│  │  DB_HOST=      │                    │   │                  │   │   │
│  │   postgres     │                    │   │  Tables:         │   │   │
│  │                │                    │   │  - clash_results │   │   │
│  │  DB_DATABASE=  │                    │   │  - conversion_   │   │   │
│  │   n8n          │                    │   │    results       │   │   │
│  │                │                    │   │  - tester_results│   │   │
│  │  depends_on:   │                    │   │  - diff_results  │   │   │
│  │  - postgres    │                    │   └──────────────────┘   │   │
│  │  - api-gateway │                    │                          │   │
│  └────────┬───────┘                    │   ┌──────────────────┐   │   │
│           │                            │   │  2. n8n          │   │   │
│           │                            │   │                  │   │   │
│           │                            │   │  Tables:         │   │   │
│           │                            │   │  - workflow_     │   │   │
│           │                            │   │    entity        │   │   │
│           │                            │   │  - credentials_  │   │   │
│           │                            │   │    entity        │   │   │
│  ┌────────▼────────────┐               │   │  - execution_    │   │   │
│  │  Volume Mappings    │               │   │    entity        │   │   │
│  │                     │               │   │  - tag_entity    │   │   │
│  │  Host → Container   │               │   │  - webhook_      │   │   │
│  │                     │               │   │    entity        │   │   │
│  │  ./n8n-data →      │               │   │  - (30+ more)    │   │   │
│  │   /home/node/.n8n  │               │   └──────────────────┘   │   │
│  │                     │               │                          │   │
│  │  ./shared/uploads → │               └──────────┬───────────────┘   │
│  │   /uploads          │                          │                   │
│  │                     │                          │                   │
│  │  ./shared/output →  │               ┌──────────▼───────────────┐   │
│  │   /output           │               │   postgres-data (volume) │   │
│  │                     │               │                          │   │
│  │  ./shared/examples →│               │   Persistent storage     │   │
│  │   /examples         │               │   for both databases     │   │
│  └─────────────────────┘               └──────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘

External Access:
  User → http://localhost:5678 → n8n Web Interface
  User → http://localhost:8081 → PgWeb (PostgreSQL Admin)
  User → http://localhost:8000 → API Gateway (IFC Pipeline)
```

## Environment Variables Flow

```
┌──────────────────────┐
│    .env file         │
│                      │
│  POSTGRES_USER       │─┐
│  POSTGRES_PASSWORD   │ │
│  POSTGRES_DB         │ │
│  DB_TYPE             │ │
│  N8N_POSTGRES_DB     │ │
└──────────────────────┘ │
                         │
                         │ Loaded by Docker Compose
                         │
                         ▼
┌─────────────────────────────────────────────┐
│         docker-compose.yml                   │
│                                              │
│  n8n service:                                │
│    environment:                              │
│      - DB_TYPE=${DB_TYPE:-postgresdb}       │───┐
│      - DB_POSTGRESDB_DATABASE=              │   │
│          ${N8N_POSTGRES_DB:-n8n}            │   │
│      - DB_POSTGRESDB_HOST=postgres          │   │
│      - DB_POSTGRESDB_PORT=5432              │   │
│      - DB_POSTGRESDB_USER=                  │   │
│          ${POSTGRES_USER:-ifcpipeline}      │   │
│      - DB_POSTGRESDB_PASSWORD=              │   │
│          ${POSTGRES_PASSWORD}               │   │
│      - DB_POSTGRESDB_SCHEMA=public          │   │
└─────────────────────────────────────────────┘   │
                                                  │
                                                  │ Passed to container
                                                  │
                                                  ▼
┌──────────────────────────────────────────────────────┐
│              n8n Container                            │
│                                                       │
│  On startup, n8n reads environment variables:        │
│    1. Detects DB_TYPE=postgresdb                     │
│    2. Reads PostgreSQL connection parameters         │
│    3. Connects to postgres:5432/n8n                  │
│    4. Creates tables if not exist                    │
│    5. Ready for use                                  │
└──────────────────────────────────────────────────────┘
```

## Database Initialization Flow

```
docker compose up --build -d
         │
         ▼
┌────────────────────────┐
│  PostgreSQL Container  │
│  Starts First          │
└────────┬───────────────┘
         │
         ▼
┌───────────────────────────────────────┐
│  Runs init scripts in order:          │
│                                        │
│  1. /docker-entrypoint-initdb.d/      │
│     01-init.sql                        │
│     ├─ CREATE DATABASE ifcpipeline    │
│     ├─ CREATE TABLE clash_results     │
│     ├─ CREATE TABLE conversion_results│
│     ├─ CREATE TABLE tester_results    │
│     └─ CREATE TABLE diff_results      │
│                                        │
│  2. /docker-entrypoint-initdb.d/      │
│     02-n8n-init.sql                    │
│     ├─ CREATE DATABASE n8n            │
│     ├─ GRANT PRIVILEGES               │
│     └─ SET DEFAULT PRIVILEGES         │
└───────────┬───────────────────────────┘
            │
            ▼
┌───────────────────────────┐
│  PostgreSQL Ready         │
│  - ifcpipeline database   │
│  - n8n database           │
│  - User permissions set   │
└───────┬───────────────────┘
        │
        ▼
┌────────────────────────────┐
│  n8n Container Starts      │
│  (depends_on: postgres)    │
└────────┬───────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│  n8n Startup Sequence:               │
│                                      │
│  1. Read environment variables       │
│  2. Detect DB_TYPE=postgresdb        │
│  3. Connect to postgres:5432         │
│  4. Use database 'n8n'               │
│  5. Run database migrations          │
│  6. Create tables (if not exist):    │
│     - workflow_entity                │
│     - credentials_entity             │
│     - execution_entity               │
│     - tag_entity                     │
│     - webhook_entity                 │
│     - settings                       │
│     - user                           │
│     - (30+ more tables)              │
│  7. Start web server                 │
│  8. Ready at port 5678               │
└──────────────────────────────────────┘
```

## Data Flow - Workflow Execution

```
User creates workflow in n8n UI
         │
         ▼
┌─────────────────────────┐
│  n8n Web Interface      │
│  http://localhost:5678  │
└────────┬────────────────┘
         │
         │ Save workflow
         ▼
┌─────────────────────────────────┐
│  n8n Backend                     │
│                                  │
│  INSERT INTO workflow_entity     │
│  VALUES (workflow_definition)    │
└────────┬────────────────────────┘
         │
         │ PostgreSQL query
         ▼
┌──────────────────────────────┐
│  PostgreSQL n8n Database     │
│                              │
│  workflow_entity table       │
│  ┌────────────────────────┐  │
│  │ id: 1                  │  │
│  │ name: "My Workflow"    │  │
│  │ active: true           │  │
│  │ nodes: {...}           │  │
│  │ connections: {...}     │  │
│  │ createdAt: timestamp   │  │
│  └────────────────────────┘  │
└──────────────────────────────┘

User executes workflow
         │
         ▼
┌─────────────────────────────────┐
│  n8n Execution Engine            │
│                                  │
│  1. Load workflow from DB        │
│  2. Execute nodes                │
│  3. Save execution to DB         │
└────────┬────────────────────────┘
         │
         ▼
┌──────────────────────────────┐
│  PostgreSQL n8n Database     │
│                              │
│  execution_entity table      │
│  ┌────────────────────────┐  │
│  │ id: 1                  │  │
│  │ workflowId: 1          │  │
│  │ finished: true         │  │
│  │ mode: "manual"         │  │
│  │ data: {...}            │  │
│  │ startedAt: timestamp   │  │
│  │ stoppedAt: timestamp   │  │
│  └────────────────────────┘  │
└──────────────────────────────┘
```

## Integration with IFC Pipeline

```
┌──────────────────────────────────────────────────────────────┐
│                      Complete System                          │
│                                                               │
│  ┌────────────┐    ┌────────────┐    ┌──────────────────┐   │
│  │   User     │───►│    n8n     │───►│  API Gateway     │   │
│  │  Browser   │    │            │    │  (IFC Pipeline)  │   │
│  └────────────┘    └─────┬──────┘    └────────┬─────────┘   │
│                          │                     │             │
│                          │                     │             │
│                          │                     │             │
│                   ┌──────▼─────────────────────▼─────────┐   │
│                   │       PostgreSQL Server              │   │
│                   │                                      │   │
│                   │  ┌────────────┐  ┌──────────────┐   │   │
│                   │  │ n8n        │  │ ifcpipeline  │   │   │
│                   │  │ database   │  │ database     │   │   │
│                   │  │            │  │              │   │   │
│                   │  │ Stores:    │  │ Stores:      │   │   │
│                   │  │ - Workflows│  │ - Clash data │   │   │
│                   │  │ - Creds    │  │ - Diffs      │   │   │
│                   │  │ - Executions│ │ - Tests      │   │   │
│                   │  └────────────┘  └──────────────┘   │   │
│                   └───────────────────────────────────────┘   │
│                                                               │
│  Workflow Example:                                            │
│  ┌──────────────────────────────────────────────────────┐    │
│  │ 1. n8n Webhook receives IFC file URL                │    │
│  │ 2. n8n calls API Gateway to process file            │    │
│  │ 3. API Gateway queues job to workers                │    │
│  │ 4. Workers process and save results to ifcpipeline  │    │
│  │ 5. n8n polls job status                             │    │
│  │ 6. n8n retrieves results                            │    │
│  │ 7. n8n sends notification/email                     │    │
│  │ 8. Execution saved in n8n database                  │    │
│  └──────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
```

## Network Communication

```
┌─────────────────────────────────────────────────────────────┐
│              Docker Internal Network (default)               │
│                                                              │
│  Service Name Resolution:                                    │
│  ┌──────────┐      ┌──────────┐      ┌──────────────┐      │
│  │   n8n    │─────►│ postgres │      │ api-gateway  │      │
│  │          │      │          │      │              │      │
│  │ connects │      │ listens  │      │              │      │
│  │ to:      │      │ on:      │      │              │      │
│  │ postgres │      │ 5432     │      │              │      │
│  │ :5432    │      │          │      │              │      │
│  └──────────┘      └──────────┘      └──────────────┘      │
│       │                  │                    │             │
└───────┼──────────────────┼────────────────────┼─────────────┘
        │                  │                    │
        │                  │                    │
        │ Port Mapping     │ Port Mapping       │ Port Mapping
        │ 5678:5678        │ 5432:5432          │ 8000:80
        │                  │                    │
        ▼                  ▼                    ▼
┌───────────────────────────────────────────────────────────┐
│                      Host Machine                          │
│                                                            │
│  localhost:5678  localhost:5432  localhost:8000           │
└───────────────────────────────────────────────────────────┘
```

## File System Layout

```
/workspace/
├── n8n-data/                          ← n8n persistent data
│   ├── .n8n_encryption_key           ← Critical! Backup this!
│   ├── config/                       ← n8n settings
│   └── nodes/                        ← Custom/community nodes
│
├── shared/
│   ├── uploads/                      ← Shared with n8n and workers
│   ├── output/                       ← Shared with n8n and workers
│   └── examples/                     ← Shared with n8n and workers
│
├── postgres/
│   ├── init/
│   │   ├── 01-init.sql              ← IFC Pipeline tables
│   │   └── 02-n8n-init.sql          ← n8n database setup
│   ├── backup.sh
│   └── maintenance.sh
│
├── docker-compose.yml                ← Service orchestration
├── .env                              ← Configuration (not in git)
├── .env.example                      ← Template with n8n config
│
└── Documentation:
    ├── N8N_POSTGRES_QUICKSTART.md
    ├── N8N_POSTGRES_CONFIGURATION_ANALYSIS.md
    ├── N8N_POSTGRES_IMPLEMENTATION_SUMMARY.md
    ├── N8N_POSTGRES_ARCHITECTURE.md  ← This file
    └── test_n8n_postgres.sh          ← Verification script

Docker Volumes:
├── postgres-data                     ← PostgreSQL data files
│   ├── base/                        ← Database files
│   │   ├── 16384/                   ← ifcpipeline database
│   │   └── 16385/                   ← n8n database
│   └── pg_wal/                      ← Write-ahead logs
│
└── n8n-data (from ./n8n-data)       ← Mounted from host
```

## Security Architecture

```
┌──────────────────────────────────────────────────────────┐
│                   Security Layers                         │
│                                                           │
│  Layer 1: Network Isolation                              │
│  ┌────────────────────────────────────────────────┐      │
│  │  Docker Internal Network                        │      │
│  │  - n8n and PostgreSQL on private network       │      │
│  │  - No direct external PostgreSQL access        │      │
│  └────────────────────────────────────────────────┘      │
│                                                           │
│  Layer 2: Authentication                                 │
│  ┌────────────────────────────────────────────────┐      │
│  │  PostgreSQL User Authentication                │      │
│  │  - Username: ifcpipeline                       │      │
│  │  - Password: from ${POSTGRES_PASSWORD}         │      │
│  │  - No public/anonymous access                  │      │
│  └────────────────────────────────────────────────┘      │
│                                                           │
│  Layer 3: Database Isolation                             │
│  ┌────────────────────────────────────────────────┐      │
│  │  Separate Databases                            │      │
│  │  - n8n database: only n8n accesses             │      │
│  │  - ifcpipeline database: only workers access   │      │
│  │  - No cross-database permissions               │      │
│  └────────────────────────────────────────────────┘      │
│                                                           │
│  Layer 4: Application Security                           │
│  ┌────────────────────────────────────────────────┐      │
│  │  n8n Security                                  │      │
│  │  - User authentication required                │      │
│  │  - Credentials encrypted in database           │      │
│  │  - Encryption key stored in volume             │      │
│  └────────────────────────────────────────────────┘      │
│                                                           │
│  Layer 5: Data Security                                  │
│  ┌────────────────────────────────────────────────┐      │
│  │  Encryption at Rest                            │      │
│  │  - n8n credentials encrypted                   │      │
│  │  - Encryption key in /home/node/.n8n/          │      │
│  │  - PostgreSQL data in Docker volume            │      │
│  └────────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────────┘

SSL/TLS Configuration (Internal Network):
┌────────────────────────────────────┐
│  Current Setup (Docker Internal):  │
│  - NODE_TLS_REJECT_UNAUTHORIZED=0  │
│  - PGSSLMODE=disable               │
│  → Secure by network isolation     │
└────────────────────────────────────┘

Production Enhancement (Optional):
┌────────────────────────────────────┐
│  Enable SSL/TLS:                   │
│  - DB_POSTGRESDB_SSL_ENABLED=true │
│  - PGSSLMODE=require              │
│  - Provide SSL certificates        │
└────────────────────────────────────┘
```

## Scalability Architecture

```
Current Setup (Single Instance):
┌────────────────────────────────┐
│  n8n (single container)        │
│  ├─ 4 CPU cores               │
│  ├─ 6GB RAM                   │
│  └─ Handles: ~100 workflows   │
└────────┬───────────────────────┘
         │
         ▼
┌────────────────────────────────┐
│  PostgreSQL (single instance) │
│  ├─ 0.5 CPU cores             │
│  ├─ 512MB RAM                 │
│  └─ Connection pool: default  │
└───────────────────────────────┘

Scaling Option 1: Vertical Scaling
┌────────────────────────────────┐
│  n8n (increase resources)      │
│  ├─ 8 CPU cores               │
│  ├─ 12GB RAM                  │
│  └─ Handles: ~500 workflows   │
└────────┬───────────────────────┘
         │
         ▼
┌────────────────────────────────┐
│  PostgreSQL (more resources)  │
│  ├─ 2 CPU cores               │
│  ├─ 2GB RAM                   │
│  └─ Connection pool: 20       │
└───────────────────────────────┘

Scaling Option 2: Horizontal Scaling (Future)
┌──────────────┐  ┌──────────────┐
│  n8n Main    │  │  n8n Worker  │
│  ├─ UI       │  │  ├─ Executor │
│  └─ API      │  │  └─ Executor │
└──────┬───────┘  └──────┬───────┘
       │                 │
       └────────┬────────┘
                ▼
┌────────────────────────────────┐
│  PostgreSQL (with replication) │
│  ├─ Primary (write)            │
│  └─ Replica (read)             │
└────────────────────────────────┘
```

## Backup Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Backup Strategy                       │
│                                                          │
│  Component 1: PostgreSQL Database                        │
│  ┌────────────────────────────────────────────────┐     │
│  │  n8n database                                  │     │
│  │  ├─ pg_dump to .dump file                     │     │
│  │  ├─ Frequency: Daily (2:00 AM)                │     │
│  │  └─ Retention: 30 days                        │     │
│  └────────────────────────────────────────────────┘     │
│                                                          │
│  Component 2: n8n Data Volume                           │
│  ┌────────────────────────────────────────────────┐     │
│  │  ./n8n-data/                                   │     │
│  │  ├─ Encryption key (CRITICAL!)                │     │
│  │  ├─ Settings                                   │     │
│  │  └─ Custom nodes                               │     │
│  │  ├─ tar.gz archive                            │     │
│  │  ├─ Frequency: Daily (2:00 AM)                │     │
│  │  └─ Retention: 30 days                        │     │
│  └────────────────────────────────────────────────┘     │
│                                                          │
│  Backup Flow:                                           │
│  ┌─────────┐     ┌──────────┐     ┌─────────────┐     │
│  │ Cron    │────►│ Backup   │────►│ Compressed  │     │
│  │ Job     │     │ Script   │     │ Archive     │     │
│  └─────────┘     └──────────┘     └─────────────┘     │
│                                           │             │
│                                           ▼             │
│                                    ┌─────────────┐     │
│                                    │ Offsite     │     │
│                                    │ Storage     │     │
│                                    │ (optional)  │     │
│                                    └─────────────┘     │
└─────────────────────────────────────────────────────────┘

Recovery Time Objective (RTO): < 1 hour
Recovery Point Objective (RPO): 24 hours
```

This architecture provides a complete view of how n8n integrates with PostgreSQL in your IFC Pipeline infrastructure.
