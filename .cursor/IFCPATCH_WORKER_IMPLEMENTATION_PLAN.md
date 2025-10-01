# IfcPatch Worker Implementation Plan

## Executive Summary

This document outlines the implementation plan for an **ifcpatch-worker** that:
1. Supports all built-in IfcPatch recipes from the IfcOpenShell library
2. Allows custom user-defined recipes
3. Integrates seamlessly with the IFC Pipeline architecture
4. Provides flexible recipe discovery and execution

---

## Table of Contents

- [Overview](#overview)
- [Architecture Design](#architecture-design)
- [Implementation Phases](#implementation-phases)
- [Technical Specifications](#technical-specifications)
- [Custom Recipe Development](#custom-recipe-development)
- [API Design](#api-design)
- [Testing Strategy](#testing-strategy)
- [Deployment Plan](#deployment-plan)

---

## Overview

### What is IfcPatch?

According to the [IfcOpenShell documentation](https://docs.ifcopenshell.org/autoapi/ifcpatch/index.html), IfcPatch is a utility for applying modifications to IFC files using predefined "recipes". Each recipe performs specific transformations like:
- Extracting elements
- Converting units
- Optimizing files
- Merging projects
- Migrating schemas
- And many more operations

### Project Goals

1. **Built-in Recipe Support**: Execute any recipe from [ifcpatch.recipes](https://docs.ifcopenshell.org/autoapi/ifcpatch/recipes/index.html)
2. **Custom Recipe Support**: Allow users to define and execute custom recipes
3. **API Integration**: Seamless integration with the existing API Gateway
4. **Scalability**: Handle multiple concurrent patch operations
5. **Maintainability**: Clear structure for adding new custom recipes

---

## Architecture Design

### Component Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        API Gateway                          │
│  - Recipe validation                                        │
│  - Job enqueueing                                           │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│                      Redis Queue                            │
│  Queue: ifcpatch                                            │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│                   IfcPatch Worker                           │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ Recipe Discovery System                               │  │
│  │  - Built-in recipes (from ifcpatch.recipes)          │  │
│  │  - Custom recipes (/app/custom_recipes/)             │  │
│  └───────────────────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ Recipe Executor                                       │  │
│  │  - Load recipe class                                 │  │
│  │  - Validate arguments                                │  │
│  │  - Execute patch                                     │  │
│  │  - Return results                                    │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│                    Shared Volumes                           │
│  /uploads       - Input IFC files                          │
│  /output/patch  - Patched IFC files                        │
└─────────────────────────────────────────────────────────────┘
```

### Directory Structure

```
ifc-pipeline/
├── ifcpatch-worker/
│   ├── tasks.py                    # Main worker logic
│   ├── recipe_loader.py            # Recipe discovery and loading
│   ├── custom_recipes/             # User-defined custom recipes
│   │   ├── __init__.py
│   │   ├── README.md              # Guide for creating custom recipes
│   │   └── example_recipe.py      # Example custom recipe template
│   ├── Dockerfile
│   └── requirements.txt
├── shared/
│   └── classes.py                  # Add IfcPatchRequest
└── docker-compose.yml              # Add ifcpatch-worker service
```

---

## Implementation Phases

### Phase 1: Core Worker Setup (Week 1)

**Objectives:**
- Set up basic worker structure
- Implement built-in recipe support
- Create request/response models

**Deliverables:**
- Basic `tasks.py` with recipe execution
- `Dockerfile` with ifcpatch installed
- Request model in `shared/classes.py`
- API endpoint in API Gateway

### Phase 2: Custom Recipe System (Week 2)

**Objectives:**
- Implement recipe discovery system
- Create custom recipe loader
- Develop custom recipe template

**Deliverables:**
- `recipe_loader.py` module
- Custom recipe directory structure
- Example custom recipes
- Documentation for recipe development

### Phase 3: Advanced Features (Week 3)

**Objectives:**
- Add recipe validation
- Implement recipe documentation extraction
- Add recipe listing endpoint

**Deliverables:**
- Recipe validation system
- `/patch/recipes` endpoint (list available recipes)
- Recipe introspection capabilities

### Phase 4: Testing & Documentation (Week 4)

**Objectives:**
- Comprehensive testing
- Documentation
- Performance optimization

**Deliverables:**
- Unit tests for recipes
- Integration tests
- Performance benchmarks
- User documentation

---

## Technical Specifications

### 1. Request Model

Add to `shared/classes.py`:

```python
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field

class IfcPatchRequest(BaseModel):
    """Request model for IfcPatch operations"""
    input_file: str = Field(..., description="Input IFC filename in /uploads")
    output_file: str = Field(..., description="Output IFC filename")
    recipe: str = Field(..., description="Recipe name (built-in or custom)")
    arguments: Optional[List[Any]] = Field(default=[], description="Recipe-specific arguments")
    use_custom: bool = Field(default=False, description="Whether to use custom recipe")
    
    class Config:
        schema_extra = {
            "example": {
                "input_file": "model.ifc",
                "output_file": "model_patched.ifc",
                "recipe": "ExtractElements",
                "arguments": [".IfcWall"],
                "use_custom": False
            }
        }

class IfcPatchListRecipesRequest(BaseModel):
    """Request to list available recipes"""
    include_custom: bool = Field(default=True, description="Include custom recipes")
    include_builtin: bool = Field(default=True, description="Include built-in recipes")

class RecipeInfo(BaseModel):
    """Information about a recipe"""
    name: str
    description: str
    is_custom: bool
    parameters: List[Dict[str, Any]]
    output_type: Optional[str] = None

class IfcPatchListRecipesResponse(BaseModel):
    """Response with available recipes"""
    recipes: List[RecipeInfo]
    total_count: int
    builtin_count: int
    custom_count: int
```

### 2. Worker Implementation - tasks.py

```python
import logging
import os
import sys
import importlib
import ifcopenshell
import ifcpatch
from pathlib import Path
from typing import Dict, Any, List
from shared.classes import IfcPatchRequest, RecipeInfo

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add custom recipes directory to path
CUSTOM_RECIPES_DIR = Path("/app/custom_recipes")
sys.path.insert(0, str(CUSTOM_RECIPES_DIR))

def discover_custom_recipes() -> List[str]:
    """
    Discover all custom recipes in the custom_recipes directory.
    
    Returns:
        List of custom recipe names (without .py extension)
    """
    if not CUSTOM_RECIPES_DIR.exists():
        logger.warning(f"Custom recipes directory not found: {CUSTOM_RECIPES_DIR}")
        return []
    
    recipes = []
    for file in CUSTOM_RECIPES_DIR.glob("*.py"):
        if file.stem not in ["__init__", "example_recipe"]:
            recipes.append(file.stem)
    
    logger.info(f"Discovered {len(recipes)} custom recipes: {recipes}")
    return recipes

def load_custom_recipe(recipe_name: str):
    """
    Dynamically load a custom recipe module.
    
    Args:
        recipe_name: Name of the custom recipe (without .py)
    
    Returns:
        The Patcher class from the custom recipe module
    """
    try:
        module = importlib.import_module(recipe_name)
        
        # Find the Patcher class in the module
        if hasattr(module, 'Patcher'):
            return module.Patcher
        else:
            raise AttributeError(f"Custom recipe '{recipe_name}' must define a 'Patcher' class")
    
    except ImportError as e:
        logger.error(f"Failed to import custom recipe '{recipe_name}': {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Error loading custom recipe '{recipe_name}': {str(e)}")
        raise

def run_ifcpatch(job_data: dict) -> dict:
    """
    Execute an IfcPatch recipe on an IFC file.
    
    Args:
        job_data: Dictionary containing job parameters conforming to IfcPatchRequest.
        
    Returns:
        Dictionary containing the operation results.
    """
    try:
        request = IfcPatchRequest(**job_data)
        logger.info(f"Starting IfcPatch job: recipe='{request.recipe}', input='{request.input_file}'")
        
        # Define paths
        models_dir = "/uploads"
        output_dir = "/output/patch"
        input_path = os.path.join(models_dir, request.input_file)
        output_path = os.path.join(output_dir, request.output_file)
        
        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)
        
        # Validate input file
        if not os.path.exists(input_path):
            logger.error(f"Input IFC file not found: {input_path}")
            raise FileNotFoundError(f"Input file {request.input_file} not found")
        
        logger.info(f"Input file found: {input_path}")
        
        # Load IFC file
        logger.info("Loading IFC file...")
        ifc_file = ifcopenshell.open(input_path)
        logger.info(f"IFC file loaded: schema={ifc_file.schema}, elements={len(ifc_file)}")
        
        # Prepare ifcpatch arguments
        patch_args = {
            "input": input_path,
            "file": ifc_file,
            "recipe": request.recipe,
            "arguments": request.arguments or []
        }
        
        # If using custom recipe, load it
        if request.use_custom:
            logger.info(f"Loading custom recipe: {request.recipe}")
            custom_patcher = load_custom_recipe(request.recipe)
            
            # Instantiate and execute custom patcher
            patcher_instance = custom_patcher(ifc_file, logger)
            patcher_instance.patch()
            output = patcher_instance.get_output()
            
        else:
            logger.info(f"Executing built-in recipe: {request.recipe}")
            # Execute built-in recipe using ifcpatch.execute()
            output = ifcpatch.execute(patch_args)
        
        # Write output
        logger.info(f"Writing output to: {output_path}")
        ifcpatch.write(output, output_path)
        
        # Verify output file was created
        if not os.path.exists(output_path):
            raise RuntimeError("Output file was not created successfully")
        
        output_size = os.path.getsize(output_path)
        logger.info(f"IfcPatch completed successfully. Output size: {output_size} bytes")
        
        return {
            "success": True,
            "message": f"Successfully applied recipe '{request.recipe}'",
            "output_path": output_path,
            "recipe": request.recipe,
            "is_custom": request.use_custom,
            "output_size_bytes": output_size,
            "arguments_used": request.arguments
        }
    
    except FileNotFoundError as e:
        logger.error(f"File not found error: {str(e)}", exc_info=True)
        raise
    except AttributeError as e:
        logger.error(f"Recipe error: {str(e)}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Error during IfcPatch execution: {str(e)}", exc_info=True)
        raise

def list_available_recipes(job_data: dict) -> dict:
    """
    List all available IfcPatch recipes (built-in and custom).
    
    Args:
        job_data: Dictionary containing filter parameters
        
    Returns:
        Dictionary with recipe information
    """
    try:
        from shared.classes import IfcPatchListRecipesRequest
        request = IfcPatchListRecipesRequest(**job_data)
        
        recipes = []
        
        # Get built-in recipes
        if request.include_builtin:
            logger.info("Discovering built-in recipes...")
            try:
                import ifcpatch.recipes
                recipes_module = ifcpatch.recipes
                
                # Get all recipe modules
                for item_name in dir(recipes_module):
                    if not item_name.startswith('_'):
                        try:
                            # Try to extract documentation
                            recipe_doc = ifcpatch.extract_docs(
                                'ifcpatch.recipes',
                                item_name
                            )
                            
                            if recipe_doc:
                                recipes.append({
                                    "name": item_name,
                                    "description": recipe_doc.get('description', 'No description available'),
                                    "is_custom": False,
                                    "parameters": list(recipe_doc.get('inputs', {}).values()),
                                    "output_type": recipe_doc.get('output')
                                })
                        except Exception as e:
                            logger.debug(f"Could not extract docs for {item_name}: {str(e)}")
                            # Add minimal info
                            recipes.append({
                                "name": item_name,
                                "description": "Built-in IfcPatch recipe",
                                "is_custom": False,
                                "parameters": [],
                                "output_type": None
                            })
            except Exception as e:
                logger.error(f"Error discovering built-in recipes: {str(e)}")
        
        # Get custom recipes
        if request.include_custom:
            logger.info("Discovering custom recipes...")
            custom_recipe_names = discover_custom_recipes()
            
            for recipe_name in custom_recipe_names:
                try:
                    module = importlib.import_module(recipe_name)
                    doc = module.__doc__ or "Custom recipe (no description)"
                    
                    recipes.append({
                        "name": recipe_name,
                        "description": doc.strip(),
                        "is_custom": True,
                        "parameters": [],
                        "output_type": "ifcopenshell.file"
                    })
                except Exception as e:
                    logger.error(f"Error loading custom recipe '{recipe_name}': {str(e)}")
        
        builtin_count = sum(1 for r in recipes if not r['is_custom'])
        custom_count = sum(1 for r in recipes if r['is_custom'])
        
        logger.info(f"Found {len(recipes)} total recipes (built-in: {builtin_count}, custom: {custom_count})")
        
        return {
            "success": True,
            "recipes": recipes,
            "total_count": len(recipes),
            "builtin_count": builtin_count,
            "custom_count": custom_count
        }
    
    except Exception as e:
        logger.error(f"Error listing recipes: {str(e)}", exc_info=True)
        raise
```

### 3. Recipe Loader Module - recipe_loader.py

```python
"""
Recipe loader utility for discovering and loading IfcPatch recipes.
"""
import logging
import importlib
import inspect
from pathlib import Path
from typing import Dict, List, Optional, Type
import ifcpatch

logger = logging.getLogger(__name__)

class RecipeLoader:
    """Handles discovery and loading of IfcPatch recipes."""
    
    def __init__(self, custom_recipes_path: Path):
        self.custom_recipes_path = custom_recipes_path
        self._builtin_cache = None
        self._custom_cache = None
    
    def get_builtin_recipes(self) -> Dict[str, Type]:
        """Get all built-in IfcPatch recipes."""
        if self._builtin_cache is not None:
            return self._builtin_cache
        
        recipes = {}
        try:
            import ifcpatch.recipes as recipes_module
            
            for name in dir(recipes_module):
                if not name.startswith('_'):
                    try:
                        module = getattr(recipes_module, name)
                        if hasattr(module, 'Patcher'):
                            recipes[name] = module.Patcher
                    except Exception as e:
                        logger.debug(f"Skipping {name}: {str(e)}")
        
        except Exception as e:
            logger.error(f"Error loading built-in recipes: {str(e)}")
        
        self._builtin_cache = recipes
        return recipes
    
    def get_custom_recipes(self) -> Dict[str, Type]:
        """Get all custom recipes from the custom_recipes directory."""
        if self._custom_cache is not None:
            return self._custom_cache
        
        recipes = {}
        
        if not self.custom_recipes_path.exists():
            logger.warning(f"Custom recipes path not found: {self.custom_recipes_path}")
            return recipes
        
        for recipe_file in self.custom_recipes_path.glob("*.py"):
            if recipe_file.stem.startswith('_') or recipe_file.stem == 'example_recipe':
                continue
            
            try:
                module = importlib.import_module(recipe_file.stem)
                if hasattr(module, 'Patcher'):
                    recipes[recipe_file.stem] = module.Patcher
                else:
                    logger.warning(f"Custom recipe {recipe_file.stem} missing Patcher class")
            
            except Exception as e:
                logger.error(f"Failed to load custom recipe {recipe_file.stem}: {str(e)}")
        
        self._custom_cache = recipes
        return recipes
    
    def get_recipe(self, recipe_name: str, is_custom: bool = False) -> Optional[Type]:
        """
        Get a specific recipe by name.
        
        Args:
            recipe_name: Name of the recipe
            is_custom: Whether to look in custom recipes
        
        Returns:
            Recipe class or None if not found
        """
        if is_custom:
            recipes = self.get_custom_recipes()
        else:
            recipes = self.get_builtin_recipes()
        
        return recipes.get(recipe_name)
    
    def validate_recipe_exists(self, recipe_name: str, is_custom: bool = False) -> bool:
        """Check if a recipe exists."""
        recipe = self.get_recipe(recipe_name, is_custom)
        return recipe is not None
```

### 4. Custom Recipe Template

Create `ifcpatch-worker/custom_recipes/example_recipe.py`:

```python
"""
Example Custom Recipe Template

This is a template for creating custom IfcPatch recipes.
Copy this file and modify it to create your own recipes.

Recipe Name: ExampleRecipe
Description: This recipe demonstrates the structure of a custom recipe.
Author: Your Name
Date: 2025-01-01
"""

import logging
import ifcopenshell
from ifcpatch import BasePatcher

logger = logging.getLogger(__name__)

class Patcher(BasePatcher):
    """
    Example custom patcher that demonstrates the recipe structure.
    
    This recipe does [describe what your recipe does].
    
    Parameters:
        file: The IFC model to patch
        logger: Logger instance for output
        argument1: Description of argument 1
        argument2: Description of argument 2
    
    Example:
        patcher = Patcher(ifc_file, logger, "value1", 123)
        patcher.patch()
        output = patcher.get_output()
    """
    
    def __init__(self, file: ifcopenshell.file, logger: logging.Logger, *args):
        """
        Initialize the patcher.
        
        Args:
            file: IFC file to patch
            logger: Logger instance
            *args: Additional recipe-specific arguments
        """
        super().__init__(file, logger)
        
        # Parse your custom arguments here
        self.argument1 = args[0] if len(args) > 0 else "default_value"
        self.argument2 = args[1] if len(args) > 1 else 0
        
        logger.info(f"Initialized ExampleRecipe with arg1={self.argument1}, arg2={self.argument2}")
    
    def patch(self) -> None:
        """
        Execute the patching logic.
        
        This method contains the main logic of your recipe.
        Modify self.file as needed.
        """
        self.logger.info("Starting ExampleRecipe patch operation")
        
        # Example: Iterate through all walls
        walls = self.file.by_type("IfcWall")
        self.logger.info(f"Found {len(walls)} walls to process")
        
        for wall in walls:
            # Your modification logic here
            # Example: Add or modify properties
            pass
        
        self.logger.info("ExampleRecipe patch operation completed")
    
    def get_output(self) -> ifcopenshell.file:
        """
        Return the patched IFC file.
        
        Returns:
            The modified IFC file object
        """
        return self.file
```

### 5. Dockerfile

```dockerfile
# ifcpatch-worker/Dockerfile
FROM python:3.10-slim AS base

WORKDIR /app

# Install system dependencies for IfcOpenShell
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy shared library and install it
COPY shared /app/shared
RUN pip install -e /app/shared

# Install service-specific dependencies
COPY ifcpatch-worker/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy worker code
COPY ifcpatch-worker/tasks.py /app/
COPY ifcpatch-worker/recipe_loader.py /app/

# Copy custom recipes directory
COPY ifcpatch-worker/custom_recipes /app/custom_recipes/

# Create necessary directories
RUN mkdir -p /output/patch /uploads
RUN chmod -R 777 /output /uploads

# Run the RQ worker pointing to the specific queue
CMD ["rq", "worker", "ifcpatch", "--url", "redis://redis:6379/0"]
```

### 6. requirements.txt

```txt
# Core dependencies
rq
pydantic

# IFC dependencies
ifcopenshell
ifcpatch

# Additional utilities
typing-extensions
```

---

## Custom Recipe Development

### Creating a Custom Recipe

1. **Create a new Python file** in `ifcpatch-worker/custom_recipes/`
2. **Define a Patcher class** that inherits from `BasePatcher`
3. **Implement required methods**: `__init__`, `patch()`, `get_output()`
4. **Add documentation** as docstrings
5. **Test locally** before deploying

### Custom Recipe Guidelines

#### Naming Conventions
- Use PascalCase for recipe names: `MyCustomRecipe`
- File names should match recipe names: `MyCustomRecipe.py`
- Class must be named `Patcher`

#### Required Structure

```python
from ifcpatch import BasePatcher
import ifcopenshell

class Patcher(BasePatcher):
    def __init__(self, file: ifcopenshell.file, logger, *args):
        super().__init__(file, logger)
        # Initialize your arguments
    
    def patch(self) -> None:
        # Implement your patching logic
        pass
    
    def get_output(self) -> ifcopenshell.file:
        # Return the modified file
        return self.file
```

#### Best Practices

1. **Logging**: Use `self.logger` for all logging operations
2. **Error Handling**: Catch and log exceptions appropriately
3. **Validation**: Validate arguments in `__init__`
4. **Documentation**: Provide comprehensive docstrings
5. **Testing**: Test with various IFC schemas (IFC2X3, IFC4, etc.)
6. **Performance**: Consider performance for large models
7. **Immutability**: Consider whether to modify in-place or create a copy

### Example: Custom Recipe for Adding Timestamps

```python
"""
Add timestamp properties to all elements.
"""
import logging
from datetime import datetime
import ifcopenshell
import ifcopenshell.api
from ifcpatch import BasePatcher

class Patcher(BasePatcher):
    """
    Adds a timestamp property to all IfcProduct elements.
    
    Parameters:
        property_set_name: Name of the property set (default: "Custom_Timestamps")
        property_name: Name of the timestamp property (default: "ProcessedDate")
    """
    
    def __init__(self, file: ifcopenshell.file, logger: logging.Logger, 
                 property_set_name: str = "Custom_Timestamps",
                 property_name: str = "ProcessedDate"):
        super().__init__(file, logger)
        self.property_set_name = property_set_name
        self.property_name = property_name
        self.timestamp = datetime.now().isoformat()
    
    def patch(self) -> None:
        self.logger.info(f"Adding timestamp properties to all elements")
        
        products = self.file.by_type("IfcProduct")
        self.logger.info(f"Processing {len(products)} products")
        
        for product in products:
            try:
                # Create or get property set
                pset = ifcopenshell.api.run(
                    "pset.add_pset",
                    self.file,
                    product=product,
                    name=self.property_set_name
                )
                
                # Add timestamp property
                ifcopenshell.api.run(
                    "pset.edit_pset",
                    self.file,
                    pset=pset,
                    properties={self.property_name: self.timestamp}
                )
            except Exception as e:
                self.logger.warning(f"Failed to add timestamp to {product}: {str(e)}")
        
        self.logger.info("Timestamp properties added successfully")
    
    def get_output(self) -> ifcopenshell.file:
        return self.file
```

---

## API Design

### Endpoints to Implement

#### 1. Execute Patch Recipe

```
POST /patch/execute
```

**Request Body:**
```json
{
  "input_file": "model.ifc",
  "output_file": "model_patched.ifc",
  "recipe": "ExtractElements",
  "arguments": [".IfcWall"],
  "use_custom": false
}
```

**Response:**
```json
{
  "job_id": "abc123-def456-ghi789"
}
```

#### 2. List Available Recipes

```
GET /patch/recipes
```

**Query Parameters:**
- `include_custom` (bool): Include custom recipes (default: true)
- `include_builtin` (bool): Include built-in recipes (default: true)

**Response:**
```json
{
  "recipes": [
    {
      "name": "ExtractElements",
      "description": "Extract specific elements from an IFC file",
      "is_custom": false,
      "parameters": [
        {
          "name": "query",
          "type": "str",
          "description": "Selector query for elements to extract"
        }
      ],
      "output_type": "ifcopenshell.file"
    }
  ],
  "total_count": 50,
  "builtin_count": 45,
  "custom_count": 5
}
```

#### 3. Get Recipe Documentation

```
GET /patch/recipes/{recipe_name}
```

**Query Parameters:**
- `is_custom` (bool): Whether the recipe is custom (default: false)

**Response:**
```json
{
  "name": "ExtractElements",
  "description": "Detailed description...",
  "is_custom": false,
  "parameters": [...],
  "examples": [...],
  "documentation_url": "https://docs.ifcopenshell.org/..."
}
```

### API Gateway Implementation

Add to `api-gateway/api-gateway.py`:

```python
# Create queue
ifcpatch_queue = Queue('ifcpatch', connection=redis_conn)

# Add to health check
all_queues = {
    "ifcpatch_queue": ifcpatch_queue,
    # ... other queues
}

# Execute patch endpoint
@app.post("/patch/execute", tags=["Patch"])
async def execute_patch(request: IfcPatchRequest, _: str = Depends(verify_access)):
    """
    Execute an IfcPatch recipe on an IFC file.
    
    Supports both built-in recipes from IfcOpenShell and custom user-defined recipes.
    """
    try:
        job = ifcpatch_queue.enqueue(
            "tasks.run_ifcpatch",
            request.dict(),
            job_timeout="2h"  # Patches can be time-consuming
        )
        
        logger.info(f"Enqueued ifcpatch job with ID: {job.id}")
        return {"job_id": job.id}
    except Exception as e:
        logger.error(f"Error enqueueing ifcpatch job: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# List recipes endpoint
@app.post("/patch/recipes/list", tags=["Patch"])
async def list_patch_recipes(
    request: IfcPatchListRecipesRequest = IfcPatchListRecipesRequest(),
    _: str = Depends(verify_access)
):
    """
    List all available IfcPatch recipes (built-in and custom).
    """
    try:
        job = ifcpatch_queue.enqueue(
            "tasks.list_available_recipes",
            request.dict(),
            job_timeout="1m"
        )
        
        # Wait for result (this is a quick operation)
        import time
        max_wait = 10  # seconds
        start_time = time.time()
        
        while time.time() - start_time < max_wait:
            job.refresh()
            if job.is_finished:
                return job.result
            elif job.is_failed:
                raise HTTPException(status_code=500, detail="Failed to list recipes")
            time.sleep(0.1)
        
        raise HTTPException(status_code=408, detail="Request timeout")
        
    except Exception as e:
        logger.error(f"Error listing recipes: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
```

---

## Testing Strategy

### Unit Tests

Create `ifcpatch-worker/test_recipes.py`:

```python
import pytest
import ifcopenshell
from tasks import run_ifcpatch, discover_custom_recipes, load_custom_recipe

class TestBuiltinRecipes:
    def test_extract_elements(self):
        """Test ExtractElements recipe"""
        job_data = {
            "input_file": "test_model.ifc",
            "output_file": "test_output.ifc",
            "recipe": "ExtractElements",
            "arguments": [".IfcWall"],
            "use_custom": False
        }
        result = run_ifcpatch(job_data)
        assert result["success"] == True
        assert "output_path" in result

class TestCustomRecipes:
    def test_discover_recipes(self):
        """Test custom recipe discovery"""
        recipes = discover_custom_recipes()
        assert isinstance(recipes, list)
    
    def test_load_custom_recipe(self):
        """Test loading a custom recipe"""
        # Assuming a custom recipe exists
        pass

class TestRecipeValidation:
    def test_invalid_recipe_name(self):
        """Test handling of invalid recipe names"""
        with pytest.raises(Exception):
            job_data = {
                "input_file": "test.ifc",
                "output_file": "out.ifc",
                "recipe": "NonExistentRecipe",
                "arguments": [],
                "use_custom": False
            }
            run_ifcpatch(job_data)
```

### Integration Tests

```python
import requests

def test_full_workflow():
    """Test complete workflow from API to result"""
    # Upload file
    files = {'file': open('test_model.ifc', 'rb')}
    upload_response = requests.post(
        'http://localhost:8000/upload/ifc',
        files=files,
        headers={'X-API-Key': 'test-key'}
    )
    
    # Execute patch
    patch_response = requests.post(
        'http://localhost:8000/patch/execute',
        json={
            "input_file": "test_model.ifc",
            "output_file": "patched.ifc",
            "recipe": "ExtractElements",
            "arguments": [".IfcWall"],
            "use_custom": False
        },
        headers={'X-API-Key': 'test-key'}
    )
    
    job_id = patch_response.json()['job_id']
    
    # Check status
    import time
    time.sleep(5)
    
    status_response = requests.get(
        f'http://localhost:8000/jobs/{job_id}/status',
        headers={'X-API-Key': 'test-key'}
    )
    
    assert status_response.json()['status'] == 'finished'
```

---

## Deployment Plan

### Step 1: Add to docker-compose.yml

```yaml
ifcpatch-worker:
  build:
    context: .
    dockerfile: ifcpatch-worker/Dockerfile
  volumes:
    - ./shared/uploads:/uploads
    - ./shared/output:/output
    - ./shared/examples:/examples
    - ./ifcpatch-worker/custom_recipes:/app/custom_recipes  # Mount custom recipes
  environment:
    - PYTHONUNBUFFERED=1
    - LOG_LEVEL=DEBUG
    - REDIS_URL=redis://redis:6379/0
  depends_on:
    - redis
  restart: unless-stopped
  deploy:
    resources:
      limits:
        cpus: '1.0'
        memory: 2G
```

### Step 2: Update API Gateway Dependencies

```yaml
api-gateway:
  depends_on:
    - ifcpatch-worker  # Add this
    - ifcconvert-worker
    # ... other workers
```

### Step 3: Build and Deploy

```bash
# Build the worker
docker-compose build ifcpatch-worker

# Start the worker
docker-compose up -d ifcpatch-worker

# Check logs
docker-compose logs -f ifcpatch-worker

# Verify in RQ Dashboard
# Visit http://localhost:9181
```

---

## Custom Recipe Management

### Adding New Custom Recipes

1. **Create recipe file**:
   ```bash
   cd ifcpatch-worker/custom_recipes
   cp example_recipe.py MyNewRecipe.py
   ```

2. **Edit MyNewRecipe.py** with your logic

3. **Restart worker**:
   ```bash
   docker-compose restart ifcpatch-worker
   ```

4. **Test recipe**:
   ```bash
   curl -X POST "http://localhost:8000/patch/execute" \
     -H "Content-Type: application/json" \
     -H "X-API-Key: your-key" \
     -d '{
       "input_file": "test.ifc",
       "output_file": "result.ifc",
       "recipe": "MyNewRecipe",
       "arguments": [],
       "use_custom": true
     }'
   ```

### Hot-Reloading Custom Recipes

For development, mount the custom_recipes directory:

```yaml
volumes:
  - ./ifcpatch-worker/custom_recipes:/app/custom_recipes:ro
```

Changes will be picked up on the next job (no restart needed if using importlib).

---

## Documentation

### User Documentation

Create `ifcpatch-worker/custom_recipes/README.md`:

```markdown
# Custom IfcPatch Recipes

## Overview
This directory contains custom IfcPatch recipes for the IFC Pipeline.

## Creating a Recipe
1. Copy `example_recipe.py` to a new file
2. Rename the file to match your recipe name
3. Implement the `Patcher` class with your logic
4. Test locally before deployment

## Recipe Structure
See `example_recipe.py` for the complete template.

## Available Custom Recipes
- ExampleRecipe: Template recipe
- [Add your recipes here]
```

---

## Performance Considerations

### Optimization Strategies

1. **Caching**: Cache recipe discovery results
2. **Lazy Loading**: Only load recipes when needed
3. **Resource Limits**: Set appropriate memory/CPU limits
4. **Parallel Processing**: Use multiprocessing for large files
5. **Streaming**: Consider streaming for very large files

### Monitoring

- Monitor queue length in RQ Dashboard
- Track job execution times
- Set up alerts for failed jobs
- Monitor memory usage

---

## Security Considerations

1. **Input Validation**: Validate all recipe arguments
2. **Sandboxing**: Custom recipes run in isolated containers
3. **Code Review**: Review custom recipes before deployment
4. **Access Control**: Restrict who can add custom recipes
5. **Logging**: Log all recipe executions

---

## Future Enhancements

### Phase 5: Advanced Features (Future)

- **Recipe Marketplace**: Share recipes across installations
- **Visual Recipe Builder**: GUI for creating simple recipes
- **Recipe Chaining**: Execute multiple recipes in sequence
- **Conditional Execution**: Execute recipes based on model properties
- **Version Control**: Track recipe versions
- **Recipe Templates**: Pre-built templates for common operations

---

## References

- [IfcPatch Documentation](https://docs.ifcopenshell.org/autoapi/ifcpatch/index.html)
- [IfcPatch Recipes](https://docs.ifcopenshell.org/autoapi/ifcpatch/recipes/index.html)
- [IfcOpenShell Documentation](https://docs.ifcopenshell.org/)
- [Worker Creation Guide](./WORKER_CREATION_GUIDE.md)

---

## Support

For issues or questions:
1. Check the example recipes
2. Review IfcPatch documentation
3. Check worker logs: `docker-compose logs ifcpatch-worker`
4. Consult the Worker Creation Guide

---

## Appendix: Complete Recipe List

### Built-in Recipes (Examples)

1. **ExtractElements** - Extract specific elements
2. **ConvertLengthUnit** - Convert measurement units
3. **MergeProjects** - Merge multiple IFC projects
4. **Optimise** - Optimize IFC file size
5. **Migrate** - Migrate between IFC schemas
6. **ResetAbsoluteCoordinates** - Reset coordinate system
7. **SplitByBuildingStorey** - Split by levels
8. And many more...

See [full list](https://docs.ifcopenshell.org/autoapi/ifcpatch/recipes/index.html).

---

**End of Implementation Plan**

*Last Updated: 2025-01-01*
*Version: 1.0*

