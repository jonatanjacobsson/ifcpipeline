"""
SetAttributeBySelector Recipe

This custom recipe sets attributes on IFC elements based on selector syntax.
Supports multiple operations with literal values or dynamic extraction using 'from' field.

Recipe Name: SetAttributeBySelector
Description: Set attributes on IFC elements using selector syntax
Author: IFC Pipeline Team
Date: 2025-01-27

    Example Usage (Literal Values):
    op1 = '{"selector": "IfcWall", "attribute": "Name", "value": "My Wall"}'
    op2 = '{"selector": ".IfcDoor", "attribute": "Description", "value": "Fire Door"}'
    
    Example Usage (Extract from Property):
    op1 = '{"selector": "IfcWall", "attribute": "Name", "from": "Pset_WallCommon.Status"}'
    op2 = '{"selector": "IfcDoor", "attribute": "Description", "from": "Pset_DoorCommon.FireRating"}'
    
    Example Usage (Extract with Regex):
    op1 = '{"selector": "IfcElement", "attribute": "Tag", "from": "material.Name=/S[0-9]{3}[A-Za-z0-9]*/"}'
    op2 = '{"selector": "IfcWall", "attribute": "Description", "from": "type.Name=/Wall.*Type/"}'
    
    patcher = Patcher(ifc_file, logger, operation1=op1, operation2=op2, operation3=op3)
    patcher.patch()
    output = patcher.get_output()
"""

import json
import logging
import re
import ifcopenshell
import ifcopenshell.api
import ifcopenshell.api.attribute
import ifcopenshell.util.element
import ifcopenshell.util.selector

logger = logging.getLogger(__name__)


class Patcher:
    """
    Custom patcher for setting attributes on IFC elements using selector syntax.
    
    This recipe:
    - Accepts multiple operations as separate JSON string arguments
    - Uses IfcOpenShell selector syntax to find elements
    - Uses the official ifcopenshell.api.attribute.edit_attributes API
    - Maintains consistency for PredefinedType and ownership history
    - Supports literal values or dynamic extraction with 'from' field
    - Supports regex pattern extraction from source values
    
    Parameters:
        file: The IFC model to patch
        logger: Logger instance for output
        operation1-5: JSON strings for up to 5 operations (only non-empty operations are processed)
        
    Each operation requires 2-3 fields:
        - selector: IfcOpenShell selector syntax string
        - attribute: Attribute name (e.g., "Name", "Description", "ObjectType", "Tag")
        - value: (optional) Literal value to set
        - from: (optional) Source to extract value from, with optional regex pattern
    
    Note: Either 'value' OR 'from' must be provided (not both)
    
    'from' field format:
        - Property: "Pset_WallCommon.Status"
        - Attribute: "Name", "Description"
        - Material: "material.Name"
        - Type: "type.Name"
        - With regex: "material.Name=/S[0-9]{3}[A-Za-z0-9]*/"
    
    Example (Literal Values):
        op1 = '{"selector": "IfcWall", "attribute": "Name", "value": "My Wall"}'
        
    Example (Extract from Property):
        op1 = '{"selector": "IfcWall", "attribute": "Name", "from": "Pset_WallCommon.Status"}'
        
    Example (Extract with Regex):
        op1 = '{"selector": "IfcElement", "attribute": "Tag", "from": "material.Name=/S[0-9]{3}[A-Za-z0-9]*/"}'
        patcher = Patcher(ifc_file, logger, operation1=op1)
        patcher.patch()
        output = patcher.get_output()
    """
    
    # Common attributes that can be set on most IFC elements
    COMMON_ATTRIBUTES = [
        'Name', 'Description', 'ObjectType', 'Tag', 
        'PredefinedType', 'LongName', 'ObjectPlacement',
        'Representation', 'CompositionType'
    ]
    
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
            operation1: JSON operation string (literal). Example: {"selector": "IfcWall", "attribute": "Name", "value": "My Wall"}
            operation2: JSON operation string (extract from property). Example: {"selector": "IfcWall", "attribute": "Description", "from": "Pset_WallCommon.Status"}
            operation3: JSON operation string (extract with regex). Example: {"selector": "IfcElement", "attribute": "Description", "from": "material.Name=/S[0-9]{3}[A-Za-z0-9]*/"}
            operation4: JSON operation string (extract from type). Example: {"selector": "IfcBeam", "attribute": "Description", "from": "type.Name"}
            operation5: JSON operation string (extract from attribute). Example: {"selector": "IfcColumn", "attribute": "Tag", "from": "Name"}
        """
        self.file = file
        self.logger = logger
        
        self.operations = []
        self.stats = {
            'operations_total': 0,
            'operations_completed': 0,
            'operations_failed': 0,
            'elements_modified': 0,
            'attributes_set': 0
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
            if 'selector' not in op or 'attribute' not in op:
                self.logger.warning(f"Argument {idx + 1}: Missing required fields 'selector' and/or 'attribute', skipping")
                continue
            
            # Validate that either 'value' or 'from' is provided (but not both)
            has_value = 'value' in op
            has_from = 'from' in op
            
            if not has_value and not has_from:
                self.logger.warning(f"Argument {idx + 1}: Must provide either 'value' or 'from' field, skipping")
                continue
            
            if has_value and has_from:
                self.logger.warning(f"Argument {idx + 1}: Cannot provide both 'value' and 'from' fields, skipping")
                continue
            
            # Validate attribute name is a non-empty string
            if not isinstance(op['attribute'], str) or not op['attribute'].strip():
                self.logger.warning(f"Argument {idx + 1}: attribute must be a non-empty string, skipping")
                continue
            
            validated_operations.append(op)
        
        return validated_operations
    
    def _parse_from_field(self, from_string: str):
        """
        Parse the 'from' field to extract source and optional regex pattern.
        
        Args:
            from_string: Format like "source.attribute=/regex/" or "source.attribute"
            
        Returns:
            Tuple of (source, attribute, regex_pattern) where regex_pattern is None if not provided
        """
        # Check if there's a regex pattern (ends with /pattern/)
        regex_pattern = None
        source_path = from_string
        
        if '=/' in from_string and from_string.endswith('/'):
            # Extract regex pattern
            parts = from_string.split('=/', 1)
            source_path = parts[0]
            regex_pattern = parts[1][:-1]  # Remove trailing /
        
        # Parse source path
        if '.' in source_path:
            parts = source_path.split('.', 1)
            source = parts[0]
            attribute = parts[1]
        else:
            # Just an attribute name (like "Name")
            source = None
            attribute = source_path
        
        return source, attribute, regex_pattern
    
    def _extract_value_from_element(self, element, from_string: str):
        """
        Extract value from an element based on the 'from' field specification.
        
        Args:
            element: IFC element
            from_string: Source specification (e.g., "Pset_WallCommon.Status", "material.Name=/S[0-9]{3}/")
            
        Returns:
            Extracted value as string, or None if not found
        """
        try:
            source, attribute, regex_pattern = self._parse_from_field(from_string)
            
            value = None
            
            # Extract value based on source type
            if source is None:
                # Direct attribute on element (e.g., "Name", "Description")
                if hasattr(element, attribute):
                    value = getattr(element, attribute)
                else:
                    self.logger.debug(
                        f"Attribute '{attribute}' not found on element "
                        f"{element.is_a()} (GlobalId: {element.GlobalId})"
                    )
                    return None
                    
            elif source == 'material':
                # Extract from material (e.g., "material.Name")
                materials = ifcopenshell.util.element.get_materials(element)
                if materials:
                    # Get first material if multiple
                    material = materials[0] if isinstance(materials, list) else materials
                    if hasattr(material, attribute):
                        value = getattr(material, attribute)
                    else:
                        self.logger.debug(f"Material attribute '{attribute}' not found")
                        return None
                else:
                    self.logger.debug(f"No material found on element {element.GlobalId}")
                    return None
                    
            elif source == 'type':
                # Extract from type (e.g., "type.Name")
                element_type = ifcopenshell.util.element.get_type(element)
                if element_type and hasattr(element_type, attribute):
                    value = getattr(element_type, attribute)
                else:
                    self.logger.debug(f"Type or type attribute '{attribute}' not found")
                    return None
                    
            else:
                # Assume it's a property set (e.g., "Pset_WallCommon.Status")
                psets = ifcopenshell.util.element.get_psets(element)
                if source in psets and attribute in psets[source]:
                    value = psets[source][attribute]
                else:
                    self.logger.debug(
                        f"Property '{source}.{attribute}' not found on element {element.GlobalId}"
                    )
                    return None
            
            # Convert value to string
            if value is None:
                return None
            
            value_str = str(value)
            
            # Apply regex extraction if pattern provided
            if regex_pattern:
                match = re.search(regex_pattern, value_str)
                if match:
                    # Return the matched group (full match if no groups)
                    extracted = match.group(0)
                    self.logger.debug(
                        f"Regex '{regex_pattern}' matched '{extracted}' from '{value_str}'"
                    )
                    return extracted
                else:
                    self.logger.debug(
                        f"Regex '{regex_pattern}' did not match in '{value_str}'"
                    )
                    return None
            
            return value_str
            
        except Exception as e:
            self.logger.warning(
                f"Failed to extract value from '{from_string}' on element "
                f"{element.is_a()} (GlobalId: {element.GlobalId}): {str(e)}"
            )
            return None
    
    def _set_attribute_on_element(self, element, attribute_name: str, literal_value=None, from_source=None) -> bool:
        """
        Set an attribute on an element using the ifcopenshell API.
        
        Args:
            element: IFC element
            attribute_name: Attribute name to set
            literal_value: Literal value to set (if from_source is None)
            from_source: Source to extract value from (if literal_value is None)
            
        Returns:
            True if attribute was set successfully, False otherwise
        """
        try:
            # Check if the element has this attribute
            if not hasattr(element, attribute_name):
                self.logger.warning(
                    f"Element {element.is_a()} (GlobalId: {element.GlobalId}) "
                    f"does not have attribute '{attribute_name}'"
                )
                return False
            
            # Determine the value to set
            if from_source is not None:
                # Extract value from source
                actual_value = self._extract_value_from_element(element, from_source)
                if actual_value is None:
                    # Could not extract value, skip element
                    return False
            else:
                # Use literal value
                actual_value = literal_value
                # Convert to string if needed
                if not isinstance(actual_value, str):
                    actual_value = str(actual_value)
            
            # Use the official API to edit attributes
            # This maintains ownership history and PredefinedType consistency
            ifcopenshell.api.attribute.edit_attributes(
                self.file,
                product=element,
                attributes={attribute_name: actual_value}
            )
            
            return True
            
        except Exception as e:
            self.logger.warning(
                f"Failed to set attribute '{attribute_name}' on element "
                f"{element.is_a()} (GlobalId: {element.GlobalId}): {str(e)}"
            )
            return False
    
    def _execute_operation(self, operation: dict, operation_idx: int) -> dict:
        """
        Execute a single operation.
        
        Args:
            operation: Operation dictionary
            operation_idx: Index of the operation (for logging)
            
        Returns:
            Dictionary with operation results
        """
        selector_str = operation['selector']
        attribute_name = operation['attribute']
        
        # Determine if using literal value or extraction
        has_from = 'from' in operation
        literal_value = operation.get('value')
        from_source = operation.get('from')
        
        result = {
            'success': False,
            'elements_found': 0,
            'elements_modified': 0,
            'error': None,
            'mode': 'extract' if has_from else 'literal'
        }
        
        try:
            # Select elements using selector syntax
            elements = ifcopenshell.util.selector.filter_elements(self.file, selector_str)
            result['elements_found'] = len(elements)
            
            if len(elements) == 0:
                self.logger.warning(f"No elements matched selector: '{selector_str}'")
                result['success'] = True  # Not an error, just no matches
                return result
            
            if has_from:
                self.logger.info(
                    f"Found {len(elements)} element(s) matching selector '{selector_str}', "
                    f"extracting from: '{from_source}'"
                )
            else:
                self.logger.info(
                    f"Found {len(elements)} element(s) matching selector '{selector_str}', "
                    f"setting literal value: '{literal_value}'"
                )
            
            # Process each element
            modified_count = 0
            for i, element in enumerate(elements):
                # Log progress for large selections
                if len(elements) > 500 and (i + 1) % 500 == 0:
                    self.logger.info(f"Processing element {i + 1}/{len(elements)}")
                
                if self._set_attribute_on_element(element, attribute_name, 
                                                 literal_value=literal_value, 
                                                 from_source=from_source):
                    modified_count += 1
                    self.stats['attributes_set'] += 1
            
            result['elements_modified'] = modified_count
            result['success'] = True
            
            if has_from:
                self.logger.info(
                    f"Successfully set {attribute_name} from '{from_source}' on "
                    f"{modified_count}/{len(elements)} elements"
                )
            else:
                self.logger.info(
                    f"Successfully set {attribute_name}='{literal_value}' on "
                    f"{modified_count}/{len(elements)} elements"
                )
            
            # Update global stats
            self.stats['elements_modified'] += modified_count
            
        except ValueError as e:
            result['error'] = str(e)
            self.logger.error(f"Operation failed: {str(e)}")
        except Exception as e:
            result['error'] = str(e)
            self.logger.error(f"Unexpected error during operation: {str(e)}", exc_info=True)
        
        return result
    
    def patch(self) -> None:
        """
        Execute all operations to patch the IFC file.
        
        This method:
        - Iterates through all parsed operations
        - Selects elements using selector syntax
        - Sets attributes using the ifcopenshell API
        - Tracks statistics and errors
        """
        if self.stats['operations_total'] == 0:
            self.logger.warning("No valid operations to execute")
            return
        
        try:
            # Execute each operation
            for idx, operation in enumerate(self.operations):
                if 'from' in operation:
                    self.logger.info(
                        f"Processing operation: setting '{operation['attribute']}' from "
                        f"'{operation['from']}' on '{operation['selector']}'"
                    )
                else:
                    self.logger.info(
                        f"Processing operation: setting '{operation['attribute']}' = "
                        f"'{operation['value']}' on '{operation['selector']}'"
                    )
                result = self._execute_operation(operation, idx)
                
                if result['success']:
                    self.stats['operations_completed'] += 1
                else:
                    self.stats['operations_failed'] += 1
            
            # Log summary
            self.logger.info(
                f"SetAttributeBySelector: {self.stats['operations_completed']}/"
                f"{self.stats['operations_total']} operations completed, "
                f"{self.stats['elements_modified']} elements modified, "
                f"{self.stats['attributes_set']} attributes set"
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

