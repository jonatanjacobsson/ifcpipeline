"""
SetPropertyBySelector Recipe

This custom recipe writes properties to IFC elements based on selector syntax.
Supports multiple operations with property set merging (not replacement).
Supports literal values or dynamic extraction using 'from' field with regex.

Recipe Name: SetPropertyBySelector
Description: Write properties to IFC elements using selector syntax with data type specification
Author: Jonatan Jacobsson
Date: 2025-01-08

    Example Usage (Literal Values):
    op1 = '{"selector": "IfcWall", "property": "Pset_WallCommon.Status", "data_type": "IfcText", "value": "Approved"}'
    op2 = '{"selector": ".IfcDoor", "property": "Pset_DoorCommon.FireRating", "data_type": "IfcLabel", "value": "FD60"}'
    
    Example Usage (Extract from Source):
    op1 = '{"selector": "IfcWall", "property": "Pset_Custom.TypeName", "data_type": "IfcLabel", "from": "type.Name"}'
    op2 = '{"selector": "IfcElement", "property": "BIP.SteelGrade", "data_type": "IfcLabel", "from": "material.Name=/S[0-9]{3}[A-Za-z0-9]*/"}'
    
    patcher = Patcher(ifc_file, logger, operation1=op1, operation2=op2, operation3=op3)
    patcher.patch()
    output = patcher.get_output()
"""

import json
import logging
import re
import ifcopenshell
import ifcopenshell.guid
import ifcopenshell.util.element
import ifcopenshell.util.selector

logger = logging.getLogger(__name__)


class Patcher:
    """
    Custom patcher for writing properties to IFC elements using selector syntax.
    
    This recipe:
    - Accepts multiple operations as separate JSON string arguments
    - Uses IfcOpenShell selector syntax to find elements
    - Creates or updates property sets (merges with existing)
    - Supports multiple IFC data types
    - Supports literal values or dynamic extraction with 'from' field
    - Supports regex pattern extraction from source values
    
    Parameters:
        file: The IFC model to patch
        logger: Logger instance for output
        operation1-5: JSON strings for up to 5 operations (only non-empty operations are processed)
        
    Each operation requires 3-4 fields:
        - selector: IfcOpenShell selector syntax string
        - property: PropertySetName.PropertyName (e.g., "Pset_Custom.Status")
        - data_type: IFC data type (IfcText, IfcInteger, IfcReal, IfcBoolean, IfcLabel, IfcIdentifier)
        - value: (optional) Literal value to set
        - from: (optional) Source to extract value from, with optional regex pattern
    
    Note: Either 'value' OR 'from' must be provided (not both)
    
    'from' field format:
        - Property: "Pset_WallCommon.Status"
        - Attribute: "Name", "Description"
        - Material: "material.Name"
        - Type: "type.Name"
        - With regex: "material.Name=/S[0-9]{3}[A-Za-z0-9]*/"
    
    Example (Literal):
        op1 = '{"selector": "IfcWall", "property": "Pset_WallCommon.Status", "data_type": "IfcText", "value": "Approved"}'
        
    Example (Extract):
        op1 = '{"selector": "IfcElement", "property": "BIP.SteelGrade", "data_type": "IfcLabel", "from": "material.Name=/S[0-9]{3}[A-Za-z0-9]*/"}'
        patcher = Patcher(ifc_file, logger, operation1=op1)
        patcher.patch()
        output = patcher.get_output()
    """
    
    # Supported IFC data types and their Python type converters
    SUPPORTED_DATA_TYPES = {
        'IfcText': str,
        'IfcLabel': str,
        'IfcIdentifier': str,
        'IfcInteger': int,
        'IfcReal': float,
        'IfcBoolean': lambda x: x if isinstance(x, bool) else str(x).lower() in ('true', '1', 'yes')
    }
    
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
            operation1: JSON operation string (literal). Example: {"selector": "IfcWall", "property": "Pset_WallCommon.Status", "data_type": "IfcText", "value": "Approved"}
            operation2: JSON operation string (extract from property). Example: {"selector": "IfcWall", "property": "Pset_WallCommon.FireRating", "data_type": "IfcLabel", "from": "BIP.FireRating"}
            operation3: JSON operation string (extract with regex). Example: {"selector": "IfcElement", "property": "BIP.SteelGrade", "data_type": "IfcLabel", "from": "material.Name=/S[0-9]{3}[A-Za-z0-9]*/"}
            operation4: JSON operation string (extract from attribute). Example: {"selector": "IfcBeam", "property": "Pset_WallCommon.Reference", "data_type": "IfcLabel", "from": "Name"}
            operation5: JSON operation string (literal boolean). Example: {"selector": "IfcColumn", "property": "Pset_ColumnCommon.LoadBearing", "data_type": "IfcBoolean", "value": true}
        """
        self.file = file
        self.logger = logger
        
        self.operations = []
        self.stats = {
            'operations_total': 0,
            'operations_completed': 0,
            'operations_failed': 0,
            'elements_modified': 0,
            'properties_set': 0
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
            if 'selector' not in op or 'property' not in op or 'data_type' not in op:
                self.logger.warning(f"Argument {idx + 1}: Missing required fields 'selector', 'property', and/or 'data_type', skipping")
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
            
            # Validate property format (must contain dot)
            if '.' not in op['property']:
                self.logger.warning(f"Argument {idx + 1}: property '{op['property']}' must be in format 'PropertySetName.PropertyName', skipping")
                continue
            
            # Validate data type
            if op['data_type'] not in self.SUPPORTED_DATA_TYPES:
                self.logger.warning(f"Argument {idx + 1}: unsupported data type '{op['data_type']}', skipping. Supported: {list(self.SUPPORTED_DATA_TYPES.keys())}")
                continue
            
            validated_operations.append(op)
        
        return validated_operations
    
    def _convert_value(self, value, data_type: str):
        """
        Convert a value to the appropriate Python type for the given IFC data type.
        
        Args:
            value: Value to convert
            data_type: Target IFC data type
            
        Returns:
            Converted value
            
        Raises:
            ValueError: If conversion fails
        """
        try:
            converter = self.SUPPORTED_DATA_TYPES[data_type]
            return converter(value)
        except Exception as e:
            raise ValueError(f"Cannot convert value '{value}' to {data_type}: {str(e)}")
    
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
    
    def _get_or_create_owner_history(self):
        """
        Get existing OwnerHistory or create a minimal one if none exists.
        
        Returns:
            IfcOwnerHistory entity
        """
        owner_histories = self.file.by_type("IfcOwnerHistory")
        if owner_histories:
            return owner_histories[0]
        
        # If no owner history exists, create a minimal one
        # This is a fallback and should rarely be needed in valid IFC files
        self.logger.warning("No IfcOwnerHistory found in file, creating minimal one")
        
        # Get or create required entities
        person_orgs = self.file.by_type("IfcPersonAndOrganization")
        application = self.file.by_type("IfcApplication")
        
        if not person_orgs:
            person = self.file.create_entity("IfcPerson", None, None, None)
            org = self.file.create_entity("IfcOrganization", None, "Unknown")
            person_org = self.file.create_entity("IfcPersonAndOrganization", person, org)
        else:
            person_org = person_orgs[0]
        
        if not application:
            app = self.file.create_entity("IfcApplication", 
                                         person_org.TheOrganization if hasattr(person_org, 'TheOrganization') else person_org,
                                         "Unknown", "Unknown", "Unknown")
        else:
            app = application[0]
        
        owner_history = self.file.create_entity("IfcOwnerHistory",
                                                person_org, app, None, None, None, None, None, 0)
        return owner_history
    
    def _find_property_set(self, element, pset_name: str):
        """
        Find an existing property set on an element.
        
        Args:
            element: IFC element
            pset_name: Name of the property set to find
            
        Returns:
            Tuple of (IfcPropertySet entity or None, IfcRelDefinesByProperties or None)
        """
        # Get all property sets for the element
        if not hasattr(element, 'IsDefinedBy') or not element.IsDefinedBy:
            return None, None
        
        for rel in element.IsDefinedBy:
            if rel.is_a('IfcRelDefinesByProperties'):
                related_props = rel.RelatingPropertyDefinition
                if related_props.is_a('IfcPropertySet') and related_props.Name == pset_name:
                    return related_props, rel
        
        return None, None
    
    def _find_property_in_set(self, property_set, property_name: str):
        """
        Find a specific property within a property set.
        
        Args:
            property_set: IfcPropertySet entity
            property_name: Name of the property to find
            
        Returns:
            IfcPropertySingleValue entity or None
        """
        if not hasattr(property_set, 'HasProperties') or not property_set.HasProperties:
            return None
        
        for prop in property_set.HasProperties:
            if prop.is_a('IfcPropertySingleValue') and prop.Name == property_name:
                return prop
        
        return None
    
    def _create_property_value(self, property_name: str, data_type: str, value):
        """
        Create an IfcPropertySingleValue entity.
        
        Args:
            property_name: Name of the property
            data_type: IFC data type
            value: Converted value
            
        Returns:
            IfcPropertySingleValue entity
        """
        # Create the typed value entity
        typed_value = self.file.create_entity(data_type, value)
        
        # Create the property single value
        prop_single_value = self.file.create_entity(
            "IfcPropertySingleValue",
            property_name,
            None,  # Description
            typed_value,
            None   # Unit
        )
        
        return prop_single_value
    
    def _update_property_value(self, property_entity, data_type: str, value):
        """
        Update an existing property's value.
        
        Args:
            property_entity: Existing IfcPropertySingleValue
            data_type: IFC data type
            value: Converted value
        """
        # Create new typed value
        typed_value = self.file.create_entity(data_type, value)
        
        # Update the NominalValue attribute
        property_entity.NominalValue = typed_value
    
    def _create_property_set(self, element, pset_name: str, property_name: str, 
                            data_type: str, value):
        """
        Create a new property set and link it to an element.
        
        Args:
            element: IFC element
            pset_name: Name of the property set
            property_name: Name of the property
            data_type: IFC data type
            value: Converted value
        """
        owner_history = self._get_or_create_owner_history()
        
        # Create property value
        prop_value = self._create_property_value(property_name, data_type, value)
        
        # Create property set
        property_set = self.file.create_entity(
            "IfcPropertySet",
            ifcopenshell.guid.new(),
            owner_history,
            pset_name,
            None,  # Description
            [prop_value]
        )
        
        # Link to element via IfcRelDefinesByProperties
        self.file.create_entity(
            "IfcRelDefinesByProperties",
            ifcopenshell.guid.new(),
            owner_history,
            None,  # Name
            None,  # Description
            [element],
            property_set
        )
    
    def _add_property_to_existing_set(self, property_set, property_name: str,
                                      data_type: str, value):
        """
        Add a new property to an existing property set.
        
        Args:
            property_set: Existing IfcPropertySet
            property_name: Name of the property
            data_type: IFC data type
            value: Converted value
        """
        # Create new property value
        prop_value = self._create_property_value(property_name, data_type, value)
        
        # Add to the HasProperties list
        if property_set.HasProperties:
            properties_list = list(property_set.HasProperties)
            properties_list.append(prop_value)
            property_set.HasProperties = properties_list
        else:
            property_set.HasProperties = [prop_value]
    
    def _set_property_on_element(self, element, pset_name: str, property_name: str,
                                 data_type: str, literal_value=None, from_source=None) -> bool:
        """
        Set a property on an element, creating or updating as needed.
        
        Args:
            element: IFC element
            pset_name: Property set name
            property_name: Property name
            data_type: IFC data type
            literal_value: Literal value to set (if from_source is None)
            from_source: Source to extract value from (if literal_value is None)
            
        Returns:
            True if property was set successfully, False otherwise
        """
        try:
            # Determine the value to set
            if from_source is not None:
                # Extract value from source
                extracted_value = self._extract_value_from_element(element, from_source)
                if extracted_value is None:
                    # Could not extract value, skip element
                    return False
                raw_value = extracted_value
            else:
                # Use literal value
                raw_value = literal_value
            
            # Convert to appropriate type
            converted_value = self._convert_value(raw_value, data_type)
            
            # Find existing property set
            property_set, rel = self._find_property_set(element, pset_name)
            
            if property_set:
                # Property set exists, check if property exists
                existing_property = self._find_property_in_set(property_set, property_name)
                
                if existing_property:
                    # Update existing property
                    self._update_property_value(existing_property, data_type, converted_value)
                else:
                    # Add new property to existing set
                    self._add_property_to_existing_set(property_set, property_name, data_type, converted_value)
            else:
                # Create new property set
                self._create_property_set(element, pset_name, property_name, data_type, converted_value)
            
            return True
            
        except Exception as e:
            self.logger.warning(f"Failed to set property on element {element.GlobalId}: {str(e)}")
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
        property_path = operation['property']
        data_type = operation['data_type']
        
        # Determine if using literal value or extraction
        has_from = 'from' in operation
        literal_value = operation.get('value')
        from_source = operation.get('from')
        
        # Parse property path
        pset_name, property_name = property_path.split('.', 1)
        
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
                
                if self._set_property_on_element(element, pset_name, property_name, 
                                                 data_type, literal_value=literal_value,
                                                 from_source=from_source):
                    modified_count += 1
                    self.stats['properties_set'] += 1
            
            result['elements_modified'] = modified_count
            result['success'] = True
            
            if has_from:
                self.logger.info(
                    f"Successfully set {pset_name}.{property_name} from '{from_source}' on "
                    f"{modified_count}/{len(elements)} elements"
                )
            else:
                self.logger.info(
                    f"Successfully set '{literal_value}' in {pset_name}.{property_name} on "
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
        - Creates or updates property sets
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
                        f"Processing operation: setting '{operation['property']}' from "
                        f"'{operation['from']}' on '{operation['selector']}'"
                    )
                else:
                    self.logger.info(
                        f"Processing operation: setting '{operation['property']}' = "
                        f"'{operation['value']}' on '{operation['selector']}'"
                    )
                result = self._execute_operation(operation, idx)
                
                if result['success']:
                    self.stats['operations_completed'] += 1
                else:
                    self.stats['operations_failed'] += 1
            
            # Log summary
            self.logger.info(f"SetPropertyBySelector: {self.stats['operations_completed']}/{self.stats['operations_total']} operations completed, "
                           f"{self.stats['elements_modified']} elements modified, {self.stats['properties_set']} properties set")
            
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

