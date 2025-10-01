"""
Example Custom Recipe Template

This is a template for creating custom IfcPatch recipes.
Copy this file and modify it to create your own recipes.

Recipe Name: ExampleRecipe
Description: This recipe demonstrates the structure of a custom recipe.
Author: IFC Pipeline Team
Date: 2025-01-01
"""

import logging
import ifcopenshell
from ifcpatch import BasePatcher

logger = logging.getLogger(__name__)

class Patcher(BasePatcher):
    """
    Example custom patcher that demonstrates the recipe structure.
    
    This recipe demonstrates how to:
    - Accept custom arguments
    - Iterate through IFC elements
    - Modify element properties
    - Use logging effectively
    
    Parameters:
        file: The IFC model to patch
        logger: Logger instance for output
        element_type: Type of elements to process (default: "IfcWall")
        property_name: Name of property to add (default: "Processed")
    
    Example:
        patcher = Patcher(ifc_file, logger, "IfcWall", "CustomProperty")
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
                args[0]: element_type (str) - Type of elements to process
                args[1]: property_name (str) - Name of property to add
        """
        super().__init__(file, logger)
        
        # Parse custom arguments with defaults
        self.element_type = args[0] if len(args) > 0 else "IfcWall"
        self.property_name = args[1] if len(args) > 1 else "Processed"
        
        # Validate arguments
        if not isinstance(self.element_type, str):
            raise ValueError("element_type must be a string")
        if not isinstance(self.property_name, str):
            raise ValueError("property_name must be a string")
        
        self.logger.info(f"Initialized ExampleRecipe with element_type='{self.element_type}', property_name='{self.property_name}'")
    
    def patch(self) -> None:
        """
        Execute the patching logic.
        
        This method contains the main logic of your recipe.
        It demonstrates:
        - Querying elements by type
        - Iterating through elements
        - Error handling
        - Progress logging
        """
        self.logger.info(f"Starting ExampleRecipe patch operation for {self.element_type}")
        
        try:
            # Query elements by type
            elements = self.file.by_type(self.element_type)
            self.logger.info(f"Found {len(elements)} {self.element_type} elements to process")
            
            if len(elements) == 0:
                self.logger.warning(f"No {self.element_type} elements found in model")
                return
            
            # Process each element
            processed_count = 0
            for i, element in enumerate(elements):
                try:
                    # Log progress every 100 elements
                    if (i + 1) % 100 == 0:
                        self.logger.info(f"Processing element {i + 1}/{len(elements)}")
                    
                    # Example: Access element properties
                    element_name = element.Name if hasattr(element, 'Name') else "Unnamed"
                    element_guid = element.GlobalId if hasattr(element, 'GlobalId') else "No GUID"
                    
                    self.logger.debug(f"Processing {element_type}: {element_name} (GUID: {element_guid})")
                    
                    # Your custom logic here
                    # Example: You might add properties, modify geometry, etc.
                    # For this example, we're just counting
                    
                    processed_count += 1
                    
                except Exception as e:
                    self.logger.warning(f"Failed to process element {i}: {str(e)}")
                    continue
            
            self.logger.info(f"ExampleRecipe patch operation completed. Processed {processed_count}/{len(elements)} elements")
            
        except Exception as e:
            self.logger.error(f"Error during ExampleRecipe patch: {str(e)}", exc_info=True)
            raise
    
    def get_output(self) -> ifcopenshell.file:
        """
        Return the patched IFC file.
        
        Returns:
            The modified IFC file object
        """
        return self.file

