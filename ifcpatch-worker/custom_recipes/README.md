# Custom IfcPatch Recipes

## Overview
This directory contains custom IfcPatch recipes for the IFC Pipeline. Custom recipes allow you to define your own IFC file transformations beyond the built-in recipes provided by IfcOpenShell.

## Creating a Custom Recipe

### Quick Start
1. Copy `example_recipe.py` to a new file
2. Rename the file to match your recipe name (e.g., `MyRecipe.py`)
3. Implement the `Patcher` class with your custom logic
4. Test your recipe locally before deployment

### Recipe Structure

Every custom recipe must follow this structure:

```python
import ifcopenshell
import logging

class Patcher:
    def __init__(self, file: ifcopenshell.file, logger: logging.Logger, *args):
        self.file = file
        self.logger = logger
        # Initialize your arguments here
    
    def patch(self) -> None:
        # Implement your patching logic here
        pass
    
    def get_output(self) -> ifcopenshell.file:
        # Return the modified IFC file
        return self.file
```

### Required Components

1. **Class Name**: Must be `Patcher`
2. **__init__ method**: Initialize with `file`, `logger`, and optional `*args`; store them as instance variables
3. **patch method**: Contains your transformation logic
4. **get_output method**: Returns the modified IFC file

### Best Practices

1. **Logging**: Use `self.logger` for all log messages
   ```python
   self.logger.info("Processing elements...")
   self.logger.warning("Element not found")
   self.logger.error("An error occurred", exc_info=True)
   ```

2. **Error Handling**: Wrap operations in try-except blocks
   ```python
   try:
       # Your code
   except Exception as e:
       self.logger.error(f"Error: {str(e)}", exc_info=True)
       raise
   ```

3. **Documentation**: Provide clear docstrings
   ```python
   """
   Brief description of what your recipe does.
   
   Parameters:
       param1: Description of parameter 1
       param2: Description of parameter 2
   
   Example:
       patcher = Patcher(ifc_file, logger, "value1", 123)
       patcher.patch()
   """
   ```

4. **Validation**: Validate your arguments in `__init__`
   ```python
   if not isinstance(arg1, str):
       raise ValueError("arg1 must be a string")
   ```

5. **Performance**: Consider performance for large models
   - Use bulk operations when possible
   - Avoid nested loops where possible
   - Log progress for long-running operations

## Using Your Custom Recipe

### Via API

```bash
curl -X POST "http://localhost:8000/patch/execute" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "input_file": "model.ifc",
    "output_file": "modified.ifc",
    "recipe": "YourRecipeName",
    "arguments": ["arg1", "arg2"],
    "use_custom": true
  }'
```

### Via Python

```python
import ifcopenshell
from YourRecipeName import Patcher

# Load IFC file
ifc_file = ifcopenshell.open("model.ifc")

# Create patcher
patcher = Patcher(ifc_file, logger, "arg1", "arg2")

# Execute patch
patcher.patch()

# Get result
result = patcher.get_output()

# Save result
result.write("modified.ifc")
```

## Available Custom Recipes

### MergeTasksFromPrevious
**Description**: Preserves IfcTask history across IFC model versions by re-injecting tasks from previous models and appending new tasks from diff results. Automatically generates "PM" property sets on affected elements with task history.  
**Author**: IFC Pipeline Team  
**Status**: Production Ready  
**Documentation**: [MERGE_TASKS_README.md](MERGE_TASKS_README.md)  
**Use Case**: Project management change tracking, maintaining PM history across model versions

### CeilingGrids
**Description**: Custom recipe for processing ceiling grids  
**Author**: IFC Pipeline Team  
**Status**: In Development

Add descriptions of your custom recipes here as you create them.

## Testing Your Recipe

1. **Unit Testing**: Test individual functions
2. **Integration Testing**: Test with real IFC files
3. **Schema Testing**: Test with different IFC schemas (IFC2X3, IFC4, IFC4X3)
4. **Edge Cases**: Test with empty models, large models, etc.

## Troubleshooting

### Common Issues

**Recipe not found**
- Ensure the file name matches the recipe name
- Check that the `Patcher` class is defined
- Restart the worker after adding new recipes

**Import errors**
- Verify all required packages are installed
- Check the requirements.txt file
- Ensure IfcOpenShell is properly installed

**Execution errors**
- Check worker logs: `docker-compose logs ifcpatch-worker`
- Verify your IFC file is valid
- Test with a simpler IFC model first

## Resources

- [IfcPatch Documentation](https://docs.ifcopenshell.org/autoapi/ifcpatch/index.html)
- [IfcOpenShell API](https://docs.ifcopenshell.org/)
- [Worker Creation Guide](../../WORKER_CREATION_GUIDE.md)
- [Implementation Plan](../../IFCPATCH_WORKER_IMPLEMENTATION_PLAN.md)

## Support

For questions or issues:
1. Check the example recipes
2. Review the implementation plan
3. Consult the IfcOpenShell documentation
4. Check worker logs for errors

