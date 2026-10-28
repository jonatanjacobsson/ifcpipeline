"""
PipeBottomLinesDwg Recipe

Writes the bottom-of-pipe lines (the outside bottom, BOP; Swedish
vattengång, VG) of the pipes and conduits in an IFC to a DWG of 3D
polylines, one layer per group and size (layer names: see
pipe_bottom_lines/labels.py). It is the IFC
twin of a pyRevit VG-linjer button in Revit: same rules, same layer names,
same DWG writer, so a DWG from the IFC export matches the one from Revit.

- The pipe-bottom line is the centreline dropped straight down by half the outside
  diameter. Fittings become lines to their centre, elbows arcs.
- A conduit bend at least ``long_bend_min_mm`` long along its arc is cut into
  segments of about ``bend_segment_mm``; a shorter one is drawn straight.
- What is drawn: with no ``selector``, every pipe, conduit and fitting that
  lies in the ground: an element whose midpoint is below the floor of the
  lowest storey (storeys with under 1 % of the elements left out), or whose
  type, name, system or a property value has a ground word in it
  ("markavloppsrör", "Kulvertrör", "bottenplatta", ...; GROUND_WORDS) and
  whose midpoint is below the floor of the storey at the project's ±0, plus
  the fittings on those pipes. With a ``selector``, everything it picks, in
  the ground or not, plus the fittings on the chosen pipes and conduits: the
  selector decides.
- Pieces that meet end to end on the same layer join into one 3D polyline.
- Layers are ``VG - <group> - <colour rule> - <size>mm``, for example
  ``VG - 611 - Markrör KRAFT - 110mm``. The colour rule is the first of
  ``color_rules`` whose selector matches the element, the size its outside
  diameter, and the group, with ``group_properties``, the first of them the
  element has, else the first found along its connected line (port to port
  through pipes, conduits and fittings). Without them (auto), or where the
  line has none, the group is the line's system: System Type / Systemtyp /
  System Classification, its IfcSystem, or System Name. A line nothing
  names is a group of its own, ``Ledning 1``, ``Ledning 2``, ... numbered
  west to east, so separate lines get separate layers and colours.
- Colours, one per system: a matching colour rule's, else the colour its pipes
  or conduits carry in the model when it is a real one (not the black Revit
  writes for uncoloured materials), else a distinct default per system. A
  fitting leg takes the system of the pipe on its port.
- Coordinates are the IFC's world coordinates, in metres. The output can go
  into another coordinate system: it is turned by ``rotation_deg`` about
  the origin (counter-clockwise in plan), then moved by ``offset_x/y/z``,
  the way IfcMapConversion maps a model to map coordinates. PipeBottomLinesIfc takes
  the same four arguments.

The output is the DWG, not an IFC: name the output file ``*.dwg``. The job
summary lists the layers, the polyline counts and the skipped elements.
PipeBottomLinesIfc writes the same runs, with level markers, as IFC.

Requirements. Nothing installs these by itself:

- Python: IfcOpenShell (tested 0.8.4 and 0.9.0) and numpy.
  ifcpatch-worker/requirements.txt has both.
- Not Python: the .NET 8 runtime, pythonnet 3.0.x and ACadSharp.dll 3.8.0
  (``ACADSHARP_DLL``, default /opt/acadsharp/ACadSharp.dll). The upstream
  ifcpipeline worker image has none of them; pipe_bottom_lines/dwg.py lists the
  steps that add them.

The recipe checks for them before it reads any geometry. When one is
missing, the job fails at once with DwgUnavailable, naming it. Where .NET
can't be installed, use PipeBottomLinesIfc, which writes the same lines as IFC and
needs no .NET.

Recipe Name: PipeBottomLinesDwg
Author: ifcpipeline (2026-10)

Positional arguments (n8n IfcPatch node order). An argument of ``-`` means
empty (its default): the n8n IfcPatch node drops empty values, which would
shift every argument after it.
    selector:         IfcOpenShell selector for the elements to draw, in the
                      ground or not. Empty: every pipe, conduit and fitting
                      (not cable trays) in the ground.
    color_rules:      JSON list of {"name", "color", "selector"}; the first rule
                      whose IfcOpenShell selector matches an element colours it
                      and names its layer, like a Revit view filter. Empty: no
                      rules, colours come from the model.
    group_properties: comma-separated ``Pset.Property`` or ``Property`` names;
                      the first one found on the element, else along its
                      line, names the group (``CQ BSAB`` matches the
                      Revit button). Empty or ``auto`` (default): the line's
                      system, else ``Ledning N``.
    offset_x:         metres added to X, after the rotation (default ``0``).
    offset_y:         metres added to Y, after the rotation (default ``0``).
    offset_z:         metres added to Z (default ``0``).
    rotation_deg:     degrees the output is turned about the origin,
                      counter-clockwise in plan, before the offset (default ``0``).

Fixed in the code (class attributes, the same for every job): the size is
the outside diameter; conduit bends at least LONG_BEND_MIN_MM (300) long are
cut into BEND_SEGMENT_MM (200) segments and shorter ones drawn straight,
like the Revit button; the fittings on the selected pipes and conduits come
along; the ground is found from GROUND_WORDS and the lowest storey.

Example color_rules for an electrical export whose yellow ("Gul") and green
("Grön") ground conduits carry the Markrör KRAFT and Markrör TELE colours of
the project's Revit view filters::

    [{"name": "Markrör KRAFT", "color": "FFFF00",
      "selector": "IfcCableCarrierSegment, IfcCableCarrierFitting, Name=/.*SRN.*Gul.*/"},
     {"name": "Markrör TELE", "color": "00FF00",
      "selector": "IfcCableCarrierSegment, IfcCableCarrierFitting, Name=/.*SRN.*Grön.*/"}]
"""

import json
import logging
import os
import tempfile

import ifcopenshell

from pipe_bottom_lines import dwg, export
from pipe_bottom_lines.ifc_geometry import GROUND_WORDS, MM, parse_color_rules

logger = logging.getLogger(__name__)


def _number(value, default):
    text = str(value).strip() if value is not None else ""
    if not text:
        return float(default)
    return float(text.replace(",", "."))


def _blank(value):
    """"-" stands for an empty argument (the n8n IfcPatch node drops empty values)."""
    return "" if value is not None and str(value).strip() == "-" else value


class Patcher:
    """Write the pipe-bottom lines of an IFC's pipes and conduits to a 3D-polyline DWG."""

    # Fixed for every job (see the module docstring). Conduit bends at least
    # this long (mm) are cut into segments of about BEND_SEGMENT_MM; shorter
    # ones are drawn straight, like the Revit button.
    LONG_BEND_MIN_MM = 300
    BEND_SEGMENT_MM = 200
    # A selector also draws the fittings on the ports of the pipes and conduits it picks.
    INCLUDE_CONNECTED_FITTINGS = True

    def __init__(self, file: ifcopenshell.file, logger: logging.Logger,
                 selector: str = "",
                 color_rules: str = "",
                 group_properties: str = "auto",
                 offset_x: str = "0",
                 offset_y: str = "0",
                 offset_z: str = "0",
                 rotation_deg: str = "0"):
        """
        :param selector: IfcOpenShell selector for the elements to draw, in the ground or not; empty draws every pipe, conduit and fitting in the ground.
        :param color_rules: JSON list of {"name", "color", "selector"}; first match colours an element and names its layer.
        :param group_properties: Comma-separated Pset.Property or Property names naming the group (element, then its line); "auto" uses the line's system, else "Ledning N".
        :param offset_x: Metres added to X, after the rotation.
        :param offset_y: Metres added to Y, after the rotation.
        :param offset_z: Metres added to Z.
        :param rotation_deg: Degrees the output is turned about the origin, counter-clockwise, before the offset.
        """
        self._init(file, logger, selector, color_rules, group_properties, offset_x, offset_y, offset_z, rotation_deg)

    def _init(self, file, logger, selector, color_rules, group_properties, offset_x, offset_y, offset_z,
              rotation_deg):
        """The arguments both recipes share, "-" read as empty."""
        (selector, color_rules, group_properties, offset_x, offset_y, offset_z, rotation_deg) = (
            _blank(value) for value in (
                selector, color_rules, group_properties, offset_x, offset_y, offset_z, rotation_deg))
        self.file = file
        self.logger = logger
        rules = json.loads(color_rules) if color_rules and color_rules.strip() else []
        if isinstance(rules, dict):
            rules = [rules]
        self.color_rules = parse_color_rules(rules)
        self.selector = (selector or "").strip() or None
        specs = [part.strip() for part in (group_properties or "").split(",") if part.strip()]
        # None of them, or "auto": the line's system (see the module docstring).
        self.group_properties = [] if [spec.lower() for spec in specs] == ["auto"] else specs
        self.long_bend_min = self.LONG_BEND_MIN_MM * MM
        self.bend_segment = self.BEND_SEGMENT_MM * MM
        self.include_connected_fittings = self.INCLUDE_CONNECTED_FITTINGS
        # The selector decides what is drawn; without one, what lies in the ground.
        self.in_ground = self.selector is None
        self.ground_words = list(GROUND_WORDS)
        self.offset = (_number(offset_x, 0), _number(offset_y, 0), _number(offset_z, 0))
        self.rotation = _number(rotation_deg, 0)
        self.report = None
        self._output = None

    def patch(self) -> None:
        # Missing .NET fails here, not after minutes of geometry.
        dwg.load()
        self.build_report()
        handle, path = tempfile.mkstemp(prefix="pipe-bottom-lines-", suffix=".dwg")
        os.close(handle)
        export.write_dwg(self.report, path, self.rotation, self.offset)
        self.log_move()
        self._output = path

    def log_move(self):
        if self.rotation or any(self.offset):
            self.logger.info("Turned %.6f degrees and moved by %s m", self.rotation, self.offset)

    def build_report(self) -> None:
        """The pipe-bottom pieces and runs of the model, logged; raises when there are none."""
        self.report = export.build(
            self.file,
            selector=self.selector,
            include_connected_fittings=self.include_connected_fittings,
            group_properties=self.group_properties,
            color_rules=self.color_rules,
            long_bend_min=self.long_bend_min,
            bend_segment=self.bend_segment,
            in_ground=self.in_ground,
            ground_words=self.ground_words,
        )
        summary = self.report.summary()
        self.logger.info(
            "Pipe-bottom lines: %d of %d elements (%s), %d pieces, %d polylines on %d layers; "
            "%d long and %d short conduit bends",
            summary["elements"], summary["considered"],
            "selector" if self.selector else "ground level %s m" % summary["ground_level_m"], summary["pieces"],
            summary["polylines"], len(summary["layers"]), summary["long_bends"], summary["short_bends"])
        if summary["skipped"]:
            self.logger.info("Skipped: %s", summary["skipped"])
        if not self.report.polylines:
            raise ValueError("No pipe-bottom lines could be made: the IFC has no pipes or conduits with ports and a diameter")

    def get_output(self) -> str:
        """Path of the written DWG; ifcpatch.write moves it to the output file."""
        return self._output

    def get_summary(self) -> dict:
        if self.report is None:
            return {}
        summary = self.report.summary()
        summary["rotation_deg"] = self.rotation
        summary["offset_m"] = list(self.offset)
        return summary
