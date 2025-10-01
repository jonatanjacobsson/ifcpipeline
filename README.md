# IfcPipeline

<table>
  <tr>
    <td>
      <img src="https://github.com/user-attachments/assets/1279d904-6bc3-41aa-9e9a-9e30e37c3c44" alt="ifcpipeline-smol" width="500"/>
    </td>
    <td style="vertical-align: middle;">
      <p>
        <strong>IfcPipeline</strong> is a FastAPI-based service for processing Industry Foundation Classes (IFC) files, integrated with n8n for workflow automation. It provides a set of endpoints for various IFC-related operations, including CSV export, clash detection, and IDS validation.
      </p>
    </td>
  </tr>
</table>

## Features

### Core Processing Operations
- **Format Conversion**: Convert IFC to GLB, STEP, DAE, OBJ, XML, and other formats
- **CSV Export/Import**: Bidirectional data exchange with CSV, XLSX, and ODS formats
- **Clash Detection**: Advanced geometric clash detection with smart grouping
- **IDS Validation**: Validate IFC models against Information Delivery Specification
- **Model Comparison**: Diff analysis to track changes between IFC versions
- **Quantity Takeoff**: Automatic calculation and insertion of quantities (5D BIM)
- **IFC Patching**: Apply built-in and custom IfcPatch recipes to modify models
- **JSON Conversion**: Convert IFC files to JSON format for web applications
- **ML Classification**: CatBoost-based element classification

### Platform Features
- **Workflow Automation**: Integrated n8n with custom community nodes
- **3D Viewer**: Web-based IFC viewer using @thatopen/components
- **Asynchronous Processing**: Redis Queue (RQ) based job management
- **PostgreSQL Storage**: Persistent storage for processing results
- **API Gateway**: FastAPI-based REST API with comprehensive documentation
- **Token-based File Sharing**: Secure temporary download links with expiry
- **Monitoring Dashboard**: RQ Dashboard for queue monitoring
- **Automatic Cleanup**: Scheduled cleanup of old processing results

![chrome_sDioFJ8Vvy](https://github.com/user-attachments/assets/c2336ad4-c5bd-4a1f-9346-1b710135a9c9)

## Project Status

### Implemented Features ✅
- [x] **ifcCsv** - CSV/XLSX/ODS export and import
- [x] **ifcClash** - Clash detection with smart grouping
- [x] **ifcTester** - IDS validation
- [x] **ifcDiff** - Model comparison and change tracking
- [x] **ifcConvert** - Format conversion (GLB, STEP, etc.)
- [x] **ifc5D** - Quantity takeoff calculations
- [x] **ifc2json** - JSON conversion using https://github.com/bimaps/ifc2json
- [x] **ifcPatch** - IfcPatch recipe execution (built-in + custom)
- [x] **IFC Classifier** - ML-based element classification
- [x] **IFC Viewer** - Web-based 3D viewer
- [x] **n8n Integration** - Custom community nodes package
- [x] **API Key Authentication** - Environment variable based security
- [x] **PostgreSQL Storage** - Persistent result storage
- [x] **Worker Architecture** - Containerized Python workers

### Roadmap 🚀
- [ ] **ifc4D** - Time/scheduling integration
- [ ] **Enhanced Error Handling** - Better logging and error recovery
- [ ] **Webhook Notifications** - Job completion callbacks
- [ ] **Result Caching** - Performance optimization layer

### Documentation Needed 📚
- [ ] Quick introductory video (1 min)
- [ ] Use case examples video (15 min)
- [ ] PowerBI integration examples
- [ ] Example n8n workflow library
- [ ] Getting started with n8n guide

## Architecture

IFC Pipeline follows a **microservice architecture** with distributed workers for asynchronous processing.

### System Components

1. **API Gateway** (FastAPI) - Central orchestration point with REST endpoints
2. **Worker Services** - Specialized Python workers for each operation:
   - `ifcconvert-worker` - Format conversion
   - `ifcclash-worker` - Clash detection
   - `ifccsv-worker` - CSV export/import
   - `ifctester-worker` - IDS validation
   - `ifcdiff-worker` - Model comparison
   - `ifc5d-worker` - Quantity calculations
   - `ifcpatch-worker` - IFC patching
   - `ifc2json-worker` - JSON conversion
3. **IFC Viewer** - Web-based 3D viewer (Vite + @thatopen/components)
4. **n8n** - Workflow automation platform with custom nodes
5. **Redis** - Job queue and result backend
6. **PostgreSQL** - Persistent storage for processing results
7. **Monitoring** - RQ Dashboard and PgWeb for observability

### Architecture Diagram
<img width="1698" height="1712" alt="diagram-export-2025-10-01-13_46_57" src="https://github.com/user-attachments/assets/840f3e4e-4562-44ef-9172-71b24e9d2b38" />

### Key Design Patterns
- **Queue-based Communication**: All operations are asynchronous via Redis Queue
- **Shared Volumes**: `/uploads`, `/output`, and `/examples` for file management
- **Token-based Access**: Secure temporary download links with 30-minute expiry
- **Horizontal Scalability**: Workers can be replicated for load balancing

## Installation and Setup

### Prerequisites

- Git
- Docker & Docker Compose

### Quick Start

1. **Install prerequisites** (on Ubuntu/Debian):
   ```bash
   sudo apt install git docker-compose
   ```

2. **Clone the repository**:
   ```bash
   git clone https://github.com/jonatanjacobsson/ifcpipeline.git
   cd ifcpipeline
   ```

3. **Set up environment variables**:
   ```bash
   cp .env.example .env
   # Edit .env with your settings (API keys, passwords, URLs)
   ```

4. **Build and start all services**:
   ```bash
   docker compose up --build -d
   ```

5. **Access the services**:
   - **API Gateway**: http://localhost:8000
   - **API Documentation**: http://localhost:8000/docs
   - **n8n Workflows**: http://localhost:5678
   - **IFC Viewer**: http://localhost:8001
   - **RQ Dashboard**: http://localhost:9181
   - **PgWeb (Database)**: http://localhost:8081

6. **Install n8n community nodes**:
   - Open n8n at http://localhost:5678
   - Go to **Settings** > **Community Nodes**
   - Search and install: `n8n-nodes-ifcpipeline`
   - [Community Nodes Guide](https://docs.n8n.io/integrations/community-nodes/installation/gui-install/)

### Verify Installation

Check system health:
```bash
curl http://localhost:8000/health
```

View running services:
```bash
docker compose ps
```
    
## Configuration

### Required Environment Variables

Create a `.env` file in the project root with the following variables:

#### Security & Access
```bash
IFC_PIPELINE_API_KEY=your-secret-api-key
IFC_PIPELINE_ALLOWED_IP_RANGES=127.0.0.1/32,172.18.0.0/16
IFC_PIPELINE_EXTERNAL_URL=https://your-domain.com
IFC_PIPELINE_PREVIEW_EXTERNAL_URL=https://viewer.your-domain.com
```

#### n8n Configuration
```bash
N8N_WEBHOOK_URL=https://your-n8n-webhooks.com
N8N_COMMUNITY_PACKAGES_ENABLED=true
```

#### Database Configuration
```bash
POSTGRES_USER=ifcpipeline
POSTGRES_PASSWORD=your-secure-password
POSTGRES_DB=ifcpipeline
```

#### Redis Configuration
```bash
REDIS_URL=redis://redis:6379/0
```

> 💡 **Tip**: IP ranges in CIDR format can bypass API key authentication for trusted networks

## Usage

### API Endpoints

The API Gateway exposes comprehensive REST endpoints for IFC operations:

#### Processing Operations
- `POST /ifcconvert` - Convert IFC to other formats (GLB, STEP, OBJ, etc.)
- `POST /ifccsv` - Export IFC data to CSV/XLSX/ODS
- `POST /ifccsv/import` - Import CSV/XLSX/ODS data back to IFC
- `POST /ifcclash` - Detect clashes between IFC models
- `POST /ifctester` - Validate IFC against IDS specification
- `POST /ifcdiff` - Compare two IFC files and generate diff
- `POST /ifc2json` - Convert IFC to JSON format
- `POST /calculate-qtos` - Calculate quantities (5D)
- `POST /patch/execute` - Apply IfcPatch recipes
- `POST /patch/recipes/list` - List available patch recipes

#### Classification
- `POST /classify` - Classify single IFC element (ML-based)
- `POST /classify/batch` - Classify multiple elements

#### File Operations
- `POST /upload/{file_type}` - Upload IFC, IDS, or CSV files
- `POST /download-from-url` - Download file from external URL
- `POST /create_download_link` - Create temporary download token
- `GET /download/{token}` - Download file using token
- `GET /list_directories` - List available files and directories

#### Job Management
- `GET /jobs/{job_id}/status` - Check job status and results
- `GET /health` - System health check

#### Viewer
- `GET /{token}` - Serve IFC viewer with file access

### Interactive API Documentation

Visit the auto-generated Swagger UI for interactive API testing:

**http://localhost:8000/docs**

![image](https://github.com/user-attachments/assets/7e356a27-2763-4e7c-aeb0-80617166232a)

### Example: Converting IFC to GLB

```bash
# 1. Upload IFC file
curl -X POST http://localhost:8000/upload/ifc \
  -H "X-API-Key: your-api-key" \
  -F "file=@model.ifc"

# 2. Start conversion job
curl -X POST http://localhost:8000/ifcconvert \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "input_filename": "model.ifc",
    "output_filename": "model.glb"
  }'

# Returns: {"job_id": "abc-123"}

# 3. Check job status
curl http://localhost:8000/jobs/abc-123/status \
  -H "X-API-Key: your-api-key"

# 4. Download result when complete
curl -X POST http://localhost:8000/create_download_link \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"file_path": "/output/glb/model.glb"}'
```

### n8n Workflow Automation

n8n provides a visual interface for creating automated IFC processing workflows.

#### Available n8n Nodes

The `n8n-nodes-ifcpipeline` community package provides:

1. **IfcPipeline** - File operations, uploads, downloads, viewer links
2. **IfcConversion** - Format conversion with configuration
3. **IfcCsv** - CSV/XLSX/ODS export and import
4. **IfcClash** - Clash detection with smart grouping
5. **IfcTester** - IDS validation
6. **IfcDiff** - Model comparison
7. **IfcToJson** - JSON conversion
8. **IfcQuantityTakeoff** - Quantity calculations
9. **IfcPatch** - Apply recipes (dynamic recipe loading)

#### Example Workflow: Automated QA Pipeline

```
Webhook (New IFC File URL)
  ↓
Download File from URL
  ↓
Validate against IDS
  ↓
Run Clash Detection
  ↓
Export Results to CSV
  ↓
Send Email with Results
```

#### Getting Started with n8n

1. Access n8n at **http://localhost:5678**
2. Create your account
3. Install the `n8n-nodes-ifcpipeline` community package
4. Configure credentials (API Key + URL)
5. Start building workflows!

> ⚠️ **Note**: Be aware of n8n's [Sustainable Use License](https://docs.n8n.io/sustainable-use-license/)

### Database & Storage

#### PostgreSQL Database

Stores persistent results from workers:
- Clash detection results
- Validation reports  
- Model comparison data
- Conversion metadata

**Access PgWeb**: http://localhost:8081

#### Shared Volumes

All workers access shared filesystem:
- `/uploads` - Input files
- `/output` - Processing results (organized by worker type)
- `/examples` - Sample files for testing

#### Automatic Cleanup

The cleanup service runs daily to remove:
- Files older than 7 days in `/output/clash` and `/output/diff`
- Empty directories

## Monitoring & Troubleshooting

### Health Check

Check system status:
```bash
curl http://localhost:8000/health
```

Returns health status of:
- API Gateway
- Redis
- All worker queues
- Active workers

### RQ Dashboard

Monitor job queues at **http://localhost:9181**:
- Queue depths
- Worker status
- Failed jobs
- Job history and results

### Common Issues

#### Worker not processing jobs
```bash
# Check worker logs
docker compose logs ifcconvert-worker -f

# Restart specific worker
docker compose restart ifcconvert-worker
```

#### Out of memory
```bash
# Check resource usage
docker stats

# Increase memory limits in docker-compose.yml
```

#### Redis connection issues
```bash
# Check Redis status
docker compose logs redis

# Restart Redis
docker compose restart redis
```

### Logs

View logs for all services:
```bash
docker compose logs -f
```

View logs for specific service:
```bash
docker compose logs api-gateway -f
```

## Development

### Project Structure

```
ifc-pipeline/
├── api-gateway/          # FastAPI application
├── shared/               # Shared Python library
├── *-worker/             # Worker services (ifcconvert, ifcclash, etc.)
├── ifc-viewer/           # Vite-based 3D viewer
├── ifc-classifier-service/ # ML classification service
├── n8n-data/             # n8n persistent data
├── postgres/             # Database utilities
└── docker-compose.yml    # Service orchestration
```

### Custom IfcPatch Recipes

Add custom recipes to `ifcpatch-worker/custom_recipes/`:
1. Create Python file following IfcPatch recipe structure
2. Restart ifcpatch-worker
3. Recipe auto-discovered and available in API

## Performance Considerations

### Resource Allocation

Heavy workers (configured with higher resources):
- **ifcclash-worker**: 4 CPU, 12GB RAM
- **ifcdiff-worker**: 4 CPU, 12GB RAM (2 replicas)
- **n8n**: 4 CPU, 6GB RAM

Light workers:
- **ifccsv-worker**: 0.5 CPU, 1GB RAM
- **ifctester-worker**: 0.3 CPU, 1GB RAM

### Scaling

Increase replicas for heavy workloads:
```yaml
# In docker-compose.yml
ifcdiff-worker:
  deploy:
    replicas: 4  # Increase from 2 to 4
```

## Security

- **API Key Authentication**: Required for all API endpoints
- **IP Whitelisting**: CIDR ranges can bypass API key requirement
- **Token Expiry**: Download tokens expire after 30 minutes
- **Network Isolation**: Workers communicate on internal Docker network only

## Contributing

We welcome contributions! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

See [WORKER_CREATION_GUIDE.md](WORKER_CREATION_GUIDE.md) for adding new workers.

## Acknowledgements

This project wouldn't be possible without:

- **[IfcOpenShell](https://ifcopenshell.org/)** - Open-source IFC toolkit
- **[n8n](https://n8n.io/)** - Workflow automation platform
- **[@thatopen/components](https://github.com/ThatOpen/engine_components)** - BIM viewer framework
- **[BuildingSMART](https://www.buildingsmart.org/)** - IFC standards development

## License

This project is licensed under the [MIT License](LICENSE).

---

**Questions or Issues?** Open an issue on [GitHub](https://github.com/jonatanjacobsson/ifcpipeline/issues)
