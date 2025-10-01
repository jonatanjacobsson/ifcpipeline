# Dynamic Parameter Inspection for IfcPatch Recipes

## Overview

This implementation adds automatic parameter extraction from IfcPatch recipes using Python's `inspect` module. The system dynamically discovers recipe parameters from their `__init__` method signatures and docstrings.

## How It Works

### 1. Parameter Extraction (`extract_recipe_parameters`)

The system inspects each recipe's `Patcher` class `__init__` method to extract:

- **Parameter names**: From the function signature
- **Parameter types**: From type hints (e.g., `str`, `bool`, `Union[str, None]`)
- **Descriptions**: Parsed from docstring `:param` sections
- **Default values**: From parameter defaults in the signature
- **Required flag**: Whether the parameter has a default value

### 2. Docstring Parsing (`parse_docstring_params`)

Parses parameter descriptions from docstrings using the reStructuredText format:

```python
:param query: A query to select the subset of IFC elements.
:param assume_asset_uniqueness_by_name: Avoid adding assets with the same name...
```

### 3. Type Formatting (`format_type_annotation`)

Converts Python type annotations to readable strings:

- `str` → `"str"`
- `bool` → `"bool"`
- `Union[str, None]` → `"Optional[str]"`
- `typing.List[str]` → `"List[str]"`

## Example Output

For the `ExtractElements` recipe:

```json
{
  "name": "ExtractElements",
  "description": "Extract certain elements into a new model",
  "is_custom": false,
  "parameters": [
    {
      "name": "query",
      "type": "str",
      "required": false,
      "default": "IfcWall",
      "description": "A query to select the subset of IFC elements."
    },
    {
      "name": "assume_asset_uniqueness_by_name",
      "type": "bool",
      "required": false,
      "default": true,
      "description": "Avoid adding assets (profiles, materials, styles) with the same name multiple times. Which helps in avoiding duplicated assets."
    }
  ]
}
```

## API Endpoint

The `/patch/recipes/list` endpoint now returns enhanced recipe information:

```bash
curl -X POST http://localhost:8000/patch/recipes/list \
  -H "Content-Type: application/json" \
  -d '{"include_builtin": true, "include_custom": true}'
```

## n8n Node Integration

The n8n IfcPatch node uses this information to:

1. Display parameter count in recipe dropdown: `ExtractElements (2 params)`
2. Show recipe-specific input fields when a recipe is selected
3. Provide proper types, descriptions, and default values for each parameter

### Supported Recipes with Custom UI

Currently, the n8n node has explicit parameter definitions for:

- **ExtractElements**: Query (string), Assume Asset Uniqueness By Name (boolean)
- **ConvertLengthUnit**: Target Unit (dropdown: METRE, MILLIMETRE, FOOT, INCH)

All other recipes use a generic arguments collection.

## Adding Support for New Recipes

To add explicit parameter support for a recipe in the n8n node:

1. Add parameter fields in `IfcPatch.node.ts` properties array:

```typescript
{
    displayName: 'Parameter Name',
    name: 'param_parametername',
    type: 'string', // or 'boolean', 'number', 'options'
    displayOptions: {
        show: {
            recipeName: ['YourRecipeName'],
        },
    },
    default: 'defaultValue',
    description: 'Parameter description',
}
```

2. Add handling in the execute method:

```typescript
else if (recipeName === 'YourRecipeName') {
    const param1 = this.getNodeParameter('param_parameter1', i, 'default') as string;
    const param2 = this.getNodeParameter('param_parameter2', i, true) as boolean;
    args.push(param1, param2);
}
```

## Benefits

1. **Automatic Discovery**: No manual maintenance of recipe parameter lists
2. **Rich Metadata**: Types, descriptions, defaults automatically extracted
3. **Better UX**: Users see exactly what parameters each recipe needs
4. **Type Safety**: Parameter types are preserved and validated
5. **Documentation**: Descriptions come directly from recipe docstrings

## Implementation Details

### Files Modified

1. **`ifc-pipeline/ifcpatch-worker/tasks.py`**:
   - Added `parse_docstring_params()` function
   - Added `format_type_annotation()` function  
   - Added `extract_recipe_parameters()` function
   - Updated `list_available_recipes()` to use inspection

2. **`n8n-nodes-ifcpipeline/nodes/IfcPatch/IfcPatch.node.ts`**:
   - Added parameter count to recipe dropdown labels
   - Added explicit parameter fields for common recipes
   - Updated execute method to build arguments from named parameters
   - Kept fallback generic argument collection for other recipes

### Dependencies

- `inspect` (Python built-in)
- `typing` with `get_type_hints`, `get_origin`, `get_args`
- `typing-extensions` (already in requirements.txt)

## Testing

Test the parameter extraction:

```python
# In Python shell or test script
from tasks import extract_recipe_parameters
import ifcpatch.recipes.ExtractElements as ExtractElements

params = extract_recipe_parameters(ExtractElements.Patcher)
print(params)
```

Expected output:
```python
[
    {
        'name': 'query',
        'type': 'str',
        'required': False,
        'default': 'IfcWall',
        'description': 'A query to select the subset of IFC elements.'
    },
    {
        'name': 'assume_asset_uniqueness_by_name',
        'type': 'bool',
        'required': False,
        'default': True,
        'description': 'Avoid adding assets (profiles, materials, styles) with the same name multiple times...'
    }
]
```

## Future Enhancements

1. **Full Dynamic UI**: Generate all n8n parameter fields dynamically (requires n8n core changes)
2. **Parameter Validation**: Validate parameter types and values before sending to API
3. **Autocomplete**: Use parameter metadata for autocomplete suggestions
4. **Recipe Examples**: Extract example code from docstrings
5. **Parameter Groups**: Group related parameters together

