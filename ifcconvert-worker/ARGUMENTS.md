# IfcConvert Worker - Complete Argument Reference

This document describes all supported arguments for the IfcConvert worker, based on [IfcOpenShell IfcConvert 0.8.x](https://docs.ifcopenshell.org/ifcconvert/usage.html).

## Basic Usage

The worker accepts requests with the `IfcConvertRequest` model which includes support for ALL IfcConvert command-line arguments.

### Required Parameters

- `input_filename` (str): Path to the input IFC file
- `output_filename` (str): Path to the output file (format determined by extension)

## Supported Output Formats

- `.obj` - WaveFront OBJ (with .mtl material file)
- `.dae` - Collada Digital Assets Exchange
- `.glb` - Binary glTF v2.0
- `.stp` - STEP (Standard for the Exchange of Product Data)
- `.igs` - IGES (Initial Graphics Exchange Specification)
- `.xml` - XML (Property definitions and decomposition tree)
- `.svg` - SVG (Scalable Vector Graphics for 2D floor plans)
- `.h5` - HDF (Hierarchical Data Format)
- `.cityjson` - City JSON format for geospatial data
- `.ttl` - TTL/WKT (RDF Turtle with Well-Known-Text geometry)
- `.ifc` - IFC-SPF (Industry Foundation Classes)

## Command Line Options

### General Options

- `verbose` (bool, default: False): Enable verbose logging (-v flag)
- `quiet` (bool, default: False): Less status and progress output
- `yes` (bool, default: True): Auto-answer 'yes' to prompts
- `cache` (bool, default: False): Enable geometry caching
- `cache_file` (str, optional): Path to cache file
- `stderr_progress` (bool, default: False): Output progress to stderr
- `no_progress` (bool, default: False): Suppress progress indicators
- `log_format` (str, optional): Log format - "plain" or "json"
- `log_file` (str, optional): Custom log file path

## Geometry Options

### General Geometry Settings

- `kernel` (str, optional): Geometry kernel - "opencascade", "cgal", or "cgal-simple"
- `threads` (int, optional): Number of parallel processing threads (-j flag)
- `center_model` (bool, default: False): Center elements by placement center point
- `center_model_geometry` (bool, default: False): Center elements by mesh vertices center

### Filtering Options

**Entity/Attribute Filtering:**

- `include` (list[str], optional): List of entities/attributes to include
- `include_type` (str, optional): Type of include filter - "entities", "layers", or "attribute"
- `include_plus` (list[str], optional): Include with decomposition/containment
- `include_plus_type` (str, optional): Type for include+ filter
- `exclude` (list[str], optional): List of entities/attributes to exclude
- `exclude_type` (str, optional): Type of exclude filter
- `exclude_plus` (list[str], optional): Exclude with decomposition/containment
- `exclude_plus_type` (str, optional): Type for exclude+ filter
- `filter_file` (str, optional): Path to filter configuration file

**Examples:**
```python
# Include only walls and slabs
{
  "include": ["IfcWall", "IfcSlab"],
  "include_type": "entities"
}

# Include by GlobalId attribute
{
  "include": ["1yETHMphv6LwABqR4Pbs5g"],
  "include_type": "attribute",
}

# Include with decomposition (e.g., all objects on "Level 1")
{
  "include_plus": ["Level 1"],
  "include_plus_type": "attribute"
}
```

### Materials and Rendering

- `default_material_file` (str, optional): Material file for objects without materials
- `exterior_only` (str, optional): Export only exterior shell - "convex-decomposition", "minkowski-triangles", or "halfspace-snapping"
- `apply_default_materials` (bool, default: False): Apply default materials
- `use_material_names` (bool, default: False): Use material names instead of IDs
- `surface_colour` (bool, default: False): Prioritize surface color over diffuse

### Representation Types

- `plan` (bool, default: False): Include curves (Plan/Axis representations)
- `model` (bool, default: True): Include surfaces/solids (Body/Facetation)
- `dimensionality` (int, optional): Control curves and/or surfaces/solids inclusion

### Mesher Settings

- `mesher_linear_deflection` (float, optional): Linear deflection for curved surfaces detail
- `mesher_angular_deflection` (float, optional): Angular tolerance in radians (default: 0.5)
- `reorient_shells` (bool, default: False): Orient IfcConnectedFaceSet faces consistently

### Units and Precision

- `length_unit` (float, optional): Length unit multiplier
- `angle_unit` (float, optional): Angle unit multiplier
- `precision` (float, optional): Geometric precision
- `precision_factor` (float, optional): Tolerance multiplier for permissive operations
- `convert_back_units` (bool, default: False): Convert to original IFC units (not meters)

### Layer and Material Processing

- `layerset_first` (bool, default: False): Assign first layer material to complete product
- `enable_layerset_slicing` (bool, default: False): Enable slicing by IfcMaterialLayerSet

### Boolean Operations

- `disable_boolean_result` (bool, default: False): Disable IfcBooleanResult operations
- `disable_opening_subtractions` (bool, default: False): Disable IfcOpeningElement subtractions
- `merge_boolean_operands` (bool, default: False): Merge boolean operands
- `boolean_attempt_2d` (bool, default: False): Don't attempt 2D boolean processing
- `debug` (bool, default: False): Write boolean operands to file for debugging

### Wire and Edge Processing

- `no_wire_intersection_check` (bool, default: False): Skip wire intersection check
- `no_wire_intersection_tolerance` (float, optional): Set wire intersection tolerance
- `edge_arrows` (bool, default: False): Add arrow heads to edge segments

### Vertex and Shape Processing

- `weld_vertices` (bool, default: False): Weld vertices for manifold mesh
- `unify_shapes` (bool, default: False): Unify adjacent co-planar/co-linear subshapes
- `sew_shells` (bool, default: False): Sew shells together

### Coordinate Systems

- `use_world_coords` (bool, default: False): Apply placements directly to coordinates
- `building_local_placement` (bool, default: False): Place in IfcBuilding coordinate system
- `site_local_placement` (bool, default: False): Place in IfcSite coordinate system
- `model_offset` (str, optional): Arbitrary offset in "x,y,z" format
- `model_rotation` (str, optional): Quaternion rotation in "x,y,z,w" format

### Context and Output

- `context_ids` (list[str], optional): Specific context IDs to process
- `iterator_output` (int, optional): Iterator output type

### Normals and UVs

- `no_normals` (bool, default: False): Disable normal computation
- `generate_uvs` (bool, default: False): Generate UV coordinates via box projection

### Validation and Hierarchy

- `validate` (bool, default: False): Check geometry against explicit quantities
- `element_hierarchy` (bool, default: False): Assign elements using parent hierarchy

### Spaces and Bounding Boxes

- `force_space_transparency` (float, optional): Override space transparency
- `keep_bounding_boxes` (bool, default: False): Don't remove IfcBoundingBox

### CGAL Specific

- `circle_segments` (int, optional): Segments for full circle approximation (default: 16)

### Function Curves

- `function_step_type` (int, optional): Step size method for function-based curves
- `function_step_param` (float, optional): Step size parameter value

### Performance

- `no_parallel_mapping` (bool, default: False): Disable parallel mapping

### Triangulation

- `triangulation_type` (int, optional): Type of planar facet to emit

## Serialization Options

### SVG Specific Options

- `bounds` (str, optional): Bounding rectangle (e.g., "512x512")
- `scale` (str, optional): Scale ratio (e.g., "1:100")
- `center` (str, optional): Center location (e.g., "0.5x0.5")
- `section_ref` (str, optional): Element for cross sections
- `elevation_ref` (str, optional): Element for elevation drawings
- `elevation_ref_guid` (list[str], optional): Element GUIDs for drawings
- `auto_section` (bool, default: False): Auto-create cross section drawings
- `auto_elevation` (bool, default: False): Auto-create elevation drawings
- `draw_storey_heights` (str, optional): Draw storey height lines - "full", "left", or "none"
- `storey_height_line_length` (float, optional): Line length for storey heights
- `svg_xmlns` (bool, default: False): Use namespace for name/guid
- `svg_poly` (bool, default: False): Use polygonal HLR algorithm
- `svg_prefilter` (bool, default: False): Prefilter faces/shapes
- `svg_segment_projection` (bool, default: False): Segment projection results
- `svg_write_poly` (bool, default: False): Approximate curves as polygonal
- `svg_project` (bool, default: False): Always enable HLR rendering
- `svg_without_storeys` (bool, default: False): Don't emit storey drawings
- `svg_no_css` (bool, default: False): Don't emit CSS declarations
- `door_arcs` (bool, default: False): Draw door opening arcs
- `section_height` (float, optional): Cut section height for 2D geometry
- `section_height_from_storeys` (bool, default: False): Derive section height from storey
- `print_space_names` (bool, default: False): Print IfcSpace names
- `print_space_areas` (bool, default: False): Print calculated IfcSpace areas
- `space_name_transform` (str, optional): Transform for space labels

### Naming Conventions

- `use_element_names` (bool, default: False): Use IfcRoot.Name for naming
- `use_element_guids` (bool, default: False): Use IfcRoot.GlobalId for naming
- `use_element_step_ids` (bool, default: False): Use numeric step identifier for naming
- `use_element_types` (bool, default: False): Use element types for naming

### Coordinate System and Format

- `y_up` (bool, default: False): Change 'up' axis to positive Y (default is Z)
- `ecef` (bool, default: False): Write glTF in Earth-Centered Earth-Fixed coordinates

### Precision

- `digits` (int, optional): Floating-point precision (default: 15)

### RDF/WKT

- `base_uri` (str, optional): Base URI for RDF-based serializations
- `wkt_use_section` (bool, default: False): Use geometrical section for TTL WKT

## Example Usage

### Basic Conversion with Multiple Threads

```json
{
  "input_filename": "/uploads/model.ifc",
  "output_filename": "/output/model.glb",
  "threads": 8
}
```

### Convert Only Walls to OBJ with World Coordinates

```json
{
  "input_filename": "/uploads/building.ifc",
  "output_filename": "/output/walls.obj",
  "include": ["IfcWall"],
  "include_type": "entities",
  "use_world_coords": true,
  "threads": 4
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
  "print_space_areas": true,
  "threads": 4
}
```

### High-Quality STEP Export with Original Units

```json
{
  "input_filename": "/uploads/model.ifc",
  "output_filename": "/output/model.stp",
  "convert_back_units": true,
  "mesher_linear_deflection": 0.0001,
  "mesher_angular_deflection": 0.1,
  "threads": 8
}
```

### Centered Model with Offset

```json
{
  "input_filename": "/uploads/large-model.ifc",
  "output_filename": "/output/model.glb",
  "center_model": true,
  "model_offset": "10000,10000,0"
}
```

## Notes

1. **Thread Count**: For best performance, set `threads` to your CPU core count + 1
2. **Memory Usage**: Large models with high detail settings may require significant RAM
3. **Filtering**: Include/exclude filters cannot be placed right before input file in actual CLI, but the worker handles this correctly
4. **Log Files**: If not specified, logs are automatically created in `/output/converted/`
5. **Default Behavior**: By default, IfcConvert converts units to meters. Use `convert_back_units: true` to preserve original units

## API Reference

For the complete API reference and IfcOpenShell documentation, see:
- [IfcConvert Usage Documentation](https://docs.ifcopenshell.org/ifcconvert/usage.html)
- [IfcOpenShell Documentation](https://docs.ifcopenshell.org/)


