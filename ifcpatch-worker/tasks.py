# Patch typing module for Python 3.10 compatibility with ifcopenshell 0.8.x
import typing
from typing_extensions import NotRequired
if not hasattr(typing, 'NotRequired'):
    typing.NotRequired = NotRequired

import logging
import os
import sys
import importlib
import inspect
import types
from typing import Any

# Fix circular import in ifcopenshell 0.8.4.post1
# Workaround for circular import between shape_builder and shape modules
# Issue: shape_builder -> element -> representation -> shape -> shape_builder (VectorType)
def _fix_circular_import():
    """Fix circular import between shape_builder and shape modules.
    
    Strategy:
    1. Create a mock shape_builder module with VectorType defined
    2. Import shape.py which will use the mock VectorType and store a reference to it
    3. Use importlib to load the real shape_builder module (shape is already loaded, so no circular import)
    4. The real module stays in sys.modules with all attributes including ShapeBuilder
    """
    # Always check and fix - don't rely on sys.modules check as something might clear it
    # Check if we already have a properly initialized shape_builder
    if 'ifcopenshell.util.shape_builder' in sys.modules:
        sb = sys.modules['ifcopenshell.util.shape_builder']
        # If it has both VectorType and ShapeBuilder, we're good
        if hasattr(sb, 'VectorType') and hasattr(sb, 'ShapeBuilder'):
            return
    
    # Need to apply the fix
    if True:  # Always apply fix
        # Step 1: Create temporary mock module with VectorType defined
        # This allows shape.py to import VectorType during initialization
        mock_shape_builder = types.ModuleType('ifcopenshell.util.shape_builder')
        mock_shape_builder.VectorType = Any
        sys.modules['ifcopenshell.util.shape_builder'] = mock_shape_builder
        
        # Step 2: Import ifcopenshell base module
        import ifcopenshell
        
        # Step 3: Import shape which will use the mock VectorType
        # This breaks the circular dependency because shape gets VectorType from mock
        # shape.py stores a direct reference to the VectorType object (Any)
        import ifcopenshell.util.shape
        
        # Step 4: Remove mock and use direct import to load real shape_builder module
        # Since shape.py is already loaded and cached, it won't trigger the circular import
        # shape.py's reference to VectorType is a direct reference to the Any object, so it still works
        del sys.modules['ifcopenshell.util.shape_builder']
        
        # Use direct import statement - this works because shape.py is already cached
        # and won't try to re-import VectorType
        # Wrap in try-except to catch any import errors
        try:
            import ifcopenshell.util.shape_builder
        except ImportError as e:
            # If import fails, re-create mock and raise error
            mock_shape_builder = types.ModuleType('ifcopenshell.util.shape_builder')
            mock_shape_builder.VectorType = Any
            sys.modules['ifcopenshell.util.shape_builder'] = mock_shape_builder
            raise RuntimeError(f"Failed to import ifcopenshell.util.shape_builder: {e}") from e
        except Exception as e:
            # Catch any other exceptions
            mock_shape_builder = types.ModuleType('ifcopenshell.util.shape_builder')
            mock_shape_builder.VectorType = Any
            sys.modules['ifcopenshell.util.shape_builder'] = mock_shape_builder
            raise RuntimeError(f"Unexpected error importing ifcopenshell.util.shape_builder: {e}") from e
        
        # Step 5: Verify the real module is in sys.modules and has required attributes
        if 'ifcopenshell.util.shape_builder' not in sys.modules:
            raise RuntimeError("ifcopenshell.util.shape_builder not in sys.modules after import")
        
        real_shape_builder = sys.modules['ifcopenshell.util.shape_builder']
        if not hasattr(real_shape_builder, 'VectorType'):
            real_shape_builder.VectorType = Any
        if not hasattr(real_shape_builder, 'ShapeBuilder'):
            raise RuntimeError("ifcopenshell.util.shape_builder loaded but ShapeBuilder not available")

# Apply the fix before importing ifcopenshell
_fix_circular_import()

# Import ifcopenshell at module level so it's available throughout the module
# Note: ifcopenshell is imported inside _fix_circular_import, but we need it at module level
import ifcopenshell

# Import ifcpatch after the fix is applied
import ifcpatch

# Verify shape_builder is still available after ifcpatch import
# If not, it means the fix didn't work or was cleared - re-apply it
if 'ifcopenshell.util.shape_builder' not in sys.modules:
    # Re-apply fix if ifcpatch import cleared it or fix didn't work initially
    _fix_circular_import()
    
# Final verification - log warning instead of raising error
# The fix will be re-applied when needed (e.g., when OffsetObjectPlacements is imported)
if 'ifcopenshell.util.shape_builder' not in sys.modules:
    # Don't raise error here - the fix will be applied lazily when needed
    # This allows tasks.py to load even if the fix didn't work initially
    pass
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

def filter_arguments_for_recipe(recipe_name: str, arguments: List[Any], use_custom: bool = False) -> List[Any]:
    """
    Filter arguments to match what the recipe actually expects.
    
    Uses introspection to determine the recipe's __init__ signature and filters
    arguments to only include positional parameters (excluding 'file' and 'logger').
    
    Args:
        recipe_name: Name of the recipe
        arguments: List of arguments to filter
        use_custom: Whether this is a custom recipe
    
    Returns:
        Filtered list of arguments matching the recipe's signature
    """
    if not arguments:
        return []
    
    try:
        # Load the recipe module
        if use_custom:
            recipe_module = importlib.import_module(recipe_name)
        else:
            recipe_module = importlib.import_module(f"ifcpatch.recipes.{recipe_name}")
        
        if not hasattr(recipe_module, 'Patcher'):
            logger.warning(f"Recipe '{recipe_name}' has no Patcher class, passing arguments as-is")
            return arguments
        
        patcher_class = recipe_module.Patcher
        
        # Get the __init__ method signature
        init_method = patcher_class.__init__
        signature = inspect.signature(init_method)
        
        # Count positional parameters (excluding 'self', 'file', 'logger', 'src')
        # For ExtractElements, we want to allow both query and assume_asset_uniqueness_by_name
        # even though they have defaults, because they can be passed positionally
        if recipe_name == 'ExtractElements':
            # ExtractElements signature: (self, file, logger, query="IfcWall", assume_asset_uniqueness_by_name=True)
            # We allow up to 2 arguments: query and assume_asset_uniqueness_by_name
            if len(arguments) > 2:
                filtered_args = arguments[:2]  # Only pass first 2 args (query, assume_asset_uniqueness_by_name)
                logger.warning(
                    f"ExtractElements: Received {len(arguments)} arguments, but only accepts 2 "
                    f"(query, assume_asset_uniqueness_by_name). Filtering to first 2 arguments."
                )
            else:
                filtered_args = arguments  # Pass all arguments (0, 1, or 2 are all valid)
                if len(filtered_args) == 2:
                    logger.debug(
                        f"ExtractElements: Passing both query and assume_asset_uniqueness_by_name parameters"
                    )
        else:
            # Count all positional parameters (excluding 'self', 'file', 'logger', 'src')
            all_params = []
            required_params = []
            for param_name, param in signature.parameters.items():
                if param_name in ['self', 'file', 'logger', 'src']:
                    continue
                # Only count positional parameters (not keyword-only)
                if param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.POSITIONAL_ONLY):
                    all_params.append(param_name)
                    if param.default == inspect.Parameter.empty:
                        required_params.append(param_name)
            
            # For custom recipes, pass all provided arguments up to the number of parameters
            # This allows optional parameters to be passed positionally
            if use_custom:
                max_params = len(all_params)
                if len(arguments) > max_params:
                    filtered_args = arguments[:max_params]
                    logger.warning(
                        f"Custom recipe '{recipe_name}' accepts {max_params} parameter(s) "
                        f"({', '.join(all_params)}), but {len(arguments)} argument(s) were provided. "
                        f"Filtering to first {max_params} argument(s)."
                    )
                else:
                    filtered_args = arguments
                    if len(filtered_args) > 0:
                        logger.debug(
                            f"Custom recipe '{recipe_name}': Passing {len(filtered_args)} argument(s) "
                            f"to parameters: {', '.join(all_params[:len(filtered_args)])}"
                        )
            else:
                # For built-in recipes, pass arguments for all positional parameters (including optional ones)
                # This is important for recipes like OffsetObjectPlacements where all params have defaults
                # but users still need to provide them
                max_params = len(all_params)
                filtered_args = arguments[:max_params]
                
                # Log filtering for non-ExtractElements recipes
                if len(arguments) > max_params:
                    logger.warning(
                        f"Recipe '{recipe_name}' accepts {max_params} parameter(s) "
                        f"({', '.join(all_params)}), but {len(arguments)} argument(s) were provided. "
                        f"Filtering to first {max_params} argument(s)."
                    )
                    logger.debug(f"Filtered arguments: {filtered_args} (removed: {arguments[max_params:]})")
                elif len(filtered_args) > 0:
                    logger.debug(
                        f"Recipe '{recipe_name}': Passing {len(filtered_args)} argument(s) "
                        f"to parameters: {', '.join(all_params[:len(filtered_args)])}"
                    )
        
        return filtered_args
        
    except Exception as e:
        logger.warning(
            f"Could not introspect recipe '{recipe_name}' signature: {str(e)}. "
            f"Passing arguments as-is without filtering."
        )
        return arguments

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
        arguments = request.arguments or []
        
        # Special handling for MergeProject/MergeProjects recipe
        # This recipe expects arguments to be wrapped in a list because it unpacks with *args
        # and then expects to iterate over a list of filepaths
        if request.recipe in ["MergeProject", "MergeProjects"]:
            # Check if arguments are strings (JSON stringified arrays from n8n)
            if arguments and isinstance(arguments[0], str):
                # Try to parse if it looks like a JSON array string
                if arguments[0].startswith('[') and arguments[0].endswith(']'):
                    try:
                        import json
                        arguments = [json.loads(arguments[0])]
                        logger.info(f"Parsed JSON string arguments for {request.recipe}")
                    except:
                        # If parsing fails, wrap the arguments in a list
                        arguments = [arguments]
                        logger.info(f"Wrapped arguments in list for {request.recipe}")
                else:
                    # Not a JSON string, just wrap the list
                    arguments = [arguments]
                    logger.info(f"Wrapped arguments in list for {request.recipe}")
        
        # Apply circular import fix before filtering (filtering needs to import the recipe)
        # This ensures the introspection in filter_arguments_for_recipe works
        if 'ifcopenshell.util.shape_builder' not in sys.modules or \
           not hasattr(sys.modules.get('ifcopenshell.util.shape_builder', None), 'ShapeBuilder'):
            logger.debug("Applying circular import fix before argument filtering")
            _fix_circular_import()
        
        # Filter arguments to match what the recipe actually expects
        # This prevents passing too many arguments that would cause TypeError
        if not request.use_custom:
            original_arg_count = len(arguments) if arguments else 0
            arguments = filter_arguments_for_recipe(request.recipe, arguments, use_custom=False)
            if original_arg_count != len(arguments):
                logger.info(
                    f"Filtered arguments for recipe '{request.recipe}': "
                    f"{original_arg_count} -> {len(arguments) if arguments else 0} argument(s)"
                )
        
        # Build patch_args for ifcpatch.execute()
        # With ifcpatch 0.8.4, we can include "input" for all recipes - it's optional
        patch_args = {
            "input": input_path,
            "file": ifc_file,
            "recipe": request.recipe,
            "arguments": arguments
        }
        
        # Debug: Log the arguments being passed
        logger.info(f"Recipe: {request.recipe}")
        logger.info(f"Arguments count: {len(arguments) if arguments else 0}")
        logger.info(f"Arguments: {arguments}")
        
        # If using custom recipe, load it
        if request.use_custom:
            logger.info(f"Loading custom recipe: {request.recipe}")
            custom_patcher = load_custom_recipe(request.recipe)
            
            # Set the actual input path as an attribute on the IFC file object
            # This allows custom recipes to know the real filename
            ifc_file._input_file_path = input_path
            
            # Filter arguments for custom recipe as well
            custom_args = filter_arguments_for_recipe(request.recipe, arguments, use_custom=True)
            
            # Instantiate and execute custom patcher
            # Pass filtered arguments if any
            if custom_args:
                patcher_instance = custom_patcher(ifc_file, logger, *custom_args)
            else:
                patcher_instance = custom_patcher(ifc_file, logger)
            
            patcher_instance.patch()
            output = patcher_instance.get_output()
            
        else:
            logger.info(f"Executing built-in recipe: {request.recipe}")
            # Ensure circular import fix is applied before executing recipe
            # This is needed because recipe imports happen when ifcpatch.execute() is called
            # Some recipes (like OffsetObjectPlacements) require shape_builder which has a circular import issue
            needs_fix = False
            if 'ifcopenshell.util.shape_builder' not in sys.modules:
                needs_fix = True
                logger.debug("shape_builder not in sys.modules, applying fix")
            else:
                sb_module = sys.modules.get('ifcopenshell.util.shape_builder')
                if sb_module is None or not hasattr(sb_module, 'ShapeBuilder'):
                    needs_fix = True
                    logger.debug("shape_builder missing ShapeBuilder attribute, applying fix")
            
            if needs_fix:
                logger.info("Applying circular import fix before recipe execution")
                try:
                    _fix_circular_import()
                    # Verify fix worked
                    if 'ifcopenshell.util.shape_builder' in sys.modules:
                        sb = sys.modules['ifcopenshell.util.shape_builder']
                        if hasattr(sb, 'ShapeBuilder'):
                            logger.debug("Circular import fix applied successfully")
                            
                            # Pre-import the recipe module to ensure it uses the fixed shape_builder
                            # This prevents the circular import when ifcpatch.execute() imports it
                            try:
                                recipe_module_name = f"ifcpatch.recipes.{request.recipe}"
                                if recipe_module_name not in sys.modules:
                                    logger.debug(f"Pre-importing recipe module: {recipe_module_name}")
                                    importlib.import_module(recipe_module_name)
                                    logger.debug(f"Successfully pre-imported {recipe_module_name}")
                            except Exception as pre_import_error:
                                logger.warning(f"Failed to pre-import recipe module (will try normal import): {pre_import_error}")
                        else:
                            logger.warning("Fix applied but ShapeBuilder not available")
                    else:
                        logger.warning("Fix applied but shape_builder not in sys.modules")
                except Exception as e:
                    logger.error(f"Failed to apply circular import fix: {e}", exc_info=True)
                    # Continue anyway - might work for recipes that don't need shape_builder
            
            # Execute built-in recipe using ifcpatch.execute()
            # With ifcpatch 0.8.4 and ifcopenshell 0.8.4, this works correctly for all recipes
            # including ExtractElements with assume_asset_uniqueness_by_name parameter
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
