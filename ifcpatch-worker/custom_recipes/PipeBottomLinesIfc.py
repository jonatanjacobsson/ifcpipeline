"""
PipeBottomLinesIfc Recipe

Writes the bottom-of-pipe lines (the outside bottom, BOP; Swedish
vattengång, VG) of the pipes and conduits in an IFC to an IFC4 of
annotations, for viewers such as StreamBIM: the same lines, layers and
colours as PipeBottomLinesDwg's DWG, with level markers on them. The names
the output shows (layers, types, property sets) are Swedish by default and
all live in pipe_bottom_lines/labels.py.
The output is that IFC, not the model: name the output file ``*.ifc``.

- The lines follow PipeBottomLinesDwg's rules (see there): what the ``selector``
  picks, or with no selector what lies in the ground; pieces joined end to
  end into one run per group and diameter; layers ``VG - <group> - <colour
  rule> - <size>mm``, the group from ``group_properties`` or, by default,
  the connected line's system (else ``Ledning N``); colours from the colour
  rules or the model. Where a
  pipe turns steeper than 10°, the line moves onto its lowest wall line,
  square to the pipe, so on risers it runs up the pipe's wall and not inside
  it, one wall per riser (the one the run comes in on); level and gently
  sloping lines are the DWG's, right up to where they meet a riser. Conduit
  bends are followed in 50 mm steps, where the DWG draws short ones straight.
- Every run is one IfcAnnotation of type "VG Linjer" named after its layer,
  drawn as a tube TUBE_DIAMETER_MM across in its colour. It has no 3D
  polyline (WRITE_POLYLINE): StreamBIM draws one straight from end to end in
  its own blue. Runs carry their VG level start and end and their diameter
  in ``run_pset``.
- Level markers, of type "VG Markör", named "VG +12.425": upside-down square pyramids
  with their tip on the line, pointing straight out of the pipe, at least
  MARKER_SIZE_MM and 50 mm more than the pipe's outside diameter, with
  the level ("+12.75") as 3D text set into their plate, 1 mm proud of it, in
  a monoline DIN-style lettering (pipe_bottom_lines.font), reading along the pipe.
  Both ends of every pipe at least MARKER_SPACING_MM long and the ends
  of a run's first and last pipe get one, half a marker in from the joint so
  it sits on its own pipe (on the joint where the pipe is too steep for that);
  other joints only where no marker of the run is closer than
  MARKER_SPACING_MM along it. On a riser the marker stands beside it,
  pointing at the wall the line runs on.
- A marker shows the level of the pipe end it marks, rounded half up: a
  pipe's lower-end marker reads the same as the pipe's own Revit "+VG". It
  carries it as MÅTT "+VG" ("+12.75") and "Höjd UK Plushöjd" (a number, for
  StreamBIM tags).
- The types are the annotations' ObjectType and an IfcTypeProduct each (IFC4
  has no PredefinedType on IfcAnnotation).
- A run and its markers share one Description ("Spillvatten - 75mm #4
  (+12.48 → +12.65)"), so grouping by Description isolates a run.
- ``content`` splits them: ``lines`` writes only the runs, ``markers`` only
  the markers (same GlobalIds as in the full file), so they can be two files
  on two viewer layers.
- The source model's length unit and map conversion; runs and markers in its
  world coordinates, so the IFC overlays the model in a federation.
  GlobalIds come from the source elements, so they stay the same from one
  export to the next.
- ``rotation_deg`` and ``offset_x/y/z`` move runs and markers into another
  coordinate system, as PipeBottomLinesDwg moves its DWG: turned about the origin,
  counter-clockwise in plan, then moved, in metres. The levels on the
  markers and runs move with them. The source's map conversion is then left
  out, since the file is already in the target system; its true north turns
  with the runs. GlobalIds do not change.

- Pipes and fittings without a placed port (MagiCAD's IFC2X3 writes none,
  Civil 3D's IFC4X3 writes them without placement) take their ports from
  their bodies: an open tube's round rims, or a closed pipe's two end rings
  (pipe_bottom_lines.ifc_geometry.MeshPort). The summary counts them as
  mesh_port_elements. A selector naming IfcBuildingElementProxy also draws
  the proxies whose body is a straight round tube at least ten times as long
  as it is wide: the pipes of a CAD drawing turned IFC, without its wells.

Requirements: Python only, with no .NET (unlike PipeBottomLinesDwg). It needs
IfcOpenShell (tested 0.8.4 and 0.9.0), numpy, and shapely>=2 for the
markers' lettering. ifcpatch-worker/requirements.txt has all three, so the
worker image needs nothing added. Nothing installs them by itself
elsewhere. Without shapely the job fails before it reads any geometry,
with IfcUnavailable naming it. numpy, which IfcOpenShell needs as well, is
imported with the recipe: without it the job fails with "No module named
'numpy'".

Recipe Name: PipeBottomLinesIfc
Author: ifcpipeline (2026-10)

Positional arguments (n8n IfcPatch node order). An argument of ``-`` means
empty (its default): the n8n IfcPatch node drops empty values, which would
shift every argument after it.
    selector:         IfcOpenShell selector for the elements to draw, in the
                      ground or not. Empty: every pipe, conduit and fitting
                      (not cable trays) in the ground.
    content:          ``all`` (default), ``lines`` (the runs only) or
                      ``markers`` (the level markers only).
    color_rules:      JSON list of {"name", "color", "selector"}; the first rule
                      whose IfcOpenShell selector matches an element colours it
                      and names its layer. Empty: colours come from the model.
    group_properties: comma-separated ``Pset.Property`` or ``Property`` names;
                      the first one found on the element, else along its
                      connected line, names the group. Empty or ``auto``
                      (default): the line's system (System Type, IfcSystem
                      or System Name), else ``Ledning N``, one group per
                      line nothing names (see PipeBottomLinesDwg).
    run_pset:         property set for the runs' levels and diameter:
                      ``VG`` (default; "VG level start", "VG level end",
                      "Diameter"), another name, ``Name:Prefix`` for a
                      prefixed property convention (``Entreprenör:CC_``:
                      "CC_VG level start", "CC_VG level end",
                      "CC_diameter"), or ``none`` (no set).
    offset_x:         metres added to X, after the rotation (default ``0``).
    offset_y:         metres added to Y, after the rotation (default ``0``).
    offset_z:         metres added to Z (default ``0``).
    rotation_deg:     degrees runs and markers are turned about the origin,
                      counter-clockwise in plan, before the offset (default ``0``).

Fixed in the code (class attributes, the same for every job): the size is
the outside diameter; every conduit bend is cut into BEND_SEGMENT_MM (50)
segments (LONG_BEND_MIN_MM 0), at most a few millimetres off the arc; the
fittings on the selected pipes and conduits come along; tube
TUBE_DIAMETER_MM (20) across, no 3D polyline (WRITE_POLYLINE); markers at
least MARKER_SIZE_MM (150) wide, MARKER_SPACING_MM (500) apart; without a
selector, the ground is found from GROUND_WORDS and the lowest storey.
"""

import logging

import ifcopenshell

import PipeBottomLinesDwg
from pipe_bottom_lines import export
from pipe_bottom_lines.ifc_geometry import MM

logger = logging.getLogger(__name__)


def _or_default(value, default):
    return value if value is not None and str(value).strip() else default


class Patcher(PipeBottomLinesDwg.Patcher):
    """Write the pipe-bottom lines of an IFC's pipes and conduits, with level markers, to an IFC of annotations."""

    # Fixed for every job (see the module docstring). Every conduit bend is
    # followed, in segments of about BEND_SEGMENT_MM.
    LONG_BEND_MIN_MM = 0
    BEND_SEGMENT_MM = 50
    # The tube drawn along each run (mm across); no 3D polyline, which
    # StreamBIM draws straight from end to end, past the bends.
    TUBE_DIAMETER_MM = 20
    WRITE_POLYLINE = False
    # Markers are at least this wide and high (mm); pipes at least
    # MARKER_SPACING_MM long get one at both ends, other joints only this far
    # from any marker of the run.
    MARKER_SIZE_MM = 150
    MARKER_SPACING_MM = 500

    def __init__(self, file: ifcopenshell.file, logger: logging.Logger,
                 selector: str = "",
                 content: str = "all",
                 color_rules: str = "",
                 group_properties: str = "auto",
                 run_pset: str = "VG",
                 offset_x: str = "0",
                 offset_y: str = "0",
                 offset_z: str = "0",
                 rotation_deg: str = "0"):
        """
        :param selector: IfcOpenShell selector for the elements to draw, in the ground or not; empty draws every pipe, conduit and fitting in the ground.
        :param content: "all", "lines" (the runs only) or "markers" (the level markers only).
        :param color_rules: JSON list of {"name", "color", "selector"}; first match colours an element and names its layer.
        :param group_properties: Comma-separated Pset.Property or Property names naming the group (element, then its line); "auto" uses the line's system, else "Ledning N".
        :param run_pset: Property set for the runs' levels and diameter: "VG", another name, "Name:Prefix" for prefixed labels, or "none".
        :param offset_x: Metres added to X, after the rotation.
        :param offset_y: Metres added to Y, after the rotation.
        :param offset_z: Metres added to Z.
        :param rotation_deg: Degrees runs and markers are turned about the origin, counter-clockwise, before the offset.
        """
        self._init(file, logger, selector, color_rules, group_properties, offset_x, offset_y, offset_z, rotation_deg)
        content, run_pset = PipeBottomLinesDwg._blank(content), PipeBottomLinesDwg._blank(run_pset)
        self.content = (_or_default(content, "all")).strip().lower()
        if self.content not in ("all", "lines", "markers"):
            raise ValueError("content must be all, lines or markers, not %r" % content)
        self.run_pset = _or_default(run_pset, "VG").strip()
        if self.run_pset.lower() == "none":
            self.run_pset = None
        self.tube_radius = self.TUBE_DIAMETER_MM * MM / 2
        self.polyline = self.WRITE_POLYLINE
        self.marker_size = self.MARKER_SIZE_MM * MM
        self.marker_spacing = self.MARKER_SPACING_MM * MM
        self._ifc_counts = None

    def patch(self) -> None:
        # Missing shapely fails here, not after minutes of geometry.
        export.ifc_writer()
        self.build_report()
        self._output, lines, markers = export.ifc(
            self.report, self.file, self.marker_size, self.marker_spacing,
            tube_radius=self.tube_radius, polyline=self.polyline,
            lines=self.content != "markers", markers=self.content != "lines", run_pset=self.run_pset,
            rotation_deg=self.rotation, offset=self.offset)
        self._ifc_counts = (lines, markers)
        self.log_move()
        self.logger.info("Pipe-bottom IFC: %d line annotations, %d level markers", lines, markers)

    def get_output(self) -> ifcopenshell.file:
        """The IFC of line annotations and level markers."""
        return self._output

    def get_summary(self) -> dict:
        summary = super().get_summary()
        if self._ifc_counts is not None:
            summary["ifc_lines"], summary["ifc_markers"] = self._ifc_counts
        return summary
