"""
MagiCAD / MagiCAD-style shell repair for Solibri-facing meshes: ``TessellateElements`` then
``OrientFacetedBrepShells`` (custom), with the same batching rules as ``repair_full_magiad_ifc.py``.

Recipe Name: MagiadTessellateAndOrient

Arguments (positional strings, all optional except semantics):

1. **types** — IFC class selector list, comma/space separated (default ``IfcFlowFitting``).
   Use ``IfcElement`` for all building elements (batched by GlobalId; ``IfcSite`` excluded).
2. **preset_mep_flow** — ``"true"`` / ``"false"``. If true, uses a schema-specific MEP preset:
   **IFC2×3** — all distribution classes used in models like V--57 (``IfcFlowSegment`` covers duct silencers, etc.);
   **IFC4 / IFC4×3** — every concrete leaf under ``IfcDistributionElement`` (``IfcDuctSilencer``, ``IfcDamper``, …)
   plus ancillary types (``IfcDiscreteAccessory``, …; not ``IfcCovering`` — add it in **types** if you want it). **types** lists **extra** classes merged in (deduped).
3. **ifc_element_batch_size** — integer string, only for **types** = ``IfcElement`` (default ``50``).
4. **coord_decimals** — weld decimals for orientation (default ``6``).

Example API body::

    {
      "input_file": "uploads/V--57_V01000R.ifc",
      "output_file": "output/patch/V--57_V01000R_repaired.ifc",
      "recipe": "MagiadTessellateAndOrient",
      "use_custom": true,
      "arguments": ["IfcFlowFitting", "false", "50", "6"]
    }

MEP preset (duct / distribution–heavy models)::

    "arguments": ["IfcFlowFitting", "true", "50", "6"]

MEP preset **plus** extra classes (e.g. structural steel)::

    "arguments": ["IfcBeam, IfcColumn", "true", "50", "6"]
"""

from __future__ import annotations

import logging

import ifcopenshell

from _magaid_shell_repair import (
    DEFAULT_IFC_ELEMENT_BATCH_SIZE,
    COORD_DECIMALS_DEFAULT,
    merge_mep_preset_with_extras,
    parse_bool,
    parse_int,
    parse_types_arg,
    run_tessellate_and_orient,
)


class Patcher:
    def __init__(
        self,
        file: ifcopenshell.file,
        logger,
        types: str = "IfcFlowFitting",
        preset_mep_flow: str = "false",
        ifc_element_batch_size: str = "50",
        coord_decimals: str = "6",
    ):
        """
        :param types: Comma-separated IFC classes (selector) or ``IfcElement`` for full element pass.
        :param preset_mep_flow: If ``\"true\"``, use bundled MEP-related classes and merge **types** as extras.
        :param ifc_element_batch_size: Batch size when **types** resolves to ``IfcElement`` only.
        :param coord_decimals: Coordinate welding for ``OrientFacetedBrepShells``.
        """
        self.file = file
        self.logger = logger or logging.getLogger(__name__)
        self.types = (types or "IfcFlowFitting").strip()
        self.preset_mep_flow = preset_mep_flow
        self.ifc_element_batch_size = ifc_element_batch_size
        self.coord_decimals = coord_decimals

    def patch(self) -> None:
        if parse_bool(self.preset_mep_flow):
            type_list = merge_mep_preset_with_extras(self.file.schema, parse_types_arg(self.types))
        else:
            type_list = parse_types_arg(self.types)

        batch = parse_int(self.ifc_element_batch_size, DEFAULT_IFC_ELEMENT_BATCH_SIZE)
        cd = parse_int(self.coord_decimals, COORD_DECIMALS_DEFAULT)

        input_path = getattr(self.file, "_input_file_path", None) or "input.ifc"
        run_tessellate_and_orient(
            self.file,
            self.logger,
            str(input_path),
            type_list=type_list,
            ifc_element_batch_size=batch,
            coord_decimals=cd,
        )

    def get_output(self) -> ifcopenshell.file:
        return self.file
