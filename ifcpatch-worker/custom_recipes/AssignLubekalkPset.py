"""
AssignLubekalkPset Recipe

Writes a ``Lubekalk`` property set on every element of a ventilation model so
the model can be imported into Lubekalk (Elecosoft's ventilation estimating
program) without re-keying quantities.

Reza Ahmadi's Chalmers thesis (2024, ACEX30) measured model-based quantity
take-off at 40x the speed of drawing-based take-off, and found the traditional
method underestimating cost by ~8 % — but the quantities still had to be typed
into Lubekalk by hand, because it could not read model data. This recipe closes
that step: each element gets the Lubekalk code, dimensions, montageläge, duct
material and quantity it contributes, plus a status saying whether it is ready.

Status per element (``Lubekalk.Status``):

* ``Klar``     — a complete Lubekalk row (and any insulation/hanger rows).
* ``Ingår``    — priced through another element (insulation parts → host duct).
* ``Utanför``  — not ventilation calculation scope (ceilings, walls in the model).
* ``Saknas``   — in scope, but a value could not be resolved; ``Orsak`` says why.

``Källa`` records where the code came from and ``Säkerhet`` how sure it is, so a
kalkylator can review the low-confidence rows instead of all of them.

Outputs: the patched IFC, and (``export_csv``) two artifacts next to it —
``lubekalk_import`` (.csv, the file Lubekalk's *Importera från Bluebeam* reads:
semicolons, decimal commas, Windows-1252) and ``lubekalk_report`` (.json: readiness,
rows to review, insulation without a code, shortened designations). The job
result's summary carries the readiness counts. ``scripts/export_lubekalk_import.py``
writes the same import (plus an .xlsx with review sheets) from the command line.

Recipe Name: AssignLubekalkPset
Author: ifcpipeline (2026-09)

Positional arguments (n8n IfcPatch node order):
    mapping_module:  module under ``custom_recipes/mappings/`` (default
                     ``lubekalk_magicad`` — MagiCAD for Revit, tracked).
    overwrite:       replace an existing ``Lubekalk`` pset (default ``true``).
    dry_run:         resolve and count without writing (default ``false``).
    handling_source: ``Pset.Property`` for the Handling column (default
                     ``BIP.StoreyName`` — one handling per plan).
    pdel_source:     ``Pset.Property`` for the P-del column (default
                     ``BIP.SystemName`` — one part per system).
    include_hangers: add per-metre hanger rows (CU/RU) to horizontal ducts
                     (default ``false`` — upphängning is priced outside Lubekalk).
    include_voids:   code provisions for voids as håltagning (CH/RH) (default
                     ``false`` — they are ``Utanför`` with the reason recorded).
    export_csv:      also return the import CSV and the JSON report (default ``true``).
    beteckning_max:  longest Beteckning in the CSV (default ``15``, what Lubekalk's
                     article register holds); longer names get a stable short form.
    keep_beteckning_only: comma-separated codes that keep their Beteckning in the
                     CSV (e.g. ``SAK``); empty keeps all.
"""

from __future__ import annotations

import importlib
import json
import logging
import tempfile
from pathlib import Path

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.element

from _lubekalk import (
    PSET,
    STATUS_MISSING,
    Assigner,
    Assignment,
    Mapping,
    VentilationModel,
    assignment_properties,
    readiness,
)
from _lubekalk_export import BETECKNING_MAX, collect, write_lubekalk_csv
from _property_mapping_utils import normalize_bool_argument, normalize_mapping_module, is_blank_argument

_DEFAULT_MAPPING_MODULE = "lubekalk_magicad"


class Patcher:
    """Assign Lubekalk calculation rows to ventilation model elements."""

    def __init__(
        self,
        file: ifcopenshell.file,
        logger: logging.Logger,
        mapping_module: str = _DEFAULT_MAPPING_MODULE,
        overwrite: str = "true",
        dry_run: str = "false",
        handling_source: str = "BIP.StoreyName",
        pdel_source: str = "BIP.SystemName",
        include_hangers: str = "false",
        include_voids: str = "false",
        export_csv: str = "true",
        beteckning_max: str = str(BETECKNING_MAX),
        keep_beteckning_only: str = "",
    ):
        """Write a Lubekalk pset (Kod, Dim 1, Dim 2, Montageläge, Kanalmaterial, Antal, Längd, status) on every element of a ventilation model, and return the Lubekalk import CSV.

        mapping_module: Mapping module in custom_recipes/mappings (default lubekalk_magicad, for MagiCAD for Revit)
        overwrite: Replace an existing Lubekalk pset (true/false)
        dry_run: Resolve and count without writing or exporting (true/false)
        handling_source: Pset.Property for the Handling column (default BIP.StoreyName)
        pdel_source: Pset.Property for the P-del column (default BIP.SystemName)
        include_hangers: Add per-metre hanger rows CU/RU (default false, upphängning is priced outside Lubekalk)
        include_voids: Code provisions for voids as håltagning CH/RH (default false)
        export_csv: Return the Lubekalk import CSV and a JSON report as artifacts (default true)
        beteckning_max: Longest Beteckning in the CSV; longer names get a stable short form (default 15)
        keep_beteckning_only: Comma-separated codes that keep their Beteckning in the CSV, e.g. SAK; empty keeps all
        """
        self.file = file
        self.logger = logger
        self.mapping_module = normalize_mapping_module(mapping_module, _DEFAULT_MAPPING_MODULE, logger=logger)
        self.overwrite = normalize_bool_argument(overwrite, True)
        self.dry_run = normalize_bool_argument(dry_run, False)
        self.handling_source = "BIP.StoreyName" if is_blank_argument(handling_source) else str(handling_source).strip()
        self.pdel_source = "BIP.SystemName" if is_blank_argument(pdel_source) else str(pdel_source).strip()
        self.include_hangers = normalize_bool_argument(include_hangers, False)
        self.include_voids = normalize_bool_argument(include_voids, False)
        self.export_csv = normalize_bool_argument(export_csv, True)
        try:
            self.beteckning_max = max(1, int(str(beteckning_max).strip()))
        except (TypeError, ValueError):
            self.beteckning_max = BETECKNING_MAX
        self.keep_beteckning = (None if is_blank_argument(keep_beteckning_only)
                                else [c for c in str(keep_beteckning_only).split(",") if c.strip()])
        self._artifacts: dict[str, str] = {}
        self.export_summary: dict = {}
        self.mapping = Mapping(importlib.import_module(f"mappings.{self.mapping_module}"))
        self.assignments: dict = {}
        self.report: dict = {}
        self.logger.info(
            "AssignLubekalkPset: mapping=%s overwrite=%s dry_run=%s handling=%s pdel=%s hangers=%s voids=%s",
            self.mapping_module, self.overwrite, self.dry_run, self.handling_source, self.pdel_source,
            self.include_hangers, self.include_voids,
        )

    def patch(self) -> None:
        model = VentilationModel(self.file, self.mapping)
        assigner = Assigner(model, self.mapping, handling_source=self.handling_source,
                            pdel_source=self.pdel_source, include_hangers=self.include_hangers,
                            include_voids=self.include_voids)
        elements = self.file.by_type("IfcElement")
        self.logger.info("AssignLubekalkPset: %s elements, %s ports", len(elements), len(model.port_owner))
        classes = {}
        written = skipped = 0
        for index, element in enumerate(elements):
            if len(elements) > 2000 and (index + 1) % 2000 == 0:
                self.logger.info("Processing element %s/%s", index + 1, len(elements))
            try:
                assignment = assigner.assign(element)
            except Exception as exc:  # one bad element must not sink the model
                self.logger.warning("Failed on %s (%s): %s", element.is_a(), element.GlobalId, exc)
                assignment = Assignment(status=STATUS_MISSING, reason=f"Fel vid tolkning: {exc}")
            self.assignments[element.GlobalId] = assignment
            classes[element.GlobalId] = element.is_a()
            if self.dry_run:
                continue
            if not self.overwrite and PSET in ifcopenshell.util.element.get_psets(element, psets_only=True):
                skipped += 1
                continue
            self._write(element, assignment_properties(assignment))
            written += 1
        self.report = readiness(self.assignments, classes)
        self.logger.info("AssignLubekalkPset: written=%s skipped=%s", written, skipped)
        self.logger.info("Lubekalk readiness: %s", self.report["by_status"])
        self.logger.info("Resolved share (Klar+Ingår+Utanför): %s", self.report["resolved_share"])
        for reason, count in list(self.report["missing_reasons"].items())[:15]:
            self.logger.info("  Saknas x%s: %s", count, reason)
        if self.export_csv and not self.dry_run:
            self._export()

    def _export(self) -> None:
        result = collect(self.file, beteckning_max=self.beteckning_max, keep_beteckning=self.keep_beteckning)
        # Next to the staged input when the worker staged one (its temp dir is removed after upload).
        staged = getattr(self.file, "_input_file_path", None)
        out_dir = Path(tempfile.mkdtemp(prefix="lubekalk_", dir=Path(staged).parent if staged else None))
        csv_path = out_dir / "lubekalk_import.csv"
        replaced = write_lubekalk_csv(result["import"], csv_path)
        report_path = out_dir / "lubekalk_report.json"
        report_path.write_text(json.dumps({
            "readiness": self.report,
            "export": result["summary"],
            "review": result["review"],
            "insulation_to_code": result["insulation"],
            "shortened_designations": result["shortened"],
        }, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        self._artifacts = {"lubekalk_import": str(csv_path), "lubekalk_report": str(report_path)}
        self.export_summary = {
            "import_rows": len(result["import"]),
            "rows_before_aggregation": result["summary"]["rows_before_aggregation"],
            "review_rows": result["summary"]["review_rows"],
            "insulation_to_code_groups": result["summary"]["insulation_to_code_groups"],
            "shortened_designations": len(result["shortened"]),
            "unencodable_characters": replaced,
        }
        self.logger.info("Lubekalk import: %s", self.export_summary)

    def _write(self, element, properties: dict) -> None:
        psets = ifcopenshell.util.element.get_psets(element, psets_only=True)
        existing = psets.get(PSET)
        if existing:
            pset = self.file.by_id(existing["id"])
            # A rerun must not leave rows the element no longer has (e.g. insulation).
            properties = {**{name: None for name in existing if name != "id"}, **properties}
        else:
            pset = ifcopenshell.api.run("pset.add_pset", self.file, product=element, name=PSET)
        ifcopenshell.api.run("pset.edit_pset", self.file, pset=pset, properties=properties)

    def get_output(self) -> ifcopenshell.file:
        return self.file

    def get_artifacts(self) -> dict[str, str]:
        """Uploaded next to the output as ``<output>_lubekalk_import.csv`` / ``_lubekalk_report.json``."""
        return dict(self._artifacts)

    def get_summary(self) -> dict:
        return {
            "by_status": dict(self.report.get("by_status", {})),
            "resolved_share": self.report.get("resolved_share"),
            **self.export_summary,
        }
