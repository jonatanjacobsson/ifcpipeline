"""
Recipe loader utility for discovering and loading IfcPatch recipes.
"""
import logging
import importlib
from pathlib import Path
from typing import Dict, Optional, Type

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

