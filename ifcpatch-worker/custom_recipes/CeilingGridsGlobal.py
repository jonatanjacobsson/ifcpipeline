"""
CeilingGridsGlobal Custom Recipe

This recipe creates IFC beams from ceiling element footprints exported from Revit.
It processes IfcCovering elements with FootPrint representations and generates:
- L-profile beams for perimeter segments (ceiling angle profiles)
- T-profile beams for interior segments (ceiling T-runners)

The beams are positioned in GLOBAL/WORLD coordinates with absolute placement 
(PlacementRelTo=None). Beams are independent of parent covering elements and 
assigned directly to spatial containers (BuildingStorey/Building).

COORDINATE SYSTEM: Global/world coordinates (absolute placement)
HIERARCHY: Beams assigned to BuildingStorey/Building (independent of coverings)
OUTPUT: Original model + independent beams, OR beams-only file (optional)

For nested beams within parent coverings, see CeilingGridsNested recipe.

Recipe Name: CeilingGridsGlobal
Description: Generate ceiling grid beams with global/absolute coordinate placement
Author: IFC Pipeline Team
Date: 2025-01-01
Version: 0.2.0
"""

import logging
import os
import numpy as np
import ifcopenshell
import ifcopenshell.api.nest
import ifcopenshell.api.root
import ifcopenshell.api.geometry
import ifcopenshell.api.pset
import ifcopenshell.api.style
import ifcopenshell.api.unit
import ifcopenshell.api.context
import ifcopenshell.api.spatial
import ifcopenshell.util.representation
import ifcopenshell.util.placement
import ifcopenshell.util.selector
import ifcopenshell.guid
from ifcpatch import BasePatcher
from typing import List, Dict, Any, Set, Tuple, Optional

logger = logging.getLogger(__name__)


class Patcher(BasePatcher):
    """
    Custom patcher for generating ceiling grid beams from IfcCovering footprints.
    
    This recipe uses GLOBAL/ABSOLUTE PLACEMENT where beams are positioned in 
    world coordinates independent of their parent IfcCovering elements. Beams 
    are assigned to spatial containers and can exist independently.
    
    This recipe:
    1. Finds IfcCovering elements with FootPrint curve representations
    2. Extracts polyline segments from footprints
    3. Transforms segment coordinates to global/world space
    4. Identifies perimeter vs interior segments using connectivity analysis
    5. Creates IFC beams with appropriate profiles (L for perimeter, T for interior)
    6. Assigns beams to spatial containers (BuildingStorey/Building)
    7. Optionally extracts beams to a separate IFC file
    
    Use Cases:
    - Create beams independent of parent ceiling elements
    - Delete covering elements while preserving beams
    - Export beams separately for analysis/coordination
    - Avoid nested hierarchy complexities
    
    For nested beams within parent coverings, use CeilingGridsNested instead.
    
    Parameters:
        file: The IFC model to patch
        logger: Logger instance for output
        extract_beams: Extract beams to separate file (default: "false")
        profile_height: Height of T-profile in mm (default: 40.0)
        profile_width: Width of profiles in mm (default: 20.0)
        profile_thickness: Thickness of profiles in mm (default: 5.0)
        tolerance: Connection tolerance in mm (default: 50.0)
        output_path: Path for extracted beams file (default: auto-generated)
    
    Example:
        # Use default dimensions, no extraction
        patcher = Patcher(ifc_file, logger)
        patcher.patch()
        
        # Custom dimensions with beam extraction
        patcher = Patcher(ifc_file, logger, "true", "50.0", "25.0", "6.0", "5.0", "/path/to/beams.ifc")
        patcher.patch()
        output = patcher.get_output()
    """
    
    def __init__(self, file: ifcopenshell.file, logger: logging.Logger,
                 extract_beams: str = "false",
                 profile_height: str = "40.0",
                 profile_width: str = "20.0", 
                 profile_thickness: str = "5.0",
                 tolerance: str = "50.0",
                 output_path: str = ""):
        """
        Initialize the CeilingGridsGlobal patcher.
        
        Args:
            file: IFC file to patch
            logger: Logger instance
            extract_beams: "true" or "false" to extract beams to separate file (default: "false")
            profile_height: Height of T-profile in mm (default: "40.0")
            profile_width: Width of profiles in mm (default: "20.0")
            profile_thickness: Thickness of profiles in mm (default: "5.0")
            tolerance: Connection tolerance in mm (default: "50.0")
            output_path: Path for extracted beams file, empty for auto-generated (default: "")
        """
        super().__init__(file, logger)
        
        # Parse arguments with defaults
        self.extract_beams = extract_beams.lower() == "true" if extract_beams else False
        self.profile_height = float(profile_height) if profile_height else 40.0
        self.profile_width = float(profile_width) if profile_width else 20.0
        self.profile_thickness = float(profile_thickness) if profile_thickness else 5.0
        self.tolerance = float(tolerance) if tolerance else 50.0
        self.output_path = output_path if output_path else None
        
        # Validate parameters
        if self.profile_height <= 0:
            raise ValueError(f"profile_height must be positive, got {self.profile_height}")
        if self.profile_width <= 0:
            raise ValueError(f"profile_width must be positive, got {self.profile_width}")
        if self.profile_thickness <= 0:
            raise ValueError(f"profile_thickness must be positive, got {self.profile_thickness}")
        if self.tolerance < 0:
            raise ValueError(f"tolerance must be non-negative, got {self.tolerance}")
        
        # Statistics
        self.stats = {
            "covering_elements": 0,
            "total_segments": 0,
            "perimeter_beams": 0,
            "interior_beams": 0,
            "total_beams": 0
        }
        
        # Cache for style, contexts, and spatial container
        self.black_style = None
        self.body_context = None
        self.axis_context = None
        self.spatial_container = None
        self.unit_scale = 1.0
        
        self.logger.info(f"Initialized CeilingGridsGlobal recipe (Global/Absolute Placement):")
        self.logger.info(f"  Profile Height: {self.profile_height}mm")
        self.logger.info(f"  Profile Width: {self.profile_width}mm")
        self.logger.info(f"  Profile Thickness: {self.profile_thickness}mm")
        self.logger.info(f"  Tolerance: {self.tolerance}mm")
        self.logger.info(f"  Extract Beams: {self.extract_beams}")
        if self.extract_beams and self.output_path:
            self.logger.info(f"  Output Path: {self.output_path}")
    
    def patch(self) -> None:
        """
        Execute the ceiling grid beam generation with global placement.
        """
        self.logger.info("Starting CeilingGridsGlobal patch operation")
        
        try:
            # Assign units if not already present
            ifcopenshell.api.unit.assign_unit(self.file)
            
            # Get unit scale
            self.unit_scale = self._get_project_unit_scale()
            self.logger.info(f"Project unit scale: {self.unit_scale}x (1.0=mm, 1000.0=meters)")
            
            # Create black style for beams
            self.black_style = self._create_black_style()
            
            # Get representation contexts
            self._setup_contexts()
            
            # Get spatial container for beams
            self.spatial_container = self._get_spatial_container()
            if self.spatial_container:
                self.logger.info(f"Spatial container: {self.spatial_container.is_a()} - {getattr(self.spatial_container, 'Name', 'Unnamed')}")
            else:
                self.logger.warning("No spatial container found - beams will not be assigned to building structure")
            
            # Find all covering elements
            covering_elements = self.file.by_type("IfcCovering")
            
            if not covering_elements:
                self.logger.warning("No IfcCovering elements found in the model")
                return
            
            self.logger.info(f"Found {len(covering_elements)} IfcCovering elements")
            
            # Extract beam data from all covering elements
            all_beam_data = []
            
            for elem_index, elem in enumerate(covering_elements):
                if elem_index % 50 == 0 and elem_index > 0:
                    self.logger.info(f"Processing covering element {elem_index}/{len(covering_elements)}...")
                
                beam_data, segments = self._extract_beam_data_from_covering(elem_index, elem)
                all_beam_data.extend(beam_data)
                self.stats["total_segments"] += segments
                if beam_data:
                    self.stats["covering_elements"] += 1
            
            self.logger.info(f"Extracted {len(all_beam_data)} beam segments from {self.stats['total_segments']} polyline segments")
            
            # Count perimeter vs interior
            perimeter_count = sum(1 for b in all_beam_data if b['segment'].get('is_perimeter', False))
            interior_count = len(all_beam_data) - perimeter_count
            
            self.logger.info(f"  Perimeter beams (L-profile): {perimeter_count}")
            self.logger.info(f"  Interior beams (T-profile): {interior_count}")
            
            # Create beams
            covering_lookup = {elem.id(): elem for elem in covering_elements}
            
            for idx, beam_info in enumerate(all_beam_data):
                if idx % 1000 == 0 and idx > 0:
                    self.logger.info(f"Created {idx}/{len(all_beam_data)} beams...")
                
                covering_element = covering_lookup[beam_info['covering_element_id']]
                self._create_beam_at_segment(
                    covering_element,
                    beam_info['segment'],
                    beam_info['segment_id'],
                    beam_info['segment'].get('is_perimeter', False)
                )
                
                if beam_info['segment'].get('is_perimeter', False):
                    self.stats["perimeter_beams"] += 1
                else:
                    self.stats["interior_beams"] += 1
                
                self.stats["total_beams"] += 1
            
            # Determine output path for extracted beams before logging statistics
            if self.extract_beams and not self.output_path:
                self.output_path = "extracted_beams.ifc"
            
            # Log statistics
            self._log_statistics()
            
            # Extract beams to separate file if requested
            if self.extract_beams:
                self._extract_beams_to_file()
            
            self.logger.info("CeilingGridsGlobal patch operation completed successfully")
            
        except Exception as e:
            self.logger.error(f"Error during CeilingGridsGlobal patch: {str(e)}", exc_info=True)
            raise
    
    def _get_project_unit_scale(self) -> float:
        """
        Get the project's length unit scale factor to convert to millimeters.
        Returns 1.0 if unit is already mm, 1000.0 if unit is meters, etc.
        """
        try:
            units = self.file.by_type("IfcUnitAssignment")
            if units:
                for unit in units[0].Units:
                    if hasattr(unit, 'UnitType') and unit.UnitType == 'LENGTHUNIT':
                        if hasattr(unit, 'Name'):
                            unit_name = unit.Name.upper()
                            if 'METRE' in unit_name or 'METER' in unit_name:
                                if hasattr(unit, 'Prefix'):
                                    if unit.Prefix == 'MILLI':
                                        return 1.0  # Already in mm
                                    elif unit.Prefix == 'CENTI':
                                        return 10.0  # cm to mm
                                # No prefix means meters
                                return 1000.0  # meters to mm
            # Default: assume meters
            return 1000.0
        except Exception as e:
            self.logger.warning(f"Could not determine project units, assuming meters: {str(e)}")
            return 1000.0
    
    def _get_global_placement_matrix(self, element: ifcopenshell.entity_instance) -> np.ndarray:
        """
        Get the global transformation matrix for an element's placement.
        Returns a 4x4 matrix in the file's project units.
        """
        try:
            matrix = ifcopenshell.util.placement.get_local_placement(element.ObjectPlacement)
            return matrix
        except Exception as e:
            self.logger.warning(f"Could not get placement matrix, using identity: {str(e)}")
            return np.eye(4)
    
    def _transform_point(self, point: Tuple[float, float, float], 
                        transform_matrix: np.ndarray) -> List[float]:
        """Transform a 3D point using a 4x4 transformation matrix"""
        point_homogeneous = np.array([point[0], point[1], point[2], 1.0])
        transformed = np.dot(transform_matrix, point_homogeneous)
        return [float(transformed[0]), float(transformed[1]), float(transformed[2])]
    
    def _transform_direction(self, direction: Tuple[float, float, float],
                            transform_matrix: np.ndarray) -> Tuple[float, float, float]:
        """Transform a direction vector (rotation only, no translation)"""
        direction_4d = np.array([direction[0], direction[1], direction[2], 0.0])
        global_direction_4d = np.dot(transform_matrix, direction_4d)
        global_direction = (float(global_direction_4d[0]), float(global_direction_4d[1]), float(global_direction_4d[2]))
        
        # Normalize
        dir_length = (global_direction[0]**2 + global_direction[1]**2 + global_direction[2]**2) ** 0.5
        if dir_length > 0:
            global_direction = tuple(float(d / dir_length) for d in global_direction)
        
        return global_direction
    
    def _get_spatial_container(self) -> Optional[ifcopenshell.entity_instance]:
        """Get the appropriate spatial container for beams (BuildingStorey or Building)"""
        # Try to find a building storey
        building_storeys = self.file.by_type("IfcBuildingStorey")
        if building_storeys:
            return building_storeys[0]
        
        # Fall back to building
        buildings = self.file.by_type("IfcBuilding")
        if buildings:
            return buildings[0]
        
        return None
    
    def _create_black_style(self) -> Optional[ifcopenshell.entity_instance]:
        """Create a black surface style for beams"""
        try:
            presentation_style = ifcopenshell.api.style.add_style(self.file, name="Black Beam Style")
            
            black_color = self.file.createIfcColourRgb("Black", 0.0, 0.0, 0.0)
            
            surface_style = self.file.createIfcSurfaceStyleRendering(
                black_color,
                0.0,  # Transparency
                None, None, None, None, None, None,
                "NOTDEFINED"
            )
            
            presentation_style.Styles = (surface_style,)
            
            return presentation_style
        except Exception as e:
            self.logger.warning(f"Could not create black style: {str(e)}")
            return None
    
    def _setup_contexts(self) -> None:
        """Setup geometric representation contexts"""
        try:
            model_context = self.file.by_type("IfcGeometricRepresentationContext")[0]
            
            self.body_context = ifcopenshell.util.representation.get_context(
                self.file, "Model", "Body", "MODEL_VIEW"
            )
            if not self.body_context:
                self.body_context = ifcopenshell.api.context.add_context(
                    self.file, context_type="Model", context_identifier="Body",
                    target_view="MODEL_VIEW", parent=model_context
                )
            
            self.axis_context = ifcopenshell.util.representation.get_context(
                self.file, "Model", "Axis", "GRAPH_VIEW"
            )
            if not self.axis_context:
                self.axis_context = ifcopenshell.api.context.add_context(
                    self.file, context_type="Model", context_identifier="Axis",
                    target_view="GRAPH_VIEW", parent=model_context
                )
        except Exception as e:
            self.logger.error(f"Could not setup contexts: {str(e)}")
            raise
    
    def _extract_footprint_curves(self, elem: ifcopenshell.entity_instance) -> List[ifcopenshell.entity_instance]:
        """Extract FootPrint curves from element"""
        curves_found = []
        
        try:
            representation = elem.Representation
            if not representation or not representation.Representations:
                return curves_found
            
            for rep in representation.Representations:
                rep_id = getattr(rep, "RepresentationIdentifier", "")
                rep_type = getattr(rep, "RepresentationType", "")
                
                if rep_id == "FootPrint" and rep_type == "Curve2D" and rep.Items:
                    for item in rep.Items:
                        if item.is_a("IfcPolyline"):
                            curves_found.append(item)
                            
        except Exception as e:
            self.logger.debug(f"Error extracting footprint curves: {str(e)}")
        
        return curves_found
    
    def _process_polyline_to_segments(self, polyline: ifcopenshell.entity_instance, 
                                     polyline_index: int) -> List[Dict[str, Any]]:
        """Process an IfcPolyline into ceiling grid segments"""
        segments = []
        
        try:
            points = polyline.Points
            if not points or len(points) < 2:
                return segments
            
            for i in range(len(points) - 1):
                start_point = points[i]
                end_point = points[i + 1]
                
                start_coords = start_point.Coordinates
                end_coords = end_point.Coordinates
                
                if start_coords and end_coords:
                    start_z = start_coords[2] if len(start_coords) > 2 else 0.0
                    end_z = end_coords[2] if len(end_coords) > 2 else 0.0
                    
                    start_mm = (start_coords[0], start_coords[1], start_z)
                    end_mm = (end_coords[0], end_coords[1], end_z)
                    
                    dx = end_mm[0] - start_mm[0]
                    dy = end_mm[1] - start_mm[1]
                    horizontal_length = (dx**2 + dy**2)**0.5
                    
                    if horizontal_length > 0.001:  # Ignore very small segments
                        direction = (dx/horizontal_length, dy/horizontal_length, 0.0)
                        
                        segment = {
                            "polyline_index": polyline_index,
                            "segment_index": i,
                            "start_point": start_mm,
                            "end_point": end_mm,
                            "direction": direction,
                            "length": horizontal_length,
                            "midpoint": (
                                (start_mm[0] + end_mm[0]) / 2,
                                (start_mm[1] + end_mm[1]) / 2,
                                (start_mm[2] + end_mm[2]) / 2
                            )
                        }
                        segments.append(segment)
                        
        except Exception as e:
            self.logger.debug(f"Error processing polyline {polyline_index}: {str(e)}")
        
        return segments
    
    def _find_closed_loop_segments(self, all_segments: List[Dict[str, Any]]) -> Set[int]:
        """
        Find segments that form a closed loop (perimeter).
        Returns the set of segment indices that form the outer perimeter loop.
        """
        def points_coincident(p1: Tuple[float, float, float], 
                            p2: Tuple[float, float, float]) -> bool:
            dx = p1[0] - p2[0]
            dy = p1[1] - p2[1]
            dz = p1[2] - p2[2]
            dist = (dx*dx + dy*dy + dz*dz) ** 0.5
            return dist < self.tolerance
        
        # Build endpoint connectivity map
        endpoint_connections = {}
        
        for idx, segment in enumerate(all_segments):
            start = segment['start_point']
            end = segment['end_point']
            
            # Count connections for each endpoint
            for point in [start, end]:
                point_key = None
                for existing_point in endpoint_connections.keys():
                    if points_coincident(point, existing_point):
                        point_key = existing_point
                        break
                
                if point_key is None:
                    point_key = point
                    endpoint_connections[point_key] = []
                
                if idx not in endpoint_connections[point_key]:
                    endpoint_connections[point_key].append(idx)
        
        # A segment is truly interior only if BOTH endpoints have 3+ connections
        truly_interior = set()
        
        for idx in range(len(all_segments)):
            segment = all_segments[idx]
            start = segment['start_point']
            end = segment['end_point']
            
            start_connections = 0
            end_connections = 0
            
            for point, connected_segs in endpoint_connections.items():
                if points_coincident(start, point):
                    start_connections = len(connected_segs)
                if points_coincident(end, point):
                    end_connections = len(connected_segs)
            
            # Interior only if both endpoints have 3+ connections
            if start_connections >= 3 and end_connections >= 3:
                truly_interior.add(idx)
        
        # Perimeter: all segments except truly interior ones
        perimeter_segment_indices = set(range(len(all_segments))) - truly_interior
        
        return perimeter_segment_indices
    
    def _extract_beam_data_from_covering(self, elem_index: int, 
                                        elem: ifcopenshell.entity_instance) -> Tuple[List[Dict[str, Any]], int]:
        """Extract beam data from a covering element"""
        try:
            curves = self._extract_footprint_curves(elem)
            beam_data = []
            all_segments = []
            
            # Process footprint curves into segments
            for curve_index, curve in enumerate(curves):
                segments = self._process_polyline_to_segments(curve, curve_index)
                all_segments.extend(segments)
            
            if not all_segments:
                return [], 0
            
            # Find perimeter segments
            perimeter_indices = self._find_closed_loop_segments(all_segments)
            
            # Create beam data with perimeter flags
            for idx, segment in enumerate(all_segments):
                is_perimeter = idx in perimeter_indices
                segment['is_perimeter'] = is_perimeter
                
                beam_data.append({
                    'segment': segment,
                    'segment_id': f"{elem_index}_{segment['polyline_index']}_{segment['segment_index']}",
                    'covering_element_id': elem.id()
                })
            
            return beam_data, len(all_segments)
            
        except Exception as e:
            self.logger.debug(f"Error processing covering element {elem_index}: {str(e)}")
            return [], 0
    
    def _create_beam_at_segment(self, covering_element: ifcopenshell.entity_instance,
                               segment: Dict[str, Any], segment_id: str,
                               is_perimeter: bool) -> ifcopenshell.entity_instance:
        """Create an IFC beam with axis and body representations using GLOBAL/ABSOLUTE placement"""
        try:
            # Create beam element
            beam = ifcopenshell.api.root.create_entity(self.file, ifc_class="IfcBeam")
            beam.ObjectType = "Grid Covering"
            
            # Set name based on type
            if is_perimeter:
                beam.Name = f"Ceiling_Profile_Angle_{segment_id}"
            else:
                beam.Name = f"Ceiling_Profile_T-Runner_{segment_id}"
            
            # Beam properties (in covering element's local coordinates)
            start_point = segment["start_point"]
            direction = segment["direction"]
            length = segment["length"]
            
            # TRANSFORM TO GLOBAL COORDINATES
            covering_transform = self._get_global_placement_matrix(covering_element)
            
            # Transform start point to global coordinates
            global_start_point = self._transform_point(start_point, covering_transform)
            
            # Transform direction vector
            global_direction = self._transform_direction(direction, covering_transform)
            
            # Create ABSOLUTE placement (PlacementRelTo = None)
            local_placement = self.file.createIfcLocalPlacement(
                None,  # None = absolute world coordinates
                self.file.createIfcAxis2Placement3D(
                    self.file.createIfcCartesianPoint(global_start_point),
                    self.file.createIfcDirection((0., 0., 1.)),
                    self.file.createIfcDirection((1., 0., 0.))
                )
            )
            beam.ObjectPlacement = local_placement
            
            # Create axis representation using global direction
            start_relative = (0.0, 0.0, 0.0)
            end_relative = (global_direction[0] * length, global_direction[1] * length, global_direction[2] * length)
            
            polyline = self.file.createIfcPolyline([
                self.file.createIfcCartesianPoint(start_relative),
                self.file.createIfcCartesianPoint(end_relative)
            ])
            
            axis_repr = self.file.createIfcShapeRepresentation(
                self.axis_context, "Axis", "Curve3D", [polyline]
            )
            
            # Create profile
            if is_perimeter:
                profile = self.file.createIfcLShapeProfileDef(
                    ProfileType="AREA",
                    ProfileName="L Beam Profile (Perimeter)",
                    Position=self.file.createIfcAxis2Placement2D(
                        self.file.createIfcCartesianPoint((0., 0.))
                    ),
                    Depth=self.profile_width,
                    Thickness=self.profile_thickness,
                    FilletRadius=0,
                    EdgeRadius=0
                )
            else:
                profile = self.file.createIfcTShapeProfileDef(
                    ProfileType="AREA",
                    ProfileName="T Beam Profile (Interior)",
                    Position=self.file.createIfcAxis2Placement2D(
                        self.file.createIfcCartesianPoint((0., 0.))
                    ),
                    Depth=self.profile_height,
                    FlangeWidth=self.profile_width,
                    WebThickness=self.profile_thickness,
                    FlangeThickness=self.profile_thickness
                )
            
            # Calculate local coordinate system using global direction
            beam_dir = global_direction
            up_ref = (0.0, 0.0, 1.0)
            dot_up = sum(a * b for a, b in zip(up_ref, beam_dir))
            
            if abs(dot_up) > 0.9:
                up_ref = (0.0, 1.0, 0.0)
                dot_up = sum(a * b for a, b in zip(up_ref, beam_dir))
            
            axis_y = tuple(a - dot_up * b for a, b in zip(up_ref, beam_dir))
            axis_y_len = sum(a**2 for a in axis_y) ** 0.5
            
            if axis_y_len < 1e-9:
                axis_y = (0.0, 1.0, 0.0)
                axis_y_len = 1.0
            
            axis_y = tuple(a / axis_y_len for a in axis_y)
            
            axis_x = (
                axis_y[1] * beam_dir[2] - axis_y[2] * beam_dir[1],
                axis_y[2] * beam_dir[0] - axis_y[0] * beam_dir[2],
                axis_y[0] * beam_dir[1] - axis_y[1] * beam_dir[0]
            )
            
            if is_perimeter:
                extrude_placement = self.file.createIfcAxis2Placement3D(
                    self.file.createIfcCartesianPoint([(self.profile_width/2), 0.0, self.profile_thickness]),
                    self.file.createIfcDirection(beam_dir),
                    self.file.createIfcDirection((axis_y[0], axis_y[1], axis_y[2]))
                )
            else:
                extrude_placement = self.file.createIfcAxis2Placement3D(
                    self.file.createIfcCartesianPoint([0.0, 0.0, 0.0]),
                    self.file.createIfcDirection(beam_dir),
                    self.file.createIfcDirection((-axis_x[0], -axis_x[1], -axis_x[2]))
                )
            
            extruded_solid = self.file.createIfcExtrudedAreaSolid(
                profile,
                extrude_placement,
                self.file.createIfcDirection((0.0, 0.0, 1.0)),
                length
            )
            
            body_repr = self.file.createIfcShapeRepresentation(
                self.body_context, "Body", "SweptSolid", [extruded_solid]
            )
            
            # Apply style
            if self.black_style:
                try:
                    ifcopenshell.api.style.assign_representation_styles(
                        self.file, shape_representation=body_repr, styles=[self.black_style]
                    )
                except Exception as e:
                    self.logger.debug(f"Could not assign style: {str(e)}")
            
            # Assign representations
            ifcopenshell.api.geometry.assign_representation(self.file, product=beam, representation=axis_repr)
            ifcopenshell.api.geometry.assign_representation(self.file, product=beam, representation=body_repr)
            
            # Add QTO properties
            try:
                qto_propset = ifcopenshell.api.pset.add_pset(self.file, product=beam, name="Qto_BeamBaseQuantities")
                ifcopenshell.api.pset.edit_pset(self.file, pset=qto_propset, properties={"Length": length})
                ifcopenshell.api.pset.edit_pset(self.file, pset=qto_propset, properties={"Height": self.profile_height})
                ifcopenshell.api.pset.edit_pset(self.file, pset=qto_propset, properties={"Width": self.profile_width})
            except Exception as e:
                self.logger.debug(f"Could not add QTO properties: {str(e)}")
            
            # Assign to spatial container (BuildingStorey/Building) instead of nesting
            if self.spatial_container:
                try:
                    ifcopenshell.api.spatial.assign_container(
                        self.file, products=[beam], relating_structure=self.spatial_container
                    )
                except Exception as e:
                    self.logger.debug(f"Could not assign spatial container: {str(e)}")
            
            return beam
            
        except Exception as e:
            self.logger.error(f"Error creating beam: {str(e)}", exc_info=True)
            raise
    
    def _extract_beams_to_file(self) -> None:
        """Extract only beams to a separate IFC file"""
        try:
            # Determine output path
            if not self.output_path:
                # Auto-generate path based on input file
                self.output_path = "extracted_beams.ifc"
            
            self.logger.info(f"Extracting beams to: {self.output_path}")
            
            source_file = self.file
            new_file = ifcopenshell.file(schema=source_file.wrapped_data.schema)
            
            # Track for relationship recreation
            contained_ins = {}
            aggregates = {}
            reuse_identities = {}
            owner_history = None
            
            # Copy owner history first
            for oh in source_file.by_type("IfcOwnerHistory"):
                owner_history = new_file.add(oh)
                break
            
            def append_asset(entity):
                """Copy entity using append_asset API"""
                try:
                    return new_file.by_guid(entity.GlobalId)
                except:
                    pass
                
                if entity.is_a("IfcProject"):
                    return new_file.add(entity)
                
                return ifcopenshell.api.run(
                    "project.append_asset", 
                    new_file, 
                    library=source_file, 
                    element=entity, 
                    reuse_identities=reuse_identities
                )
            
            def add_spatial_structures(element, new_element):
                """Track spatial container relationships"""
                for rel in getattr(element, "ContainedInStructure", []):
                    spatial_element = rel.RelatingStructure
                    new_spatial_element = append_asset(spatial_element)
                    contained_ins.setdefault(spatial_element.GlobalId, set()).add(new_element)
                    add_decomposition_parents(spatial_element, new_spatial_element)
            
            def add_decomposition_parents(element, new_element):
                """Track decomposition hierarchy"""
                for rel in element.Decomposes:
                    parent = rel.RelatingObject
                    new_parent = append_asset(parent)
                    aggregates.setdefault(parent.GlobalId, set()).add(new_element)
                    add_decomposition_parents(parent, new_parent)
                    add_spatial_structures(parent, new_parent)
            
            # Copy project
            project = source_file.by_type("IfcProject")[0]
            append_asset(project)
            
            # Filter to only get ceiling grid beams we created (not pre-existing beams)
            # Use selector syntax to match beams with names starting with "Ceiling_Profile_"
            beams = ifcopenshell.util.selector.filter_elements(
                source_file, 
                'IfcBeam, Name=/Ceiling_Profile_.*/'
            )
            
            self.logger.info(f"Extracting {len(beams)} ceiling grid beams...")
            
            for beam in beams:
                new_beam = append_asset(beam)
                if new_beam:
                    add_spatial_structures(beam, new_beam)
                    add_decomposition_parents(beam, new_beam)
            
            # Recreate spatial tree
            for relating_structure_guid, related_elements in contained_ins.items():
                new_file.createIfcRelContainedInSpatialStructure(
                    ifcopenshell.guid.new(),
                    owner_history,
                    None,
                    None,
                    list(related_elements),
                    new_file.by_guid(relating_structure_guid)
                )
            
            # Recreate decomposition hierarchy
            for relating_object_guid, related_objects in aggregates.items():
                new_file.createIfcRelAggregates(
                    ifcopenshell.guid.new(),
                    owner_history,
                    None,
                    None,
                    new_file.by_guid(relating_object_guid),
                    list(related_objects)
                )
            
            # Write output file
            new_file.write(self.output_path)
            self.logger.info(f"Successfully extracted beams to: {self.output_path}")
            
            # Replace self.file with the extracted beams file so get_output() returns it
            self.file = new_file
            self.logger.info("Output will now contain only extracted beams (not full model)")
            
        except Exception as e:
            self.logger.error(f"Error extracting beams to file: {str(e)}", exc_info=True)
    
    def _log_statistics(self) -> None:
        """Log processing statistics."""
        self.logger.info("=" * 60)
        self.logger.info("Processing Statistics (Global Placement):")
        self.logger.info(f"  Covering Elements Processed: {self.stats['covering_elements']}")
        self.logger.info(f"  Total Polyline Segments: {self.stats['total_segments']}")
        self.logger.info(f"  Total Beams Created: {self.stats['total_beams']}")
        self.logger.info(f"    - Perimeter Beams (L-profile): {self.stats['perimeter_beams']}")
        self.logger.info(f"    - Interior Beams (T-profile): {self.stats['interior_beams']}")
        if self.extract_beams:
            self.logger.info(f"  Extracted to: {self.output_path}")
        self.logger.info("=" * 60)
    
    def get_output(self) -> ifcopenshell.file:
        """
        Return the patched IFC file.
        
        Returns:
            The modified IFC file object with generated ceiling grid beams
            positioned in global coordinates
        """
        return self.file



