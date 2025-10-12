"""
CeilingGridsNested Custom Recipe

This recipe creates IFC beams from ceiling element footprints exported from Revit.
It processes IfcCovering elements with FootPrint representations and generates:
- L-profile beams for perimeter segments (ceiling angle profiles)
- T-profile beams for interior segments (ceiling T-runners)

The beams are NESTED within their parent covering elements using local/relative 
coordinate placement. This maintains the logical grouping and hierarchy between 
ceiling coverings and their grid beams.

COORDINATE SYSTEM: Local placement relative to parent IfcCovering
HIERARCHY: Beams nested within parent IfcCovering (dependent on parent)
OUTPUT: Original model + new nested beams

For independent beams in global coordinates, see CeilingGridsGlobal recipe.

Recipe Name: CeilingGridsNested
Description: Generate ceiling grid beams with nested/local placement within parent coverings
Author: IFC Pipeline Team
Date: 2025-10-07
Version: 0.2.0
"""

import logging
import uuid
import ifcopenshell
import ifcopenshell.api.nest
import ifcopenshell.api.root
import ifcopenshell.api.geometry
import ifcopenshell.api.pset
import ifcopenshell.api.style
import ifcopenshell.api.unit
import ifcopenshell.api.context
import ifcopenshell.util.representation
from typing import List, Dict, Any, Set, Tuple

logger = logging.getLogger(__name__)


class Patcher:
    """
    Custom patcher for generating ceiling grid beams from IfcCovering footprints.
    
    This recipe uses NESTED/LOCAL PLACEMENT where beams are positioned relative 
    to their parent IfcCovering elements. Beams maintain dependency on parent 
    elements and will transform with them.
    
    This recipe:
    1. Finds IfcCovering elements with FootPrint curve representations
    2. Extracts polyline segments from footprints
    3. Identifies perimeter vs interior segments using connectivity analysis
    4. Creates IFC beams with appropriate profiles (L for perimeter, T for interior)
    5. Nests beams within their parent covering elements (local placement)
    6. Adds QTO properties to beams
    
    Use Cases:
    - Maintain logical grouping between ceilings and their grids
    - Beams should move/transform with parent ceiling
    - Preserve hierarchical relationships
    
    For independent beams in global coordinates, use CeilingGridsGlobal instead.
    
    Parameters:
        file: The IFC model to patch
        logger: Logger instance for output
        profile_height: Height of T-profile in mm (default: 40.0)
        profile_width: Width of profiles in mm (default: 20.0)
        profile_thickness: Thickness of profiles in mm (default: 5.0)
        tolerance: Connection tolerance in mm (default: 50.0)
    
    Example:
        # Use default dimensions
        patcher = Patcher(ifc_file, logger)
        patcher.patch()
        
        # Custom dimensions
        patcher = Patcher(ifc_file, logger, "50.0", "25.0", "6.0", "5.0")
        patcher.patch()
        output = patcher.get_output()
    """
    
    def __init__(self, file: ifcopenshell.file, logger: logging.Logger,
                 profile_height: str = "40.0",
                 profile_width: str = "20.0",
                 profile_thickness: str = "5.0",
                 tolerance: str = "50.0"):
        """
        Initialize the CeilingGridsNested patcher.
        
        Args:
            file: IFC file to patch
            logger: Logger instance
            profile_height: Height of T-profile in mm (default: "40.0")
            profile_width: Width of profiles in mm (default: "20.0")
            profile_thickness: Thickness of profiles in mm (default: "5.0")
            tolerance: Connection tolerance in mm (default: "50.0")
        """
        self.file = file
        self.logger = logger
        
        # Parse arguments with defaults
        self.profile_height = float(profile_height) if profile_height else 40.0
        self.profile_width = float(profile_width) if profile_width else 20.0
        self.profile_thickness = float(profile_thickness) if profile_thickness else 5.0
        self.tolerance = float(tolerance) if tolerance else 50.0
        
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
        
        # Cache for style and contexts
        self.grid_covering_style = None
        self.body_context = None
        self.axis_context = None
        
        self.logger.info(f"Initialized CeilingGridsNested recipe (Local/Nested Placement):")
        self.logger.info(f"  Profile Height: {self.profile_height}mm")
        self.logger.info(f"  Profile Width: {self.profile_width}mm")
        self.logger.info(f"  Profile Thickness: {self.profile_thickness}mm")
        self.logger.info(f"  Tolerance: {self.tolerance}mm")
    
    def patch(self) -> None:
        """
        Execute the ceiling grid beam generation with nested placement.
        """
        self.logger.info("Starting CeilingGridsNested patch operation")
        
        try:
            # Assign units if not already present
            ifcopenshell.api.unit.assign_unit(self.file)
            
            # Create grid covering style for beams
            self.grid_covering_style = self._create_grid_covering_style()
            
            # Get representation contexts
            self._setup_contexts()
            
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
                if idx % 100 == 0 and idx > 0:
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
            
            # Log statistics
            self._log_statistics()
            
            self.logger.info("CeilingGridsNested patch operation completed successfully")
            
        except Exception as e:
            self.logger.error(f"Error during CeilingGridsNested patch: {str(e)}", exc_info=True)
            raise
    
    def _create_grid_covering_style(self) -> ifcopenshell.entity_instance:
        """Create a grid covering surface style for beams"""
        try:
            presentation_style = ifcopenshell.api.style.add_style(self.file, name="Grid Covering Style")
            
            grey_color = self.file.createIfcColourRgb("Grid Covering", 0.5, 0.5, 0.5)
            
            surface_style = self.file.createIfcSurfaceStyleRendering(
                grey_color,
                0.0,  # Transparency
                None, None, None, None, None, None,
                "NOTDEFINED"
            )
            
            presentation_style.Styles = (surface_style,)
            
            return presentation_style
        except Exception as e:
            self.logger.warning(f"Could not create grid covering style: {str(e)}")
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
        
        # Build connectivity map
        connectivity = {}
        for idx in range(len(all_segments)):
            connectivity[idx] = {'start': [], 'end': []}
        
        # Check each pair for connections
        for i in range(len(all_segments)):
            seg_i = all_segments[i]
            for j in range(i + 1, len(all_segments)):
                seg_j = all_segments[j]
                
                if points_coincident(seg_i['start_point'], seg_j['start_point']):
                    connectivity[i]['start'].append(j)
                    connectivity[j]['start'].append(i)
                elif points_coincident(seg_i['start_point'], seg_j['end_point']):
                    connectivity[i]['start'].append(j)
                    connectivity[j]['end'].append(i)
                elif points_coincident(seg_i['end_point'], seg_j['start_point']):
                    connectivity[i]['end'].append(j)
                    connectivity[j]['start'].append(i)
                elif points_coincident(seg_i['end_point'], seg_j['end_point']):
                    connectivity[i]['end'].append(j)
                    connectivity[j]['end'].append(i)
        
        # Find perimeter segments: those with endpoints having <= 2 connections
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
        
        # Segments are perimeter if at least one endpoint has <= 2 connections
        perimeter_segment_indices = set()
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
            else:
                perimeter_segment_indices.add(idx)
        
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
        """Create an IFC beam with axis and body representations using LOCAL/NESTED placement"""
        try:
            # Create beam element
            beam = ifcopenshell.api.root.create_entity(self.file, ifc_class="IfcBeam")
            beam.ObjectType = "Grid Covering"
            
            # Set name based on type
            if is_perimeter:
                beam.Name = f"Ceiling_Profile_Angle_{segment_id}"
            else:
                beam.Name = f"Ceiling_Profile_T-Runner_{segment_id}"
            
            # Beam properties
            start_point = segment["start_point"]
            direction = segment["direction"]
            length = segment["length"]
            
            # Create LOCAL placement relative to parent covering element
            local_placement = self.file.createIfcLocalPlacement(
                covering_element.ObjectPlacement,  # Parent placement - creates nested relationship
                self.file.createIfcAxis2Placement3D(
                    self.file.createIfcCartesianPoint(start_point),
                    self.file.createIfcDirection((0., 0., 1.)),
                    self.file.createIfcDirection((1., 0., 0.))
                )
            )
            beam.ObjectPlacement = local_placement
            
            # Create axis representation
            start_relative = (0.0, 0.0, 0.0)
            end_relative = (direction[0] * length, direction[1] * length, direction[2] * length)
            
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
            
            # Calculate local coordinate system
            beam_dir = direction
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
                    self.file.createIfcCartesianPoint([0.0, 0.0, (self.profile_width+self.profile_thickness)]),
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
            if self.grid_covering_style:
                try:
                    ifcopenshell.api.style.assign_representation_styles(
                        self.file, shape_representation=body_repr, styles=[self.grid_covering_style]
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
            
            # Nest beam within covering element - creates logical hierarchy
            try:
                ifcopenshell.api.nest.assign_object(self.file, related_objects=[beam], relating_object=covering_element)
            except Exception as e:
                self.logger.debug(f"Could not nest beam: {str(e)}")
            
            return beam
            
        except Exception as e:
            self.logger.error(f"Error creating beam: {str(e)}", exc_info=True)
            raise
    
    def _log_statistics(self) -> None:
        """Log processing statistics."""
        self.logger.info("=" * 60)
        self.logger.info("Processing Statistics (Nested Placement):")
        self.logger.info(f"  Covering Elements Processed: {self.stats['covering_elements']}")
        self.logger.info(f"  Total Polyline Segments: {self.stats['total_segments']}")
        self.logger.info(f"  Total Beams Created: {self.stats['total_beams']}")
        self.logger.info(f"    - Perimeter Beams (L-profile): {self.stats['perimeter_beams']}")
        self.logger.info(f"    - Interior Beams (T-profile): {self.stats['interior_beams']}")
        self.logger.info("=" * 60)
    
    def get_output(self) -> ifcopenshell.file:
        """
        Return the patched IFC file.
        
        Returns:
            The modified IFC file object with generated ceiling grid beams
            nested within their parent IfcCovering elements
        """
        return self.file





