"""Ingest element-to-space relationships from a RoomStamp JSON report.

Use when ``stamp=true`` fails on very large models but a dry-run report
(``stamp=false``, ``report_detail=full``) completed successfully.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, List, Set

from ingest_scripts import Ingester as _Base, Relationship

_STATUS_SKIP = frozenset({"", "unmatched", "Unmatched"})


class Ingester(_Base):
    SCRIPT_NAME = "ExtractRoomStampReport"
    DESCRIPTION = (
        "Extract contained_in_space edges from a topologicpy roomstamp JSON report "
        "(dry-run with report_detail=full)"
    )

    def __init__(
        self,
        ifc_files: List[Path],
        log: logging.Logger,
        min_confidence: float = 0.0,
        include_ambiguous: bool = True,
    ):
        """Parse roomstamp report JSON and emit element-to-space containment edges.

        Pass the report path as the sole ``input_files`` entry (``.json`` extension).

        :param min_confidence: Skip matches below this confidence threshold.
        :param include_ambiguous: When false, only import ``Contained`` / legacy single-match rows.
        """
        super().__init__(ifc_files, log)
        self.min_confidence = float(min_confidence)
        self.include_ambiguous = include_ambiguous

    def extract(self) -> None:
        t0 = time.time()
        elements_scanned = 0
        edges_created = 0
        skipped_unmatched = 0
        skipped_low_confidence = 0
        skipped_ambiguous = 0
        methods_seen: Set[str] = set()

        for report_path in self.ifc_files:
            if report_path.suffix.lower() != ".json":
                self.log.warning(
                    "roomstamp_report: skipping non-JSON input %s", report_path.name
                )
                continue

            self.log.info("roomstamp_report: loading %s", report_path.name)
            with open(report_path, encoding="utf-8") as fh:
                report = json.load(fh)

            elements = report.get("elements") or []
            if not isinstance(elements, list):
                self.log.warning("roomstamp_report: no elements array in %s", report_path.name)
                continue

            self.log.info("roomstamp_report: parsing %d element rows", len(elements))

            for row in elements:
                if not isinstance(row, dict):
                    continue
                elements_scanned += 1

                status = str(row.get("match_status") or "").strip()
                if status in _STATUS_SKIP:
                    skipped_unmatched += 1
                    continue

                if not self.include_ambiguous and status.lower() not in ("contained",):
                    skipped_ambiguous += 1
                    continue

                confidence_raw = row.get("match_confidence")
                confidence = self._parse_confidence(confidence_raw)
                if confidence is not None and confidence < self.min_confidence:
                    skipped_low_confidence += 1
                    continue

                element_gid = str(row.get("global_id") or "").strip()
                space = row.get("matched_space")
                if not element_gid or not isinstance(space, dict):
                    skipped_unmatched += 1
                    continue

                space_gid = str(space.get("global_id") or "").strip()
                if not space_gid:
                    skipped_unmatched += 1
                    continue

                method = str(row.get("match_method") or "")
                methods_seen.add(method or status)

                self._relationships.append(Relationship(
                    subject_global_id=element_gid,
                    object_global_id=space_gid,
                    relationship_family="spatial",
                    relationship_type="contained_in_space",
                    confidence=confidence if confidence is not None else 0.9,
                    source_kind="topologic_ingest_ExtractRoomStampReport",
                    evidence={
                        "spatial_match_status": status,
                        "spatial_match_method": method,
                        "spatial_match_confidence": confidence,
                        "space_name": space.get("name") or "",
                        "space_long_name": space.get("long_name") or "",
                        "element_ifc_class": row.get("ifc_class") or "",
                        "element_name": row.get("name") or "",
                        "source_file": report_path.name,
                        "report_source": row.get("source_file") or "",
                    },
                ))
                edges_created += 1

        elapsed = time.time() - t0
        self._summary = {
            "elements_scanned": elements_scanned,
            "contained_in_space_edges": edges_created,
            "skipped_unmatched": skipped_unmatched,
            "skipped_low_confidence": skipped_low_confidence,
            "skipped_ambiguous": skipped_ambiguous,
            "methods_seen": sorted(methods_seen),
            "elapsed_seconds": round(elapsed, 2),
        }
        self.log.info(
            "roomstamp_report: %d contained_in_space edges from %d elements in %.1fs",
            edges_created,
            elements_scanned,
            elapsed,
        )

    @staticmethod
    def _parse_confidence(raw: Any) -> float | None:
        if raw is None or raw == "":
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
