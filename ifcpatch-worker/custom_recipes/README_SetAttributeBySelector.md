# SetAttributeBySelector Recipe

A powerful IFC patching recipe that sets attributes on elements using selector syntax, with support for both literal values and dynamic property-based values.

## Quick Start

### Basic Usage - Literal Values

Set the Name attribute to a fixed value:

```python
op1 = '{"selector": "IfcWall", "attribute": "Name", "value": "Standard Wall"}'
patcher = Patcher(ifc_file, logger, operation1=op1)
patcher.patch()
```

### Advanced Usage - Property Values

Populate the Name attribute from a property set value:

```python
op1 = '{"selector": "IfcWall", "attribute": "Name", "value": "Pset_WallCommon.Status"}'
patcher = Patcher(ifc_file, logger, operation1=op1)
patcher.patch()
```

This will:
- Find all walls
- Read the `Status` property from their `Pset_WallCommon` property set
- Set each wall's `Name` attribute to that property value (converted to string)
- Skip walls that don't have this property

## Features

✅ **Selector-based element selection** - Use powerful IfcOpenShell selector syntax  
✅ **Batch operations** - Process up to 5 operations in one execution  
✅ **Two value modes**:
  - **Literal values**: Set fixed values (strings, numbers, booleans)
  - **Property values**: Dynamically copy from property sets (format: `Pset.Property`)  
✅ **Automatic string conversion** - All values are converted to strings  
✅ **Official API** - Uses `ifcopenshell.api.attribute.edit_attributes` for compliance  
✅ **Smart handling** - Maintains ownership history and PredefinedType consistency  
✅ **Error handling** - Skips invalid elements/properties gracefully  
✅ **Progress logging** - Detailed statistics and progress tracking  

## Operation Format

Each operation is a JSON string with 3 required fields:

```json
{
  "selector": "IfcWall",              // Selector syntax
  "attribute": "Name",                // Attribute name to set
  "value": "My Value"                 // Literal value OR property path
}
```

### Value Types

#### 1. Literal Value (any type)
```json
"value": "My Wall"        // String
"value": 123              // Number (converted to "123")
"value": true             // Boolean (converted to "True")
```

#### 2. Property Path (string with dot)
```json
"value": "Pset_WallCommon.Status"           // Standard property set
"value": "Pset_DoorCommon.FireRating"       // Door property
"value": "CustomPset.CustomProperty"        // Custom property set
"value": "Qto_WallBaseQuantities.Height"    // Quantity set
```

The script automatically detects property paths (format: `PropertySetName.PropertyName`)

## Common Use Cases

### 1. Standardize Element Names from Properties
```python
# Copy reference numbers from property sets to Name
op1 = '{"selector": "IfcWall", "attribute": "Name", "value": "Pset_WallCommon.Reference"}'
op2 = '{"selector": "IfcDoor", "attribute": "Name", "value": "Pset_DoorCommon.Reference"}'
```

### 2. Auto-Generate Descriptions from Fire Ratings
```python
# Set door descriptions based on fire rating
op1 = '{"selector": "IfcDoor", "attribute": "Description", "value": "Pset_DoorCommon.FireRating"}'
```

### 3. Create Tags from Custom Properties
```python
# Generate tags from custom property values
op1 = '{"selector": "IfcBeam", "attribute": "Tag", "value": "CustomPset_Beam.ElementMark"}'
```

### 4. Mixed Mode Operations
```python
# Combine literal and property-based values
op1 = '{"selector": "IfcWall", "attribute": "ObjectType", "value": "Wall"}'  # Literal
op2 = '{"selector": "IfcWall", "attribute": "Description", "value": "Pset_WallCommon.Status"}'  # Property
```

### 5. Conditional Updates with Filters
```python
# Only update elements that have the property
op1 = '{"selector": "IfcDoor[Pset_DoorCommon.FireRating]", "attribute": "Description", "value": "Pset_DoorCommon.FireRating"}'
```

## Selector Syntax Examples

```python
"IfcWall"                               # All walls
".IfcDoor"                              # All doors (class selector)
"IfcWall[Name=Wall-001]"               # Wall with specific name
"IfcSlab[LoadBearing=TRUE]"            # Load-bearing slabs
"IfcDoor[Pset_DoorCommon.FireRating]"  # Doors with fire rating property
"IfcWall, IfcColumn"                   # Multiple types
```

See [IfcOpenShell Selector Documentation](https://docs.ifcopenshell.org/ifcopenshell-python/selector_syntax.html) for full syntax.

## Common Attributes

Attributes you can set on most IFC elements:

| Attribute | Description | Example |
|-----------|-------------|---------|
| `Name` | Element name | "Wall-001" |
| `Description` | Element description | "Fire-rated wall" |
| `ObjectType` | Custom type designation | "Structural Wall" |
| `Tag` | Element tag/mark | "W-01" |
| `LongName` | Long name (spaces/zones) | "Conference Room A" |
| `PredefinedType` | Type enumeration | "SOLIDWALL" |

## Property Path Behavior

### When Property Exists
```python
# Element has: Pset_WallCommon.Status = "Approved"
op1 = '{"selector": "IfcWall", "attribute": "Name", "value": "Pset_WallCommon.Status"}'
# Result: Name = "Approved"
```

### When Property Doesn't Exist
```python
# Element missing Pset_WallCommon.Status
op1 = '{"selector": "IfcWall", "attribute": "Name", "value": "Pset_WallCommon.Status"}'
# Result: Element is skipped (not modified)
```

### Data Type Conversion
All property values are automatically converted to strings:

- `"text value"` → `"text value"`
- `123` → `"123"`
- `45.67` → `"45.67"`
- `true` → `"True"`
- `false` → `"False"`
- `None` → Element skipped

## Complete Example

```python
import ifcopenshell
from custom_recipes.SetAttributeBySelector import Patcher
import logging

# Setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
ifc_file = ifcopenshell.open("input.ifc")

# Define operations
op1 = '{"selector": "IfcWall", "attribute": "Name", "value": "Pset_WallCommon.Reference"}'
op2 = '{"selector": "IfcDoor", "attribute": "Description", "value": "Pset_DoorCommon.FireRating"}'
op3 = '{"selector": "IfcWindow", "attribute": "Tag", "value": "WIN-"}'
op4 = '{"selector": "IfcSpace", "attribute": "LongName", "value": "Pset_SpaceCommon.Reference"}'

# Execute
patcher = Patcher(ifc_file, logger, 
                  operation1=op1, 
                  operation2=op2, 
                  operation3=op3, 
                  operation4=op4)
patcher.patch()

# Save
output = patcher.get_output()
output.write("output.ifc")

# Check stats
print(f"Modified {patcher.stats['elements_modified']} elements")
```

## Testing

Use the provided test script:

```bash
cd /home/bimbot-ubuntu/apps/ifcpipeline/ifcpatch-worker/custom_recipes
python test_SetAttributeBySelector.py input.ifc output.ifc
```

## Error Handling

The recipe handles errors gracefully:

- ❌ **Invalid JSON**: Skips operation with warning
- ❌ **Missing fields**: Skips operation with warning
- ❌ **Invalid selector**: Logs error
- ❌ **Attribute doesn't exist**: Skips element with warning
- ❌ **Property not found**: Skips element (debug log)
- ❌ **Invalid property path**: Treats as literal value

## Performance

- Processes 500+ elements with progress logging
- Efficient property lookups using `ifcopenshell.util.element.get_psets`
- Batch operations for optimal performance

## Logging Levels

- **INFO**: Operation progress, summary statistics
- **WARNING**: Invalid operations, missing attributes
- **DEBUG**: Missing properties on individual elements
- **ERROR**: Critical failures

## Tips & Best Practices

1. **Test first**: Always test on a copy of your IFC file
2. **Use filters**: Combine selectors with property filters for precision
3. **Check logs**: Review logs to see how many elements were skipped
4. **Validate output**: Manually verify a few elements after patching
5. **Property existence**: Use conditional selectors to ensure properties exist
6. **String conversion**: Remember all values become strings
7. **Batch operations**: Use multiple operations for efficiency

## Related Recipes

- **SetPropertyBySelector**: Set properties in property sets (not attributes)
- Both recipes use the same selector syntax but operate on different data

## Documentation

- [Full Examples](./SetAttributeBySelector_examples.md)
- [IfcOpenShell Attribute API](https://docs.ifcopenshell.org/autoapi/ifcopenshell/api/attribute/index.html)
- [Selector Syntax Guide](https://docs.ifcopenshell.org/ifcopenshell-python/selector_syntax.html)

## License

Part of the IFC Pipeline project.

## Author

IFC Pipeline Team  
Date: 2025-01-27

