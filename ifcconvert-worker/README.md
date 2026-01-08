# IfcConvert Worker

A Redis Queue (RQ) worker that provides comprehensive IFC file conversion capabilities using IfcOpenShell's IfcConvert tool.

## Overview

This worker processes IFC conversion jobs from the Redis queue and supports **ALL IfcConvert command-line arguments** from IfcOpenShell 0.8.x, providing full control over geometry processing, filtering, and serialization options.

## Features

- ✅ **Complete IfcConvert Support**: All 100+ command-line arguments supported
- ✅ **Multiple Output Formats**: OBJ, DAE, glTF, STEP, IGES, XML, SVG, HDF, CityJSON, TTL/WKT, IFC
- ✅ **Advanced Filtering**: Filter by entity types, attributes, layers, with hierarchical support
- ✅ **Parallel Processing**: Multi-threaded geometry interpretation
- ✅ **Flexible Coordinate Systems**: World coords, building/site local placement, custom offsets
- ✅ **SVG Floor Plans**: Comprehensive 2D drawing generation with customization
- ✅ **Material & Rendering Control**: Surface colors, materials, transparency, normals, UVs
- ✅ **Boolean Operations**: Full control over clipping, openings, and boolean processing
- ✅ **Database Integration**: Automatic storage of conversion results and options

## Quick Start

### Basic Conversion

```json
{
    "input_filename": "/uploads/model.ifc",
    "output_filename": "/output/model.glb",
    "threads": 8
}
```

### Convert Specific Elements

```json
{
    "input_filename": "/uploads/building.ifc",
    "output_filename": "/output/walls-slabs.obj",
    "include": ["IfcWall", "IfcSlab"],
    "include_type": "entities",
    "threads": 4,
    "use_world_coords": true
}
```

### Generate SVG Floor Plan

```json
{
    "input_filename": "/uploads/building.ifc",
    "output_filename": "/output/floorplan.svg",
    "exclude": ["IfcOpeningElement", "IfcSpace"],
    "exclude_type": "entities",
    "bounds": "1024x768",
    "print_space_names": true,
    "print_space_areas": true
}
```

## Supported Output Formats

| Extension | Format | Description |
|-----------|--------|-------------|
| `.obj` | WaveFront OBJ | 3D geometry with materials (.mtl file) |
| `.dae` | Collada | Digital Assets Exchange format |
| `.glb` | glTF Binary | Modern 3D format (glTF v2.0) |
| `.stp` | STEP | Standard for Product Data Exchange |
| `.igs` | IGES | Initial Graphics Exchange |
| `.xml` | XML | Property definitions and tree |
| `.svg` | SVG | 2D floor plans and elevations |
| `.h5` | HDF | Hierarchical Data Format |
| `.cityjson` | CityJSON | Geospatial data format |
| `.ttl` | TTL/WKT | RDF Turtle with geometry |
| `.ifc` | IFC-SPF | Industry Foundation Classes |

## Documentation

- **[ARGUMENTS.md](./ARGUMENTS.md)** - Complete reference for all supported arguments
- **[EXAMPLES.md](./EXAMPLES.md)** - Practical usage examples and scenarios
- **[docs/TROUBLESHOOTING.md](./docs/TROUBLESHOOTING.md)** - Troubleshooting guide and debugging tips
- **[IfcConvert Official Docs](https://docs.ifcopenshell.org/ifcconvert/usage.html)** - IfcOpenShell documentation

## API Usage

Submit a job via the API gateway:

```bash
curl -X POST http://localhost:8000/ifcconvert \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "input_filename": "/uploads/model.ifc",
    "output_filename": "/output/model.glb",
    "threads": 8,
    "use_world_coords": true
  }'
```

Response:
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

Check job status:
```bash
curl http://localhost:8000/jobs/550e8400-e29b-41d4-a716-446655440000/status \
  -H "Authorization: Bearer YOUR_TOKEN"
```

## Performance Tips

1. **Use Multiple Threads**: Set `threads` to CPU cores + 1 for optimal performance
2. **Enable Caching**: Use `cache: true` for repeated conversions of the same model
3. **Filter Early**: Use `include`/`exclude` to process only needed elements
4. **Adjust Mesher**: Lower deflection values increase quality but also processing time
5. **Disable Unused Features**: Turn off normals, UVs if not needed

## Environment Variables

The worker uses the following environment variables (set via docker-compose):

- `REDIS_HOST` - Redis server hostname
- `REDIS_PORT` - Redis server port
- `REDIS_QUEUE_NAME` - Queue name to listen on
- `POSTGRES_*` - Database connection settings

## Development

### Running Locally

```bash
cd ifcconvert-worker
docker build -t ifcconvert-worker .
docker run --env-file ../.env ifcconvert-worker
```

### Testing

Send a job via the API gateway:

```bash
curl -X POST http://localhost:8000/ifcconvert \
  -H "Content-Type: application/json" \
  -d '{
    "input_filename": "/uploads/test.ifc",
    "output_filename": "/output/test.obj",
    "threads": 4
  }'
```

## Files

- `tasks.py` - Main worker implementation with full IfcConvert argument support
- `Dockerfile` - Container configuration for the worker
- `requirements.txt` - Python dependencies

## Version

Based on IfcOpenShell IfcConvert 0.8.x

## License

This worker is part of the ifcpipeline project.

## References

- [IfcOpenShell Documentation](https://docs.ifcopenshell.org/)
- [IfcConvert CLI Manual](https://docs.ifcopenshell.org/ifcconvert/usage.html)
- [IFC Schema Documentation](https://standards.buildingsmart.org/IFC/)