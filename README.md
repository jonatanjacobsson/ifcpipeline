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

- IFC to CSV conversion
- Clash detection between IFC models
- IFC validation against IDS (Information Delivery Specification)
- IFC file download from URL
- Integration with n8n for workflow automation

![chrome_sDioFJ8Vvy](https://github.com/user-attachments/assets/c2336ad4-c5bd-4a1f-9346-1b710135a9c9)

## TODO

Utilities to implement:
- [x] ifcCsv
- [x] ifcClash
- [x] ifcTester
- [x] ifcDiff
- [x] ifcConvert
- [ ] ifc4D
- [ ] ifc5D
- [x] ifc2json, using https://github.com/bimaps/ifc2json
- [ ] ifcPatch

Other stuff:
- [x] simple way to handle API keys, using environment variables
- [x] some clever way to add endpoints for custom python tools, add custom containers for them
- [ ] better Error Handling and Logging

Documentation:
- [ ] quick introductory video (1 min)
- [ ] video on potential use cases (15 min)
- [ ] PowerBI example request to copy/paste
- [ ] example n8n workflows
- [ ] links to getting started with n8n

## Architecture

The project consists of two main components:

1. **IFC Pipeline**: A FastAPI service that handles IFC file processing operations.
2. **n8n**: A workflow automation tool that orchestrates processes and interacts with the IFC Pipeline service.

## Installation and Setup

### Prerequisites

- Git
- Docker Compose

### Getting Started

1. Install prerequisites (on Ubuntu/Debian):
   ```bash
   sudo apt install git docker-compose
   ```

2. Clone the repository:
   ```bash
   git clone https://github.com/jonatanjacobsson/ifcpipeline.git
   cd ifcpipeline
   ```

3. Set up environment variables:
   ```bash
   cp .env.example .env
   # Edit .env with your preferred settings
   ```

4. Build and run the Docker containers:
   ```bash
   docker-compose up --build -d
   ```

5. Access the services:
   - IFC Pipeline API: `http://localhost:8000`
   - n8n interface: `http://localhost:5678`
   - API Documentation: `http://localhost:8000/docs`

## Environment Variables

This project uses environment variables for configuration:

### API Gateway Security
- `IFC_PIPELINE_API_KEY`: API key for authenticating with the API gateway
- `IFC_PIPELINE_ALLOWED_IP_RANGES`: Comma-separated list of allowed IP ranges in CIDR format

### n8n Configuration
- `N8N_WEBHOOK_URL`: The webhook URL for n8n
- `N8N_COMMUNITY_PACKAGES_ENABLED`: Enable/disable community packages

## Usage

### IFC Pipeline API

The service exposes the following endpoints:

- `/health`: Health check endpoint
- `/list_models`: List available IFC models
- `/ifccsv`: Convert IFC to CSV
- `/ifcclash`: Perform clash detection
- `/ifctester`: Validate IFC against IDS
- `/download_ifc`: Download IFC file from URL

For detailed API documentation, visit `http://localhost:8000/docs` after starting the service.

![image](https://github.com/user-attachments/assets/7e356a27-2763-4e7c-aeb0-80617166232a)

### n8n Workflows

n8n is used to create and manage workflows that interact with the IFC Pipeline service. 
It's simple, extendable and powerful enough for this usecase.
Just be aware of the licensing: https://docs.n8n.io/sustainable-use-license/

You can use n8n to:

- Automate IFC processing tasks
- Create complex workflows involving multiple IFC operations
- Integrate IFC processing with other services and tools (!)

Access the n8n interface at `http://localhost:5678` to create your user and manage workflows.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the [MIT License](LICENSE).
