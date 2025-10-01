# IFC Classifier Service

A high-performance CPU-based IFC element classification service using CatBoost machine learning. This service provides near-instant classification of IFC elements based on their Revit properties.

## Features

- **Fast Classification**: < 2ms response time per element
- **Batch Processing**: Classify multiple elements in a single request
- **CPU Optimized**: No GPU required for inference
- **RESTful API**: Simple HTTP endpoints for integration
- **Docker Ready**: Containerized for easy deployment
- **Health Monitoring**: Built-in health checks and model status

## API Endpoints

### Health Check
```
GET /health
```
Returns service health status and model loading state.

### Model Information
```
GET /model/info
```
Returns model metadata, feature names, and class count.

### Single Classification
```
POST /classify
Content-Type: application/json

{
  "category": "Walls",
  "family": "Basic Wall", 
  "type": "Generic - 200mm",
  "manufacturer": "Autodesk",
  "description": "Interior partition wall"
}
```

Response:
```json
{
  "result": {
    "ifc_class": "IfcWall",
    "predefined_type": "PARTITIONING",
    "confidence": 0.95
  },
  "processing_time_ms": 1.2
}
```

### Batch Classification
```
POST /classify/batch
Content-Type: application/json

{
  "elements": [
    {
      "category": "Walls",
      "family": "Basic Wall",
      "type": "Generic - 200mm",
      "manufacturer": "Autodesk",
      "description": "Interior partition wall"
    },
    {
      "category": "Doors",
      "family": "Single-Flush", 
      "type": "900 x 2100mm",
      "manufacturer": "Autodesk",
      "description": "Standard door"
    }
  ]
}
```

## Model Requirements

The service requires a pre-trained CatBoost model file placed in the `/app/models/` directory as `ifc_classifier.cbm`. The model should be trained to classify IFC elements based on Revit properties:

- `Category`: Revit category (e.g., "Walls", "Doors")
- `Family`: Revit family name (e.g., "Basic Wall")
- `Type`: Revit type name (e.g., "Generic - 200mm")
- `Manufacturer`: Manufacturer name (optional)
- `Description`: Element description (optional)

The model should output classifications in format "IfcClass|PredefinedType" or just "IfcClass".

## Deployment

### Using Docker Compose (Recommended)

The service is integrated into the main `docker-compose.yml`:

```bash
# Start the classifier service
docker-compose up ifc-classifier

# Or start all services
docker-compose up
```

### Standalone Docker

```bash
# Build the image
docker build -t ifc-classifier -f ifc-classifier-service/Dockerfile .

# Run the container
docker run -d \
  --name ifc-classifier \
  -p 8002:8000 \
  -v ./ifc-classifier-service/models:/app/models \
  ifc-classifier
```

## Testing

Run the test suite to verify the service is working:

```bash
# Install test dependencies
pip install requests

# Run tests (service must be running)
python ifc-classifier-service/tests/test_classifier.py
```

## Performance

- **Single Classification**: < 2ms per element
- **Batch Processing**: ~100ms for 100 elements
- **Memory Usage**: < 512MB
- **CPU Usage**: < 50% on 2-core system

## Integration with API Gateway

The service is automatically integrated with the main API Gateway. Access classification through:

- `POST http://localhost:8000/classify` - Single element
- `POST http://localhost:8000/classify/batch` - Multiple elements

All requests require the same authentication as other IFC Pipeline services.

## Troubleshooting

### Service Won't Start
- Check if model file exists in `/app/models/ifc_classifier.cbm`
- Verify Docker build completed successfully
- Check logs: `docker-compose logs ifc-classifier`

### Poor Classification Accuracy
- Verify model was trained with representative data
- Check model file integrity
- Ensure input data matches expected format

### Performance Issues
- Monitor CPU usage
- Check for memory leaks
- Verify batch size is appropriate
- Consider horizontal scaling

## API via Gateway

When accessed through the API Gateway (port 8000), the classification endpoints are:

```bash
# Single classification
curl -X POST http://localhost:8000/classify \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "category": "Walls",
    "family": "Basic Wall",
    "type": "Generic - 200mm",
    "manufacturer": "Autodesk",
    "description": "Interior partition wall"
  }'

# Batch classification  
curl -X POST http://localhost:8000/classify/batch \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "elements": [
      {
        "category": "Walls",
        "family": "Basic Wall",
        "type": "Generic - 200mm",
        "manufacturer": "Autodesk",
        "description": "Interior partition wall"
      }
    ]
  }'
```

## License

This service is part of the IFC Pipeline project. See the main project LICENSE for details. 