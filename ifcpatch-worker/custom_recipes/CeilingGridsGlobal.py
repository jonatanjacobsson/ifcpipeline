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
Author: Jonatan Jacobsson
Date: 2025-01-01
Version: 0.3.0
"""

import logging
import time
import numpy as np
import ifcopenshell
import ifcopenshell.api.root
import ifcopenshell.api.pset
import ifcopenshell.api.style
import ifcopenshell.api.unit
import ifcopenshell.api.context
import ifcopenshell.api.spatial
import ifcopenshell.util.representation
import ifcopenshell.util.placement
import ifcopenshell.guid
from collections import defaultdict
from typing import List, Dict, Any, Set, Tuple, Optional

logger = logging.getLogger(__name__)


class Patcher:
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
    7. Optionally outputs beams to a separate lightweight IFC file
    
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
        self.file = file
        self.logger = logger
        self.target_file = None
        
        self.extract_beams = extract_beams.lower() == "true" if extract_beams else False
        self.profile_height = float(profile_height) if profile_height else 40.0
        self.profile_width = float(profile_width) if profile_width else 20.0
        self.profile_thickness = float(profile_thickness) if profile_thickness else 5.0
        self.tolerance = float(tolerance) if tolerance else 50.0
        self.output_path = output_path if output_path else None
        
        if self.profile_height <= 0:
            raise ValueError(f"profile_height must be positive, got {self.profile_height}")
        if self.profile_width <= 0:
            raise ValueError(f"profile_width must be positive, got {self.profile_width}")
        if self.profile_thickness <= 0:
            raise ValueError(f"profile_thickness must be positive, got {self.profile_thickness}")
        if self.tolerance < 0:
            raise ValueError(f"tolerance must be non-negative, got {self.tolerance}")
        
        self.stats = {
            "covering_elements": 0,
            "total_segments": 0,
            "perimeter_beams": 0,
            "interior_beams": 0,
            "total_beams": 0
        }
        
        self.grid_covering_style = None
        self.body_context = None
        self.axis_context = None
        self.spatial_container = None
        
        self._dir_z = None
        self._dir_x = None
        self._origin_3d = None
        self._l_profile = None
        self._t_profile = None
        self._style_wrapper = None
        self._perimeter_offset_pt = None
        self._interior_offset_pt = None
        
        self.logger.info(
            f"CeilingGridsGlobal: h={self.profile_height} w={self.profile_width} "
            f"t={self.profile_thickness} tol={self.tolerance} extract={self.extract_beams}"
        )
    
    def patch(self) -> None:
        """Execute the ceiling grid beam generation with global placement."""
        t_start = time.time()
        
        try:
            unit_scale = self._get_project_unit_scale()
            if unit_scale != 1.0:
                fu = 1.0 / unit_scale
                self.logger.info(
                    f"Project length unit scale={unit_scale}, "
                    f"converting mm dimensions to file units (factor={fu})"
                )
                self.profile_height *= fu
                self.profile_width *= fu
                self.profile_thickness *= fu
                self.tolerance *= fu
            
            covering_elements = self.file.by_type("IfcCovering")
            if not covering_elements:
                self.logger.warning("No IfcCovering elements found")
                return
            
            # Extract beam geometry from source (read-only pass)
            all_beam_data = []
            transform_cache = {}
            
            for elem_index, elem in enumerate(covering_elements):
                beam_data, segments = self._extract_beam_data_from_covering(
                    elem_index, elem, transform_cache
                )
                all_beam_data.extend(beam_data)
                self.stats["total_segments"] += segments
                if beam_data:
                    self.stats["covering_elements"] += 1
            
            # Prepare target file
            if self.extract_beams:
                self.target_file = self._create_lightweight_ifc()
            else:
                self.target_file = self.file
                ifcopenshell.api.unit.assign_unit(self.target_file)
            
            self._setup_contexts()
            self.grid_covering_style = self._create_grid_covering_style()
            self._setup_shared_entities()
            self.spatial_container = self._get_spatial_container()
            
            # Create beams in target file
            all_beams = []
            for beam_info in all_beam_data:
                beam = self._create_beam_at_segment(
                    beam_info['covering_transform'],
                    beam_info['segment'],
                    beam_info['segment_id'],
                    beam_info['segment'].get('is_perimeter', False)
                )
                all_beams.append(beam)
                
                if beam_info['segment'].get('is_perimeter', False):
                    self.stats["perimeter_beams"] += 1
                else:
                    self.stats["interior_beams"] += 1
                self.stats["total_beams"] += 1
            
            # Batch spatial assignment
            if self.spatial_container and all_beams:
                owner_history = None
                ohs = self.target_file.by_type("IfcOwnerHistory")
                if ohs:
                    owner_history = ohs[0]
                self.target_file.createIfcRelContainedInSpatialStructure(
                    ifcopenshell.guid.new(), owner_history,
                    None, None, all_beams, self.spatial_container
                )
            
            if self.extract_beams:
                if not self.output_path:
                    self.output_path = "extracted_beams.ifc"
                self.file = self.target_file
            
            self._log_statistics(time.time() - t_start)
            
        except Exception as e:
            self.logger.error(f"Error during CeilingGridsGlobal patch: {str(e)}", exc_info=True)
            raise
    
    # ------------------------------------------------------------------ #
    #  Lightweight IFC creation (replaces append_asset extraction)        #
    # ------------------------------------------------------------------ #
    
    def _create_lightweight_ifc(self) -> ifcopenshell.file:
        """
        Create a new lightweight IFC file with minimal project structure,
        copying units and owner history from the source to preserve
        coordinate system and enable API entity creation.
        """
        source = self.file
        new_file = ifcopenshell.file(schema=source.wrapped_data.schema)
        
        # Copy OwnerHistory (and its Person/Org/App references) from source
        # so that api.root.create_entity can find existing owner info.
        source_ohs = source.by_type("IfcOwnerHistory")
        if source_ohs:
            new_file.add(source_ohs[0])
        
        project = ifcopenshell.api.root.create_entity(new_file, ifc_class="IfcProject")
        source_projects = source.by_type("IfcProject")
        if source_projects:
            project.Name = getattr(source_projects[0], 'Name', None)
            src_units = source_projects[0].UnitsInContext
            if src_units:
                project.UnitsInContext = new_file.add(src_units)
        
        if not project.UnitsInContext:
            ifcopenshell.api.unit.assign_unit(new_file)
        
        owner_history = None
        ohs = new_file.by_type("IfcOwnerHistory")
        if ohs:
            owner_history = ohs[0]
        
        site = ifcopenshell.api.root.create_entity(new_file, ifc_class="IfcSite")
        new_file.createIfcRelAggregates(
            ifcopenshell.guid.new(), owner_history, None, None, project, [site]
        )
        
        building = ifcopenshell.api.root.create_entity(new_file, ifc_class="IfcBuilding")
        source_buildings = source.by_type("IfcBuilding")
        if source_buildings:
            building.Name = getattr(source_buildings[0], 'Name', None)
        new_file.createIfcRelAggregates(
            ifcopenshell.guid.new(), owner_history, None, None, site, [building]
        )
        
        storey = ifcopenshell.api.root.create_entity(new_file, ifc_class="IfcBuildingStorey")
        source_storeys = source.by_type("IfcBuildingStorey")
        if source_storeys:
            storey.Name = getattr(source_storeys[0], 'Name', None)
        new_file.createIfcRelAggregates(
            ifcopenshell.guid.new(), owner_history, None, None, building, [storey]
        )
        
        return new_file
    
    # ------------------------------------------------------------------ #
    #  Shared entity setup (eliminates ~50K duplicate entity creations)   #
    # ------------------------------------------------------------------ #
    
    def _setup_shared_entities(self) -> None:
        """Create shared IFC entities that are reused across all beams."""
        f = self.target_file
        
        self._dir_z = f.createIfcDirection((0., 0., 1.))
        self._dir_x = f.createIfcDirection((1., 0., 0.))
        self._origin_3d = f.createIfcCartesianPoint((0., 0., 0.))
        origin_2d = f.createIfcCartesianPoint((0., 0.))
        
        profile_placement = f.createIfcAxis2Placement2D(origin_2d)
        
        self._l_profile = f.createIfcLShapeProfileDef(
            ProfileType="AREA",
            ProfileName="L Beam Profile (Perimeter)",
            Position=profile_placement,
            Depth=self.profile_width,
            Width=self.profile_width,
            Thickness=self.profile_thickness,
            FilletRadius=0,
            EdgeRadius=0
        )
        self._t_profile = f.createIfcTShapeProfileDef(
            ProfileType="AREA",
            ProfileName="T Beam Profile (Interior)",
            Position=profile_placement,
            Depth=self.profile_height,
            FlangeWidth=self.profile_width,
            WebThickness=self.profile_thickness,
            FlangeThickness=self.profile_thickness
        )
        
        self._perimeter_offset_pt = f.createIfcCartesianPoint(
            (self.profile_width / 2, 0.0, self.profile_thickness)
        )
        self._interior_offset_pt = f.createIfcCartesianPoint(
            (0.0, 0.0, self.profile_width + self.profile_thickness)
        )
        
        if self.grid_covering_style:
            schema = f.schema
            if 'IFC2X3' in schema:
                assignment = f.createIfcPresentationStyleAssignment(
                    (self.grid_covering_style,)
                )
                self._style_wrapper = (assignment,)
            else:
                self._style_wrapper = (self.grid_covering_style,)
    
    # ------------------------------------------------------------------ #
    #  Source model helpers (read-only)                                   #
    # ------------------------------------------------------------------ #
    
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
                                        return 1.0
                                    elif unit.Prefix == 'CENTI':
                                        return 10.0
                                return 1000.0
            return 1000.0
        except Exception as e:
            self.logger.warning(f"Could not determine project units, assuming meters: {str(e)}")
            return 1000.0
    
    def _get_global_placement_matrix(self, element: ifcopenshell.entity_instance) -> np.ndarray:
        """Get the global transformation matrix for an element's placement."""
        try:
            return ifcopenshell.util.placement.get_local_placement(element.ObjectPlacement)
        except Exception as e:
            self.logger.warning(f"Could not get placement matrix, using identity: {str(e)}")
            return np.eye(4)
    
    def _transform_point(self, point: Tuple[float, float, float], 
                        transform_matrix: np.ndarray) -> List[float]:
        """Transform a 3D point using a 4x4 transformation matrix."""
        point_h = np.array([point[0], point[1], point[2], 1.0])
        transformed = np.dot(transform_matrix, point_h)
        return [float(transformed[0]), float(transformed[1]), float(transformed[2])]
    
    def _transform_direction(self, direction: Tuple[float, float, float],
                            transform_matrix: np.ndarray) -> Tuple[float, float, float]:
        """Transform a direction vector (rotation only, no translation)."""
        dir_4d = np.array([direction[0], direction[1], direction[2], 0.0])
        global_dir_4d = np.dot(transform_matrix, dir_4d)
        gd = (float(global_dir_4d[0]), float(global_dir_4d[1]), float(global_dir_4d[2]))
        
        length = (gd[0]**2 + gd[1]**2 + gd[2]**2) ** 0.5
        if length > 0:
            gd = (gd[0] / length, gd[1] / length, gd[2] / length)
        return gd
    
    def _extract_footprint_curves(self, elem: ifcopenshell.entity_instance) -> List[ifcopenshell.entity_instance]:
        """Extract FootPrint curves from element."""
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
        """Process an IfcPolyline into ceiling grid segments."""
        segments = []
        try:
            points = polyline.Points
            if not points or len(points) < 2:
                return segments
            
            for i in range(len(points) - 1):
                start_coords = points[i].Coordinates
                end_coords = points[i + 1].Coordinates
                
                if start_coords and end_coords:
                    sz = start_coords[2] if len(start_coords) > 2 else 0.0
                    ez = end_coords[2] if len(end_coords) > 2 else 0.0
                    s = (start_coords[0], start_coords[1], sz)
                    e = (end_coords[0], end_coords[1], ez)
                    
                    dx = e[0] - s[0]
                    dy = e[1] - s[1]
                    h_len = (dx**2 + dy**2)**0.5
                    
                    if h_len > 0.001:
                        segments.append({
                            "polyline_index": polyline_index,
                            "segment_index": i,
                            "start_point": s,
                            "end_point": e,
                            "direction": (dx / h_len, dy / h_len, 0.0),
                            "length": h_len,
                            "midpoint": ((s[0]+e[0])/2, (s[1]+e[1])/2, (s[2]+e[2])/2)
                        })
        except Exception as e:
            self.logger.debug(f"Error processing polyline {polyline_index}: {str(e)}")
        return segments
    
    # ------------------------------------------------------------------ #
    #  Perimeter detection with spatial hash (degree-based classification)#
    # ------------------------------------------------------------------ #
    
    def _find_closed_loop_segments(self, all_segments: List[Dict[str, Any]]) -> Set[int]:
        """
        Find perimeter segments using spatial hashing for fast connectivity,
        then degree-based classification: perimeter if at least one endpoint
        has <= 2 connections, interior if both endpoints have 3+ connections.
        """
        if not all_segments:
            return set()
        
        tol = self.tolerance
        tol_sq = tol * tol
        cell_size = tol if tol > 0 else 1.0
        
        def _cell(p):
            return (int(p[0] // cell_size), int(p[1] // cell_size), int(p[2] // cell_size))
        
        grid = defaultdict(list)
        for idx, seg in enumerate(all_segments):
            for ep_key in ('start_point', 'end_point'):
                p = seg[ep_key]
                grid[_cell(p)].append((idx, p))
        
        endpoint_groups: List[List[int]] = []
        point_to_group: Dict[Tuple[int, str], int] = {}
        
        for idx, seg in enumerate(all_segments):
            for ep_key in ('start_point', 'end_point'):
                key = (idx, ep_key)
                if key in point_to_group:
                    continue
                
                p = seg[ep_key]
                cx, cy, cz = _cell(p)
                group_segments = set()
                
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        for dz in (-1, 0, 1):
                            for o_idx, o_p in grid.get((cx+dx, cy+dy, cz+dz), ()):
                                d_sq = (p[0]-o_p[0])**2 + (p[1]-o_p[1])**2 + (p[2]-o_p[2])**2
                                if d_sq < tol_sq:
                                    group_segments.add(o_idx)
                
                gid = len(endpoint_groups)
                endpoint_groups.append(sorted(group_segments))
                
                for s_idx in group_segments:
                    s_seg = all_segments[s_idx]
                    for s_ep in ('start_point', 'end_point'):
                        sp = s_seg[s_ep]
                        d_sq = (p[0]-sp[0])**2 + (p[1]-sp[1])**2 + (p[2]-sp[2])**2
                        if d_sq < tol_sq:
                            point_to_group[(s_idx, s_ep)] = gid
        
        perimeter_indices = set()
        for idx in range(len(all_segments)):
            start_gid = point_to_group.get((idx, 'start_point'))
            end_gid = point_to_group.get((idx, 'end_point'))
            
            start_conns = len(endpoint_groups[start_gid]) if start_gid is not None else 0
            end_conns = len(endpoint_groups[end_gid]) if end_gid is not None else 0
            
            if start_conns < 3 or end_conns < 3:
                perimeter_indices.add(idx)
        
        return perimeter_indices
    
    # ------------------------------------------------------------------ #
    #  Beam data extraction (with transform caching)                     #
    # ------------------------------------------------------------------ #
    
    def _extract_beam_data_from_covering(self, elem_index: int, 
                                        elem: ifcopenshell.entity_instance,
                                        transform_cache: Dict[int, np.ndarray]
                                        ) -> Tuple[List[Dict[str, Any]], int]:
        """Extract beam data from a covering element, caching transforms."""
        try:
            curves = self._extract_footprint_curves(elem)
            beam_data = []
            all_segments = []
            
            for curve_index, curve in enumerate(curves):
                segments = self._process_polyline_to_segments(curve, curve_index)
                all_segments.extend(segments)
            
            if not all_segments:
                return [], 0
            
            elem_id = elem.id()
            if elem_id not in transform_cache:
                transform_cache[elem_id] = self._get_global_placement_matrix(elem)
            covering_transform = transform_cache[elem_id]
            
            perimeter_indices = self._find_closed_loop_segments(all_segments)
            
            for idx, segment in enumerate(all_segments):
                segment['is_perimeter'] = idx in perimeter_indices
                beam_data.append({
                    'segment': segment,
                    'segment_id': f"{elem_index}_{segment['polyline_index']}_{segment['segment_index']}",
                    'covering_transform': covering_transform
                })
            
            return beam_data, len(all_segments)
            
        except Exception as e:
            self.logger.debug(f"Error processing covering element {elem_index}: {str(e)}")
            return [], 0
    
    # ------------------------------------------------------------------ #
    #  Target file setup helpers                                         #
    # ------------------------------------------------------------------ #
    
    def _get_spatial_container(self) -> Optional[ifcopenshell.entity_instance]:
        """Get the appropriate spatial container for beams from the target file."""
        storeys = self.target_file.by_type("IfcBuildingStorey")
        if storeys:
            return storeys[0]
        buildings = self.target_file.by_type("IfcBuilding")
        if buildings:
            return buildings[0]
        return None
    
    def _create_grid_covering_style(self) -> Optional[ifcopenshell.entity_instance]:
        """Create a grid covering surface style in the target file."""
        try:
            f = self.target_file
            style = ifcopenshell.api.style.add_style(f, name="Grid Covering Style")
            grey = f.createIfcColourRgb("Grid Covering", 0.5, 0.5, 0.5)
            rendering = f.createIfcSurfaceStyleRendering(
                grey, 0.0, None, None, None, None, None, None, "NOTDEFINED"
            )
            style.Styles = (rendering,)
            return style
        except Exception as e:
            self.logger.warning(f"Could not create grid covering style: {str(e)}")
            return None
    
    def _setup_contexts(self) -> None:
        """Setup geometric representation contexts in the target file."""
        f = self.target_file
        root_contexts = [c for c in f.by_type("IfcGeometricRepresentationContext")
                         if not c.is_a("IfcGeometricRepresentationSubContext")]
        
        if root_contexts:
            model_context = root_contexts[0]
        else:
            model_context = ifcopenshell.api.context.add_context(f, context_type="Model")
        
        self.body_context = ifcopenshell.util.representation.get_context(
            f, "Model", "Body", "MODEL_VIEW"
        )
        if not self.body_context:
            self.body_context = ifcopenshell.api.context.add_context(
                f, context_type="Model", context_identifier="Body",
                target_view="MODEL_VIEW", parent=model_context
            )
        
        self.axis_context = ifcopenshell.util.representation.get_context(
            f, "Model", "Axis", "GRAPH_VIEW"
        )
        if not self.axis_context:
            self.axis_context = ifcopenshell.api.context.add_context(
                f, context_type="Model", context_identifier="Axis",
                target_view="GRAPH_VIEW", parent=model_context
            )
    
    # ------------------------------------------------------------------ #
    #  Optimized beam creation                                           #
    # ------------------------------------------------------------------ #
    
    def _create_beam_at_segment(self, covering_transform: np.ndarray,
                               segment: Dict[str, Any], segment_id: str,
                               is_perimeter: bool) -> ifcopenshell.entity_instance:
        """
        Create an IFC beam using shared entities, pre-computed transform,
        and direct entity creation (minimizing API call overhead).
        """
        f = self.target_file
        
        beam = ifcopenshell.api.root.create_entity(f, ifc_class="IfcBeam")
        beam.ObjectType = "Grid Covering"
        if is_perimeter:
            beam.Name = f"Ceiling_Profile_Angle_{segment_id}"
        else:
            beam.Name = f"Ceiling_Profile_T-Runner_{segment_id}"
        
        start_point = segment["start_point"]
        direction = segment["direction"]
        length = segment["length"]
        
        global_start = self._transform_point(start_point, covering_transform)
        global_dir = self._transform_direction(direction, covering_transform)
        
        # Placement using shared direction entities
        beam.ObjectPlacement = f.createIfcLocalPlacement(
            None,
            f.createIfcAxis2Placement3D(
                f.createIfcCartesianPoint(global_start),
                self._dir_z, self._dir_x
            )
        )
        
        # Axis representation using shared origin point
        end_relative = (global_dir[0] * length, global_dir[1] * length, global_dir[2] * length)
        polyline = f.createIfcPolyline([
            self._origin_3d,
            f.createIfcCartesianPoint(end_relative)
        ])
        axis_repr = f.createIfcShapeRepresentation(self.axis_context, "Axis", "Curve3D", [polyline])
        
        # Shared profile and shared extrusion direction
        profile = self._l_profile if is_perimeter else self._t_profile
        
        # Extrusion coordinate system (direction-dependent, created per beam)
        bd = global_dir
        up = (0.0, 0.0, 1.0)
        dot = bd[0]*up[0] + bd[1]*up[1] + bd[2]*up[2]
        if abs(dot) > 0.9:
            up = (0.0, 1.0, 0.0)
            dot = bd[0]*up[0] + bd[1]*up[1] + bd[2]*up[2]
        
        ay = (up[0] - dot*bd[0], up[1] - dot*bd[1], up[2] - dot*bd[2])
        ay_len = (ay[0]**2 + ay[1]**2 + ay[2]**2) ** 0.5
        if ay_len < 1e-9:
            ay = (0.0, 1.0, 0.0)
            ay_len = 1.0
        ay = (ay[0]/ay_len, ay[1]/ay_len, ay[2]/ay_len)
        
        if is_perimeter:
            extrude_placement = f.createIfcAxis2Placement3D(
                self._perimeter_offset_pt,
                f.createIfcDirection(bd),
                f.createIfcDirection(ay)
            )
        else:
            ax = (
                ay[1]*bd[2] - ay[2]*bd[1],
                ay[2]*bd[0] - ay[0]*bd[2],
                ay[0]*bd[1] - ay[1]*bd[0]
            )
            extrude_placement = f.createIfcAxis2Placement3D(
                self._interior_offset_pt,
                f.createIfcDirection(bd),
                f.createIfcDirection((-ax[0], -ax[1], -ax[2]))
            )
        
        extruded_solid = f.createIfcExtrudedAreaSolid(
            profile, extrude_placement, self._dir_z, length
        )
        
        body_repr = f.createIfcShapeRepresentation(self.body_context, "Body", "SweptSolid", [extruded_solid])
        
        # Style using pre-created wrapper (avoids per-beam API dispatch)
        if self._style_wrapper:
            f.createIfcStyledItem(extruded_solid, self._style_wrapper, None)
        
        # Single representation assignment (replaces 2 API calls)
        beam.Representation = f.createIfcProductDefinitionShape(None, None, (axis_repr, body_repr))
        
        # Consolidated property set (2 calls instead of 4)
        try:
            qto = ifcopenshell.api.pset.add_pset(f, product=beam, name="Qto_BeamBaseQuantities")
            ifcopenshell.api.pset.edit_pset(f, pset=qto, properties={
                "Length": length,
                "Height": self.profile_height,
                "Width": self.profile_width
            })
        except Exception as e:
            self.logger.debug(f"Could not add QTO properties: {str(e)}")
        
        return beam
    
    # ------------------------------------------------------------------ #
    #  Output                                                            #
    # ------------------------------------------------------------------ #
    
    def _log_statistics(self, elapsed: float) -> None:
        """Log processing statistics."""
        s = self.stats
        self.logger.info(
            f"CeilingGridsGlobal done in {elapsed:.1f}s: "
            f"{s['covering_elements']} coverings, "
            f"{s['total_beams']} beams "
            f"({s['perimeter_beams']} perimeter, {s['interior_beams']} interior)"
            f"{' [extracted]' if self.extract_beams else ''}"
        )
    
    def get_output(self) -> ifcopenshell.file:
        """
        Return the patched IFC file.
        
        Returns:
            The modified IFC file object with generated ceiling grid beams
            positioned in global coordinates
        """
        return self.file
