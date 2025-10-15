# n8n Workflow Examples

This directory contains example n8n workflows in JSON format for the ifcpipeline project.

## How to Import Workflows into n8n

There are three main methods to import these JSON workflow files into your n8n instance:

### Method 1: Import via Editor UI (Recommended)

1. Open your n8n Editor UI
2. Click the **three dots menu** (⋮) in the upper right corner
3. Select **Import from File**
4. Choose the JSON file from this directory
5. The workflow will load onto your canvas

### Method 2: Copy and Paste

1. Open any `.json` file from this directory in a text editor
2. Copy the entire contents (`Ctrl+A`, `Ctrl+C`)
3. Go to your n8n Editor UI
4. Paste directly onto the canvas (`Ctrl+V` or `Cmd+V` on Mac)

### Method 3: Command Line

If you have access to the n8n CLI:

```bash
n8n import:workflow --input=/path/to/workflow.json
```

## Available Workflows

- `✨Getting Started_ ifcpipeline.json` - Getting started workflow for ifcpipeline

## Important Notes

⚠️ **Credentials**: Imported workflows will reference credential names but won't include actual credential data. You'll need to:
- Set up or select appropriate credentials for any nodes that require authentication
- Review HTTP Request nodes for any sensitive information before sharing workflows

## Need Help?

For more details on n8n workflows, visit the [n8n documentation](https://docs.n8n.io/workflows/export-import/).

