import logging
import os
import sys
import importlib
import inspect
import ifcopenshell
import ifcpatch
from pathlib import Path
from typing import List, Dict, Any, get_type_hints, get_origin, get_args
from shared.classes import IfcPatchRequest, IfcPatchListRecipesRequest

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add custom recipes directory to path
CUSTOM_RECIPES_DIR = Path("/app/custom_recipes")
sys.path.insert(0, str(CUSTOM_RECIPES_DIR))

def parse_docstring_params(docstring: str) -> Dict[str, str]:
    """
    Parse parameter descriptions from docstring.
    
    Looks for patterns like:
    :param query: A query to select the subset of IFC elements.
    :param assume_asset_uniqueness_by_name: Avoid adding assets...
    
    Args:
        docstring: The docstring to parse
        
    Returns:
        Dictionary mapping parameter names to their descriptions
    """
    param_descriptions = {}
    
    if not docstring:
        return param_descriptions
        
    lines = docstring.split('\n')
    current_param = None
    current_desc = []
    
    for line in lines:
        line = line.strip()
        
        # Look for :param name: description pattern
        if line.startswith(':param ') and ':' in line[7:]:
            # Save previous parameter if exists
            if current_param and current_desc:
                param_descriptions[current_param] = ' '.join(current_desc).strip()
            
            # Parse new parameter
            param_part = line[7:]  # Remove ':param '
            colon_idx = param_part.find(':')
            if colon_idx > 0:
                current_param = param_part[:colon_idx].strip()
                current_desc = [param_part[colon_idx + 1:].strip()]
            else:
                current_param = None
                current_desc = []
                
        elif current_param and line and not line.startswith(':'):
            # Continue description on next line
            current_desc.append(line)
        elif line.startswith(':') or not line:
            # End of current parameter description
            if current_param and current_desc:
                param_descriptions[current_param] = ' '.join(current_desc).strip()
            current_param = None
            current_desc = []
    
    # Save last parameter
    if current_param and current_desc:
        param_descriptions[current_param] = ' '.join(current_desc).strip()
        
    return param_descriptions

def format_type_annotation(type_annotation: Any) -> str:
    """
    Format type annotation for display.
    
    Args:
        type_annotation: The type annotation to format
        
    Returns:
        String representation of the type
    """
    try:
        if type_annotation is inspect.Parameter.empty:
            return "Any"
            
        if hasattr(type_annotation, '__name__'):
            return type_annotation.__name__
            
        type_str = str(type_annotation)
        
        # Handle typing module types
        if 'typing.' in type_str:
            # Remove typing. prefix
            type_str = type_str.replace('typing.', '')
            
            # Handle Union types
            if type_str.startswith('Union['):
                try:
                    args = get_args(type_annotation)
                    if len(args) == 2 and type(None) in args:
                        # Optional type
                        non_none_type = next(arg for arg in args if arg != type(None))
                        return f"Optional[{format_type_annotation(non_none_type)}]"
                except:
                    pass
                    
        return type_str
    except Exception as e:
        logger.debug(f"Error formatting type annotation: {e}")
        return "Any"

def extract_recipe_parameters(recipe_class: type) -> List[Dict[str, Any]]:
    """
    Extract parameters from a recipe's __init__ method using inspection.
    
    Args:
        recipe_class: The Patcher class to inspect
        
    Returns:
        List of parameter dictionaries with name, type, description, default, and required
    """
    parameters = []
    
    try:
        # Get the __init__ method signature
        init_method = recipe_class.__init__
        signature = inspect.signature(init_method)
        
        # Get type hints if available
        try:
            type_hints = get_type_hints(init_method)
        except Exception as e:
            logger.debug(f"Could not get type hints: {e}")
            type_hints = {}
        
        # Get docstring and parse parameter descriptions
        docstring = inspect.getdoc(init_method) or ""
        param_descriptions = parse_docstring_params(docstring)
        
        # Iterate through parameters, skipping 'self', 'file', and 'logger'
        for param_name, param in signature.parameters.items():
            if param_name in ['self', 'file', 'logger']:
                continue
                
            # Extract parameter information
            param_info = {
                "name": param_name,
                "required": param.default == inspect.Parameter.empty,
            }
            
            # Get description from docstring
            if param_name in param_descriptions:
                param_info["description"] = param_descriptions[param_name]
            else:
                param_info["description"] = f"Parameter {param_name}"
            
            # Get type information
            if param_name in type_hints:
                param_type = type_hints[param_name]
                param_info["type"] = format_type_annotation(param_type)
            elif param.annotation != inspect.Parameter.empty:
                param_info["type"] = format_type_annotation(param.annotation)
            else:
                param_info["type"] = "Any"
            
            # Get default value
            if param.default != inspect.Parameter.empty:
                # Convert default value to string for JSON serialization
                default_value = param.default
                if isinstance(default_value, bool):
                    param_info["default"] = default_value
                elif isinstance(default_value, (int, float)):
                    param_info["default"] = default_value
                elif isinstance(default_value, str):
                    param_info["default"] = default_value
                else:
                    param_info["default"] = str(default_value)
                
            parameters.append(param_info)
            
    except Exception as e:
        logger.debug(f"Error extracting parameters from {recipe_class.__name__}: {str(e)}")
        
    return parameters

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
        
        # Count elements (IfcOpenShell file doesn't support len() directly)
        try:
            element_count = len(ifc_file.by_type('IfcRoot'))
            logger.info(f"IFC file loaded: schema={ifc_file.schema}, elements={element_count}")
        except Exception:
            logger.info(f"IFC file loaded: schema={ifc_file.schema}")
        
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
            
            # Set the actual input path as an attribute on the IFC file object
            # This allows custom recipes to know the real filename
            ifc_file._input_file_path = input_path
            
            # Instantiate and execute custom patcher
            # Pass arguments if any
            if request.arguments:
                patcher_instance = custom_patcher(ifc_file, logger, *request.arguments)
            else:
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
        request = IfcPatchListRecipesRequest(**job_data)
        
        recipes = []
        
        # Get built-in recipes
        if request.include_builtin:
            logger.info("Discovering built-in recipes with parameter inspection...")
            try:
                import ifcpatch.recipes
                recipes_dir = Path(ifcpatch.recipes.__file__).parent
                
                # Get all recipe files
                for recipe_file in recipes_dir.glob("*.py"):
                    if recipe_file.stem.startswith('_'):
                        continue
                    
                    recipe_name = recipe_file.stem
                    try:
                        # Import the recipe module
                        recipe_module = importlib.import_module(f'ifcpatch.recipes.{recipe_name}')
                        
                        if hasattr(recipe_module, 'Patcher'):
                            patcher_class = recipe_module.Patcher
                            
                            # Extract parameters using inspection
                            parameters = extract_recipe_parameters(patcher_class)
                            
                            # Get description from __init__ docstring
                            description = inspect.getdoc(patcher_class.__init__) or "Built-in IfcPatch recipe"
                            
                            # Take first line/paragraph as description (before the Args: section)
                            if '\n\n' in description:
                                description = description.split('\n\n')[0]
                            elif ':param' in description:
                                description = description.split(':param')[0]
                            elif 'Args:' in description:
                                description = description.split('Args:')[0]
                            
                            # Clean up the description
                            description = ' '.join(description.split()).strip()
                            
                            recipes.append({
                                "name": recipe_name,
                                "description": description,
                                "is_custom": False,
                                "parameters": parameters,
                                "output_type": None
                            })
                            
                            logger.debug(f"Extracted {len(parameters)} parameters for {recipe_name}")
                            
                    except Exception as e:
                        logger.debug(f"Could not inspect {recipe_name}: {str(e)}")
                        # Fallback to minimal info
                        recipes.append({
                            "name": recipe_name,
                            "description": "Built-in IfcPatch recipe",
                            "is_custom": False,
                            "parameters": [],
                            "output_type": None
                        })
                        
                logger.info(f"Found {len([r for r in recipes if not r['is_custom']])} built-in recipes")
            except Exception as e:
                logger.error(f"Error discovering built-in recipes: {str(e)}", exc_info=True)
        
        # Get custom recipes
        if request.include_custom:
            logger.info("Discovering custom recipes with parameter inspection...")
            custom_recipe_names = discover_custom_recipes()
            
            for recipe_name in custom_recipe_names:
                try:
                    # Import custom recipe
                    module = importlib.import_module(recipe_name)
                    
                    if hasattr(module, 'Patcher'):
                        patcher_class = module.Patcher
                        
                        # Extract parameters using inspection
                        parameters = extract_recipe_parameters(patcher_class)
                        
                        # Get description from __init__ docstring
                        description = inspect.getdoc(patcher_class.__init__) or "Custom IfcPatch recipe"
                        
                        # Take first line/paragraph as description
                        if '\n\n' in description:
                            description = description.split('\n\n')[0]
                        elif ':param' in description:
                            description = description.split(':param')[0]
                        elif 'Args:' in description:
                            description = description.split('Args:')[0]
                        
                        # Clean up the description
                        description = ' '.join(description.split()).strip()
                        
                        recipes.append({
                            "name": recipe_name,
                            "description": description,
                            "is_custom": True,
                            "parameters": parameters,
                            "output_type": "ifcopenshell.file"
                        })
                        
                        logger.debug(f"Extracted {len(parameters)} parameters for custom recipe {recipe_name}")
                    else:
                        logger.warning(f"Custom recipe {recipe_name} missing Patcher class")
                        
                except Exception as e:
                    logger.error(f"Error inspecting custom recipe '{recipe_name}': {str(e)}")
        
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
