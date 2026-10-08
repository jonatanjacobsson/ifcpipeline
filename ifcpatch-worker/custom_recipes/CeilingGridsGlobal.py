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
import ifcopenshell.util.selector
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
        query: IfcOpenShell selector for the coverings to process
            (default: "IfcCovering", every covering). Narrow it to a ceiling
            family with e.g. 'IfcCovering, Name=/.*pendlat.*/' - suspended,
            demountable ceilings carry a grid, direct-mounted ("diktmonterad")
            panels do not, and only the type name says which is which.
        extract_beams: Extract beams to separate file (default: "false")
        profile_height: Height of T-profile in mm (default: 40.0)
        profile_width: Width of profiles in mm (default: 20.0)
        profile_thickness: Thickness of profiles in mm (default: 5.0)
        tolerance: Connection tolerance in mm (default: 50.0)
        output_path: Path for extracted beams file (default: auto-generated)
        require_interior: Only build grids for coverings whose FootPrint has
            lines inside the boundary, i.e. a real grid rather than just an
            outline (default: "true"). Pass "false" for the old behaviour of
            ringing every covering with an angle profile.
        interior_z_offset: Vertical nudge for T-runners in mm, positive up
            (default: 0.0). At 0.0 the runner flange is flush with the
            perimeter angle's leg, so both carry the ceiling at one level.
        slim: Draw every grid line as one plain rectangular bar instead of
            L- and T-profiles (default: "false"). The bar is slim_width wide
            by profile_height tall, centred on the footprint line, with its
            underside profile_thickness below the ceiling plane - so the
            visible reveal matches the profiled version. Perimeter and
            interior lines become the same element, which is why the
            boundary-normal alignment is skipped: a centred rectangle is
            symmetric, so there is no inside face to turn.
        slim_width: Width of the slim bar in mm (default: 10.0). With the
            default profile_height of 40.0 this gives the 10x40 bar.
    
    Example:
        # Use default dimensions, no extraction
        patcher = Patcher(ifc_file, logger)
        patcher.patch()
        
        # Suspended ceilings only, custom dimensions, beams extracted
        patcher = Patcher(ifc_file, logger, "IfcCovering, Name=/.*pendlat.*/",
                          "true", "50.0", "25.0", "6.0", "5.0", "/path/to/beams.ifc")
        patcher.patch()
        output = patcher.get_output()
    """
    
    def __init__(self, file: ifcopenshell.file, logger: logging.Logger,
                 query: str = "IfcCovering",
                 extract_beams: str = "false",
                 profile_height: str = "40.0",
                 profile_width: str = "20.0", 
                 profile_thickness: str = "5.0",
                 tolerance: str = "50.0",
                 output_path: str = "",
                 require_interior: str = "true",
                 interior_z_offset: str = "0.0",
                 slim: str = "false",
                 slim_width: str = "10.0"):
        self.file = file
        self.logger = logger
        self.target_file = None
        
        self.extract_beams = extract_beams.lower() == "true" if extract_beams else False
        self.profile_height = float(profile_height) if profile_height else 40.0
        self.profile_width = float(profile_width) if profile_width else 20.0
        self.profile_thickness = float(profile_thickness) if profile_thickness else 5.0
        self.tolerance = float(tolerance) if tolerance else 50.0
        self.output_path = output_path if output_path else None
        self.require_interior = require_interior.lower() != "false" if require_interior else True
        self.query = query.strip() if query and query.strip() else "IfcCovering"
        self.interior_z_offset = float(interior_z_offset) if interior_z_offset else 0.0
        # Three simplification levels:
        #   1 (default) - L/T profile, one IfcBeam per grid line
        #   2           - same profiles, all of a ceiling's lines grouped
        #                 into one element as separate solids
        #   3           - one 2D sketch per ceiling, extruded down
        _sm = (slim or "false").strip().lower()
        if _sm in ("3", "level3", "sketch", "ceiling", "merged"):
            self.slim_mode = "sketch"
        elif _sm in ("2", "level2", "grouped", "profiles"):
            self.slim_mode = "grouped"
        elif _sm in ("bar", "true", "yes"):
            self.slim_mode = "bar"   # legacy: rectangle per line, saves nothing
        else:
            self.slim_mode = None
        # Existing per-segment code branches on the bar variant only.
        self.slim = self.slim_mode == "bar"
        self.slim_width = float(slim_width) if slim_width else 10.0
        
        if self.profile_height <= 0:
            raise ValueError(f"profile_height must be positive, got {self.profile_height}")
        if self.profile_width <= 0:
            raise ValueError(f"profile_width must be positive, got {self.profile_width}")
        if self.profile_thickness <= 0:
            raise ValueError(f"profile_thickness must be positive, got {self.profile_thickness}")
        if self.tolerance < 0:
            raise ValueError(f"tolerance must be non-negative, got {self.tolerance}")
        if self.slim_width <= 0:
            raise ValueError(f"slim_width must be positive, got {self.slim_width}")
        
        self.stats = {
            "sketch_elements": 0,
            "sketch_lines": 0,
            "covering_elements": 0,
            "skipped_no_interior": 0,
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
        self._slim_profile = None
        self._style_wrapper = None
        self._perimeter_offset_pt = None
        self._interior_offset_pt = None
        self._perimeter_offset_cache = {}
        
        self.logger.info(
            f"CeilingGridsGlobal: h={self.profile_height} w={self.profile_width} "
            f"t={self.profile_thickness} tol={self.tolerance} extract={self.extract_beams} "
            f"require_interior={self.require_interior} "
            f"interior_z_offset={self.interior_z_offset}"
            + (f" slim={self.slim_mode}:{self.slim_width}x{self.profile_height}"
               if self.slim_mode else "")
            + (f" query={self.query!r}" if self.query != "IfcCovering" else "")
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
                self.interior_z_offset *= fu
            
            covering_elements = self.file.by_type("IfcCovering")
            if not covering_elements:
                self.logger.warning("No IfcCovering elements found")
                return
            
            if self.query != "IfcCovering":
                selected = ifcopenshell.util.selector.filter_elements(self.file, self.query)
                covering_elements = [e for e in covering_elements if e in selected]
                self.logger.info(
                    f"Selector {self.query!r} kept {len(covering_elements)} coverings"
                )
                if not covering_elements:
                    self.logger.warning("Selector matched no IfcCovering elements")
                    return
            
            # Extract beam geometry from source (read-only pass)
            all_beam_data = []
            transform_cache = {}
            
            for elem_index, elem in enumerate(covering_elements):
                beam_data, segments = self._extract_beam_data_from_covering(
                    elem_index, elem, transform_cache
                )
                if not beam_data:
                    continue
                
                # A ceiling whose footprint is only an outline has no grid to
                # build - ringing it with an angle profile would be noise.
                if self.require_interior and not any(
                    not d['segment']['is_perimeter'] for d in beam_data
                ):
                    self.stats["skipped_no_interior"] += 1
                    continue
                
                all_beam_data.extend(beam_data)
                self.stats["total_segments"] += segments
                self.stats["covering_elements"] += 1
            
            # Prepare target file
            if self.extract_beams:
                self.target_file = self._create_lightweight_ifc()
            else:
                self.target_file = self.file
                # Idempotent: a rerun on an already-patched model (e.g. an n8n
                # retry of the whole processing subflow) must replace the grid,
                # not add a second one on top of it.
                self._remove_existing_grid()
                ifcopenshell.api.unit.assign_unit(self.target_file)
            
            self._setup_contexts()
            self.grid_covering_style = self._create_grid_covering_style()
            self._setup_shared_entities()
            self.spatial_container = self._get_spatial_container()
            
            # Create beams in target file
            all_beams = []
            if self.slim_mode in ("sketch", "grouped"):
                builder = (self._create_grid_sketch if self.slim_mode == "sketch"
                           else self._create_grid_grouped)
                grouped = defaultdict(list)
                for bi in all_beam_data:
                    grouped[bi['elem_index']].append(bi)
                for elem_index, infos in grouped.items():
                    el = builder(elem_index, infos)
                    if el is None:
                        continue
                    all_beams.append(el)
                    self.stats["sketch_elements"] += 1
                    self.stats["total_beams"] += 1
                    self.stats["sketch_lines"] += len(infos)
                all_beam_data = []
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

    def _remove_existing_grid(self) -> None:
        """Remove grid elements a previous run of this recipe left in the model.

        Every level tags its elements IfcBeam / ObjectType "Grid Covering" with
        a "Ceiling_" name, so real beams are never touched. Removal goes through
        RemoveElements' bulk path; per-element remove_product is far too slow
        for tens of thousands of beams.
        """
        old = [
            b for b in self.file.by_type("IfcBeam")
            if b.ObjectType == "Grid Covering" and (b.Name or "").startswith("Ceiling_")
        ]
        if not old:
            return
        import os
        import sys
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        from RemoveElements import Patcher as RemoveElements

        remover = RemoveElements(self.file, self.logger, query="IfcBeam")
        remover._select_elements = lambda: old
        remover.patch()
        self.stats["removed_existing_grid"] = len(old)
        self.logger.warning(
            f"CeilingGridsGlobal: removed {len(old)} grid elements from a previous run "
            "before regenerating"
        )

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
        if self.slim:
            # One plain bar for every line: slim_width x profile_height,
            # centred on its own origin so the same profile serves perimeter
            # and interior without a handed variant.
            self._slim_profile = f.createIfcRectangleProfileDef(
                ProfileType="AREA",
                ProfileName="Slim Grid Bar",
                Position=profile_placement,
                XDim=self.slim_width,
                YDim=self.profile_height
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
        
        # Both profiles are positioned by their bounding-box centre. The angle's
        # leg lands at -profile_thickness below the footprint plane, its top face
        # flush with it, so the ceiling bears on the leg and the underside shows.
        self._perimeter_offset_cache = {}
        self._perimeter_offset_pt = f.createIfcCartesianPoint(
            (self.profile_width / 2, 0.0, self.profile_thickness)
        )
        # Put the T-runner's flange at the same level, so runners carry the
        # ceiling alongside the angle instead of floating above it.
        self._interior_offset_pt = f.createIfcCartesianPoint(
            (0.0, 0.0,
             self.profile_height / 2 - self.profile_thickness + self.interior_z_offset)
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
                        if item.is_a("IfcPolyline") or item.is_a("IfcIndexedPolyCurve"):
                            curves_found.append(item)
        except Exception as e:
            self.logger.debug(f"Error extracting footprint curves: {str(e)}")
        return curves_found
    
    @staticmethod
    def _as_3d(coords: Any) -> Tuple[float, float, float]:
        """Pad a 2D or 3D coordinate tuple to 3D."""
        z = float(coords[2]) if len(coords) > 2 else 0.0
        return (float(coords[0]), float(coords[1]), z)

    def _curve_vertex_runs(self, curve: ifcopenshell.entity_instance) -> List[List[Tuple[float, float, float]]]:
        """
        Return a curve's vertex runs as lists of 3D coordinates.

        Handles both FootPrint flavours Revit/ODA emits: IfcPolyline (explicit
        IfcCartesianPoints) and IfcIndexedPolyCurve (IfcCartesianPointList with
        optional segment indices). Arc segments are reduced to their chord so
        endpoint connectivity stays intact for perimeter detection.
        """
        if curve.is_a("IfcPolyline"):
            points = curve.Points or []
            run = [self._as_3d(p.Coordinates) for p in points if p.Coordinates]
            return [run] if len(run) >= 2 else []

        if curve.is_a("IfcIndexedPolyCurve"):
            point_list = curve.Points
            coord_list = getattr(point_list, "CoordList", None) if point_list else None
            if not coord_list:
                return []
            points = [self._as_3d(c) for c in coord_list]

            segments = getattr(curve, "Segments", None)
            if not segments:
                return [points] if len(points) >= 2 else []

            runs = []
            for seg in segments:
                raw = getattr(seg, "wrappedValue", seg)
                try:
                    indices = [int(i) for i in raw]
                except (TypeError, ValueError):
                    continue
                is_arc = False
                try:
                    is_arc = seg.is_a("IfcArcIndex")
                except AttributeError:
                    is_arc = False
                if is_arc and len(indices) == 3:
                    indices = [indices[0], indices[2]]
                run = [points[i - 1] for i in indices if 1 <= i <= len(points)]
                if len(run) >= 2:
                    runs.append(run)
            return runs

        return []

    def _process_polyline_to_segments(self, curve: ifcopenshell.entity_instance,
                                     polyline_index: int) -> List[Dict[str, Any]]:
        """Process a FootPrint curve into ceiling grid segments."""
        segments = []
        try:
            segment_index = 0
            for run in self._curve_vertex_runs(curve):
                for i in range(len(run) - 1):
                    s = run[i]
                    e = run[i + 1]

                    dx = e[0] - s[0]
                    dy = e[1] - s[1]
                    h_len = (dx**2 + dy**2)**0.5

                    if h_len > 0.001:
                        segments.append({
                            "polyline_index": polyline_index,
                            "segment_index": segment_index,
                            "start_point": s,
                            "end_point": e,
                            "direction": (dx / h_len, dy / h_len, 0.0),
                            "length": h_len,
                            "midpoint": ((s[0]+e[0])/2, (s[1]+e[1])/2, (s[2]+e[2])/2)
                        })
                        segment_index += 1
        except Exception as e:
            self.logger.debug(f"Error processing curve {polyline_index}: {str(e)}")
        return segments
    
    # ------------------------------------------------------------------ #
    #  Perimeter detection with spatial hash (degree-based classification)#
    # ------------------------------------------------------------------ #
    
    def _find_closed_loop_segments(self, all_segments: List[Dict[str, Any]]) -> Set[int]:
        """
        Find perimeter segments using spatial hashing for fast connectivity.

        An endpoint is "constrained" when the grid continues past it: either 3+
        segments share it, or it lands mid-span on another segment (a T-junction).
        A segment is interior (T-runner) when both endpoints are constrained, and
        perimeter (L angle) otherwise. The mid-span test matters because Revit
        FootPrint exports do not split grid lines at intersections - an interior
        runner then just stops against an unbroken boundary line, and endpoint
        counting alone would read it as perimeter.
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
        
        # Segment index by cell, so the mid-span test only visits nearby segments.
        span_grid = defaultdict(set)
        for idx, seg in enumerate(all_segments):
            s, e = seg['start_point'], seg['end_point']
            x0, x1 = sorted((s[0], e[0]))
            y0, y1 = sorted((s[1], e[1]))
            for cx in range(int((x0 - tol) // cell_size), int((x1 + tol) // cell_size) + 1):
                for cy in range(int((y0 - tol) // cell_size), int((y1 + tol) // cell_size) + 1):
                    span_grid[(cx, cy)].add(idx)
        
        def _meets_span(p, own_idx) -> bool:
            """True if p sits on another segment's span, away from its endpoints."""
            cx, cy = int(p[0] // cell_size), int(p[1] // cell_size)
            candidates = set()
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    candidates |= span_grid.get((cx+dx, cy+dy), set())
            
            for o_idx in candidates:
                if o_idx == own_idx:
                    continue
                o = all_segments[o_idx]
                s, e = o['start_point'], o['end_point']
                if abs(p[2] - s[2]) > tol:
                    continue
                dx, dy = e[0] - s[0], e[1] - s[1]
                len_sq = dx*dx + dy*dy
                if len_sq <= 0:
                    continue
                t = ((p[0] - s[0]) * dx + (p[1] - s[1]) * dy) / len_sq
                length = len_sq ** 0.5
                if t * length <= tol or (1.0 - t) * length <= tol:
                    continue  # at (or beyond) an endpoint, not mid-span
                d_sq = (p[0] - (s[0] + t*dx))**2 + (p[1] - (s[1] + t*dy))**2
                if d_sq < tol_sq:
                    return True
            return False
        
        perimeter_indices = set()
        for idx, seg in enumerate(all_segments):
            constrained = []
            for ep_key in ('start_point', 'end_point'):
                gid = point_to_group.get((idx, ep_key))
                conns = len(endpoint_groups[gid]) if gid is not None else 0
                constrained.append(conns >= 3 or _meets_span(seg[ep_key], idx))
            
            if not all(constrained):
                perimeter_indices.add(idx)
        
        return perimeter_indices
    
    @staticmethod
    def _point_inside_boundary(x: float, y: float, boundary: List[Dict[str, Any]]) -> bool:
        """Even-odd test of a point against the covering's perimeter segments."""
        inside = False
        for seg in boundary:
            x0, y0 = seg['start_point'][0], seg['start_point'][1]
            x1, y1 = seg['end_point'][0], seg['end_point'][1]
            if (y0 > y) != (y1 > y):
                if x0 + (y - y0) * (x1 - x0) / (y1 - y0) > x:
                    inside = not inside
        return inside

    def _interior_normal(self, segment: Dict[str, Any],
                         boundary: List[Dict[str, Any]]) -> Tuple[float, float]:
        """
        Unit normal of a perimeter segment pointing into the ceiling.

        The angle profile has to be offset towards the ceiling, not along a fixed
        world axis, or it lands sideways by up to half its width depending on
        which way the segment happens to run. Probing both sides against the
        perimeter loop handles concave footprints and holes; for a segment that
        is not part of a closed loop the footprint's centre is the fallback.
        """
        d = segment['direction']
        n = (-d[1], d[0])
        s0 = segment['start_point']
        length = segment['length']
        step = max(self.profile_width, 1.0)
        
        # Vote from three points along the segment: a notch or a doorway at the
        # midpoint alone would otherwise pick the wrong side for the whole run.
        votes = 0
        for frac in (0.25, 0.5, 0.75):
            px = s0[0] + d[0] * length * frac
            py = s0[1] + d[1] * length * frac
            left = self._point_inside_boundary(px + n[0]*step, py + n[1]*step, boundary)
            right = self._point_inside_boundary(px - n[0]*step, py - n[1]*step, boundary)
            if left != right:
                votes += 1 if left else -1
        if votes:
            return n if votes > 0 else (-n[0], -n[1])
        
        mx, my = segment['midpoint'][0], segment['midpoint'][1]
        cx = sum(s['midpoint'][0] for s in boundary) / len(boundary)
        cy = sum(s['midpoint'][1] for s in boundary) / len(boundary)
        return n if (cx - mx) * n[0] + (cy - my) * n[1] >= 0 else (-n[0], -n[1])

    def _perimeter_offset_point(self, normal: Tuple[float, float, float]
                                ) -> ifcopenshell.entity_instance:
        """Profile origin that puts the angle's back face on the footprint line."""
        half = self.profile_width / 2
        pt = (normal[0] * half, normal[1] * half,
              normal[2] * half + self.profile_thickness)
        key = (round(pt[0], 4), round(pt[1], 4), round(pt[2], 4))
        point = self._perimeter_offset_cache.get(key)
        if point is None:
            point = self.target_file.createIfcCartesianPoint(pt)
            self._perimeter_offset_cache[key] = point
        return point

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
            boundary = [all_segments[i] for i in sorted(perimeter_indices)]
            
            for idx, segment in enumerate(all_segments):
                segment['is_perimeter'] = idx in perimeter_indices
                if segment['is_perimeter']:
                    segment['interior_normal'] = self._interior_normal(segment, boundary)
                beam_data.append({
                    'segment': segment,
                    'segment_id': f"{elem_index}_{segment['polyline_index']}_{segment['segment_index']}",
                    'covering_transform': covering_transform,
                    'elem_index': elem_index
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
    
    def _create_grid_grouped(self, elem_index: int,
                             infos: List[Dict[str, Any]]
                             ) -> Optional[ifcopenshell.entity_instance]:
        """
        Level 2: the same L/T profiles level 1 draws, but all of one ceiling's
        lines become solids inside a single element.

        Each line keeps its own IfcExtrudedAreaSolid, so every bar stays a
        closed swept solid - no overlapping loops in a shared profile, which is
        what makes level 3 non-manifold where lines cross. What is shed is the
        per-line scaffolding: element, placement, axis curve, product shape and
        quantity pset, ~22 entities down to ~6.

        The element sits at the origin, so each solid carries its own absolute
        position rather than one relative to a per-beam placement.
        """
        f = self.target_file
        solids = []
        half_w = self.profile_width / 2.0
        lift = self.profile_height / 2 - self.profile_thickness + self.interior_z_offset

        for info in infos:
            seg = info['segment']
            t = info['covering_transform']
            is_perimeter = seg.get('is_perimeter', False)
            gstart = self._transform_point(seg["start_point"], t)
            gdir = self._transform_direction(seg["direction"], t)
            length = seg["length"]

            off = (0.0, 0.0, lift)
            if is_perimeter:
                normal = seg.get("interior_normal")
                if normal is not None:
                    n = self._transform_direction((normal[0], normal[1], 0.0), t)
                    # Same handedness rule as level 1: flip the bar when the
                    # angle's leg would point out of the ceiling.
                    if gdir[1] * n[0] - gdir[0] * n[1] < 0:
                        gstart = self._transform_point(seg["end_point"], t)
                        gdir = (-gdir[0], -gdir[1], -gdir[2])
                    off = (n[0] * half_w, n[1] * half_w,
                           n[2] * half_w + self.profile_thickness)
                else:
                    off = (half_w, 0.0, self.profile_thickness)

            bd = gdir
            up = (0.0, 0.0, 1.0)
            dot = bd[0]*up[0] + bd[1]*up[1] + bd[2]*up[2]
            if abs(dot) > 0.9:
                up = (0.0, 1.0, 0.0)
                dot = bd[0]*up[0] + bd[1]*up[1] + bd[2]*up[2]
            ay = (up[0] - dot*bd[0], up[1] - dot*bd[1], up[2] - dot*bd[2])
            ay_len = (ay[0]**2 + ay[1]**2 + ay[2]**2) ** 0.5
            if ay_len < 1e-9:
                ay = (0.0, 1.0, 0.0); ay_len = 1.0
            ay = (ay[0]/ay_len, ay[1]/ay_len, ay[2]/ay_len)

            if is_perimeter:
                profile = self._l_profile
                ref = f.createIfcDirection(ay)
            else:
                profile = self._t_profile
                ax = (ay[1]*bd[2] - ay[2]*bd[1],
                      ay[2]*bd[0] - ay[0]*bd[2],
                      ay[0]*bd[1] - ay[1]*bd[0])
                ref = f.createIfcDirection((-ax[0], -ax[1], -ax[2]))

            pos = f.createIfcAxis2Placement3D(
                f.createIfcCartesianPoint(
                    (gstart[0] + off[0], gstart[1] + off[1], gstart[2] + off[2])),
                f.createIfcDirection(bd),
                ref
            )
            solid = f.createIfcExtrudedAreaSolid(profile, pos, self._dir_z, length)
            if self._style_wrapper:
                f.createIfcStyledItem(solid, self._style_wrapper, None)
            solids.append(solid)

        if not solids:
            return None

        element = ifcopenshell.api.root.create_entity(f, ifc_class="IfcBeam")
        element.ObjectType = "Grid Covering"
        element.Name = f"Ceiling_Grid_{elem_index}"
        element.ObjectPlacement = f.createIfcLocalPlacement(
            None,
            f.createIfcAxis2Placement3D(self._origin_3d, self._dir_z, self._dir_x)
        )
        body = f.createIfcShapeRepresentation(
            self.body_context, "Body", "SweptSolid", solids
        )
        element.Representation = f.createIfcProductDefinitionShape(None, None, (body,))
        return element

    def _create_grid_sketch(self, elem_index: int,
                            infos: List[Dict[str, Any]]
                            ) -> Optional[ifcopenshell.entity_instance]:
        """
        One element per ceiling: every footprint line of the covering is
        widened to slim_width about its own centreline, and all of those bands
        become a single composite profile extruded downwards in one go.

        This is where the saving is. The per-segment path spends ~22 entities
        on each bar (placement, axis curve, two shape representations, product
        shape, style, quantity pset and their owner histories); here a line
        costs only its four corner points, a polyline and a profile, and the
        whole ceiling shares one element.

        The extrusion starts profile_height - profile_thickness above the
        footprint plane and runs down profile_height, so the underside sits
        profile_thickness below the ceiling - the same reveal the profiled
        beams give.
        """
        f = self.target_file

        half = self.slim_width / 2.0
        profiles = []
        zs = []
        pts = []

        for info in infos:
            seg = info['segment']
            t = info['covering_transform']
            start = self._transform_point(seg["start_point"], t)
            d = self._transform_direction(seg["direction"], t)
            length = seg["length"]
            end = (start[0] + d[0] * length,
                   start[1] + d[1] * length,
                   start[2] + d[2] * length)
            dx, dy = end[0] - start[0], end[1] - start[1]
            plan_len = (dx * dx + dy * dy) ** 0.5
            if plan_len < 1e-9:
                continue  # a purely vertical line has no band in plan
            ux, uy = dx / plan_len, dy / plan_len
            nx, ny = -uy, ux
            zs.append(start[2]); zs.append(end[2])
            pts.append((start[0], start[1], end[0], end[1], nx, ny))

        if not pts:
            return None

        plane_z = sum(zs) / len(zs)
        cx = sum(p[0] + p[2] for p in pts) / (2 * len(pts))
        cy = sum(p[1] + p[3] for p in pts) / (2 * len(pts))

        # Widen every line to slim_width and UNION them. Bands overlap wherever
        # grid lines cross; emitting them as separate loops in one composite
        # profile makes the extrusion non-manifold at each crossing (edges shared
        # by more than two faces), so the solid is not watertight. Unioning first
        # yields real, disjoint rings - and the holes are the ceiling tiles.
        rings = []
        try:
            from shapely.geometry import MultiLineString
            from shapely.geometry.polygon import orient
            mls = MultiLineString([[(sx, sy), (ex, ey)] for sx, sy, ex, ey, _, _ in pts])
            merged = mls.buffer(half, cap_style=2, join_style=2)
            polys = list(getattr(merged, "geoms", [merged]))
            for poly in polys:
                if poly.is_empty:
                    continue
                # orient(sign=1.0): exterior counter-clockwise, holes clockwise,
                # which is what IFC expects of profile curves.
                poly = orient(poly, sign=1.0)
                rings.append((list(poly.exterior.coords),
                              [list(r.coords) for r in poly.interiors]))
            self._sketch_unioned = True
        except Exception as e:
            self.logger.debug(f"shapely union unavailable ({e}); using raw bands")
            for sx, sy, ex, ey, nx, ny in pts:
                ox, oy = nx * half, ny * half
                # counter-clockwise for a left-hand normal
                rings.append(([(sx - ox, sy - oy), (ex - ox, ey - oy),
                               (ex + ox, ey + oy), (sx + ox, sy + oy),
                               (sx - ox, sy - oy)], []))
            self._sketch_unioned = False

        for outer, holes in rings:
            def _curve(coords):
                cp = [f.createIfcCartesianPoint((x - cx, y - cy)) for x, y in coords[:-1]]
                return f.createIfcPolyline(cp + [cp[0]])
            oc = _curve(outer)
            if holes:
                profiles.append(f.createIfcArbitraryProfileDefWithVoids(
                    "AREA", None, oc, [_curve(h) for h in holes]))
            else:
                profiles.append(f.createIfcArbitraryClosedProfileDef("AREA", None, oc))

        if len(profiles) == 1:
            sweep = profiles[0]
        else:
            sweep = f.createIfcCompositeProfileDef("AREA", None, profiles, None)

        element = ifcopenshell.api.root.create_entity(f, ifc_class="IfcBeam")
        element.ObjectType = "Grid Covering"
        element.Name = f"Ceiling_Grid_{elem_index}"
        element.ObjectPlacement = f.createIfcLocalPlacement(
            None,
            f.createIfcAxis2Placement3D(self._origin_3d, self._dir_z, self._dir_x)
        )

        top_z = plane_z + self.profile_height - self.profile_thickness
        solid = f.createIfcExtrudedAreaSolid(
            sweep,
            f.createIfcAxis2Placement3D(
                f.createIfcCartesianPoint((cx, cy, top_z)),
                self._dir_z, self._dir_x
            ),
            f.createIfcDirection((0., 0., -1.)),
            self.profile_height
        )

        if self._style_wrapper:
            f.createIfcStyledItem(solid, self._style_wrapper, None)

        body = f.createIfcShapeRepresentation(
            self.body_context, "Body", "SweptSolid", [solid]
        )
        element.Representation = f.createIfcProductDefinitionShape(None, None, (body,))
        return element

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
        if self.slim:
            beam.Name = f"Ceiling_Grid_Bar_{segment_id}"
        elif is_perimeter:
            beam.Name = f"Ceiling_Profile_Angle_{segment_id}"
        else:
            beam.Name = f"Ceiling_Profile_T-Runner_{segment_id}"
        
        start_point = segment["start_point"]
        direction = segment["direction"]
        length = segment["length"]
        
        global_start = self._transform_point(start_point, covering_transform)
        global_dir = self._transform_direction(direction, covering_transform)
        
        # The slim bar is centred on the line, so it needs the interior lift
        # (underside profile_thickness below the ceiling) and none of the
        # perimeter handedness - a symmetric section has no face to turn.
        offset_pt = self._interior_offset_pt
        if is_perimeter and not self.slim:
            offset_pt = self._perimeter_offset_pt
            normal = segment.get("interior_normal")
            if normal is not None:
                n = self._transform_direction((normal[0], normal[1], 0.0), covering_transform)
                # The angle's horizontal leg extends towards (direction x Z), so
                # reverse the beam when that points out of the ceiling - then the
                # leg lies inside it and the upstand sits on the boundary line.
                if global_dir[1] * n[0] - global_dir[0] * n[1] < 0:
                    global_start = self._transform_point(segment["end_point"], covering_transform)
                    global_dir = (-global_dir[0], -global_dir[1], -global_dir[2])
                offset_pt = self._perimeter_offset_point(n)
        
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
        if self.slim:
            profile = self._slim_profile
        else:
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
        
        if is_perimeter and not self.slim:
            extrude_placement = f.createIfcAxis2Placement3D(
                offset_pt,
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
                offset_pt,
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
                "Width": self.slim_width if self.slim else self.profile_width
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
        skipped = ""
        if s['skipped_no_interior']:
            skipped = f", skipped {s['skipped_no_interior']} coverings with no interior lines"
        if self.slim_mode == "sketch":
            body = (f"{s['sketch_elements']} grid elements "
                    f"from {s['sketch_lines']} lines "
                    f"({self.slim_width}mm wide, one sketch per ceiling)")
        elif self.slim_mode == "grouped":
            body = (f"{s['sketch_elements']} grid elements "
                    f"from {s['sketch_lines']} profiled solids "
                    f"(L/T profiles, one element per ceiling)")
        else:
            body = (f"{s['total_beams']} beams "
                    f"({s['perimeter_beams']} perimeter, {s['interior_beams']} interior)")
        self.logger.info(
            f"CeilingGridsGlobal done in {elapsed:.1f}s: "
            f"{s['covering_elements']} coverings, "
            f"{body}"
            f"{skipped}"
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
