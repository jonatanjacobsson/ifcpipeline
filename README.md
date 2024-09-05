# IFC Pipeline

IFC Pipeline is a FastAPI-based service for processing Industry Foundation Classes (IFC) files, integrated with n8n for workflow automation. It provides a set of endpoints for various IFC-related operations, including CSV export, clash detection, and IDS validation.

## Features

- IFC to CSV conversion
- Clash detection between IFC models
- IFC validation against IDS (Information Delivery Specification)
- IFC file download from URL
- Integration with n8n for workflow automation

## Architecture

The project consists of two main components:

1. **IFC Pipeline**: A FastAPI service that handles IFC file processing operations.
2. **n8n**: A workflow automation tool that orchestrates processes and interacts with the IFC Pipeline service.

## Getting Started

### Prerequisites

- Docker
- Docker Compose

### Installation

1. Clone the repository:
   ```
   git clone https://github.com/yourusername/ifc-pipeline.git
   cd ifc-pipeline
   ```

2. Build and run the Docker containers:
   ```
   docker-compose up --build
   ```

The IFC Pipeline service will be available at `http://localhost:8000`.
The n8n interface will be accessible at `http://localhost:5678`.

## Usage

### IFC Pipeline Service

The service exposes several endpoints:

- `/health`: Health check endpoint
- `/list_models`: List available IFC models
- `/ifccsv`: Convert IFC to CSV
- `/ifcclash`: Perform clash detection
- `/ifctester`: Validate IFC against IDS
- `/download_ifc`: Download IFC file from URL

For detailed API documentation, visit `http://localhost:8000/docs` after starting the service.

### n8n Workflows

n8n is used to create and manage workflows that interact with the IFC Pipeline service. You can use n8n to:

- Automate IFC processing tasks
- Create complex workflows involving multiple IFC operations
- Integrate IFC processing with other services and tools

Access the n8n interface at `http://localhost:5678` to create and manage workflows.

## Configuration

The services can be configured using environment variables in the `docker-compose.yml` file.

## TODO

The following utilities are yet to be converted into endpoints:

1. IFC Diff: Compare two IFC files and generate a diff report
2. IFC Patch: Apply patches to IFC files
3. IFC Geolocation: Add or modify geolocation data in IFC files
4. IFC Merge: Merge multiple IFC files into a single file
5. IFC Split: Split an IFC file into multiple files based on certain criteria
6. IFC Property Set: Add, modify, or delete property sets in IFC files

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the [MIT License](LICENSE).