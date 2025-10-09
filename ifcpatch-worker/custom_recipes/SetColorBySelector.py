"""
SetColorBySelector Recipe

This custom recipe assigns colors to IFC elements based on selector syntax.
Supports multiple operations with filter groups and hex color assignments.

Recipe Name: SetColorBySelector
Description: Assign colors to IFC elements using IfcOpenShell selector syntax
Author: IFC Pipeline Team
Date: 2025-01-08

Example Usage:
    op1 = '{"selectors": "IfcWall", "hex": "FF0000"}'
    op2 = '{"selectors": "IfcWall + IfcDoor", "hex": "#FF0000 + #00FF00"}'
    op3 = '{"selectors": "IfcSlab, [LoadBearing=TRUE]", "hex": "0000FF"}'
    
    patcher = Patcher(ifc_file, logger, operation1=op1, operation2=op2, operation3=op3)
    patcher.patch()
    output = patcher.get_output()
"""

import json
import logging
import re
import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.selector
from ifcpatch import BasePatcher

logger = logging.getLogger(__name__)


class Patcher(BasePatcher):
    """
    Custom patcher for assigning colors to IFC elements using selector syntax.
    
    This recipe:
    - Accepts multiple operations as separate JSON string arguments
    - Uses IfcOpenShell selector syntax (including filter groups) to find elements
    - Creates or reuses styles for color assignment
    - Assigns colors to all representations of matched elements
    
    Parameters:
        file: The IFC model to patch
        logger: Logger instance for output
        operation1-5: JSON strings for up to 5 operations (only non-empty operations are processed)
        
    Each operation requires 2 fields:
        - selectors: IfcOpenShell selector syntax string (can use filter groups with +)
        - hex: Hex color string (can use + separator for multiple colors)
        
    Selector Syntax (per IfcOpenShell documentation):
        - Filter groups separated by + (results unioned together)
        - Within a filter group, filters separated by , (chained left to right)
    
    Hex Color Handling:
        - Single color: applies same color to all matched elements
        - Multiple colors with +: must match number of filter groups, colors assigned by position
        - Hex colors can optionally include # prefix (e.g., "#FF0000" or "FF0000")
    
    Example:
        op1 = '{"selectors": "IfcWall", "hex": "FF0000"}'
        op2 = '{"selectors": "IfcWall + IfcDoor", "hex": "#FF0000 + #00FF00"}'
        op3 = '{"selectors": "IfcSlab, [LoadBearing=TRUE]", "hex": "0000FF"}'
        patcher = Patcher(ifc_file, logger, operation1=op1, operation2=op2, operation3=op3)
        patcher.patch()
        output = patcher.get_output()
    """
    
    def __init__(self, file: ifcopenshell.file, logger: logging.Logger,
                 operation1: str = "",
                 operation2: str = "",
                 operation3: str = "",
                 operation4: str = "",
                 operation5: str = ""):
        """
        Initialize the patcher.
        
        Args:
            file: IFC file to patch
            logger: Logger instance
            operation1: JSON operation string. Example: {"selectors": "IfcWall", "hex": "FF0000"}
            operation2: JSON operation string. Example: {"selectors": "IfcWall + IfcDoor", "hex": "FF0000 + 00FF00"}
            operation3: JSON operation string. Example: {"selectors": "IfcSlab, [LoadBearing=TRUE]", "hex": "0000FF"}
            operation4: JSON operation string. Example: {"selectors": "IfcBeam + IfcColumn", "hex": "00FFFF + FFFF00"}
            operation5: JSON operation string. Example: {"selectors": "IfcWindow", "hex": "FF00FF"}
        """
        super().__init__(file, logger)
        
        self.operations = []
        self.style_cache = {}  # Cache styles by hex value to avoid duplicates
        self.stats = {
            'operations_total': 0,
            'operations_completed': 0,
            'operations_failed': 0,
            'elements_colored': 0,
            'styles_created': 0,
            'styles_reused': 0
        }
        
        # Collect all non-empty operations
        operation_args = tuple(
            op for op in [operation1, operation2, operation3, operation4, operation5]
            if op and op.strip()
        )
        
        # Parse and validate operations
        try:
            self.operations = self._parse_operations(operation_args)
            self.stats['operations_total'] = len(self.operations)
            self.logger.info(f"Initialized SetColorBySelector with {len(self.operations)} operation(s)")
        except Exception as e:
            self.logger.error(f"Failed to parse operations: {str(e)}")
            raise ValueError(f"Invalid operations: {str(e)}")
    
    def _parse_operations(self, operation_args: tuple) -> list:
        """
        Parse and validate the operation arguments.
        
        Args:
            operation_args: Tuple of JSON strings, each representing one operation
            
        Returns:
            List of validated operation dictionaries
            
        Raises:
            ValueError: If JSON is invalid or operations are malformed
        """
        if not operation_args:
            return []
        
        validated_operations = []
        
        for idx, operation_json in enumerate(operation_args):
            # Skip empty strings
            if not operation_json or (isinstance(operation_json, str) and operation_json.strip() == ""):
                self.logger.warning(f"Argument {idx + 1} is empty, skipping")
                continue
            
            # Parse the JSON string
            try:
                op = json.loads(operation_json)
            except json.JSONDecodeError as e:
                self.logger.warning(f"Argument {idx + 1}: Invalid JSON format - {str(e)}, skipping")
                continue
            
            if not isinstance(op, dict):
                self.logger.warning(f"Argument {idx + 1}: Expected JSON object, got {type(op).__name__}, skipping")
                continue
            
            # Validate required fields
            required_fields = ['selectors', 'hex']
            missing_fields = [f for f in required_fields if f not in op]
            
            if missing_fields:
                self.logger.warning(f"Argument {idx + 1}: Missing required fields: {missing_fields}, skipping")
                continue
            
            # Validate selectors is non-empty
            if not op['selectors'] or not isinstance(op['selectors'], str) or not op['selectors'].strip():
                self.logger.warning(f"Argument {idx + 1}: 'selectors' must be a non-empty string, skipping")
                continue
            
            # Validate hex format
            hex_value = op['hex']
            if not isinstance(hex_value, str):
                self.logger.warning(f"Argument {idx + 1}: 'hex' must be a string, skipping")
                continue
            
            # Parse hex colors (split by + if present)
            hex_colors = [h.strip() for h in hex_value.split('+') if h.strip()]
            
            if not hex_colors:
                self.logger.warning(f"Argument {idx + 1}: 'hex' cannot be empty, skipping")
                continue
            
            # Validate each hex color
            invalid_hex = False
            for i, h in enumerate(hex_colors):
                if not self._validate_hex_format(h):
                    self.logger.warning(f"Argument {idx + 1}: Invalid hex format at position {i + 1}: '{h}', skipping")
                    invalid_hex = True
                    break
            
            if invalid_hex:
                continue
            
            validated_operations.append(op)
        
        return validated_operations
    
    def _validate_hex_format(self, hex_str: str) -> bool:
        """
        Validate that a string is a valid hex color (6 characters, valid hex digits).
        Supports both "FF0000" and "#FF0000" formats.
        
        Args:
            hex_str: Hex color string (with or without # prefix)
            
        Returns:
            True if valid, False otherwise
        """
        # Remove # if present (supports both "FF0000" and "#FF0000")
        hex_str = hex_str.lstrip('#')
        
        # Check length and valid hex characters
        if len(hex_str) != 6:
            return False
        
        try:
            int(hex_str, 16)
            return True
        except ValueError:
            return False
    
    def _parse_hex_color(self, hex_str: str) -> tuple:
        """
        Convert a hex color string to RGB tuple (normalized 0-1 for IFC).
        Supports both "FF0000" and "#FF0000" formats.
        
        Args:
            hex_str: Hex color string (e.g., "FF0000" or "#FF0000")
            
        Returns:
            Tuple of (r, g, b) as floats in range 0-1
        """
        # Remove # if present (supports both "FF0000" and "#FF0000")
        hex_str = hex_str.lstrip('#').upper()
        
        # Parse RGB values (0-255)
        r = int(hex_str[0:2], 16)
        g = int(hex_str[2:4], 16)
        b = int(hex_str[4:6], 16)
        
        # Normalize to 0-1 range for IFC
        return (r / 255.0, g / 255.0, b / 255.0)
    
    def _get_or_create_style(self, hex_value: str):
        """
        Get existing style for a hex color or create a new one.
        Supports both "FF0000" and "#FF0000" formats.
        
        Args:
            hex_value: Hex color string (with or without # prefix)
            
        Returns:
            IfcSurfaceStyle entity
        """
        # Normalize hex value (remove # if present and convert to uppercase)
        hex_value = hex_value.lstrip('#').upper()
        
        # Check cache
        if hex_value in self.style_cache:
            self.stats['styles_reused'] += 1
            return self.style_cache[hex_value]
        
        # Parse RGB values
        r, g, b = self._parse_hex_color(hex_value)
        
        # Create new style
        style = ifcopenshell.api.run("style.add_style", self.file, name=f"Color_{hex_value}")
        
        # Add surface shading with color
        ifcopenshell.api.run("style.add_surface_style", self.file, 
                            style=style, 
                            ifc_class="IfcSurfaceStyleShading", 
                            attributes={
                                "SurfaceColour": {
                                    "Name": None, 
                                    "Red": r, 
                                    "Green": g, 
                                    "Blue": b
                                }
                            })
        
        # Cache the style
        self.style_cache[hex_value] = style
        self.stats['styles_created'] += 1
        
        return style
    
    def _assign_color_to_element(self, element, style) -> bool:
        """
        Assign a color style to all representations of an element.
        
        Args:
            element: IFC element
            style: IfcSurfaceStyle entity
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Get all shape representations for the element
            if not hasattr(element, 'Representation') or not element.Representation:
                self.logger.debug(f"Element {element.GlobalId} has no representation, skipping")
                return False
            
            representations = []
            
            # Collect all shape representations
            if element.Representation.is_a('IfcProductDefinitionShape'):
                for rep in element.Representation.Representations:
                    if rep.is_a('IfcShapeRepresentation'):
                        representations.append(rep)
            
            if not representations:
                self.logger.debug(f"Element {element.GlobalId} has no shape representations, skipping")
                return False
            
            # Assign style to all representations
            for rep in representations:
                ifcopenshell.api.run("style.assign_representation_styles", 
                                    self.file, 
                                    shape_representation=rep, 
                                    styles=[style])
            
            return True
            
        except Exception as e:
            self.logger.warning(f"Failed to assign color to element {element.GlobalId}: {str(e)}")
            return False
    
    def _execute_operation(self, operation: dict, operation_idx: int) -> dict:
        """
        Execute a single color assignment operation.
        
        Args:
            operation: Operation dictionary with 'selectors' and 'hex'
            operation_idx: Index of the operation (for logging)
            
        Returns:
            Dictionary with operation results
        """
        selectors_str = operation['selectors']
        hex_value = operation['hex']
        
        result = {
            'success': False,
            'filter_groups_processed': 0,
            'elements_colored': 0,
            'error': None
        }
        
        try:
            # Split selector string into filter groups
            filter_groups = [fg.strip() for fg in selectors_str.split('+') if fg.strip()]
            
            if not filter_groups:
                self.logger.warning(f"No valid filter groups found in selector: '{selectors_str}'")
                result['success'] = True  # Not an error, just no groups
                return result
            
            # Parse hex colors (split by + if present)
            hex_colors = [h.strip() for h in hex_value.split('+') if h.strip()]
            
            # Validate hex colors match filter groups
            if len(hex_colors) > 1 and len(hex_colors) != len(filter_groups):
                raise ValueError(f"Number of hex colors ({len(hex_colors)}) must match number of filter groups ({len(filter_groups)})")
            
            # If single hex color, apply to all filter groups
            if len(hex_colors) == 1:
                hex_list = hex_colors * len(filter_groups)
            else:
                hex_list = hex_colors
            
            self.logger.info(f"Processing {len(filter_groups)} filter group(s)")
            
            # Process each filter group with its corresponding hex color
            total_colored = 0
            for group_idx, (filter_group, hex_color) in enumerate(zip(filter_groups, hex_list)):
                self.logger.debug(f"Filter group {group_idx + 1}/{len(filter_groups)}: '{filter_group}' -> {hex_color}")
                
                # Get or create style for this hex color
                style = self._get_or_create_style(hex_color)
                
                # Select elements using this filter group
                elements = ifcopenshell.util.selector.filter_elements(self.file, filter_group)
                
                if len(elements) == 0:
                    self.logger.warning(f"No elements matched filter group: '{filter_group}'")
                    continue
                
                self.logger.info(f"Found {len(elements)} element(s) matching filter group '{filter_group}'")
                
                # Assign color to each element
                colored_count = 0
                for i, element in enumerate(elements):
                    # Log progress for large selections
                    if len(elements) > 500 and (i + 1) % 500 == 0:
                        self.logger.info(f"Processing element {i + 1}/{len(elements)}")
                    
                    if self._assign_color_to_element(element, style):
                        colored_count += 1
                
                self.logger.info(f"Successfully colored {colored_count}/{len(elements)} elements with hex {hex_color}")
                total_colored += colored_count
                result['filter_groups_processed'] += 1
            
            result['elements_colored'] = total_colored
            result['success'] = True
            
            # Update global stats
            self.stats['elements_colored'] += total_colored
            
        except ValueError as e:
            result['error'] = str(e)
            self.logger.error(f"Operation {operation_idx + 1} failed: {str(e)}")
        except Exception as e:
            result['error'] = str(e)
            self.logger.error(f"Unexpected error in operation {operation_idx + 1}: {str(e)}", exc_info=True)
        
        return result
    
    def patch(self) -> None:
        """
        Execute all operations to patch the IFC file.
        
        This method:
        - Iterates through all parsed operations
        - Selects elements using selector syntax with filter groups
        - Creates or reuses color styles
        - Assigns colors to element representations
        - Tracks statistics and errors
        """
        if self.stats['operations_total'] == 0:
            self.logger.warning("No valid operations to execute")
            return
        
        self.logger.info(f"Starting SetColorBySelector with {self.stats['operations_total']} operation(s)")
        
        try:
            # Execute each operation
            for idx, operation in enumerate(self.operations):
                self.logger.info(f"Operation {idx + 1}/{self.stats['operations_total']}: '{operation['selectors']}' -> {operation['hex']}")
                result = self._execute_operation(operation, idx)
                
                if result['success']:
                    self.stats['operations_completed'] += 1
                else:
                    self.stats['operations_failed'] += 1
            
            # Log summary
            self.logger.info(
                f"SetColorBySelector completed: "
                f"{self.stats['operations_completed']}/{self.stats['operations_total']} operations succeeded, "
                f"{self.stats['elements_colored']} elements colored, "
                f"{self.stats['styles_created']} styles created, "
                f"{self.stats['styles_reused']} styles reused"
            )
            
        except Exception as e:
            self.logger.error(f"Critical error during patch execution: {str(e)}", exc_info=True)
            raise
    
    def get_output(self) -> ifcopenshell.file:
        """
        Return the patched IFC file.
        
        Returns:
            The modified IFC file object
        """
        return self.file

