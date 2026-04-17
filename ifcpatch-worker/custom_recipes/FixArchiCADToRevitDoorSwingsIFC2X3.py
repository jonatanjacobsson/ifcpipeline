"""
IFC2X3 variant of the built-in FixArchiCADToRevitDoorSwings recipe.

The built-in recipe targets IFC4 and crashes on IFC2X3 files because it
queries IfcDoorType (IFC4-only) and IfcIndexedPolyCurve (IFC4-only).

This custom recipe ports the applicable fix sections to IFC2X3:
  A. Purge wall axis representations          (IfcWall — same in IFC2X3)
  B. Strip FootPrint from door styles         (IfcDoorStyle replaces IfcDoorType)
  C. Fix door FootPrint representations for Revit visibility:
     - Doors with Body + FootPrint: split into 3D IfcDoor + 2D proxy
     - Doors with FootPrint only:   convert FootPrint to Body in-place
       (splitting would leave the original door invisible)

Section D (arc faceting of IfcIndexedPolyCurve) is intentionally skipped
because that entity does not exist in IFC2X3.  IFC2X3 exports use
IfcPolyline / IfcTrimmedCurve / IfcCompositeCurve for 2D curves,
which Revit handles without the arc-index bug that affects IFC4.

Based on upstream FixArchiCADToRevitDoorSwings by Dion Moult.
"""

import logging

import ifcopenshell
import ifcopenshell.util.element
import ifcopenshell.util.schema

logger = logging.getLogger(__name__)


class Patcher:
    """Fix missing door swings in Revit when viewing IFC2X3 files.

    Parameters:
        file: The IFC model to patch (must be IFC2X3 schema)
        logger: Logger instance for output

    Example:
        patcher = Patcher(ifc_file, logger)
        patcher.patch()
        output = patcher.get_output()
    """

    def __init__(self, file: ifcopenshell.file, logger: logging.Logger):
        self.file = file
        self.logger = logger or logging.getLogger(__name__)

    def patch(self) -> None:
        schema = self.file.schema
        if schema != "IFC2X3":
            raise ValueError(
                f"This recipe is for IFC2X3 files only (got {schema}). "
                "Use the built-in FixArchiCADToRevitDoorSwings for IFC4+."
            )

        self._purge_wall_axis_reps()
        self._strip_footprint_from_door_styles()
        self._fix_door_footprints()

        self.logger.info(
            "IfcIndexedPolyCurve arc-faceting skipped (entity does not exist in IFC2X3)"
        )

    # ------------------------------------------------------------------
    # Section A — Purge wall axis representations
    # Revit draws the axis line through each wall without giving users
    # control over its visibility, producing extra lines across door
    # openings.  See https://github.com/Autodesk/revit-ifc/issues/360
    # ------------------------------------------------------------------
    def _purge_wall_axis_reps(self) -> None:
        walls = self.file.by_type("IfcWall")
        purged = 0
        for wall in walls:
            if not wall.Representation:
                continue
            reps = list(wall.Representation.Representations)
            filtered = [r for r in reps if r.RepresentationIdentifier != "Axis"]
            if len(filtered) != len(reps):
                wall.Representation.Representations = filtered
                purged += 1
        self.logger.info(
            f"Section A: purged Axis reps from {purged}/{len(walls)} wall(s)"
        )

    # ------------------------------------------------------------------
    # Section B — Strip FootPrint representation maps from door styles
    # In IFC2X3 the typing entity for doors is IfcDoorStyle (IFC4 uses
    # IfcDoorType).  Culling the 2D FootPrint from the type prevents a
    # Revit bug where hiding the Door category also hides the split-out
    # generic-model proxy.
    # See https://github.com/Autodesk/revit-ifc/issues/362
    # ------------------------------------------------------------------
    def _strip_footprint_from_door_styles(self) -> None:
        door_styles = self.file.by_type("IfcDoorStyle")
        stripped = 0
        for style in door_styles:
            rep_maps = list(style.RepresentationMaps or [])
            if not rep_maps:
                continue
            filtered = [
                r
                for r in rep_maps
                if r.MappedRepresentation
                if r.MappedRepresentation.RepresentationIdentifier != "FootPrint"
            ]
            if len(filtered) != len(rep_maps):
                style.RepresentationMaps = filtered if filtered else None
                stripped += 1
        self.logger.info(
            f"Section B: stripped FootPrint maps from {stripped}/{len(door_styles)} door style(s)"
        )

    # ------------------------------------------------------------------
    # Section C — Fix door FootPrint representations for Revit
    #
    # Two strategies depending on what representations a door has:
    #
    #   Body + FootPrint  → split: original IfcDoor keeps Body only,
    #                       a copy reclassed to IfcDiscreteAccessory
    #                       gets FootPrint with context switched to
    #                       Body/Model so Revit renders it as 3D lines.
    #                       See https://github.com/Autodesk/revit-ifc/issues/358
    #
    #   FootPrint only    → convert in-place: change the FootPrint rep
    #                       context to Body/Model and update identifiers
    #                       so Revit renders the curves.  Splitting here
    #                       would leave the original door with zero reps
    #                       (invisible).
    # ------------------------------------------------------------------
    def _fix_door_footprints(self) -> None:
        doors = self.file.by_type("IfcDoor")

        body_context = None
        for ctx in self.file.by_type("IfcGeometricRepresentationSubContext"):
            if ctx.ContextIdentifier == "Body" and ctx.ContextType == "Model":
                body_context = ctx
                break

        if body_context is None:
            self.logger.warning("No Body/Model sub-context found — cannot fix door footprints")
            return

        split_count = 0
        convert_count = 0
        skip_count = 0

        for door in doors:
            if not door.Representation:
                skip_count += 1
                continue

            reps = list(door.Representation.Representations)
            body_reps = [r for r in reps if r.RepresentationIdentifier != "FootPrint"]
            footprint_reps = [r for r in reps if r.RepresentationIdentifier == "FootPrint"]

            if not footprint_reps:
                skip_count += 1
                continue

            if body_reps:
                self._split_door(door, body_reps, footprint_reps, body_context)
                split_count += 1
            else:
                self._convert_footprint_in_place(door, footprint_reps, body_context)
                convert_count += 1

        self.logger.info(
            f"Section C: {split_count} door(s) split (Body+FootPrint), "
            f"{convert_count} door(s) converted in-place (FootPrint only), "
            f"{skip_count} door(s) skipped (no FootPrint)"
        )

    def _switch_rep_to_body(self, rep, body_context) -> None:
        """Traverse a FootPrint shape representation and switch all
        IfcShapeRepresentation nodes to Body/Model context."""
        for subelement in self.file.traverse(rep):
            if not subelement.is_a("IfcShapeRepresentation"):
                continue
            subelement.ContextOfItems = body_context
            subelement.RepresentationIdentifier = "Body"
            subelement.RepresentationType = "Curve3D"

    def _split_door(self, door, body_reps, footprint_reps, body_context) -> None:
        """Door has both Body and FootPrint — split into two objects."""
        door_copy = ifcopenshell.util.element.copy(self.file, door)
        door_copy = ifcopenshell.util.schema.reassign_class(
            self.file, door_copy, "IfcDiscreteAccessory"
        )
        door_copy.Representation = ifcopenshell.util.element.copy(
            self.file, door.Representation
        )
        door_copy.Representation.Representations = footprint_reps
        door.Representation.Representations = body_reps

        if door.ContainedInStructure:
            related = list(door.ContainedInStructure[0].RelatedElements)
            related.append(door_copy)
            door.ContainedInStructure[0].RelatedElements = related

        for fp_rep in footprint_reps:
            self._switch_rep_to_body(fp_rep, body_context)

    def _convert_footprint_in_place(self, door, footprint_reps, body_context) -> None:
        """Door has only FootPrint — convert context to Body/Model in-place
        so Revit renders the 2D curves as model geometry."""
        for fp_rep in footprint_reps:
            self._switch_rep_to_body(fp_rep, body_context)

    def get_output(self) -> ifcopenshell.file:
        return self.file
