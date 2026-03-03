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
Author: Jonatan Jacobsson
Date: 2025-10-07
Version: 0.3.0
"""

import logging
import time
import ifcopenshell
import ifcopenshell.api.nest
import ifcopenshell.api.root
import ifcopenshell.api.geometry
import ifcopenshell.api.pset
import ifcopenshell.api.style
import ifcopenshell.api.unit
import ifcopenshell.api.context
import ifcopenshell.util.representation
import ifcopenshell.guid
from collections import defaultdict
from typing import List, Dict, Any, Set, Tuple, Optional

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
        self.file = file
        self.logger = logger
        
        self.profile_height = float(profile_height) if profile_height else 40.0
        self.profile_width = float(profile_width) if profile_width else 20.0
        self.profile_thickness = float(profile_thickness) if profile_thickness else 5.0
        self.tolerance = float(tolerance) if tolerance else 50.0
        
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
        
        self._dir_z = None
        self._dir_x = None
        self._origin_3d = None
        self._l_profile = None
        self._t_profile = None
        self._style_wrapper = None
        self._perimeter_offset_pt = None
        self._interior_offset_pt = None
        
        self.logger.info(
            f"CeilingGridsNested: h={self.profile_height} w={self.profile_width} "
            f"t={self.profile_thickness} tol={self.tolerance}"
        )
    
    def patch(self) -> None:
        """Execute the ceiling grid beam generation with nested placement."""
        t_start = time.time()
        
        try:
            ifcopenshell.api.unit.assign_unit(self.file)
            self.grid_covering_style = self._create_grid_covering_style()
            self._setup_contexts()
            self._setup_shared_entities()
            
            covering_elements = self.file.by_type("IfcCovering")
            if not covering_elements:
                self.logger.warning("No IfcCovering elements found")
                return
            
            # Extract beam geometry
            all_beam_data = []
            for elem_index, elem in enumerate(covering_elements):
                beam_data, segments = self._extract_beam_data_from_covering(elem_index, elem)
                all_beam_data.extend(beam_data)
                self.stats["total_segments"] += segments
                if beam_data:
                    self.stats["covering_elements"] += 1
            
            # Create beams and collect per covering for batch nesting
            covering_lookup = {elem.id(): elem for elem in covering_elements}
            covering_beams: Dict[int, List[ifcopenshell.entity_instance]] = defaultdict(list)
            
            for beam_info in all_beam_data:
                covering_element = covering_lookup[beam_info['covering_element_id']]
                beam = self._create_beam_at_segment(
                    covering_element,
                    beam_info['segment'],
                    beam_info['segment_id'],
                    beam_info['segment'].get('is_perimeter', False)
                )
                covering_beams[beam_info['covering_element_id']].append(beam)
                
                if beam_info['segment'].get('is_perimeter', False):
                    self.stats["perimeter_beams"] += 1
                else:
                    self.stats["interior_beams"] += 1
                self.stats["total_beams"] += 1
            
            # Batch nesting (one IfcRelNests per covering)
            owner_history = None
            ohs = self.file.by_type("IfcOwnerHistory")
            if ohs:
                owner_history = ohs[0]
            
            for cov_id, beams in covering_beams.items():
                self.file.createIfcRelNests(
                    ifcopenshell.guid.new(), owner_history,
                    None, None, covering_lookup[cov_id], beams
                )
            
            self._log_statistics(time.time() - t_start)
            
        except Exception as e:
            self.logger.error(f"Error during CeilingGridsNested patch: {str(e)}", exc_info=True)
            raise
    
    # ------------------------------------------------------------------ #
    #  Shared entity setup (eliminates ~50K duplicate entity creations)   #
    # ------------------------------------------------------------------ #
    
    def _setup_shared_entities(self) -> None:
        """Create shared IFC entities that are reused across all beams."""
        f = self.file
        
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
    #  Style and context setup                                           #
    # ------------------------------------------------------------------ #
    
    def _create_grid_covering_style(self) -> Optional[ifcopenshell.entity_instance]:
        """Create a grid covering surface style for beams."""
        try:
            style = ifcopenshell.api.style.add_style(self.file, name="Grid Covering Style")
            grey = self.file.createIfcColourRgb("Grid Covering", 0.5, 0.5, 0.5)
            rendering = self.file.createIfcSurfaceStyleRendering(
                grey, 0.0, None, None, None, None, None, None, "NOTDEFINED"
            )
            style.Styles = (rendering,)
            return style
        except Exception as e:
            self.logger.warning(f"Could not create grid covering style: {str(e)}")
            return None
    
    def _setup_contexts(self) -> None:
        """Setup geometric representation contexts."""
        f = self.file
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
    #  Footprint extraction and segment processing                       #
    # ------------------------------------------------------------------ #
    
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
        
        # Spatial index: grid cell -> [(segment_idx, endpoint_key, point)]
        grid = defaultdict(list)
        for idx, seg in enumerate(all_segments):
            for ep_key in ('start_point', 'end_point'):
                p = seg[ep_key]
                grid[_cell(p)].append((idx, p))
        
        # Build endpoint groups using spatial hash:
        # Each unique spatial location gets a list of (segment_idx) that touch it.
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
        
        # Degree-based classification: perimeter if at least one endpoint <= 2 connections
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
    #  Beam data extraction                                              #
    # ------------------------------------------------------------------ #
    
    def _extract_beam_data_from_covering(self, elem_index: int,
                                        elem: ifcopenshell.entity_instance
                                        ) -> Tuple[List[Dict[str, Any]], int]:
        """Extract beam data from a covering element."""
        try:
            curves = self._extract_footprint_curves(elem)
            beam_data = []
            all_segments = []
            
            for curve_index, curve in enumerate(curves):
                segments = self._process_polyline_to_segments(curve, curve_index)
                all_segments.extend(segments)
            
            if not all_segments:
                return [], 0
            
            perimeter_indices = self._find_closed_loop_segments(all_segments)
            
            for idx, segment in enumerate(all_segments):
                segment['is_perimeter'] = idx in perimeter_indices
                beam_data.append({
                    'segment': segment,
                    'segment_id': f"{elem_index}_{segment['polyline_index']}_{segment['segment_index']}",
                    'covering_element_id': elem.id()
                })
            
            return beam_data, len(all_segments)
            
        except Exception as e:
            self.logger.debug(f"Error processing covering element {elem_index}: {str(e)}")
            return [], 0
    
    # ------------------------------------------------------------------ #
    #  Optimized beam creation                                           #
    # ------------------------------------------------------------------ #
    
    def _create_beam_at_segment(self, covering_element: ifcopenshell.entity_instance,
                               segment: Dict[str, Any], segment_id: str,
                               is_perimeter: bool) -> ifcopenshell.entity_instance:
        """
        Create an IFC beam using shared entities and direct entity creation,
        with LOCAL/NESTED placement relative to the parent covering.
        """
        f = self.file
        
        beam = ifcopenshell.api.root.create_entity(f, ifc_class="IfcBeam")
        beam.ObjectType = "Grid Covering"
        if is_perimeter:
            beam.Name = f"Ceiling_Profile_Angle_{segment_id}"
        else:
            beam.Name = f"Ceiling_Profile_T-Runner_{segment_id}"
        
        start_point = segment["start_point"]
        direction = segment["direction"]
        length = segment["length"]
        
        # LOCAL placement relative to parent covering
        beam.ObjectPlacement = f.createIfcLocalPlacement(
            covering_element.ObjectPlacement,
            f.createIfcAxis2Placement3D(
                f.createIfcCartesianPoint(start_point),
                self._dir_z, self._dir_x
            )
        )
        
        # Axis representation using shared origin
        end_relative = (direction[0] * length, direction[1] * length, direction[2] * length)
        polyline = f.createIfcPolyline([
            self._origin_3d,
            f.createIfcCartesianPoint(end_relative)
        ])
        axis_repr = f.createIfcShapeRepresentation(self.axis_context, "Axis", "Curve3D", [polyline])
        
        # Shared profile
        profile = self._l_profile if is_perimeter else self._t_profile
        
        # Extrusion coordinate system (direction-dependent, per beam)
        bd = direction
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
        
        if self._style_wrapper:
            f.createIfcStyledItem(extruded_solid, self._style_wrapper, None)
        
        beam.Representation = f.createIfcProductDefinitionShape(None, None, (axis_repr, body_repr))
        
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
            f"CeilingGridsNested done in {elapsed:.1f}s: "
            f"{s['covering_elements']} coverings, "
            f"{s['total_beams']} beams "
            f"({s['perimeter_beams']} perimeter, {s['interior_beams']} interior)"
        )
    
    def get_output(self) -> ifcopenshell.file:
        """
        Return the patched IFC file.
        
        Returns:
            The modified IFC file object with generated ceiling grid beams
            nested within their parent IfcCovering elements
        """
        return self.file
