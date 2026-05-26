"""BCF 2.1 export for validated clash fixes (viewpoints + proposed solutions)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

try:
    from bcf.v2.bcfxml import BcfXml
except ImportError:
    BcfXml = None  # type: ignore[misc, assignment]


@dataclass
class ValidatedFix:
    """A clash fix that passed resolution and optional global regression."""

    case_id: str
    clash_key: str
    stable_id: str
    a_global_id: str
    b_global_id: str
    a_ifc_class: str
    b_ifc_class: str
    moved_guid: str
    moved_class: str
    position: list[float]
    translation: list[float]
    route_waypoints: list[list[float]]
    attempts: int
    ag_proven: bool
    regression_passed: bool
    description: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_resolution(
        cls,
        case_id: str,
        clash: dict[str, Any],
        resolution: Any,
        *,
        regression_passed: bool,
        ag_proven: bool,
        moved_class: str = "",
    ) -> ValidatedFix:
        p1 = clash.get("p1") or [0, 0, 0]
        p2 = clash.get("p2") or p1
        pos = [(p1[i] + p2[i]) / 2 for i in range(3)]
        last = resolution.attempts[-1] if resolution.attempts else None
        return cls(
            case_id=case_id,
            clash_key=resolution.clash_key,
            stable_id=resolution.stable_id,
            a_global_id=str(clash.get("a_global_id", "")),
            b_global_id=str(clash.get("b_global_id", "")),
            a_ifc_class=str(clash.get("a_ifc_class", "")),
            b_ifc_class=str(clash.get("b_ifc_class", "")),
            moved_guid=resolution.moved_guid,
            moved_class=moved_class or "",
            position=pos,
            translation=resolution.total_translation,
            route_waypoints=last.route_waypoints if last else [],
            attempts=len(resolution.attempts),
            ag_proven=ag_proven,
            regression_passed=regression_passed,
            description="",
        )


def export_validated_fixes_bcf(
    fixes: list[ValidatedFix],
    output_path: Path,
    *,
    project_name: str = "IfcPipeline AG Clash Fixes",
    author: str = "ag-ifc-prototype",
) -> Path:
    if BcfXml is None:
        raise ImportError("bcf-client required: pip install bcf-client")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    bcf = BcfXml.create_new()
    if bcf.project_info and bcf.project_info.project:
        bcf.project_info.project.name = project_name

    for fix in fixes:
        status = "Resolved" if fix.regression_passed else "In Progress"
        desc_lines = [
            fix.description or "Validated clash fix proposal from AG-Ifc workflow.",
            "",
            f"Case: {fix.case_id}",
            f"Classes: {fix.a_ifc_class} vs {fix.b_ifc_class}",
            f"Moved: {fix.moved_class} ({fix.moved_guid[:13]}…)",
            f"Attempts: {fix.attempts}",
            f"Translation (m): {fix.translation}",
            f"AG certified: {fix.ag_proven}",
            f"Global regression: {'pass' if fix.regression_passed else 'fail'}",
        ]
        if fix.route_waypoints:
            desc_lines.append(f"Route waypoints: {fix.route_waypoints}")

        topic = bcf.add_topic(
            title=f"[{fix.case_id}] {fix.a_ifc_class} ∩ {fix.b_ifc_class}",
            description="\n".join(desc_lines),
            author=author,
            topic_type="Clash",
            topic_status=status,
        )
        pos = np.array(fix.position, dtype=np.float64)
        guids = [g for g in (fix.a_global_id, fix.b_global_id, fix.moved_guid) if g]
        topic.add_viewpoint_from_point_and_guids(pos, *guids[:3] or ("",))

        if fix.translation and np.linalg.norm(fix.translation) > 1e-9:
            proposed = pos + np.array(fix.translation, dtype=np.float64)
            topic.add_viewpoint_from_point_and_guids(
                proposed,
                fix.moved_guid or fix.a_global_id,
            )

    bcf.save(output_path)
    return output_path


def export_manifest(fixes: list[ValidatedFix], path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "fix_count": len(fixes),
                "fixes": [
                    {
                        "case_id": f.case_id,
                        "stable_id": f.stable_id,
                        "clash_key": f.clash_key,
                        "position": f.position,
                        "translation": f.translation,
                        "regression_passed": f.regression_passed,
                        "ag_proven": f.ag_proven,
                    }
                    for f in fixes
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
