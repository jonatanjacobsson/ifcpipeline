"""
SetColorBySelector Recipe (V3 - Optimized)

This custom recipe assigns colors to IFC elements based on selector syntax.
Supports multiple operations with filter groups and hex color assignments.

Recipe Name: SetColorBySelector
Description: Assign colors to IFC elements using IfcOpenShell selector syntax
Author: IFC Pipeline Team
Date: 2025-01-08
Updated: 2026-01-16 (V3 optimizations - 13.8x faster)

Performance Optimizations (V3):
    - Uses ifcopenshell.api.style.assign_representation_styles() bulk API
    - MappingSource deduplication: styles shared geometry definitions once
    - Reduces element operations

Transparency Support:
    - Transparency is supported in both IFC2X3 and IFC4+ schemas
    - Uses IfcSurfaceStyleRendering (which has Transparency attribute)
    - Falls back to IfcSurfaceStyleShading when no transparency is needed

Example Usage:
    op1 = '{"selectors": "IfcWall", "hex": "FF0000"}'
    op2 = '{"selectors": "IfcWall + IfcDoor", "hex": "#FF0000 + #00FF00"}'
    op3 = '{"selectors": "IfcSlab, [LoadBearing=TRUE]", "hex": "0000FF"}'
    op4 = '{"selectors": "IfcWindow", "hex": "FF0000", "transparency": 0.5}'
    op5 = '{"selectors": "IfcCurtainWall", "hex": "00FF00AA"}'  # 8-char hex with alpha
    
    patcher = Patcher(ifc_file, logger, operation1=op1, operation2=op2, operation3=op3)
    patcher.patch()
    output = patcher.get_output()
"""

import json
import logging
import re
from collections import defaultdict

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.api.style
import ifcopenshell.util.selector

logger = logging.getLogger(__name__)


class Patcher:
    """
    Custom patcher for assigning colors to IFC elements using selector syntax.
    
    V3 Optimizations:
    - Uses assign_representation_styles() bulk API instead of manual IfcStyledItem creation
    - MappingSource deduplication: styles shared geometry definitions once instead of per-element
    - Benchmark: 125s -> 9s (13.8x faster) on 9,403 elements
    
    This recipe:
    - Accepts multiple operations as separate JSON string arguments
    - Uses IfcOpenShell selector syntax (including filter groups) to find elements
    - Creates or reuses styles for color assignment
    - Assigns colors to all representations of matched elements
    
    Parameters:
        file: The IFC model to patch
        logger: Logger instance for output
        operation1-5: JSON strings for up to 5 operations (only non-empty operations are processed)
        
    Each operation requires these fields:
        - selectors: IfcOpenShell selector syntax string (can use filter groups with +)
        - hex: Hex color string (can use + separator for multiple colors)
        - transparency: Optional float 0-1 (0=opaque, 1=fully transparent), can use + separator
        
    Selector Syntax (per IfcOpenShell documentation):
        - Filter groups separated by + (results unioned together)
        - Within a filter group, filters separated by , (chained left to right)
    
    Hex Color Handling:
        - Single color: applies same color to all matched elements
        - Multiple colors with +: must match number of filter groups, colors assigned by position
        - Hex colors can optionally include # prefix (e.g., "#FF0000" or "FF0000")
        - Supports 8-character hex (RRGGBBAA) where AA is alpha/transparency (00=transparent, FF=opaque)
    
    Transparency Handling:
        - Optional "transparency" field: float from 0 (opaque) to 1 (fully transparent)
        - Can use + separator for multiple transparencies (must match filter groups)
        - If 8-char hex is used, alpha channel is extracted and combined with transparency field
        - 8-char hex alpha: 00=transparent (1.0), FF=opaque (0.0) - converted to IFC transparency
        - When transparency > 0, uses IfcSurfaceStyleRendering (available in IFC2X3 and IFC4+)
        - When transparency = 0, uses IfcSurfaceStyleShading for better compatibility
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
        self.file = file
        self.logger = logger
        
        self.operations = []
        self.style_cache = {}  # Cache styles by hex value to avoid duplicates
        self.styled_mapping_sources = set()  # Track already-styled MappingSources
        self.stats = {
            'operations_total': 0,
            'operations_completed': 0,
            'operations_failed': 0,
            'elements_colored': 0,
            'mapping_sources_styled': 0,
            'mapping_sources_skipped': 0,
            'representations_styled': 0,
            'styles_created': 0,
            'styles_reused': 0,
            'conflicts_detected': 0,
            'mapping_sources_duplicated': 0
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
        """Parse and validate the operation arguments."""
        if not operation_args:
            return []
        
        validated_operations = []
        
        for idx, operation_json in enumerate(operation_args):
            if not operation_json or (isinstance(operation_json, str) and operation_json.strip() == ""):
                self.logger.warning(f"Argument {idx + 1} is empty, skipping")
                continue
            
            try:
                op = json.loads(operation_json)
            except json.JSONDecodeError as e:
                self.logger.warning(f"Argument {idx + 1}: Invalid JSON format - {str(e)}, skipping")
                continue
            
            if not isinstance(op, dict):
                self.logger.warning(f"Argument {idx + 1}: Expected JSON object, got {type(op).__name__}, skipping")
                continue
            
            required_fields = ['selectors', 'hex']
            missing_fields = [f for f in required_fields if f not in op]
            
            if missing_fields:
                self.logger.warning(f"Argument {idx + 1}: Missing required fields: {missing_fields}, skipping")
                continue
            
            if not op['selectors'] or not isinstance(op['selectors'], str) or not op['selectors'].strip():
                self.logger.warning(f"Argument {idx + 1}: 'selectors' must be a non-empty string, skipping")
                continue
            
            hex_value = op['hex']
            if not isinstance(hex_value, str):
                self.logger.warning(f"Argument {idx + 1}: 'hex' must be a string, skipping")
                continue
            
            hex_colors = [h.strip() for h in hex_value.split('+') if h.strip()]
            
            if not hex_colors:
                self.logger.warning(f"Argument {idx + 1}: 'hex' cannot be empty, skipping")
                continue
            
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
        """Validate hex color format (6 or 8 characters)."""
        hex_str = hex_str.lstrip('#')
        if len(hex_str) not in [6, 8]:
            return False
        try:
            int(hex_str, 16)
            return True
        except ValueError:
            return False
    
    def _parse_hex_color(self, hex_str: str) -> tuple:
        """Convert hex color string to RGB tuple (normalized 0-1 for IFC)."""
        hex_str = hex_str.lstrip('#').upper()
        r = int(hex_str[0:2], 16) / 255.0
        g = int(hex_str[2:4], 16) / 255.0
        b = int(hex_str[4:6], 16) / 255.0
        
        if len(hex_str) == 8:
            a = int(hex_str[6:8], 16) / 255.0
            transparency = 1.0 - a
        else:
            transparency = 0.0
        
        return (r, g, b, transparency)
    
    def _get_or_create_style(self, hex_value: str, transparency: float = 0.0):
        """Get existing style or create a new one with caching."""
        hex_value = hex_value.lstrip('#').upper()
        r, g, b, hex_transparency = self._parse_hex_color(hex_value)
        final_transparency = max(hex_transparency, transparency)
        
        hex_rgb = hex_value[:6]
        cache_key = f"{hex_rgb}_T{final_transparency:.3f}"
        
        if cache_key in self.style_cache:
            self.stats['styles_reused'] += 1
            return self.style_cache[cache_key]
        
        style_name = f"Color_{hex_rgb}"
        if final_transparency > 0.0:
            style_name += f"_T{int(final_transparency * 100)}"
        
        style = ifcopenshell.api.run("style.add_style", self.file, name=style_name)
        
        attributes = {
            "SurfaceColour": {"Name": None, "Red": r, "Green": g, "Blue": b}
        }
        
        if final_transparency > 0.0:
            attributes["Transparency"] = final_transparency
            attributes["ReflectanceMethod"] = "FLAT"
            ifc_class = "IfcSurfaceStyleRendering"
        else:
            ifc_class = "IfcSurfaceStyleShading"
        
        ifcopenshell.api.run("style.add_surface_style", self.file,
                            style=style,
                            ifc_class=ifc_class,
                            attributes=attributes)
        
        self.style_cache[cache_key] = style
        self.stats['styles_created'] += 1
        return style
    
    def _get_mapping_sources_for_elements(self, elements):
        """
        Get unique MappingSources from elements.
        
        MappingSource deduplication: Many IFC elements share the same geometry definition
        via IfcMappedItem -> IfcRepresentationMap -> MappedRepresentation.
        By styling the MappedRepresentation once, we color all elements using it.
        
        Returns:
            Tuple of (mapping_source_to_elements dict, direct_rep_to_elements dict)
        """
        mapping_source_to_elements = defaultdict(list)
        direct_rep_to_elements = defaultdict(list)
        
        for elem in elements:
            if not hasattr(elem, 'Representation') or not elem.Representation:
                continue
            if not elem.Representation.is_a('IfcProductDefinitionShape'):
                continue
            
            for rep in elem.Representation.Representations:
                if not rep.is_a('IfcShapeRepresentation'):
                    continue
                
                has_mapped_item = False
                if rep.Items:
                    for item in rep.Items:
                        if item.is_a('IfcMappedItem'):
                            has_mapped_item = True
                            mapping_source = item.MappingSource
                            mapping_source_to_elements[mapping_source].append(elem)
                
                if not has_mapped_item:
                    direct_rep_to_elements[rep].append(elem)
        
        return mapping_source_to_elements, direct_rep_to_elements
    
    def _style_mapping_source(self, mapping_source, style):
        """
        Style a MappingSource's MappedRepresentation.
        This styles the shared geometry definition, affecting all elements that use it.
        """
        try:
            mapped_rep = mapping_source.MappedRepresentation
            if not mapped_rep:
                return False
            
            source_id = mapping_source.id()
            if source_id in self.styled_mapping_sources:
                self.stats['mapping_sources_skipped'] += 1
                return True
            
            use_presentation_style_assignment = self.file.schema == "IFC2X3"
            
            ifcopenshell.api.style.assign_representation_styles(
                self.file,
                shape_representation=mapped_rep,
                styles=[style],
                replace_previous_same_type_style=True,
                should_use_presentation_style_assignment=use_presentation_style_assignment
            )
            
            self.styled_mapping_sources.add(source_id)
            self.stats['mapping_sources_styled'] += 1
            return True
        except Exception as e:
            self.logger.debug(f"Failed to style MappingSource {mapping_source.id()}: {e}")
            return False
    
    def _style_representation(self, rep, style):
        """Style a representation using the bulk API."""
        try:
            use_presentation_style_assignment = self.file.schema == "IFC2X3"
            
            ifcopenshell.api.style.assign_representation_styles(
                self.file,
                shape_representation=rep,
                styles=[style],
                replace_previous_same_type_style=True,
                should_use_presentation_style_assignment=use_presentation_style_assignment
            )
            return True
        except Exception as e:
            self.logger.debug(f"Failed to style representation {rep.id()}: {e}")
            return False
    
    def _duplicate_representation(self, original_rep):
        """
        Create a new IfcShapeRepresentation that references the same geometry items
        but can be styled independently.
        
        The key insight: IfcStyledItem entities link to geometry Items, but styling
        a representation creates NEW IfcStyledItem entities. The problem is that
        when Items are shared, changing their StyledByItem affects all representations.
        
        Solution: We create new copies of the top-level geometry items (like shells)
        so that each representation can have its own styles.
        """
        try:
            new_items = []
            if original_rep.Items:
                for item in original_rep.Items:
                    # Deep copy the top-level geometry item
                    new_item = self._deep_copy_geometry_item(item)
                    new_items.append(new_item)
            
            # Create new representation instance with the copied items
            new_rep = self.file.create_entity(
                "IfcShapeRepresentation",
                ContextOfItems=original_rep.ContextOfItems,
                RepresentationIdentifier=original_rep.RepresentationIdentifier,
                RepresentationType=original_rep.RepresentationType,
                Items=tuple(new_items) if new_items else None
            )
            
            return new_rep
        except Exception as e:
            self.logger.warning(f"Failed to duplicate representation {original_rep.id()}: {e}")
            # Fallback: return original (will share but at least won't crash)
            return original_rep
    
    def _deep_copy_geometry_item(self, item):
        """
        Create a deep copy of a geometry item, excluding styles.
        This allows the copy to be styled independently.
        """
        try:
            entity_type = item.is_a()
            
            if entity_type == "IfcShellBasedSurfaceModel":
                # Copy the shell-based surface model
                new_boundaries = []
                if item.SbsmBoundary:
                    for shell in item.SbsmBoundary:
                        # Shells (IfcOpenShell, IfcClosedShell) can be referenced directly
                        # as they don't carry styles directly - the SBSM does
                        new_boundaries.append(shell)
                
                return self.file.create_entity(
                    "IfcShellBasedSurfaceModel",
                    SbsmBoundary=tuple(new_boundaries) if new_boundaries else None
                )
            
            elif entity_type == "IfcMappedItem":
                # For mapped items, we need to create a new one pointing to the same MappingSource
                # But wait - if we're duplicating the representation that contains a MappedItem,
                # we're already inside a MappedRepresentation. This shouldn't happen.
                # Just return the original
                return item
            
            elif entity_type in ("IfcFacetedBrep", "IfcManifoldSolidBrep", "IfcAdvancedBrep"):
                # Copy B-rep geometry
                return self.file.create_entity(
                    entity_type,
                    Outer=item.Outer
                )
            
            elif entity_type == "IfcBooleanResult":
                # Copy boolean result (keep references to operands)
                return self.file.create_entity(
                    "IfcBooleanResult",
                    Operator=item.Operator,
                    FirstOperand=item.FirstOperand,
                    SecondOperand=item.SecondOperand
                )
            
            elif entity_type == "IfcBooleanClippingResult":
                return self.file.create_entity(
                    "IfcBooleanClippingResult",
                    Operator=item.Operator,
                    FirstOperand=item.FirstOperand,
                    SecondOperand=item.SecondOperand
                )
            
            elif entity_type == "IfcExtrudedAreaSolid":
                return self.file.create_entity(
                    "IfcExtrudedAreaSolid",
                    SweptArea=item.SweptArea,
                    Position=item.Position,
                    ExtrudedDirection=item.ExtrudedDirection,
                    Depth=item.Depth
                )
            
            elif entity_type == "IfcSweptDiskSolid":
                return self.file.create_entity(
                    "IfcSweptDiskSolid",
                    Directrix=item.Directrix,
                    Radius=item.Radius,
                    InnerRadius=item.InnerRadius,
                    StartParam=item.StartParam,
                    EndParam=item.EndParam
                )
            
            else:
                # For other types, try generic copy
                self.logger.debug(f"Unknown geometry type {entity_type}, attempting generic copy")
                # Get all attributes (excluding inverses like StyledByItem)
                info = item.get_info(recursive=False, include_identifier=False)
                # Remove the type info
                info.pop('type', None)
                try:
                    return self.file.create_entity(entity_type, **info)
                except:
                    # If generic copy fails, just reference the original
                    self.logger.debug(f"Generic copy failed for {entity_type}, using reference")
                    return item
                    
        except Exception as e:
            self.logger.debug(f"Failed to copy geometry item {item.is_a()}: {e}, using reference")
            return item
    
    def _resolve_item_conflicts(self, mapping_source_to_operation):
        """
        Detect and resolve Item-level conflicts.
        
        Problem: Different MappingSources can share the same geometry Items. When we style
        each MappingSource, we're styling its Items. If Items are shared, the last styling
        operation wins, causing incorrect colors.
        
        Solution: Find Items that appear in multiple MappedRepresentations being styled with
        different styles, and duplicate those Items so each representation gets its own copy.
        """
        if not mapping_source_to_operation:
            return mapping_source_to_operation
        
        # Build a map: Item ID -> list of (MappingSource, style)
        item_to_styles = defaultdict(list)
        
        for ms, (op_idx, style, elems) in mapping_source_to_operation.items():
            mapped_rep = ms.MappedRepresentation
            if not mapped_rep or not mapped_rep.Items:
                continue
            
            for item in mapped_rep.Items:
                item_to_styles[item.id()].append((ms, style, item))
        
        # Find Items that are shared across MappingSources with DIFFERENT styles
        items_needing_duplication = []
        for item_id, styles_list in item_to_styles.items():
            if len(styles_list) <= 1:
                continue
            
            # Check if different styles are used
            style_ids = set(s.id() for (_, s, _) in styles_list)
            if len(style_ids) > 1:
                items_needing_duplication.append((item_id, styles_list))
        
        if not items_needing_duplication:
            self.logger.info("No Item-level conflicts detected")
            return mapping_source_to_operation
        
        self.logger.warning(
            f"Detected {len(items_needing_duplication)} Item(s) shared across MappingSources "
            f"with different styles. Duplicating to ensure independent styling..."
        )
        
        # For each conflicting Item, duplicate it for all but the first MappingSource
        # that uses it
        for item_id, styles_list in items_needing_duplication:
            # Sort by operation index so earlier operations get the original
            styles_list.sort(key=lambda x: mapping_source_to_operation[x[0]][0])
            
            # First one keeps the original Item
            first_ms, first_style, original_item = styles_list[0]
            
            # Remaining ones get duplicated Items
            for ms, style, item in styles_list[1:]:
                self._duplicate_item_in_representation(ms.MappedRepresentation, item)
                self.stats['conflicts_detected'] += 1
        
        return mapping_source_to_operation
    
    def _duplicate_item_in_representation(self, rep, item_to_replace):
        """
        Replace a shared Item in a representation with a duplicate.
        This allows the representation to be styled independently.
        """
        if not rep.Items or item_to_replace not in rep.Items:
            return
        
        # Create a duplicate of the Item
        new_item = self._deep_copy_geometry_item(item_to_replace)
        
        if new_item.id() == item_to_replace.id():
            # Duplication failed, item was returned as-is
            self.logger.debug(f"Could not duplicate Item {item_to_replace.id()}")
            return
        
        # Replace the old Item with the new one in the representation
        new_items = []
        for existing_item in rep.Items:
            if existing_item.id() == item_to_replace.id():
                new_items.append(new_item)
            else:
                new_items.append(existing_item)
        
        # Update the representation's Items
        rep.Items = tuple(new_items)
        
        self.logger.debug(
            f"Duplicated Item {item_to_replace.id()} -> {new_item.id()} "
            f"in representation {rep.id()}"
        )
    
    def _duplicate_mapping_source(self, original_mapping_source, elements_to_update):
        """
        Create a duplicate MappingSource (IfcRepresentationMap) with its own MappedRepresentation.
        This ensures each MappingSource can be styled independently when conflicts occur.
        
        Args:
            original_mapping_source: The original IfcRepresentationMap to duplicate
            elements_to_update: List of elements that should use the new MappingSource
        
        Returns:
            New IfcRepresentationMap entity with its own MappedRepresentation
        """
        # CRITICAL: Also duplicate the MappedRepresentation so styles don't conflict
        original_mapped_rep = original_mapping_source.MappedRepresentation
        new_mapped_rep = self._duplicate_representation(original_mapped_rep)
        
        # Create new IfcRepresentationMap pointing to the new MappedRepresentation
        new_mapping_source = self.file.create_entity(
            "IfcRepresentationMap",
            MappingOrigin=original_mapping_source.MappingOrigin,
            MappedRepresentation=new_mapped_rep
        )
        
        # Update all IfcMappedItem instances for these elements to use the new MappingSource
        updated_count = 0
        for elem in elements_to_update:
            if not hasattr(elem, 'Representation') or not elem.Representation:
                continue
            if not elem.Representation.is_a('IfcProductDefinitionShape'):
                continue
            
            for rep in elem.Representation.Representations:
                if not rep.is_a('IfcShapeRepresentation'):
                    continue
                
                if rep.Items:
                    for item in rep.Items:
                        if item.is_a('IfcMappedItem'):
                            if item.MappingSource.id() == original_mapping_source.id():
                                # Update this MappedItem to use the new MappingSource
                                item.MappingSource = new_mapping_source
                                updated_count += 1
        
        self.stats['mapping_sources_duplicated'] += 1
        self.logger.debug(
            f"Duplicated MappingSource {original_mapping_source.id()} -> {new_mapping_source.id()} "
            f"(with new MappedRepresentation {new_mapped_rep.id()}), "
            f"updated {updated_count} MappedItem(s)"
        )
        
        return new_mapping_source
    
    def _detect_and_resolve_conflicts(self):
        """
        First pass: Analyze all operations, detect conflicts, and resolve by duplicating MappingSources.
        
        Handles filter groups (separated by +) and multiple colors/transparencies.
        
        Returns:
            mapping_source_to_operation: dict mapping MappingSource entity -> (operation_idx, style, elements)
            direct_rep_to_operation: dict mapping Representation entity -> (operation_idx, style, elements)
        """
        # Track which operations want which MappingSources
        # mapping_source -> list of (op_idx, filter_group_idx, style, elements)
        mapping_source_to_operations = defaultdict(list)
        direct_rep_to_operations = defaultdict(list)
        
        self.logger.info("Pass 1: Analyzing operations and detecting conflicts...")
        
        for idx, operation in enumerate(self.operations):
            selectors_str = operation['selectors']
            hex_value = operation['hex']
            transparency_value = operation.get('transparency', '')
            
            # Parse filter groups
            filter_groups = [fg.strip() for fg in selectors_str.split('+') if fg.strip()]
            if not filter_groups:
                continue
            
            hex_colors = [h.strip() for h in hex_value.split('+') if h.strip()]
            if len(hex_colors) == 1:
                hex_list = hex_colors * len(filter_groups)
            else:
                hex_list = hex_colors
            
            transparency_list = []
            if transparency_value:
                if isinstance(transparency_value, (int, float)):
                    transparency_list = [float(transparency_value)] * len(filter_groups)
                elif isinstance(transparency_value, str):
                    transparency_strs = [t.strip() for t in transparency_value.split('+') if t.strip()]
                    if len(transparency_strs) == 1:
                        transparency_list = [float(transparency_strs[0])] * len(filter_groups)
                    else:
                        transparency_list = [float(t) for t in transparency_strs]
            else:
                transparency_list = [0.0] * len(filter_groups)
            
            # Process each filter group
            for group_idx, (filter_group, hex_color, transparency) in enumerate(zip(filter_groups, hex_list, transparency_list)):
                selector = filter_group.strip()
                has_ifc_class = re.search(r'\bIfc[A-Z]\w*\b', selector)
                if '.' in selector and '=' in selector and not has_ifc_class:
                    selector = f"IfcElement, {selector}"
                
                elements = ifcopenshell.util.selector.filter_elements(self.file, selector)
                if not elements:
                    continue
                
                # Get style
                style = self._get_or_create_style(hex_color, transparency)
                
                # Get unique MappingSources and direct representations
                mapping_sources, direct_reps = self._get_mapping_sources_for_elements(elements)
                
                # Track which operation/filter group wants to style each MappingSource
                for mapping_source, elems in mapping_sources.items():
                    mapping_source_to_operations[mapping_source].append((idx, group_idx, style, elems))
                
                # Track direct representations
                for rep, elems in direct_reps.items():
                    direct_rep_to_operations[rep].append((idx, group_idx, style, elems))
        
        # Resolve MappingSource conflicts (same MS requested by multiple operations)
        self.logger.info("Resolving MappingSource conflicts...")
        mapping_source_to_operation = {}
        direct_rep_to_operation = {}
        
        # Process MappingSources
        for mapping_source, operations_list in mapping_source_to_operations.items():
            if len(operations_list) == 1:
                # No conflict, use original MappingSource
                op_idx, group_idx, style, elems = operations_list[0]
                mapping_source_to_operation[mapping_source] = (op_idx, style, elems)
            else:
                # Conflict detected - duplicate MappingSource for each operation/filter group
                self.stats['conflicts_detected'] += len(operations_list) - 1
                self.logger.warning(
                    f"Conflict detected: MappingSource {mapping_source.id()} requested by "
                    f"{len(operations_list)} operation(s)/filter group(s). Creating unique copies..."
                )
                
                # Keep first operation/filter group on original MappingSource
                op_idx, group_idx, style, elems = operations_list[0]
                mapping_source_to_operation[mapping_source] = (op_idx, style, elems)
                
                # Create duplicates for remaining operations/filter groups
                for op_idx, group_idx, style, elems in operations_list[1:]:
                    new_mapping_source = self._duplicate_mapping_source(mapping_source, elems)
                    mapping_source_to_operation[new_mapping_source] = (op_idx, style, elems)
        
        # CRITICAL: Resolve Item-level conflicts (same Item shared by different MappingSources with different styles)
        mapping_source_to_operation = self._resolve_item_conflicts(mapping_source_to_operation)
        
        # Process direct representations (for now, last operation wins - could duplicate if needed)
        for rep, operations_list in direct_rep_to_operations.items():
            if len(operations_list) == 1:
                op_idx, group_idx, style, elems = operations_list[0]
                direct_rep_to_operation[rep] = (op_idx, style, elems)
            else:
                # For direct representations, last operation wins (duplicating reps is more complex)
                self.stats['conflicts_detected'] += len(operations_list) - 1
                self.logger.warning(
                    f"Conflict detected: Direct representation {rep.id()} requested by "
                    f"{len(operations_list)} operation(s)/filter group(s). Using last operation's color."
                )
                op_idx, group_idx, style, elems = operations_list[-1]
                direct_rep_to_operation[rep] = (op_idx, style, elems)
        
        if self.stats['conflicts_detected'] > 0:
            self.logger.info(
                f"Resolved {self.stats['conflicts_detected']} conflict(s) by creating "
                f"{self.stats['mapping_sources_duplicated']} unique MappingSource(s)"
            )
        else:
            self.logger.info("No conflicts detected - all MappingSources are unique per operation")
        
        return mapping_source_to_operation, direct_rep_to_operation
    
    def _execute_operation(self, operation: dict, operation_idx: int) -> dict:
        """Execute a single color assignment operation."""
        selectors_str = operation['selectors']
        hex_value = operation['hex']
        transparency_value = operation.get('transparency', '')
        
        result = {
            'success': False,
            'filter_groups_processed': 0,
            'elements_colored': 0,
            'error': None
        }
        
        try:
            filter_groups = [fg.strip() for fg in selectors_str.split('+') if fg.strip()]
            
            if not filter_groups:
                self.logger.warning(f"No valid filter groups found in selector: '{selectors_str}'")
                result['success'] = True
                return result
            
            hex_colors = [h.strip() for h in hex_value.split('+') if h.strip()]
            
            transparency_list = []
            if transparency_value:
                if isinstance(transparency_value, (int, float)):
                    transparency_list = [float(transparency_value)]
                elif isinstance(transparency_value, str):
                    transparency_strs = [t.strip() for t in transparency_value.split('+') if t.strip()]
                    for t_str in transparency_strs:
                        t_val = float(t_str)
                        if not (0.0 <= t_val <= 1.0):
                            raise ValueError(f"Transparency value {t_val} out of range [0, 1]")
                        transparency_list.append(t_val)
            
            if len(hex_colors) > 1 and len(hex_colors) != len(filter_groups):
                raise ValueError(f"Number of hex colors ({len(hex_colors)}) must match number of filter groups ({len(filter_groups)})")
            
            if transparency_list and len(transparency_list) > 1 and len(transparency_list) != len(filter_groups):
                raise ValueError(f"Number of transparency values ({len(transparency_list)}) must match number of filter groups ({len(filter_groups)})")
            
            if len(hex_colors) == 1:
                hex_list = hex_colors * len(filter_groups)
            else:
                hex_list = hex_colors
            
            if len(transparency_list) == 0:
                transparency_list = [0.0] * len(filter_groups)
            elif len(transparency_list) == 1:
                transparency_list = transparency_list * len(filter_groups)
            
            self.logger.info(f"Processing {len(filter_groups)} filter group(s)")
            
            total_colored = 0
            for group_idx, (filter_group, hex_color, transparency) in enumerate(zip(filter_groups, hex_list, transparency_list)):
                trans_str = f", transparency={transparency}" if transparency > 0.0 else ""
                self.logger.debug(f"Filter group {group_idx + 1}/{len(filter_groups)}: '{filter_group}' -> {hex_color}{trans_str}")
                
                style = self._get_or_create_style(hex_color, transparency)
                
                selector = filter_group.strip()
                has_ifc_class = re.search(r'\bIfc[A-Z]\w*\b', selector)
                if '.' in selector and '=' in selector and not has_ifc_class:
                    selector = f"IfcElement, {selector}"
                    self.logger.debug(f"Auto-prefixed selector: '{filter_group}' -> '{selector}'")
                
                elements = ifcopenshell.util.selector.filter_elements(self.file, selector)
                
                if len(elements) == 0:
                    self.logger.warning(f"No elements matched filter group: '{selector}' (original: '{filter_group}')")
                    continue
                
                self.logger.info(f"Found {len(elements)} element(s) matching filter group '{selector}'")
                
                # V3 Optimization: Get unique MappingSources and direct representations
                mapping_sources, direct_reps = self._get_mapping_sources_for_elements(elements)
                self.logger.info(
                    f"Found {len(mapping_sources)} unique MappingSource(s) and "
                    f"{len(direct_reps)} direct representation(s) to style"
                )
                
                # Style MappingSources (shared geometry definitions)
                styled_count = 0
                total_items = len(mapping_sources) + len(direct_reps)
                
                for i, (mapping_source, elems) in enumerate(mapping_sources.items()):
                    if total_items > 100 and (i + 1) % 100 == 0:
                        self.logger.info(f"Styling MappingSource {i + 1}/{len(mapping_sources)}")
                    
                    if self._style_mapping_source(mapping_source, style):
                        styled_count += 1
                
                # Style direct representations (non-mapped geometry)
                for i, (rep, elems) in enumerate(direct_reps.items()):
                    if total_items > 100 and (len(mapping_sources) + i + 1) % 100 == 0:
                        self.logger.info(f"Styling direct representation {i + 1}/{len(direct_reps)}")
                    
                    if self._style_representation(rep, style):
                        styled_count += 1
                        self.stats['representations_styled'] += 1
                
                trans_log = f" with transparency {transparency}" if transparency > 0.0 else ""
                self.logger.info(f"Successfully styled {styled_count} item(s) for {len(elements)} elements{trans_log}")
                total_colored += len(elements)
                result['filter_groups_processed'] += 1
            
            result['elements_colored'] = total_colored
            result['success'] = True
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
        
        Uses two-pass approach:
        1. Detect conflicts where multiple operations want to style the same MappingSource
        2. Resolve conflicts by duplicating MappingSources (each gets its own MappedRepresentation)
        3. Style each MappingSource once with the correct color
        """
        if self.stats['operations_total'] == 0:
            self.logger.warning("No valid operations to execute")
            return
        
        self.logger.info(f"Starting SetColorBySelector with {self.stats['operations_total']} operation(s)")
        
        # Reset tracking for fresh patch
        self.styled_mapping_sources = set()
        
        try:
            # Pass 1: Detect and resolve conflicts by duplicating MappingSources
            mapping_source_to_operation, direct_rep_to_operation = self._detect_and_resolve_conflicts()
            
            # Pass 2: Style each MappingSource/Representation once (now all unique, no conflicts)
            self.logger.info("Pass 2: Styling MappingSources and representations (each styled once, no conflicts)...")
            
            styled_count = 0
            total_items = len(mapping_source_to_operation) + len(direct_rep_to_operation)
            
            # Style MappingSources (using entity references directly)
            for i, (mapping_source, (op_idx, style, elems)) in enumerate(mapping_source_to_operation.items()):
                if total_items > 100 and (i + 1) % 100 == 0:
                    self.logger.info(f"Styling MappingSource {i + 1}/{len(mapping_source_to_operation)}")
                
                if self._style_mapping_source(mapping_source, style):
                    styled_count += 1
                    self.stats['elements_colored'] += len(elems)
            
            # Style direct representations (using entity references directly)
            for i, (rep, (op_idx, style, elems)) in enumerate(direct_rep_to_operation.items()):
                if total_items > 100 and (len(mapping_source_to_operation) + i + 1) % 100 == 0:
                    self.logger.info(f"Styling direct representation {i + 1}/{len(direct_rep_to_operation)}")
                
                if self._style_representation(rep, style):
                    styled_count += 1
                    self.stats['elements_colored'] += len(elems)
                    self.stats['representations_styled'] += 1
            
            self.stats['operations_completed'] = len(self.operations)
            
            self.logger.info(
                f"SetColorBySelector completed: "
                f"{self.stats['operations_completed']}/{self.stats['operations_total']} operations succeeded, "
                f"{self.stats['elements_colored']} elements colored, "
                f"{self.stats['mapping_sources_styled']} MappingSources styled, "
                f"{self.stats['mapping_sources_skipped']} MappingSources skipped (already styled), "
                f"{self.stats['representations_styled']} direct reps styled, "
                f"{self.stats['conflicts_detected']} conflicts detected, "
                f"{self.stats['mapping_sources_duplicated']} MappingSources duplicated, "
                f"{self.stats['styles_created']} styles created, "
                f"{self.stats['styles_reused']} styles reused"
            )
            
        except Exception as e:
            self.logger.error(f"Critical error during patch execution: {str(e)}", exc_info=True)
            raise
    
    def get_output(self) -> ifcopenshell.file:
        """Return the patched IFC file."""
        return self.file

