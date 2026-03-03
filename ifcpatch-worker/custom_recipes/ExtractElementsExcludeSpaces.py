"""
ExtractElementsExcludeSpaces - Extract elements while excluding specified types

This recipe extends ExtractElements with two exclusion mechanisms:
1. Spatial hierarchy filtering - excluded types are skipped during tree traversal
   so they never appear as containing structures.
2. Post-processing removal - after the new file is built, any instances of the
   excluded types that were pulled in through forward references (shared placements,
   representations, etc.) are removed from the output.

By default IfcSpace is excluded. You can supply any IFC type names (spatial or
non-spatial) such as IfcDuctSegment, IfcPipeFitting, etc.

Recipe Name: ExtractElementsExcludeSpaces
Description: Extract elements while excluding specified types from output
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
        """Extract certain elements into a new model, excluding specified types

        Extract a subset of elements from an existing IFC data set and save it
        to a new IFC file. Excluded types are removed from the spatial hierarchy
        during extraction AND stripped from the output file via post-processing,
        so they are completely absent from the result.

        :param query: A query to select the subset of IFC elements.
        :param assume_asset_uniqueness_by_name: Avoid adding assets (profiles, materials, styles)
            with the same name multiple times. Which helps in avoiding duplicated assets.
        :param exclude: IFC type names to exclude from the output. Accepts a list
            (e.g. ["IfcSpace"]) or a comma-separated string
            (e.g. "IfcDuctSegment, IfcPipeFitting"). Works for both spatial
            structure types and regular element types. Defaults to ["IfcSpace"].

        Example:

        .. code:: python

            # Extract all walls, excluding IfcSpaces (default)
            ifcpatch.execute({
                "input": "input.ifc",
                "file": model,
                "recipe": "ExtractElementsExcludeSpaces",
                "arguments": ["IfcWall"]
            })

            # Extract coverings, excluding duct/pipe elements that may be
            # pulled in through shared references
            ifcpatch.execute({
                "input": "input.ifc",
                "file": model,
                "recipe": "ExtractElementsExcludeSpaces",
                "arguments": [
                    "IfcCovering",
                    False,
                    "IfcDuctSegment, IfcDuctFitting, IfcPipeSegment, IfcPipeFitting"
                ]
            })

            # Extract walls, excluding both IfcSpace and IfcZone
            ifcpatch.execute({
                "input": "input.ifc",
                "file": model,
                "recipe": "ExtractElementsExcludeSpaces",
                "arguments": ["IfcWall", True, ["IfcSpace", "IfcZone"]]
            })
        """
        self.file = file
        self.logger = logger if logger else logging.getLogger(__name__)
        self.query = query.strip().rstrip(",").strip() if isinstance(query, str) else query
        
        # Handle boolean conversion (in case it's passed as string "true"/"false")
        if isinstance(assume_asset_uniqueness_by_name, str):
            self.assume_asset_uniqueness_by_name = assume_asset_uniqueness_by_name.lower() in ('true', '1', 'yes')
        else:
            self.assume_asset_uniqueness_by_name = bool(assume_asset_uniqueness_by_name)
        
        # Default to excluding IfcSpace if not specified
        if exclude is None:
            self.exclude = ["IfcSpace"]
        elif isinstance(exclude, str):
            self.exclude = [t.strip() for t in exclude.split(",") if t.strip()]
        elif isinstance(exclude, list):
            self.exclude = exclude
        else:
            # Try to convert to list
            self.exclude = list(exclude) if exclude else ["IfcSpace"]
        
        # Convert to set for faster lookup
        self.exclude_set = set(self.exclude)
        
        if self.logger:
            self.logger.info(f"Query: {self.query}, Excluding types: {self.exclude}")

    def patch(self):
        self.contained_ins: dict[str, set[ifcopenshell.entity_instance]] = {}
        self.aggregates: dict[str, set[ifcopenshell.entity_instance]] = {}
        self.new = ifcopenshell.file(schema_version=self.file.schema_version)
        self.owner_history = None
        self.reuse_identities: dict[int, ifcopenshell.entity_instance] = {}
        self._added_guids: set[str] = set()
        self._hierarchy_done: set[str] = set()
        for owner_history in self.file.by_type("IfcOwnerHistory"):
            self.owner_history = self.new.add(owner_history)
            break
        self.add_element(self.file.by_type("IfcProject")[0])
        for element in ifcopenshell.util.selector.filter_elements(self.file, self.query):
            self.add_element(element)
        self.create_spatial_tree()
        self._remove_excluded_elements()
        self.file = self.new

    def add_element(self, element: ifcopenshell.entity_instance) -> None:
        new_element = self.append_asset(element)
        if not new_element:
            return
        self.add_spatial_structures(element, new_element)
        self.add_decomposition_parents(element, new_element)

    def append_asset(self, element: ifcopenshell.entity_instance) -> Union[ifcopenshell.entity_instance, None]:
        guid = element.GlobalId
        if guid in self._added_guids:
            return self.new.by_guid(guid)
        if element.is_a("IfcProject"):
            self._added_guids.add(guid)
            return self.new.add(element)
        result = ifcopenshell.api.project.append_asset(
            self.new,
            library=self.file,
            element=element,
            reuse_identities=self.reuse_identities,
            assume_asset_uniqueness_by_name=self.assume_asset_uniqueness_by_name,
        )
        if result:
            self._added_guids.add(guid)
        return result

    def add_spatial_structures(
        self, element: ifcopenshell.entity_instance, new_element: ifcopenshell.entity_instance
    ) -> None:
        """element is IfcElement - Modified to exclude specified spatial types"""
        for rel in getattr(element, "ContainedInStructure", []):
            spatial_element = rel.RelatingStructure

            if self._should_exclude_spatial_element(spatial_element):
                self._add_parent_spatial_structures(spatial_element, new_element)
                continue

            new_spatial_element = self.append_asset(spatial_element)
            self.contained_ins.setdefault(spatial_element.GlobalId, set()).add(new_element)
            if spatial_element.GlobalId not in self._hierarchy_done:
                self._hierarchy_done.add(spatial_element.GlobalId)
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

    def _remove_excluded_elements(self) -> None:
        """Post-process the output file to remove all instances of excluded types.

        This catches elements that were pulled into self.new through forward
        references in append_asset (shared placements, representations, etc.)
        even though they were never explicitly selected by the query.

        Uses a two-pass bulk approach instead of per-element remove_product
        so that large numbers of exclusions don't cause O(n * m) relationship
        scanning.
        """
        to_remove: list[ifcopenshell.entity_instance] = []
        for excluded_type in self.exclude_set:
            try:
                elements = self.new.by_type(excluded_type)
            except Exception:
                continue
            if elements:
                self.logger.info(
                    f"Post-process: found {len(elements)} {excluded_type} element(s) to remove"
                )
                to_remove.extend(elements)

        if not to_remove:
            return

        remove_ids = {e.id() for e in to_remove}
        self.logger.info(f"Post-process: removing {len(remove_ids)} element(s) total")

        # Pass 1 – detach from relationships so remove() won't leave dangling refs
        for rel in list(self.new.by_type("IfcRelationship")):
            try:
                info = rel.get_info(recursive=False)
            except Exception:
                continue
            dirty = False
            for attr_name, val in info.items():
                if attr_name in ("id", "type"):
                    continue
                if isinstance(val, ifcopenshell.entity_instance) and val.id() in remove_ids:
                    try:
                        setattr(rel, attr_name, None)
                        dirty = True
                    except Exception:
                        pass
                elif isinstance(val, (tuple, list)):
                    filtered = [v for v in val if not (isinstance(v, ifcopenshell.entity_instance) and v.id() in remove_ids)]
                    if len(filtered) != len(val):
                        try:
                            setattr(rel, attr_name, filtered)
                            dirty = True
                        except Exception:
                            pass
            if dirty:
                # Remove the relationship entirely if it has no related objects left
                try:
                    info2 = rel.get_info(recursive=False)
                    related_attrs = [k for k in info2 if k.startswith("Related")]
                    if related_attrs and all(
                        not info2[k] or info2[k] == () for k in related_attrs
                    ):
                        self.new.remove(rel)
                except Exception:
                    pass

        # Pass 2 – remove the elements themselves
        for element in to_remove:
            try:
                self.new.remove(element)
            except Exception as e:
                self.logger.warning(
                    f"Could not remove {element.is_a()} #{element.id()}: {e}"
                )

    def _add_parent_spatial_structures(
        self, excluded_element: ifcopenshell.entity_instance, new_element: ifcopenshell.entity_instance
    ) -> None:
        """Add parent spatial structures when an intermediate one is excluded"""
        for rel in getattr(excluded_element, "ContainedInStructure", []):
            parent_spatial = rel.RelatingStructure
            if not self._should_exclude_spatial_element(parent_spatial):
                new_parent_spatial = self.append_asset(parent_spatial)
                self.contained_ins.setdefault(parent_spatial.GlobalId, set()).add(new_element)
                if parent_spatial.GlobalId not in self._hierarchy_done:
                    self._hierarchy_done.add(parent_spatial.GlobalId)
                    self.add_decomposition_parents(parent_spatial, new_parent_spatial)

    def add_decomposition_parents(
        self, element: ifcopenshell.entity_instance, new_element: ifcopenshell.entity_instance
    ) -> None:
        """element is IfcObjectDefinition"""
        for rel in element.Decomposes:
            parent = rel.RelatingObject

            if self._should_exclude_spatial_element(parent):
                self.add_decomposition_parents(parent, new_element)
                continue

            new_parent = self.append_asset(parent)
            self.aggregates.setdefault(parent.GlobalId, set()).add(new_element)
            if parent.GlobalId not in self._hierarchy_done:
                self._hierarchy_done.add(parent.GlobalId)
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
