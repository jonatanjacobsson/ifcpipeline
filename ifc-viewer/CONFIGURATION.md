# IFC Viewer Configuration

## Environment Variables

The IFC Viewer requires the following environment variable to be set:

### VITE_API_BASE

This variable specifies the base URL for the IFC pipeline API server.

### Setting the Environment Variable

#### For Development
Create a `.env` file in the root directory:
```bash
VITE_API_BASE=http://localhost:8000
```

#### For Docker Build
Pass the variable as a build argument:
```bash
docker build --build-arg VITE_API_BASE=https://your-api-server.com .
```

#### For Production
Set the environment variable in your deployment configuration:
```bash
export VITE_API_BASE=https://your-api-server.com
```

## Troubleshooting

### "Failed to fetch" Error
If you see this error in the console, check:
1. The API server is running and accessible
2. The `VITE_API_BASE` environment variable is correctly set
3. CORS is properly configured on the API server
4. The token in the URL is valid

### WebGL Framebuffer Errors
These errors have been fixed in the latest version by:
1. Adding proper viewport dimension checks
2. Implementing initialization timing controls
3. Adding error handling for renderer operations

## API Endpoints

The viewer expects the following API endpoint to be available:

- `GET ${VITE_API_BASE}/download/{token}` - Downloads IFC model by token




