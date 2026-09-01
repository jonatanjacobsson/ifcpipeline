"""
SetColorBySelector Recipe (V3.3 - Per-element last-match + isolated geometry)

This custom recipe assigns colors to IFC elements based on selector syntax.
Supports multiple operations with filter groups and hex color assignments.

Recipe Name: SetColorBySelector
Description: Assign colors to IFC elements using IfcOpenShell selector syntax
Author: Jonatan Jacobsson
Date: 2025-01-08
Updated: 2026-01-16 (V3 optimizations - 13.8x faster)
Updated: 2026-03-11 (V3.2 - partial-match MappingSource splitting)
Updated: 2026-09-01 (V3.3 - one colour per element; isolate shared mapped geometry)

V3.3 assigns exactly one colour per element (last matching operation wins) and
styles a private copy of each MappingSource / direct representation so shared
IfcFacetedBrep items cannot be overwritten by a later colour rule.

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
import ifcopenshell.util.element
import ifcopenshell.util.selector

logger = logging.getLogger(__name__)


class Patcher:
    """
    Custom patcher for assigning colors to IFC elements using selector syntax.

    V3.3: last matching operation wins per element. Shared
    MappingSources and direct representations are duplicated per colour group
    and styled on isolated top-level items so later rules cannot paint earlier
    matches.

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
            'mapping_sources_duplicated': 0,
            'mapping_sources_split': 0
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

        # Batch mode: if the only argument is a JSON array string, unpack it so
        # callers can pass more than 5 operations in a single recipe invocation.
        if len(operation_args) == 1:
            first = operation_args[0].strip() if isinstance(operation_args[0], str) else ""
            if first.startswith('['):
                try:
                    ops_list = json.loads(first)
                    if isinstance(ops_list, list):
                        operation_args = tuple(
                            json.dumps(op) if isinstance(op, dict) else str(op)
                            for op in ops_list
                        )
                        self.logger.info(
                            f"Batch mode: unpacked {len(operation_args)} operation(s) from JSON array"
                        )
                except json.JSONDecodeError as e:
                    self.logger.warning(f"Failed to parse operations JSON array: {e}, falling back to single-arg mode")

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
            
            # Validate and sanitize selectors
            sanitized_selectors = self._sanitize_selectors(op['selectors'], idx + 1)
            if sanitized_selectors is None:
                continue
            op['selectors'] = sanitized_selectors
            
            validated_operations.append(op)
        
        return validated_operations
    
    def _sanitize_selectors(self, selectors_str: str, arg_num: int) -> str | None:
        """
        Validate and sanitize selector string.
        
        Detects and fixes common issues:
        - Duplicate/concatenated selectors missing separator (e.g., "value=1value=2" -> "value=1 + value=2")
        - Trailing/leading separators
        
        Returns sanitized string or None if unfixable.
        """
        original = selectors_str
        
        # Pattern to detect concatenated property selectors without separator
        # Matches: "=valueProperty.Name" or "=valueBIP." etc. (missing + between value and next property)
        concat_pattern = re.compile(r'(=\d+)([A-Za-z][A-Za-z0-9_]*\.[A-Za-z])')
        
        # Also detect IFC class patterns concatenated: "IfcWallIfcDoor"
        ifc_concat_pattern = re.compile(r'(Ifc[A-Z][a-z]+[a-z0-9]*)(Ifc[A-Z])')
        
        fixed = selectors_str
        
        # Fix concatenated property selectors
        if concat_pattern.search(fixed):
            fixed = concat_pattern.sub(r'\1 + \2', fixed)
            self.logger.warning(
                f"Argument {arg_num}: Detected concatenated selectors, auto-fixed: "
                f"'{original[:80]}...' -> '{fixed[:80]}...'"
            )
        
        # Fix concatenated IFC class selectors
        if ifc_concat_pattern.search(fixed):
            fixed = ifc_concat_pattern.sub(r'\1 + \2', fixed)
            self.logger.warning(
                f"Argument {arg_num}: Detected concatenated IFC classes, auto-fixed"
            )
        
        # Detect if selectors look like duplicates (same pattern repeated)
        filter_groups = [fg.strip() for fg in fixed.split('+') if fg.strip()]
        if len(filter_groups) > 1:
            # Check for exact duplicates in filter groups
            seen = set()
            unique_groups = []
            for fg in filter_groups:
                if fg not in seen:
                    seen.add(fg)
                    unique_groups.append(fg)
                else:
                    self.logger.warning(
                        f"Argument {arg_num}: Removed duplicate filter group: '{fg[:50]}'"
                    )
            
            if len(unique_groups) < len(filter_groups):
                fixed = ' + '.join(unique_groups)
                self.logger.warning(
                    f"Argument {arg_num}: Deduplicated selectors: {len(filter_groups)} -> {len(unique_groups)} groups"
                )
        
        # Final validation: try to parse each filter group
        filter_groups = [fg.strip() for fg in fixed.split('+') if fg.strip()]
        if not filter_groups:
            self.logger.warning(f"Argument {arg_num}: Selector string is empty after sanitization")
            return None
        
        return fixed

    def _select_elements(self, selector: str):
        """Select elements, falling back for simple property equality filters."""
        try:
            return ifcopenshell.util.selector.filter_elements(self.file, selector)
        except Exception as e:
            elements = self._select_simple_property_elements(selector)
            if elements is None:
                raise
            self.logger.warning(
                f"IfcOpenShell selector failed for '{selector}' ({e}); "
                f"used robust property selector fallback and found {len(elements)} element(s)"
            )
            return elements

    def _select_simple_property_elements(self, selector: str):
        """
        Evaluate simple selectors like ``IfcElement, BIP.BSABe=640``.

        Some real-world IFCs contain malformed property relationships that make
        IfcOpenShell's selector crash while scanning psets. This fallback skips
        malformed definitions instead of failing the entire patch job.
        """
        parsed = self._parse_simple_property_selector(selector)
        if parsed is None:
            return None

        ifc_class, predicates = parsed
        try:
            candidates = self.file.by_type(ifc_class)
        except Exception:
            return None

        matched = []
        for element in candidates:
            if all(
                self._selector_value_matches(
                    self._get_property_value_safe(element, pset_name, property_name),
                    expected,
                )
                for pset_name, property_name, expected in predicates
            ):
                matched.append(element)
        return set(matched)

    def _parse_simple_property_selector(self, selector: str):
        parts = [part.strip() for part in selector.split(',') if part.strip()]
        if not parts:
            return None

        if re.fullmatch(r'Ifc[A-Za-z0-9_]+', parts[0]):
            ifc_class = parts[0]
            predicate_parts = parts[1:]
        else:
            ifc_class = 'IfcElement'
            predicate_parts = parts

        if not predicate_parts:
            return None

        predicates = []
        for predicate in predicate_parts:
            match = re.fullmatch(
                r'([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)',
                predicate,
            )
            if not match:
                return None
            expected = match.group(3).strip()
            if len(expected) >= 2 and expected[0] == expected[-1] and expected[0] in ("'", '"'):
                expected = expected[1:-1]
            predicates.append((match.group(1), match.group(2), expected))

        return ifc_class, predicates

    def _get_property_value_safe(self, element, pset_name: str, property_name: str):
        try:
            value = ifcopenshell.util.element.get_pset(element, pset_name, property_name)
            if value is not None:
                return value
        except Exception:
            pass

        for definition in self._iter_property_definitions(element):
            if not self._safe_is_a(definition, 'IfcPropertySet'):
                continue
            if self._safe_getattr(definition, 'Name') != pset_name:
                continue
            for prop in self._safe_getattr(definition, 'HasProperties') or ():
                if self._safe_getattr(prop, 'Name') != property_name:
                    continue
                if self._safe_is_a(prop, 'IfcPropertySingleValue'):
                    return self._unwrap_ifc_value(self._safe_getattr(prop, 'NominalValue'))
        return None

    def _iter_property_definitions(self, element):
        for rel in self._safe_getattr(element, 'IsDefinedBy') or ():
            if not self._safe_is_a(rel, 'IfcRelDefinesByProperties'):
                continue
            definition = self._safe_getattr(rel, 'RelatingPropertyDefinition')
            if definition is not None:
                yield definition

        try:
            element_type = ifcopenshell.util.element.get_type(element)
        except Exception:
            element_type = None
        for definition in self._safe_getattr(element_type, 'HasPropertySets') or ():
            if definition is not None:
                yield definition

    def _selector_value_matches(self, actual, expected: str) -> bool:
        if actual is None:
            return False

        actual_unwrapped = self._unwrap_ifc_value(actual)
        actual_text = str(actual_unwrapped).strip()
        expected_text = str(expected).strip()

        try:
            return float(actual_text) == float(expected_text)
        except ValueError:
            return actual_text == expected_text

    def _unwrap_ifc_value(self, value):
        if hasattr(value, 'wrappedValue'):
            return value.wrappedValue
        return value

    def _safe_is_a(self, entity, ifc_class: str) -> bool:
        try:
            return bool(entity and entity.is_a(ifc_class))
        except Exception:
            return False

    def _safe_getattr(self, entity, attribute: str):
        try:
            return getattr(entity, attribute) if entity is not None else None
        except Exception:
            return None
    
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

    def _prefixed_selector(self, selector: str) -> str:
        selector = selector.strip()
        has_ifc_class = re.search(r'\bIfc[A-Z]\w*\b', selector)
        if '.' in selector and '=' in selector and not has_ifc_class:
            return f"IfcElement, {selector}"
        return selector

    def _iter_operation_filter_groups(self, operation: dict):
        selectors_str = operation['selectors']
        hex_value = operation['hex']
        transparency_value = operation.get('transparency', '')

        filter_groups = [fg.strip() for fg in selectors_str.split('+') if fg.strip()]
        if not filter_groups:
            return

        hex_colors = [h.strip() for h in hex_value.split('+') if h.strip()]
        if len(hex_colors) == 1:
            hex_list = hex_colors * len(filter_groups)
        else:
            hex_list = hex_colors

        if len(hex_list) != len(filter_groups):
            raise ValueError(
                f"Number of hex colors ({len(hex_list)}) must match number of "
                f"filter groups ({len(filter_groups)})"
            )

        transparency_list = []
        if transparency_value in (None, ''):
            transparency_list = [0.0] * len(filter_groups)
        elif isinstance(transparency_value, (int, float)):
            transparency_list = [float(transparency_value)] * len(filter_groups)
        else:
            transparency_strs = [t.strip() for t in str(transparency_value).split('+') if t.strip()]
            if len(transparency_strs) == 1:
                transparency_list = [float(transparency_strs[0])] * len(filter_groups)
            else:
                transparency_list = [float(t) for t in transparency_strs]
            if len(transparency_list) != len(filter_groups):
                raise ValueError(
                    f"Number of transparency values ({len(transparency_list)}) must match "
                    f"number of filter groups ({len(filter_groups)})"
                )

        for filter_group, hex_color, transparency in zip(filter_groups, hex_list, transparency_list):
            yield filter_group, hex_color, float(transparency)

    def _resolve_element_styles(self) -> dict:
        """Last matching operation wins per element."""
        element_to_style = {}
        for idx, operation in enumerate(self.operations):
            for filter_group, hex_color, transparency in self._iter_operation_filter_groups(operation):
                selector = self._prefixed_selector(filter_group)
                elements = self._select_elements(selector)
                if not elements:
                    self.logger.warning(
                        f"No elements matched filter group: '{selector}' (original: '{filter_group}')"
                    )
                    continue
                style = self._get_or_create_style(hex_color, transparency)
                self.logger.info(
                    f"Operation {idx + 1} filter '{selector}': {len(elements)} element(s) -> {style.Name}"
                )
                for element in elements:
                    element_to_style[element] = style
        return element_to_style

    def _iter_shape_reps(self, product):
        pds = getattr(product, 'Representation', None)
        if not pds or not pds.is_a('IfcProductDefinitionShape'):
            return
        for rep in pds.Representations or ():
            if rep.is_a('IfcShapeRepresentation'):
                yield pds, rep

    def _build_geometry_indexes(self):
        ms_to_elements = defaultdict(set)
        direct_rep_to_elements = defaultdict(set)
        pds_to_elements = defaultdict(set)
        for product in self.file.by_type('IfcProduct'):
            for pds, rep in self._iter_shape_reps(product):
                pds_to_elements[pds].add(product)
                has_mapped = False
                for item in rep.Items or ():
                    if item.is_a('IfcMappedItem') and item.MappingSource:
                        has_mapped = True
                        ms_to_elements[item.MappingSource].add(product)
                if not has_mapped:
                    direct_rep_to_elements[rep].add(product)
        return ms_to_elements, direct_rep_to_elements, pds_to_elements

    def _copy_styleable_item(self, item):
        """Copy a top-level representation item so it can be styled independently.

        Nested geometry (shells, operands, swept profiles) is referenced, not
        cloned. Never returns the original item: isolation is required.
        """
        entity_type = item.is_a()
        try:
            if entity_type == "IfcShellBasedSurfaceModel":
                new_item = self.file.create_entity(
                    "IfcShellBasedSurfaceModel",
                    SbsmBoundary=item.SbsmBoundary,
                )
            elif entity_type in ("IfcFacetedBrep", "IfcManifoldSolidBrep", "IfcAdvancedBrep"):
                new_item = self.file.create_entity(entity_type, Outer=item.Outer)
            elif entity_type == "IfcFacetedBrepWithVoids":
                new_item = self.file.create_entity(
                    "IfcFacetedBrepWithVoids",
                    Outer=item.Outer,
                    Voids=item.Voids,
                )
            elif entity_type in ("IfcBooleanResult", "IfcBooleanClippingResult"):
                new_item = self.file.create_entity(
                    entity_type,
                    Operator=item.Operator,
                    FirstOperand=item.FirstOperand,
                    SecondOperand=item.SecondOperand,
                )
            elif entity_type == "IfcExtrudedAreaSolid":
                new_item = self.file.create_entity(
                    "IfcExtrudedAreaSolid",
                    SweptArea=item.SweptArea,
                    Position=item.Position,
                    ExtrudedDirection=item.ExtrudedDirection,
                    Depth=item.Depth,
                )
            elif entity_type == "IfcSweptDiskSolid":
                new_item = self.file.create_entity(
                    "IfcSweptDiskSolid",
                    Directrix=item.Directrix,
                    Radius=item.Radius,
                    InnerRadius=item.InnerRadius,
                    StartParam=item.StartParam,
                    EndParam=item.EndParam,
                )
            elif entity_type == "IfcMappedItem":
                new_item = self.file.create_entity(
                    "IfcMappedItem",
                    MappingSource=item.MappingSource,
                    MappingTarget=item.MappingTarget,
                )
            else:
                info = item.get_info(recursive=False, include_identifier=False)
                info.pop('type', None)
                info.pop('id', None)
                new_item = self.file.create_entity(entity_type, **info)
        except Exception as exc:
            raise ValueError(
                f"Cannot isolate styleable item #{item.id()} ({entity_type}): {exc}"
            ) from exc

        if new_item.id() == item.id():
            raise ValueError(
                f"Cannot isolate styleable item #{item.id()} ({entity_type}): copy returned original"
            )
        return new_item

    def _duplicate_representation_isolated(self, original_rep):
        if original_rep is None:
            raise ValueError("Cannot duplicate a missing representation")
        new_items = tuple(
            self._copy_styleable_item(item) for item in (original_rep.Items or ())
        )
        return self.file.create_entity(
            "IfcShapeRepresentation",
            ContextOfItems=original_rep.ContextOfItems,
            RepresentationIdentifier=original_rep.RepresentationIdentifier,
            RepresentationType=original_rep.RepresentationType,
            Items=new_items or None,
        )

    def _assign_item_style(self, item, style) -> None:
        """Style a single representation item without traversing nested geometry.

        Implemented locally so IFC2X3 and older IfcOpenShell 0.8 builds (which
        lack ``style.assign_item_style``) behave the same as the worker.
        """
        existing = next(iter(getattr(item, "StyledByItem", None) or ()), None)
        use_assignment = self.file.schema == "IFC2X3"
        if existing is None:
            styles = (style,)
            if use_assignment:
                styles = (self.file.create_entity("IfcPresentationStyleAssignment", (style,)),)
            self.file.create_entity("IfcStyledItem", item, styles)
            return

        if use_assignment:
            assignment = None
            for current in existing.Styles or ():
                if current.is_a("IfcPresentationStyleAssignment"):
                    assignment = current
                    break
            if assignment is not None:
                assignment.Styles = (style,)
                existing.Styles = (assignment,)
                return
            assignment = self.file.create_entity("IfcPresentationStyleAssignment", (style,))
            existing.Styles = (assignment,)
            return

        existing.Styles = (style,)

    def _style_representation_items(self, rep, style) -> int:
        if not rep or not rep.Items:
            return 0
        styled = 0
        for item in rep.Items:
            self._assign_item_style(item, style)
            styled += 1
        return styled

    def _remap_elements_to_mapping_source(self, elements, original_ms, new_ms) -> int:
        original_id = original_ms.id()
        updated = 0
        for elem in elements:
            for _pds, rep in self._iter_shape_reps(elem):
                items = list(rep.Items or ())
                changed = False
                new_items = []
                for item in items:
                    if item.is_a('IfcMappedItem') and item.MappingSource and item.MappingSource.id() == original_id:
                        new_items.append(
                            self.file.create_entity(
                                "IfcMappedItem",
                                MappingSource=new_ms,
                                MappingTarget=item.MappingTarget,
                            )
                        )
                        changed = True
                        updated += 1
                    else:
                        new_items.append(item)
                if changed:
                    rep.Items = tuple(new_items)
        return updated

    def _duplicate_mapping_source_isolated(self, original_ms, elements):
        original_mapped_rep = original_ms.MappedRepresentation
        new_mapped_rep = self._duplicate_representation_isolated(original_mapped_rep)
        new_ms = self.file.create_entity(
            "IfcRepresentationMap",
            MappingOrigin=original_ms.MappingOrigin,
            MappedRepresentation=new_mapped_rep,
        )
        updated = self._remap_elements_to_mapping_source(elements, original_ms, new_ms)
        self.stats['mapping_sources_duplicated'] += 1
        self.logger.debug(
            f"Isolated MappingSource {original_ms.id()} -> {new_ms.id()} "
            f"(rep {new_mapped_rep.id()}), remapped {updated} MappedItem(s)"
        )
        return new_ms

    def _repoint_elements_representation(self, elements, old_rep, new_rep, pds_to_elements):
        old_id = old_rep.id()
        group = set(elements)
        for elem in elements:
            pds = getattr(elem, 'Representation', None)
            if not pds or not pds.is_a('IfcProductDefinitionShape'):
                continue
            representations = list(pds.Representations or ())
            if not any(r.id() == old_id for r in representations):
                continue
            pds_users = pds_to_elements.get(pds, {elem})
            outsiders = pds_users - group
            new_reps = tuple(new_rep if r.id() == old_id else r for r in representations)
            if outsiders:
                new_pds = self.file.create_entity(
                    "IfcProductDefinitionShape",
                    Name=pds.Name,
                    Description=pds.Description,
                    Representations=new_reps,
                )
                elem.Representation = new_pds
                pds_to_elements[new_pds] = {elem}
                pds_to_elements[pds] = outsiders
            else:
                pds.Representations = new_reps

    def _partition_by_style(self, users, element_to_style):
        by_style = defaultdict(set)
        for elem in users:
            style = element_to_style.get(elem)
            if style is not None:
                by_style[style].add(elem)
        return by_style

    def _apply_isolated_colours(self, element_to_style):
        ms_to_elements, direct_rep_to_elements, pds_to_elements = self._build_geometry_indexes()
        colored_ids = {e.id() for e in element_to_style}

        for mapping_source, users in list(ms_to_elements.items()):
            by_style = self._partition_by_style(users, element_to_style)
            if not by_style:
                continue
            if len(by_style) > 1 or (users - set().union(*by_style.values())):
                self.stats['conflicts_detected'] += max(0, len(by_style) - 1)
                self.stats['mapping_sources_split'] += 1
            for style, group in by_style.items():
                new_ms = self._duplicate_mapping_source_isolated(mapping_source, group)
                styled = self._style_representation_items(new_ms.MappedRepresentation, style)
                if styled:
                    self.stats['mapping_sources_styled'] += 1

        for rep, users in list(direct_rep_to_elements.items()):
            by_style = self._partition_by_style(users, element_to_style)
            if not by_style:
                continue
            if len(by_style) > 1 or (users - set().union(*by_style.values())):
                self.stats['conflicts_detected'] += max(0, len(by_style) - 1)
            for style, group in by_style.items():
                new_rep = self._duplicate_representation_isolated(rep)
                self._repoint_elements_representation(group, rep, new_rep, pds_to_elements)
                styled = self._style_representation_items(new_rep, style)
                if styled:
                    self.stats['representations_styled'] += 1

        self.stats['elements_colored'] = len(colored_ids)

    def patch(self) -> None:
        """Assign one colour per element, isolating shared mapped geometry."""
        if self.stats['operations_total'] == 0:
            self.logger.warning("No valid operations to execute")
            return

        self.logger.info(
            f"Starting SetColorBySelector with {self.stats['operations_total']} operation(s)"
        )
        try:
            element_to_style = self._resolve_element_styles()
            self._apply_isolated_colours(element_to_style)
            self.stats['operations_completed'] = len(self.operations)
            self.logger.info(
                f"SetColorBySelector completed: "
                f"{self.stats['operations_completed']}/{self.stats['operations_total']} operations succeeded, "
                f"{self.stats['elements_colored']} elements colored, "
                f"{self.stats['mapping_sources_styled']} MappingSources styled, "
                f"{self.stats['representations_styled']} direct reps styled, "
                f"{self.stats['mapping_sources_split']} MappingSources split, "
                f"{self.stats['conflicts_detected']} colour-group conflicts, "
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
