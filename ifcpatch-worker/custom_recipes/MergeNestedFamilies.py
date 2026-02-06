"""
MergeNestedFamilies - Merge nested shared family elements into their parent

This recipe solves the issue where Revit exports nested shared families (e.g., door handles,
frames, leafs) as separate IFC elements without proper relationships to their parent.
See: https://github.com/Autodesk/revit-ifc/issues/374

The recipe identifies parent-child relationships using multiple discovery methods and
merges child geometry into the parent element.

Recipe Name: MergeNestedFamilies
Description: Merge nested shared family elements (handles, frames, etc.) into parent elements
Author: IFC Pipeline Team
"""

import logging
import re
from typing import Dict, List, Optional, Set, Tuple, Union, Any
from collections import defaultdict
from logging import Logger

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.guid
import ifcopenshell.util.element
import ifcopenshell.util.selector


class Patcher:
    def __init__(
        self,
        file: ifcopenshell.file,
        logger: Union[Logger, None] = None,
        parent_selector: str = "IfcDoor",
        child_types: str = "IfcBuildingElementProxy,IfcDoor",
        discovery_methods: str = "explicit_parent,guid_prefix,revit_id",
        guid_prefix_length: int = 20,
        revit_id_range: int = 10,
        merge_properties: bool = True,
        remove_children: bool = True,
        dry_run: bool = False,
    ):
        """Merge nested shared family elements into their parent elements.

        This recipe addresses the common issue where Revit exports nested shared families
        (door handles, frames, window sashes, etc.) as separate IFC elements with no
        relationship to their parent family.

        :param parent_selector: IFC selector for parent elements (e.g., "IfcDoor", 
            "IfcDoor[Phasing.Phase Created=Etapp 1A]")
        :param child_types: Comma-separated list of IFC types to consider as potential 
            children (e.g., "IfcBuildingElementProxy,IfcDoor")
        :param discovery_methods: Comma-separated list of methods to discover children,
            in priority order. Options: explicit_parent, guid_prefix, revit_id, spatial, naming.
            - explicit_parent: Check for NestedParentId/NestedParentGuid property (highest priority)
            - guid_prefix: Match by IFC GlobalId prefix (strong Revit correlation)
            - revit_id: Match by Revit Element ID proximity (child ID > parent ID)
            - spatial: Match by shared spatial container (same building storey)
            - naming: Match by name pattern (weakest method)
        :param guid_prefix_length: Number of characters to match for GUID prefix method (default: 20)
        :param revit_id_range: Maximum Revit Element ID distance for revit_id method (default: 10)
        :param merge_properties: Whether to copy child property sets to parent (default: True)
        :param remove_children: Whether to delete children after merging (default: True)
        :param dry_run: If True, only report what would be merged without modifying (default: False)

        Example:

        .. code:: python

            # Merge door handles into doors for a specific phase
            ifcpatch.execute({
                "input": "input.ifc",
                "file": model,
                "recipe": "MergeNestedFamilies",
                "arguments": [
                    "IfcDoor[Phasing.Phase Created=Etapp 1A]",
                    "IfcBuildingElementProxy,IfcDoor",
                    "guid_prefix,revit_id",
                    20,
                    10,
                    True,
                    True,
                    False
                ]
            })
        """
        self.file = file
        self.logger = logger if logger else logging.getLogger(__name__)
        self.parent_selector = parent_selector
        
        # Parse child types
        self.child_types = [t.strip() for t in child_types.split(",") if t.strip()]
        
        # Parse discovery methods
        self.discovery_methods = [m.strip() for m in discovery_methods.split(",") if m.strip()]
        
        # Configuration
        self.guid_prefix_length = int(guid_prefix_length)
        self.revit_id_range = int(revit_id_range)
        
        # Handle boolean conversion from strings
        self.merge_properties = self._to_bool(merge_properties)
        self.remove_children = self._to_bool(remove_children)
        self.dry_run = self._to_bool(dry_run)
        
        # Statistics
        self.stats = {
            "parents_found": 0,
            "children_found": 0,
            "merges_performed": 0,
            "children_removed": 0,
            "properties_merged": 0,
            "geometry_items_merged": 0,
        }
        
        # Cache for spatial containers
        self._spatial_cache: Dict[int, Optional[ifcopenshell.entity_instance]] = {}
        
        self.logger.info(f"MergeNestedFamilies initialized:")
        self.logger.info(f"  Parent selector: {self.parent_selector}")
        self.logger.info(f"  Child types: {self.child_types}")
        self.logger.info(f"  Discovery methods: {self.discovery_methods}")
        self.logger.info(f"  GUID prefix length: {self.guid_prefix_length}")
        self.logger.info(f"  Revit ID range: {self.revit_id_range}")
        self.logger.info(f"  Merge properties: {self.merge_properties}")
        self.logger.info(f"  Remove children: {self.remove_children}")
        self.logger.info(f"  Dry run: {self.dry_run}")

    def _to_bool(self, value) -> bool:
        """Convert various types to boolean."""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ('true', '1', 'yes', 't')
        return bool(value)

    def patch(self) -> None:
        """Execute the merge operation."""
        self.logger.info("=" * 60)
        self.logger.info("Starting MergeNestedFamilies processing")
        self.logger.info("=" * 60)
        
        # Step 1: Find parent elements
        parents = self._find_parents()
        if not parents:
            self.logger.warning("No parent elements found matching selector")
            return
        
        self.stats["parents_found"] = len(parents)
        self.logger.info(f"Found {len(parents)} parent elements")
        
        # Step 2: Build index of potential children
        child_candidates = self._build_child_index()
        self.logger.info(f"Indexed {len(child_candidates)} potential child elements")
        
        # Step 3: Match children to parents
        parent_children_map = self._match_children_to_parents(parents, child_candidates)
        
        # Count total children found
        total_children = sum(len(children) for children in parent_children_map.values())
        self.stats["children_found"] = total_children
        self.logger.info(f"Matched {total_children} children to {len(parent_children_map)} parents")
        
        # Step 4: Report or execute merges
        if self.dry_run:
            self._report_dry_run(parent_children_map)
        else:
            self._execute_merges(parent_children_map)
        
        # Log statistics
        self._log_statistics()

    def _find_parents(self) -> List[ifcopenshell.entity_instance]:
        """Find parent elements using the selector.
        
        Supports extended syntax for property filtering with spaces:
        - "IfcDoor" - standard IFC type selector
        - "IfcDoor[Phasing.Phase Created=Etapp 1A]" - extended property filter
        
        For properties with spaces, falls back to manual filtering.
        """
        # Check if we have an extended property filter syntax
        match = re.match(r'^(\w+)\[([^]]+)\]$', self.parent_selector)
        
        if match:
            ifc_type = match.group(1)
            filter_expr = match.group(2)
            
            # Parse filter expression: "PsetName.PropertyName=Value"
            filter_match = re.match(r'^([^.]+)\.([^=]+)=(.+)$', filter_expr)
            
            if filter_match:
                pset_name = filter_match.group(1)
                prop_name = filter_match.group(2)
                prop_value = filter_match.group(3)
                
                self.logger.info(f"Using extended filter: type={ifc_type}, pset={pset_name}, prop={prop_name}, value={prop_value}")
                
                # Get all elements of type and filter manually
                try:
                    all_elements = self.file.by_type(ifc_type)
                    filtered = []
                    
                    for elem in all_elements:
                        psets = ifcopenshell.util.element.get_psets(elem)
                        pset = psets.get(pset_name, {})
                        if pset.get(prop_name) == prop_value:
                            filtered.append(elem)
                    
                    return filtered
                    
                except Exception as e:
                    self.logger.error(f"Error filtering elements: {e}")
                    return []
        
        # Standard selector
        try:
            elements = ifcopenshell.util.selector.filter_elements(self.file, self.parent_selector)
            return list(elements)
        except Exception as e:
            self.logger.error(f"Error finding parents with selector '{self.parent_selector}': {e}")
            return []

    def _build_child_index(self) -> List[ifcopenshell.entity_instance]:
        """Build an index of all potential child elements."""
        candidates = []
        for child_type in self.child_types:
            try:
                elements = self.file.by_type(child_type)
                candidates.extend(elements)
                self.logger.debug(f"Found {len(elements)} elements of type {child_type}")
            except Exception as e:
                self.logger.warning(f"Error getting elements of type {child_type}: {e}")
        return candidates

    def _match_children_to_parents(
        self,
        parents: List[ifcopenshell.entity_instance],
        candidates: List[ifcopenshell.entity_instance]
    ) -> Dict[ifcopenshell.entity_instance, List[ifcopenshell.entity_instance]]:
        """Match children to parents using configured discovery methods.
        
        Uses a "best match" approach where each child is assigned to only its
        closest/best matching parent, preventing the same child from being
        merged into multiple parents.
        """
        parent_children_map: Dict[ifcopenshell.entity_instance, List[ifcopenshell.entity_instance]] = {}
        
        # Create a set of parent IDs
        parent_ids = {p.id() for p in parents}
        
        # Build a mapping of parent Revit IDs for explicit parent matching
        parent_revit_id_map = {}
        for p in parents:
            tag = p.Tag if hasattr(p, 'Tag') else None
            if tag:
                try:
                    parent_revit_id_map[int(tag)] = p
                    parent_revit_id_map[str(tag)] = p  # Also store as string
                except (ValueError, TypeError):
                    parent_revit_id_map[tag] = p
        
        # Check each candidate - don't exclude based on type alone if it has explicit parent ref
        # An element can be both a potential parent (same type) AND a child (has NestedParentId)
        filtered_candidates = []
        for c in candidates:
            # If candidate is in parent list, check if it has an explicit parent reference
            if c.id() in parent_ids:
                # Check for NestedParentId pointing to a different parent
                psets = ifcopenshell.util.element.get_psets(c)
                has_parent_ref = False
                for pset_name, props in psets.items():
                    for prop_name, prop_value in props.items():
                        if prop_name in ('NestedParentId', 'IFC_ParentElementId'):
                            if prop_value is not None:
                                # Check if it points to a different element
                                if prop_value in parent_revit_id_map:
                                    ref_parent = parent_revit_id_map[prop_value]
                                    if ref_parent.id() != c.id():
                                        has_parent_ref = True
                                        self.logger.debug(
                                            f"Element {c.id()} is same type as parent but has "
                                            f"NestedParentId={prop_value} - treating as child"
                                        )
                                        break
                    if has_parent_ref:
                        break
                
                if has_parent_ref:
                    filtered_candidates.append(c)
                # else: skip this candidate as it's a parent without explicit child reference
            else:
                filtered_candidates.append(c)
        
        candidates = filtered_candidates
        self.logger.info(f"After filtering: {len(candidates)} candidate children")
        
        # Pre-compute attributes for efficiency
        parent_data = self._precompute_element_data(parents)
        candidate_data = self._precompute_element_data(candidates)
        
        # Build a score map: child_id -> [(parent, score), ...]
        # Higher score = better match
        child_parent_scores: Dict[int, List[Tuple[ifcopenshell.entity_instance, float]]] = defaultdict(list)
        
        for parent in parents:
            pd = parent_data[parent.id()]
            
            for cid, cd in candidate_data.items():
                score = self._calculate_match_score(pd, cd, parent, candidates)
                if score > 0:
                    child_parent_scores[cid].append((parent, score))
        
        # Assign each child to its best-matching parent
        globally_assigned: Set[int] = set()
        
        for cid, parent_scores in child_parent_scores.items():
            if not parent_scores:
                continue
            
            # Sort by score descending, then by Revit ID proximity (None treated as infinity)
            def sort_key(x):
                score = -x[1]  # Negative for descending order
                distance = self._get_revit_id_distance(parent_data[x[0].id()], candidate_data[cid])
                # Use float('inf') for None so it sorts last
                return (score, distance if distance is not None else float('inf'))
            
            parent_scores.sort(key=sort_key)
            
            best_parent = parent_scores[0][0]
            best_score = parent_scores[0][1]
            
            self.logger.debug(
                f"Child {cid} best match: parent {best_parent.id()} with score {best_score}"
            )
            
            if best_parent not in parent_children_map:
                parent_children_map[best_parent] = []
            
            child_elem = candidate_data[cid]["element"]
            parent_children_map[best_parent].append(child_elem)
            globally_assigned.add(cid)
        
        return parent_children_map

    def _calculate_match_score(
        self,
        parent_data: Dict,
        child_data: Dict,
        parent: ifcopenshell.entity_instance,
        candidates: List[ifcopenshell.entity_instance]
    ) -> float:
        """Calculate match score between parent and potential child.
        
        Returns score > 0 if match, 0 if no match.
        Higher score = better match.
        
        Score priorities (highest to lowest):
        - explicit_parent: 200 points (definitive match via custom property)
        - guid_prefix: 100+ points (strong correlation from Revit export)
        - revit_id: up to 50 points (proximity in Revit element IDs)
        - spatial: 25 points (same building storey)
        - naming: 10 points (name pattern matching)
        """
        score = 0.0
        
        for method in self.discovery_methods:
            if method == "explicit_parent":
                # Highest priority - explicit parent reference from custom property
                # This is set by a pyRevit plugin before export
                if self._check_explicit_parent_match(parent_data, child_data):
                    score += 200.0  # Definitive match - highest priority
            
            elif method == "guid_prefix":
                if self._check_guid_prefix_match(parent_data, child_data):
                    score += 100.0  # Base score for GUID match
                    # Bonus for longer prefix match
                    parent_guid = parent_data.get("guid", "")
                    child_guid = child_data.get("guid", "")
                    if parent_guid and child_guid:
                        for i in range(self.guid_prefix_length, min(len(parent_guid), len(child_guid))):
                            if parent_guid[i] == child_guid[i]:
                                score += 1.0
                            else:
                                break
            
            elif method == "revit_id":
                distance = self._get_revit_id_distance(parent_data, child_data)
                # distance is None if child_id <= parent_id (invalid) or IDs unavailable
                if distance is not None and distance <= self.revit_id_range:
                    # Higher score for closer IDs (distance is always positive here)
                    score += 50.0 * (1.0 - (distance / self.revit_id_range))
            
            elif method == "spatial":
                child_elem = child_data["element"]
                parent_container = self._get_spatial_container(parent)
                child_container = self._get_spatial_container(child_elem)
                if parent_container and child_container:
                    if parent_container.id() == child_container.id():
                        score += 25.0
            
            elif method == "naming":
                if self._check_naming_match(parent_data, child_data):
                    score += 10.0
        
        return score

    def _check_guid_prefix_match(self, parent_data: Dict, child_data: Dict) -> bool:
        """Check if GUID prefixes match."""
        parent_guid = parent_data.get("guid")
        child_guid = child_data.get("guid")
        
        if not parent_guid or not child_guid:
            return False
        if len(parent_guid) < self.guid_prefix_length or len(child_guid) < self.guid_prefix_length:
            return False
        
        return (parent_guid[:self.guid_prefix_length] == child_guid[:self.guid_prefix_length] and
                parent_guid != child_guid)

    def _get_revit_id_distance(self, parent_data: Dict, child_data: Dict) -> Optional[int]:
        """Get distance between Revit IDs, or None if not available.
        
        Returns the positive difference (child_id - parent_id) only when
        child_id > parent_id. Nested children in Revit always have HIGHER
        element IDs than their parent family.
        
        Returns None if:
        - Either ID is not available
        - Child ID is less than or equal to parent ID (not a valid child)
        """
        parent_id = parent_data.get("revit_id")
        child_id = child_data.get("revit_id")
        
        if parent_id is None or child_id is None:
            return None
        
        # Children must have HIGHER Revit IDs than their parent
        # This is because nested families are created after their host in Revit
        diff = child_id - parent_id
        if diff <= 0:
            return None  # Not a valid child - has same or lower ID
        
        return diff

    def _check_naming_match(self, parent_data: Dict, child_data: Dict) -> bool:
        """Check if names indicate a parent-child relationship."""
        parent_name = parent_data.get("name")
        child_name = child_data.get("name")
        
        if not parent_name or not child_name:
            return False
        
        parent_parts = parent_name.split(":")
        parent_family = parent_parts[0] if parent_parts else ""
        
        child_parts = child_name.split(":")
        child_family = child_parts[0] if child_parts else ""
        
        parent_keywords = re.findall(r'[A-Z][a-z]+|[A-Z]+(?=[A-Z]|$)', parent_family)
        for keyword in parent_keywords:
            if len(keyword) > 3 and keyword.lower() in child_family.lower():
                return True
        
        return False

    def _check_explicit_parent_match(self, parent_data: Dict, child_data: Dict) -> bool:
        """Check if child has an explicit parent reference matching this parent.
        
        This is the most reliable method when a pyRevit plugin has been used to
        populate a custom shared parameter on nested families before IFC export.
        
        Supported property names (checked in any property set):
        - NestedParentId: Integer Revit Element ID of parent
        - IFC_ParentElementId: Alternative name for parent Element ID
        - NestedParentGuid: String IFC GlobalId of parent
        - IFC_ParentGlobalId: Alternative name for parent GlobalId
        
        Returns True if a matching parent reference is found.
        """
        child_elem = child_data.get("element")
        if not child_elem:
            return False
        
        parent_revit_id = parent_data.get("revit_id")
        parent_guid = parent_data.get("guid")
        
        try:
            psets = ifcopenshell.util.element.get_psets(child_elem)
            
            for pset_name, props in psets.items():
                for prop_name, prop_value in props.items():
                    # Check for Revit Element ID reference
                    if prop_name in ('NestedParentId', 'IFC_ParentElementId'):
                        if prop_value is not None and parent_revit_id is not None:
                            try:
                                if int(prop_value) == parent_revit_id:
                                    self.logger.debug(
                                        f"Explicit parent match via {prop_name}: "
                                        f"child references parent ID {parent_revit_id}"
                                    )
                                    return True
                            except (ValueError, TypeError):
                                pass
                    
                    # Check for IFC GlobalId reference
                    elif prop_name in ('NestedParentGuid', 'IFC_ParentGlobalId'):
                        if prop_value and parent_guid:
                            if str(prop_value) == parent_guid:
                                self.logger.debug(
                                    f"Explicit parent match via {prop_name}: "
                                    f"child references parent GUID {parent_guid}"
                                )
                                return True
        
        except Exception as e:
            self.logger.debug(f"Error checking explicit parent match: {e}")
        
        return False

    def _precompute_element_data(
        self,
        elements: List[ifcopenshell.entity_instance]
    ) -> Dict[int, Dict]:
        """Pre-compute element attributes for efficient matching."""
        data = {}
        for elem in elements:
            elem_data = {
                "id": elem.id(),
                "guid": elem.GlobalId if hasattr(elem, "GlobalId") else None,
                "name": elem.Name if hasattr(elem, "Name") else None,
                "tag": elem.Tag if hasattr(elem, "Tag") else None,
                "revit_id": None,
                "element": elem,
            }
            
            # Extract Revit Element ID from Tag (common pattern)
            if elem_data["tag"]:
                try:
                    elem_data["revit_id"] = int(elem_data["tag"])
                except (ValueError, TypeError):
                    pass
            
            # Also try to get from Name (format: "Type:Instance:ID")
            if elem_data["revit_id"] is None and elem_data["name"]:
                match = re.search(r':(\d+)$', elem_data["name"])
                if match:
                    try:
                        elem_data["revit_id"] = int(match.group(1))
                    except (ValueError, TypeError):
                        pass
            
            data[elem.id()] = elem_data
        
        return data

    def _get_spatial_container(
        self,
        element: ifcopenshell.entity_instance
    ) -> Optional[ifcopenshell.entity_instance]:
        """Get the spatial container for an element."""
        if element.id() in self._spatial_cache:
            return self._spatial_cache[element.id()]
        
        container = None
        try:
            container = ifcopenshell.util.element.get_container(element)
        except Exception:
            pass
        
        self._spatial_cache[element.id()] = container
        return container

    def _report_dry_run(
        self,
        parent_children_map: Dict[ifcopenshell.entity_instance, List[ifcopenshell.entity_instance]]
    ) -> None:
        """Report what would be merged in dry run mode."""
        self.logger.info("")
        self.logger.info("=" * 60)
        self.logger.info("DRY RUN REPORT - No modifications made")
        self.logger.info("=" * 60)
        self.logger.info("")
        
        for parent, children in parent_children_map.items():
            parent_name = parent.Name if hasattr(parent, "Name") else f"#{parent.id()}"
            parent_guid = parent.GlobalId if hasattr(parent, "GlobalId") else "N/A"
            parent_tag = parent.Tag if hasattr(parent, "Tag") else "N/A"
            
            self.logger.info(f"PARENT: {parent_name}")
            self.logger.info(f"  GUID: {parent_guid}")
            self.logger.info(f"  Revit ID (Tag): {parent_tag}")
            self.logger.info(f"  IFC Type: {parent.is_a()}")
            self.logger.info(f"  Would merge {len(children)} children:")
            
            for child in children:
                child_name = child.Name if hasattr(child, "Name") else f"#{child.id()}"
                child_guid = child.GlobalId if hasattr(child, "GlobalId") else "N/A"
                child_tag = child.Tag if hasattr(child, "Tag") else "N/A"
                child_type = child.is_a()
                
                # Count geometry items
                geom_count = self._count_geometry_items(child)
                
                self.logger.info(f"    - {child_name}")
                self.logger.info(f"      GUID: {child_guid}")
                self.logger.info(f"      Revit ID: {child_tag}")
                self.logger.info(f"      Type: {child_type}")
                self.logger.info(f"      Geometry items: {geom_count}")
            
            self.logger.info("")
        
        # Summary
        self.logger.info("=" * 60)
        self.logger.info("DRY RUN SUMMARY")
        self.logger.info("=" * 60)
        self.logger.info(f"Parents that would be modified: {len(parent_children_map)}")
        total_children = sum(len(c) for c in parent_children_map.values())
        self.logger.info(f"Children that would be merged: {total_children}")
        if self.remove_children:
            self.logger.info(f"Children that would be removed: {total_children}")
        self.logger.info("")

    def _count_geometry_items(self, element: ifcopenshell.entity_instance) -> int:
        """Count geometry representation items for an element."""
        count = 0
        if hasattr(element, "Representation") and element.Representation:
            rep = element.Representation
            if hasattr(rep, "Representations"):
                for shape_rep in rep.Representations:
                    if hasattr(shape_rep, "Items") and shape_rep.Items:
                        count += len(shape_rep.Items)
        return count

    def _execute_merges(
        self,
        parent_children_map: Dict[ifcopenshell.entity_instance, List[ifcopenshell.entity_instance]]
    ) -> None:
        """Execute the actual merge operations."""
        for parent, children in parent_children_map.items():
            parent_name = parent.Name if hasattr(parent, "Name") else f"#{parent.id()}"
            self.logger.info(f"Merging {len(children)} children into {parent_name}")
            
            for child in children:
                try:
                    # Merge geometry
                    geom_merged = self._merge_geometry(parent, child)
                    self.stats["geometry_items_merged"] += geom_merged
                    
                    # Merge properties if configured
                    if self.merge_properties:
                        props_merged = self._merge_property_sets(parent, child)
                        self.stats["properties_merged"] += props_merged
                    
                    # Remove child if configured
                    if self.remove_children:
                        self._remove_element(child)
                        self.stats["children_removed"] += 1
                    
                    self.stats["merges_performed"] += 1
                    
                except Exception as e:
                    child_name = child.Name if hasattr(child, "Name") else f"#{child.id()}"
                    self.logger.error(f"Error merging child {child_name}: {e}")

    def _merge_geometry(
        self,
        parent: ifcopenshell.entity_instance,
        child: ifcopenshell.entity_instance
    ) -> int:
        """Merge child geometry into parent with correct relative transformation.
        
        Creates new IfcMappedItems for the child's geometry with a MappingTarget
        that encodes the relative transformation from parent to child. This ensures
        the geometry appears in the exact same position as when it was a separate
        child element.
        
        The transformation chain is:
        - Original: RepMap coords -> MappingTarget -> Child local -> Child ObjectPlacement -> World
        - Merged:   RepMap coords -> NewMappingTarget(includes relative transform) -> Parent local -> Parent ObjectPlacement -> World
        
        Returns count of items merged.
        """
        import numpy as np
        
        items_merged = 0
        
        # Get parent and child representations
        if not hasattr(parent, "Representation") or not parent.Representation:
            self.logger.warning(f"Parent has no representation")
            return 0
        
        if not hasattr(child, "Representation") or not child.Representation:
            self.logger.debug(f"Child has no representation to merge")
            return 0
        
        # Calculate relative transformation (parent^-1 @ child)
        relative = self._get_relative_matrix(parent, child)
        if relative is None:
            self.logger.warning("Could not calculate relative transform")
            return 0
        
        # Extract translation and rotation axes from relative matrix
        tx, ty, tz = float(relative[0, 3]), float(relative[1, 3]), float(relative[2, 3])
        x_axis = [float(relative[0, 0]), float(relative[1, 0]), float(relative[2, 0])]
        y_axis = [float(relative[0, 1]), float(relative[1, 1]), float(relative[2, 1])]
        z_axis = [float(relative[0, 2]), float(relative[1, 2]), float(relative[2, 2])]
        
        # Find parent's Body representation
        parent_body_rep = None
        for rep in parent.Representation.Representations:
            if rep.RepresentationIdentifier == 'Body':
                parent_body_rep = rep
                break
        
        if not parent_body_rep:
            self.logger.warning("Parent has no Body representation")
            return 0
        
        existing_items = list(parent_body_rep.Items) if parent_body_rep.Items else []
        
        # Process each child representation
        for child_shape_rep in child.Representation.Representations:
            if not child_shape_rep.is_a("IfcShapeRepresentation"):
                continue
            if child_shape_rep.RepresentationIdentifier != 'Body':
                continue
            if not child_shape_rep.Items:
                continue
            
            for child_item in child_shape_rep.Items:
                if not child_item.is_a('IfcMappedItem'):
                    continue
                
                # Get the MappingSource (geometry definition)
                mapping_source = child_item.MappingSource
                
                # Create new MappingTarget with relative transform
                local_origin = self.file.create_entity(
                    'IfcCartesianPoint',
                    Coordinates=[tx, ty, tz]
                )
                
                axis1 = self.file.create_entity(
                    'IfcDirection',
                    DirectionRatios=x_axis
                )
                
                axis2 = self.file.create_entity(
                    'IfcDirection',
                    DirectionRatios=y_axis
                )
                
                axis3 = self.file.create_entity(
                    'IfcDirection',
                    DirectionRatios=z_axis
                )
                
                new_target = self.file.create_entity(
                    'IfcCartesianTransformationOperator3D',
                    Axis1=axis1,
                    Axis2=axis2,
                    LocalOrigin=local_origin,
                    Axis3=axis3,
                    Scale=1.0
                )
                
                # Create new MappedItem with same source but new target
                new_mapped_item = self.file.create_entity(
                    'IfcMappedItem',
                    MappingSource=mapping_source,
                    MappingTarget=new_target
                )
                
                existing_items.append(new_mapped_item)
                items_merged += 1
                
                self.logger.debug(
                    f"Created MappedItem #{new_mapped_item.id()} with transform "
                    f"({tx:.2f}, {ty:.2f}, {tz:.2f})"
                )
        
        # Update parent's body representation
        if items_merged > 0:
            parent_body_rep.Items = tuple(existing_items)
        
        return items_merged
    
    def _get_relative_matrix(
        self,
        parent: ifcopenshell.entity_instance,
        child: ifcopenshell.entity_instance
    ):
        """Calculate relative transformation matrix from parent to child.
        
        Returns numpy 4x4 matrix such that: child_world = parent_world @ relative
        """
        import numpy as np
        
        def get_absolute_matrix(placement):
            """Get absolute 4x4 transformation matrix from placement chain."""
            if not placement or not placement.is_a('IfcLocalPlacement'):
                return np.eye(4)
            
            matrix = np.eye(4)
            
            if placement.RelativePlacement and placement.RelativePlacement.is_a('IfcAxis2Placement3D'):
                rel = placement.RelativePlacement
                
                # Translation
                if rel.Location and rel.Location.Coordinates:
                    coords = rel.Location.Coordinates
                    matrix[0, 3] = coords[0]
                    matrix[1, 3] = coords[1]
                    matrix[2, 3] = coords[2] if len(coords) > 2 else 0
                
                # Rotation - axes as columns
                z = np.array(rel.Axis.DirectionRatios) if rel.Axis else np.array([0., 0., 1.])
                x = np.array(rel.RefDirection.DirectionRatios) if rel.RefDirection else np.array([1., 0., 0.])
                y = np.cross(z, x)
                
                # Normalize
                x = x / (np.linalg.norm(x) + 1e-10)
                y = y / (np.linalg.norm(y) + 1e-10)
                z = z / (np.linalg.norm(z) + 1e-10)
                
                # Set as columns
                matrix[0:3, 0] = x
                matrix[0:3, 1] = y
                matrix[0:3, 2] = z
            
            # Multiply by parent placement
            if placement.PlacementRelTo:
                parent_matrix = get_absolute_matrix(placement.PlacementRelTo)
                matrix = parent_matrix @ matrix
            
            return matrix
        
        parent_abs = get_absolute_matrix(parent.ObjectPlacement)
        child_abs = get_absolute_matrix(child.ObjectPlacement)
        
        # relative = parent^-1 @ child
        parent_inv = np.linalg.inv(parent_abs)
        relative = parent_inv @ child_abs
        
        return relative

    def _merge_property_sets(
        self,
        parent: ifcopenshell.entity_instance,
        child: ifcopenshell.entity_instance
    ) -> int:
        """Merge child property sets to parent. Returns count of properties merged."""
        props_merged = 0
        
        try:
            # Get child's property sets
            child_psets = ifcopenshell.util.element.get_psets(child)
            
            for pset_name, properties in child_psets.items():
                # Skip standard psets that shouldn't be merged
                if pset_name.startswith("Pset_") or pset_name == "id":
                    continue
                
                # Check if parent already has this pset
                parent_psets = ifcopenshell.util.element.get_psets(parent)
                
                if pset_name not in parent_psets:
                    # Create new pset on parent
                    try:
                        new_pset = ifcopenshell.api.pset.add_pset(
                            self.file, product=parent, name=f"Merged_{pset_name}"
                        )
                        ifcopenshell.api.pset.edit_pset(
                            self.file, pset=new_pset, properties=properties
                        )
                        props_merged += len(properties)
                    except Exception as e:
                        self.logger.debug(f"Could not merge pset {pset_name}: {e}")
        
        except Exception as e:
            self.logger.debug(f"Error getting child psets: {e}")
        
        return props_merged

    def _remove_element(self, element: ifcopenshell.entity_instance) -> None:
        """Remove an element and clean up its relationships."""
        try:
            # Remove from spatial container
            try:
                ifcopenshell.api.spatial.remove_container(self.file, products=[element])
            except Exception:
                pass
            
            # Remove the element
            ifcopenshell.api.root.remove_product(self.file, product=element)
            
        except Exception as e:
            self.logger.warning(f"Error removing element: {e}")
            # Try direct removal as fallback
            try:
                self.file.remove(element)
            except Exception:
                pass

    def _log_statistics(self) -> None:
        """Log processing statistics."""
        self.logger.info("")
        self.logger.info("=" * 60)
        self.logger.info("Processing Statistics")
        self.logger.info("=" * 60)
        self.logger.info(f"  Parents found: {self.stats['parents_found']}")
        self.logger.info(f"  Children matched: {self.stats['children_found']}")
        
        if not self.dry_run:
            self.logger.info(f"  Merges performed: {self.stats['merges_performed']}")
            self.logger.info(f"  Geometry items merged: {self.stats['geometry_items_merged']}")
            self.logger.info(f"  Properties merged: {self.stats['properties_merged']}")
            self.logger.info(f"  Children removed: {self.stats['children_removed']}")
        
        self.logger.info("=" * 60)

    def get_output(self) -> ifcopenshell.file:
        """Return the patched IFC file."""
        return self.file
