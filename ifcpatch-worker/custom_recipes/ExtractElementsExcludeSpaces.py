"""
ExtractElementsExcludeSpaces - Extract elements while excluding IfcSpaces

This recipe extends ExtractElements but excludes IfcSpace elements from the output,
even if they contain the selected elements. Other spatial structures (IfcBuildingStorey,
IfcBuilding, etc.) are still included.

Recipe Name: ExtractElementsExcludeSpaces
Description: Extract elements while excluding IfcSpaces from spatial hierarchy
Author: IFC Pipeline Team
"""

import logging
import ifcopenshell
import ifcopenshell.api
import ifcopenshell.api.project
import ifcopenshell.guid
import ifcopenshell.util.selector
from typing import Union, List
from logging import Logger


class Patcher:
    def __init__(
        self,
        file: ifcopenshell.file,
        logger: Union[Logger, None] = None,
        query: str = "IfcWall",
        assume_asset_uniqueness_by_name: bool = True,
        exclude: Union[List[str], None] = None,
    ):
        """Extract certain elements into a new model, excluding specified spatial structure types

        Extract a subset of elements from an existing IFC data set and save it
        to a new IFC file. Unlike the standard ExtractElements recipe, this allows
        you to exclude certain spatial structure types (like IfcSpace) from being
        included even if they contain the selected elements.

        :param query: A query to select the subset of IFC elements.
        :param assume_asset_uniqueness_by_name: Avoid adding assets (profiles, materials, styles)
            with the same name multiple times. Which helps in avoiding duplicated assets.
        :param exclude: List of spatial structure types to exclude (e.g., ["IfcSpace"]).
            If None, defaults to ["IfcSpace"].

        Example:

        .. code:: python

            # Extract all walls, excluding IfcSpaces
            ifcpatch.execute({
                "input": "input.ifc", 
                "file": model, 
                "recipe": "ExtractElementsExcludeSpaces", 
                "arguments": ["IfcWall"]
            })

            # Extract walls and slabs, excluding IfcSpaces
            ifcpatch.execute({
                "input": "input.ifc", 
                "file": model, 
                "recipe": "ExtractElementsExcludeSpaces", 
                "arguments": ["IfcWall, IfcSlab"]
            })

            # Extract walls, excluding both IfcSpace and IfcZone
            ifcpatch.execute({
                "input": "input.ifc", 
                "file": model, 
                "recipe": "ExtractElementsExcludeSpaces", 
                "arguments": ["IfcWall", True, ["IfcSpace", "IfcZone"]]
            })
            
            # Note: The exclude parameter defaults to ["IfcSpace"] if not provided
        """
        self.file = file
        self.logger = logger if logger else logging.getLogger(__name__)
        self.query = query
        
        # Handle boolean conversion (in case it's passed as string "true"/"false")
        if isinstance(assume_asset_uniqueness_by_name, str):
            self.assume_asset_uniqueness_by_name = assume_asset_uniqueness_by_name.lower() in ('true', '1', 'yes')
        else:
            self.assume_asset_uniqueness_by_name = bool(assume_asset_uniqueness_by_name)
        
        # Default to excluding IfcSpace if not specified
        if exclude is None:
            self.exclude = ["IfcSpace"]
        elif isinstance(exclude, str):
            # Handle case where exclude is passed as a single string
            self.exclude = [exclude]
        elif isinstance(exclude, list):
            self.exclude = exclude
        else:
            # Try to convert to list
            self.exclude = list(exclude) if exclude else ["IfcSpace"]
        
        # Convert to set for faster lookup
        self.exclude_set = set(self.exclude)
        
        if self.logger:
            self.logger.info(f"Query: {self.query}, Excluding spatial types: {self.exclude}")

    def patch(self):
        self.contained_ins: dict[str, set[ifcopenshell.entity_instance]] = {}
        self.aggregates: dict[str, set[ifcopenshell.entity_instance]] = {}
        self.new = ifcopenshell.file(schema_version=self.file.schema_version)
        self.owner_history = None
        self.reuse_identities: dict[int, ifcopenshell.entity_instance] = {}
        for owner_history in self.file.by_type("IfcOwnerHistory"):
            self.owner_history = self.new.add(owner_history)
            break
        self.add_element(self.file.by_type("IfcProject")[0])
        for element in ifcopenshell.util.selector.filter_elements(self.file, self.query):
            self.add_element(element)
        self.create_spatial_tree()
        self.file = self.new

    def add_element(self, element: ifcopenshell.entity_instance) -> None:
        new_element = self.append_asset(element)
        if not new_element:
            return
        self.add_spatial_structures(element, new_element)
        self.add_decomposition_parents(element, new_element)

    def append_asset(self, element: ifcopenshell.entity_instance) -> Union[ifcopenshell.entity_instance, None]:
        try:
            return self.new.by_guid(element.GlobalId)
        except:
            pass
        if element.is_a("IfcProject"):
            return self.new.add(element)
        return ifcopenshell.api.project.append_asset(
            self.new,
            library=self.file,
            element=element,
            reuse_identities=self.reuse_identities,
            assume_asset_uniqueness_by_name=self.assume_asset_uniqueness_by_name,
        )

    def add_spatial_structures(
        self, element: ifcopenshell.entity_instance, new_element: ifcopenshell.entity_instance
    ) -> None:
        """element is IfcElement - Modified to exclude specified spatial types"""
        for rel in getattr(element, "ContainedInStructure", []):
            spatial_element = rel.RelatingStructure
            
            # Check if this spatial element type should be excluded
            if self._should_exclude_spatial_element(spatial_element):
                if self.logger:
                    self.logger.debug(f"Excluding spatial element {spatial_element.is_a()}: {spatial_element.GlobalId}")
                # Skip this spatial structure, but continue up the hierarchy
                # by checking if it has a parent spatial structure
                self._add_parent_spatial_structures(spatial_element, new_element)
                continue
            
            new_spatial_element = self.append_asset(spatial_element)
            self.contained_ins.setdefault(spatial_element.GlobalId, set()).add(new_element)
            self.add_decomposition_parents(spatial_element, new_spatial_element)

    def _should_exclude_spatial_element(self, spatial_element: ifcopenshell.entity_instance) -> bool:
        """Check if a spatial element should be excluded"""
        # Check if the element's type or any of its parent types are in the exclude list
        element_type = spatial_element.is_a()
        
        # Direct type check
        if element_type in self.exclude_set:
            return True
        
        # Check if any excluded type is a parent class of this element
        # This handles cases where we might want to exclude subtypes
        for excluded_type in self.exclude_set:
            if spatial_element.is_a(excluded_type):
                return True
        
        return False

    def _add_parent_spatial_structures(
        self, excluded_element: ifcopenshell.entity_instance, new_element: ifcopenshell.entity_instance
    ) -> None:
        """Add parent spatial structures when an intermediate one is excluded"""
        # Check if the excluded element is contained in another spatial structure
        for rel in getattr(excluded_element, "ContainedInStructure", []):
            parent_spatial = rel.RelatingStructure
            if not self._should_exclude_spatial_element(parent_spatial):
                # This parent is not excluded, so add it
                new_parent_spatial = self.append_asset(parent_spatial)
                self.contained_ins.setdefault(parent_spatial.GlobalId, set()).add(new_element)
                self.add_decomposition_parents(parent_spatial, new_parent_spatial)
                # Also check for further parents
                self._add_parent_spatial_structures(parent_spatial, new_element)

    def add_decomposition_parents(
        self, element: ifcopenshell.entity_instance, new_element: ifcopenshell.entity_instance
    ) -> None:
        """element is IfcObjectDefinition"""
        for rel in element.Decomposes:
            parent = rel.RelatingObject
            
            # Check if parent is an excluded spatial type
            if self._should_exclude_spatial_element(parent):
                # Skip this parent but continue up the hierarchy
                self.add_decomposition_parents(parent, new_element)
                continue
            
            new_parent = self.append_asset(parent)
            self.aggregates.setdefault(parent.GlobalId, set()).add(new_element)
            self.add_decomposition_parents(parent, new_parent)
            self.add_spatial_structures(parent, new_parent)

    def create_spatial_tree(self) -> None:
        """Create spatial relationships, filtering out excluded spatial types"""
        for relating_structure_guid, related_elements in self.contained_ins.items():
            try:
                relating_structure = self.new.by_guid(relating_structure_guid)
                
                # Double-check: don't create relationships for excluded types
                if self._should_exclude_spatial_element(relating_structure):
                    if self.logger:
                        self.logger.debug(f"Skipping relationship creation for excluded spatial element: {relating_structure.is_a()}")
                    continue
                
                self.new.createIfcRelContainedInSpatialStructure(
                    ifcopenshell.guid.new(),
                    self.owner_history,
                    None,
                    None,
                    list(related_elements),
                    relating_structure,
                )
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Could not create spatial relationship for {relating_structure_guid}: {e}")
                continue
        
        for relating_object_guid, related_objects in self.aggregates.items():
            try:
                relating_object = self.new.by_guid(relating_object_guid)
                
                # Double-check: don't create relationships for excluded types
                if self._should_exclude_spatial_element(relating_object):
                    if self.logger:
                        self.logger.debug(f"Skipping aggregate relationship creation for excluded element: {relating_object.is_a()}")
                    continue
                
                self.new.createIfcRelAggregates(
                    ifcopenshell.guid.new(),
                    self.owner_history,
                    None,
                    None,
                    relating_object,
                    list(related_objects),
                )
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Could not create aggregate relationship for {relating_object_guid}: {e}")
                continue
    
    def get_output(self) -> ifcopenshell.file:
        """
        Return the patched IFC file.
        
        Returns:
            The modified IFC file object
        """
        return self.file
