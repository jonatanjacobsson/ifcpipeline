"""
SetPropertyBySelector Recipe

This custom recipe writes properties to IFC elements based on selector syntax.
Supports multiple operations with property set merging (not replacement).

Recipe Name: SetPropertyBySelector
Description: Write properties to IFC elements using selector syntax with data type specification
Author: IFC Pipeline Team
Date: 2025-01-08

    Example Usage:
    op1 = '{"selector": "IfcWall", "property": "Pset_WallCommon.Status", "data_type": "IfcText", "value": "Approved"}'
    op2 = '{"selector": ".IfcDoor", "property": "Pset_DoorCommon.FireRating", "data_type": "IfcLabel", "value": "FD60"}'
    op3 = '{"selector": "IfcSlab[LoadBearing=TRUE]", "property": "Pset_SlabCommon.IsExternal", "data_type": "IfcBoolean", "value": false}'
    
    patcher = Patcher(ifc_file, logger, operation1=op1, operation2=op2, operation3=op3)
    patcher.patch()
    output = patcher.get_output()
"""

import json
import logging
import ifcopenshell
import ifcopenshell.guid
import ifcopenshell.util.element
import ifcopenshell.util.selector
from ifcpatch import BasePatcher

logger = logging.getLogger(__name__)


class Patcher(BasePatcher):
    """
    Custom patcher for writing properties to IFC elements using selector syntax.
    
    This recipe:
    - Accepts multiple operations as separate JSON string arguments
    - Uses IfcOpenShell selector syntax to find elements
    - Creates or updates property sets (merges with existing)
    - Supports multiple IFC data types
    
    Parameters:
        file: The IFC model to patch
        logger: Logger instance for output
        operation1-5: JSON strings for up to 5 operations (only non-empty operations are processed)
        
    Each operation requires 4 fields:
        - selector: IfcOpenShell selector syntax string
        - property: PropertySetName.PropertyName (e.g., "Pset_Custom.Status")
        - data_type: IFC data type (IfcText, IfcInteger, IfcReal, IfcBoolean, IfcLabel, IfcIdentifier)
        - value: The value to set (will be converted to specified type)
    
    Example:
        op1 = '{"selector": "IfcWall", "property": "Pset_WallCommon.Status", "data_type": "IfcText", "value": "Approved"}'
        op2 = '{"selector": ".IfcDoor", "property": "Pset_DoorCommon.FireRating", "data_type": "IfcLabel", "value": "FD60"}'
        op3 = '{"selector": "IfcSlab[LoadBearing=TRUE]", "property": "Pset_SlabCommon.IsExternal", "data_type": "IfcBoolean", "value": false}'
        patcher = Patcher(ifc_file, logger, operation1=op1, operation2=op2, operation3=op3)
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
            operation1: JSON operation string. Example: {"selector": "IfcWall", "property": "Pset_WallCommon.Status", "data_type": "IfcText", "value": "Approved"}
            operation2: JSON operation string. Example: {"selector": ".IfcDoor", "property": "Pset_DoorCommon.FireRating", "data_type": "IfcLabel", "value": "FD60"}
            operation3: JSON operation string. Example: {"selector": "IfcSlab[LoadBearing=TRUE]", "property": "Pset_SlabCommon.IsExternal", "data_type": "IfcBoolean", "value": false}
            operation4: JSON operation string. Example: {"selector": "IfcBeam", "property": "Qto_BeamBaseQuantities.Length", "data_type": "IfcReal", "value": 3500.5}
            operation5: JSON operation string. Example: {"selector": "IfcColumn", "property": "Pset_ColumnCommon.Reference", "data_type": "IfcIdentifier", "value": "C-01"}
        """
        super().__init__(file, logger)
        
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
            required_fields = ['selector', 'property', 'data_type', 'value']
            missing_fields = [f for f in required_fields if f not in op]
            
            if missing_fields:
                self.logger.warning(f"Argument {idx + 1}: Missing required fields: {missing_fields}, skipping")
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
                                 data_type: str, value) -> bool:
        """
        Set a property on an element, creating or updating as needed.
        
        Args:
            element: IFC element
            pset_name: Property set name
            property_name: Property name
            data_type: IFC data type
            value: Converted value
            
        Returns:
            True if property was set successfully, False otherwise
        """
        try:
            # Find existing property set
            property_set, rel = self._find_property_set(element, pset_name)
            
            if property_set:
                # Property set exists, check if property exists
                existing_property = self._find_property_in_set(property_set, property_name)
                
                if existing_property:
                    # Update existing property
                    self._update_property_value(existing_property, data_type, value)
                else:
                    # Add new property to existing set
                    self._add_property_to_existing_set(property_set, property_name, data_type, value)
            else:
                # Create new property set
                self._create_property_set(element, pset_name, property_name, data_type, value)
            
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
        raw_value = operation['value']
        
        # Parse property path
        pset_name, property_name = property_path.split('.', 1)
        
        result = {
            'success': False,
            'elements_found': 0,
            'elements_modified': 0,
            'error': None
        }
        
        try:
            # Convert value to appropriate type
            converted_value = self._convert_value(raw_value, data_type)
            
            # Select elements using selector syntax
            elements = ifcopenshell.util.selector.filter_elements(self.file, selector_str)
            result['elements_found'] = len(elements)
            
            if len(elements) == 0:
                self.logger.warning(f"No elements matched selector: '{selector_str}'")
                result['success'] = True  # Not an error, just no matches
                return result
            
            self.logger.info(f"Found {len(elements)} element(s) matching selector '{selector_str}'")
            
            # Process each element
            modified_count = 0
            for i, element in enumerate(elements):
                # Log progress for large selections
                if len(elements) > 500 and (i + 1) % 500 == 0:
                    self.logger.info(f"Processing element {i + 1}/{len(elements)}")
                
                if self._set_property_on_element(element, pset_name, property_name, 
                                                 data_type, converted_value):
                    modified_count += 1
                    self.stats['properties_set'] += 1
            
            result['elements_modified'] = modified_count
            result['success'] = True
            
            self.logger.info(f"Successfully set '{raw_value}' in {pset_name}.{property_name} on {modified_count}/{len(elements)} elements")
            
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
                self.logger.info(f"Processing operation '{operation['value']}' into '{operation['property']}' on '{operation['selector']}'")
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

