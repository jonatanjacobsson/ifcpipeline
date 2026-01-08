# IfcConvert Worker - Usage Examples

This document provides practical examples for using the IfcConvert worker with various common scenarios.

## Table of Contents

1. [Basic Conversions](#basic-conversions)
2. [Filtering and Selection](#filtering-and-selection)
3. [SVG Floor Plans](#svg-floor-plans)
4. [Advanced Geometry Processing](#advanced-geometry-processing)
5. [Performance Optimization](#performance-optimization)
6. [Coordinate System Handling](#coordinate-system-handling)

---

## Basic Conversions

### Simple IFC to glTF Conversion

```json
{
  "input_filename": "/uploads/model.ifc",
  "output_filename": "/output/model.glb",
  "threads": 8
}
```

### IFC to OBJ with Multiple Threads

```json
{
  "input_filename": "/uploads/building.ifc",
  "output_filename": "/output/building.obj",
  "threads": 7,
  "yes": true
}
```

### IFC to STEP with Original Units

```json
{
  "input_filename": "/uploads/mechanical.ifc",
  "output_filename": "/output/mechanical.stp",
  "convert_back_units": true,
  "threads": 4
}
```

### IFC to Collada (DAE)

```json
{
  "input_filename": "/uploads/architecture.ifc",
  "output_filename": "/output/architecture.dae",
  "threads": 8,
  "element_hierarchy": true,
  "use_element_names": true
}
```

---

## Filtering and Selection

### Convert Only Walls

```json
{
  "input_filename": "/uploads/building.ifc",
  "output_filename": "/output/walls.glb",
  "include": ["IfcWall"],
  "include_type": "entities",
  "threads": 4
}
```

### Convert Walls and Slabs

```json
{
  "input_filename": "/uploads/structure.ifc",
  "output_filename": "/output/structure.obj",
  "include": ["IfcWall", "IfcSlab"],
  "include_type": "entities",
  "verbose": true,
  "threads": 8
}
```

### Exclude Spaces and Openings

```json
{
  "input_filename": "/uploads/full-model.ifc",
  "output_filename": "/output/clean-model.glb",
  "exclude": ["IfcOpeningElement", "IfcSpace"],
  "exclude_type": "entities",
  "threads": 8
}
```

### Filter by GlobalId

```json
{
  "input_filename": "/uploads/building.ifc",
  "output_filename": "/output/specific-elements.obj",
  "include": ["1yETHMphv6LwABqR4Pbs5g", "1yETHMphv6LwABqR0Pbs5g"],
  "include_type": "attribute GlobalId",
  "threads": 4
}
```

### Include All Objects on a Specific Level

```json
{
  "input_filename": "/uploads/multi-storey.ifc",
  "output_filename": "/output/level-1.glb",
  "include_plus": ["Level 1"],
  "include_plus_type": "attribute Name",
  "verbose": true,
  "threads": 8
}
```

---

## SVG Floor Plans

### Basic Floor Plan

```json
{
  "input_filename": "/uploads/building.ifc",
  "output_filename": "/output/floorplan.svg",
  "exclude": ["IfcOpeningElement", "IfcSpace"],
  "exclude_type": "entities",
  "threads": 4
}
```

### Floor Plan with Dimensions

```json
{
  "input_filename": "/uploads/building.ifc",
  "output_filename": "/output/floorplan-sized.svg",
  "bounds": "1024x768",
  "exclude": ["IfcOpeningElement"],
  "exclude_type": "entities",
  "threads": 4
}
```

### Floor Plan with Space Labels

```json
{
  "input_filename": "/uploads/offices.ifc",
  "output_filename": "/output/offices-labeled.svg",
  "print_space_names": true,
  "print_space_areas": true,
  "exclude": ["IfcOpeningElement"],
  "exclude_type": "entities",
  "bounds": "2048x1536",
  "threads": 4
}
```

### Scaled Floor Plan (1:100)

```json
{
  "input_filename": "/uploads/building.ifc",
  "output_filename": "/output/floorplan-scaled.svg",
  "bounds": "1024x768",
  "scale": "1:100",
  "center": "0.5x0.5",
  "print_space_names": true,
  "exclude": ["IfcOpeningElement", "IfcSpace"],
  "exclude_type": "entities"
}
```

### Auto-Generated Sections and Elevations

```json
{
  "input_filename": "/uploads/building.ifc",
  "output_filename": "/output/drawings.svg",
  "auto_section": true,
  "auto_elevation": true,
  "draw_storey_heights": "full",
  "exclude": ["IfcOpeningElement"],
  "exclude_type": "entities",
  "threads": 4
}
```

### Floor Plan with Door Arcs

```json
{
  "input_filename": "/uploads/residential.ifc",
  "output_filename": "/output/floorplan-doors.svg",
  "door_arcs": true,
  "print_space_names": true,
  "exclude": ["IfcOpeningElement"],
  "exclude_type": "entities",
  "bounds": "1200x900"
}
```

---

## Advanced Geometry Processing

### High-Quality Export

```json
{
  "input_filename": "/uploads/facade.ifc",
  "output_filename": "/output/facade-hq.obj",
  "mesher_linear_deflection": 0.0001,
  "mesher_angular_deflection": 0.1,
  "generate_uvs": true,
  "threads": 8
}
```

### Manifold Mesh for 3D Printing

```json
{
  "input_filename": "/uploads/component.ifc",
  "output_filename": "/output/component-print.stl",
  "weld_vertices": true,
  "reorient_shells": true,
  "sew_shells": true,
  "mesher_linear_deflection": 0.001,
  "threads": 4
}
```

### Exterior Shell Only

```json
{
  "input_filename": "/uploads/building.ifc",
  "output_filename": "/output/exterior.obj",
  "exterior_only": "minkowski-triangles",
  "threads": 8
}
```

### Simplified Geometry (No Normals)

```json
{
  "input_filename": "/uploads/large-model.ifc",
  "output_filename": "/output/simplified.obj",
  "no_normals": true,
  "mesher_linear_deflection": 0.01,
  "threads": 8
}
```

### With Material Names

```json
{
  "input_filename": "/uploads/building.ifc",
  "output_filename": "/output/building-materials.obj",
  "use_material_names": true,
  "apply_default_materials": true,
  "threads": 8
}
```

---

## Performance Optimization

### Fast Preview (Low Quality)

```json
{
  "input_filename": "/uploads/large-building.ifc",
  "output_filename": "/output/preview.glb",
  "mesher_linear_deflection": 0.1,
  "mesher_angular_deflection": 1.0,
  "no_normals": true,
  "threads": 8
}
```

### Cached Conversion

```json
{
  "input_filename": "/uploads/model.ifc",
  "output_filename": "/output/model.glb",
  "cache": true,
  "cache_file": "/cache/model.cache",
  "threads": 8
}
```

### Selective Element Processing

```json
{
  "input_filename": "/uploads/complex.ifc",
  "output_filename": "/output/structure-only.obj",
  "include": ["IfcWall", "IfcSlab", "IfcBeam", "IfcColumn"],
  "include_type": "entities",
  "disable_opening_subtractions": true,
  "threads": 8
}
```

---

## Coordinate System Handling

### World Coordinates

```json
{
  "input_filename": "/uploads/building.ifc",
  "output_filename": "/output/world-coords.obj",
  "use_world_coords": true,
  "threads": 8
}
```

### Centered Model

```json
{
  "input_filename": "/uploads/site.ifc",
  "output_filename": "/output/centered.glb",
  "center_model": true,
  "threads": 8
}
```

### Manual Offset

```json
{
  "input_filename": "/uploads/large-site.ifc",
  "output_filename": "/output/offset.glb",
  "model_offset": "500000,6000000,0",
  "threads": 8
}
```

### Site Local Placement

```json
{
  "input_filename": "/uploads/site-plan.ifc",
  "output_filename": "/output/site-local.stp",
  "site_local_placement": true,
  "convert_back_units": true,
  "threads": 4
}
```

### Building Local Placement

```json
{
  "input_filename": "/uploads/building.ifc",
  "output_filename": "/output/building-local.obj",
  "building_local_placement": true,
  "threads": 8
}
```

### ECEF Coordinates (glTF)

```json
{
  "input_filename": "/uploads/georeferenced.ifc",
  "output_filename": "/output/georef.glb",
  "ecef": true,
  "threads": 8
}
```

---

## Special Use Cases

### Y-Up Coordinate System (for Unity/Maya)

```json
{
  "input_filename": "/uploads/game-asset.ifc",
  "output_filename": "/output/asset.obj",
  "y_up": true,
  "weld_vertices": true,
  "threads": 4
}
```

### Named Elements with GUIDs

```json
{
  "input_filename": "/uploads/bim-model.ifc",
  "output_filename": "/output/named.dae",
  "use_element_names": true,
  "use_element_guids": true,
  "element_hierarchy": true,
  "threads": 8
}
```

### RDF/Turtle with WKT Geometry

```json
{
  "input_filename": "/uploads/building.ifc",
  "output_filename": "/output/building.ttl",
  "base_uri": "http://example.org/building/",
  "wkt_use_section": true
}
```

### CityJSON for GIS

```json
{
  "input_filename": "/uploads/urban-model.ifc",
  "output_filename": "/output/city.json",
  "use_world_coords": true,
  "threads": 8
}
```

### XML Property Export

```json
{
  "input_filename": "/uploads/model.ifc",
  "output_filename": "/output/properties.xml"
}
```

---

## Debugging and Validation

### Verbose Logging

```json
{
  "input_filename": "/uploads/problematic.ifc",
  "output_filename": "/output/test.obj",
  "verbose": true,
  "log_format": "json",
  "threads": 4
}
```

### Validate Geometry Against Quantities

```json
{
  "input_filename": "/uploads/model.ifc",
  "output_filename": "/output/validated.glb",
  "validate": true,
  "verbose": true,
  "threads": 8
}
```

### Debug Boolean Operations

```json
{
  "input_filename": "/uploads/complex-boolean.ifc",
  "output_filename": "/output/debug.obj",
  "debug": true,
  "verbose": true,
  "threads": 4
}
```

---

## API Usage

All examples can be sent via HTTP POST to the API gateway. See the [main README](./README.md#api-usage) for detailed API documentation.

---

## Python Client Example

For Python client integration examples, see the [main README](./README.md#api-usage) and [troubleshooting guide](./docs/TROUBLESHOOTING.md).

---

## Tips

1. **Start Simple**: Begin with basic conversions and add options as needed
2. **Use Filtering**: Filter early to reduce processing time for large models
3. **Thread Count**: Set to your CPU cores + 1 for best performance
4. **Mesher Settings**: Lower deflection = higher quality but slower processing
5. **Caching**: Enable for repeated conversions of the same model
6. **Logging**: Use verbose mode when debugging conversion issues
7. **Test Small**: Test with a small subset before processing entire models

## See Also

- [ARGUMENTS.md](./ARGUMENTS.md) - Complete argument reference
- [README.md](./README.md) - Worker overview and quick start
- [docs/TROUBLESHOOTING.md](./docs/TROUBLESHOOTING.md) - Debugging and troubleshooting
- [IfcConvert Documentation](https://docs.ifcopenshell.org/ifcconvert/usage.html) - Official IfcOpenShell docs


