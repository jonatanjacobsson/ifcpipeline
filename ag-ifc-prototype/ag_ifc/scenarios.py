"""AEC scenario models for batch AlphaGeometry evaluation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


ExpectedSetup = Literal["ok", "setup_error"]
ExpectedProven = bool | None  # None = discovery mode (no assertion)


@dataclass
class Scenario:
    id: str
    category: str
    aec_use_case: str
    ag2: str
    subcategory: str = ""
    tags: list[str] = field(default_factory=list)
    aec_utility_hypothesis: str = "unknown"  # high | medium | low | unsuitable
    expected_setup: ExpectedSetup = "ok"
    expected_proven: ExpectedProven = None
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Scenario:
        expected = data.get("expected") or {}
        return cls(
            id=data["id"],
            category=data["category"],
            subcategory=data.get("subcategory", ""),
            aec_use_case=data["aec_use_case"],
            ag2=data["ag2"],
            tags=list(data.get("tags", [])),
            aec_utility_hypothesis=data.get("aec_utility_hypothesis", "unknown"),
            expected_setup=expected.get("setup", "ok"),
            expected_proven=expected.get("proven"),
            notes=data.get("notes", ""),
        )


@dataclass
class ScenarioOutcome:
    scenario_id: str
    category: str
    subcategory: str
    aec_use_case: str
    aec_utility_hypothesis: str
    setup_ok: bool
    proven: bool
    goal: str | None
    elapsed_ms: float
    error: str | None
    expected_match: bool | None
    ag2: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "category": self.category,
            "subcategory": self.subcategory,
            "aec_use_case": self.aec_use_case,
            "aec_utility_hypothesis": self.aec_utility_hypothesis,
            "setup_ok": self.setup_ok,
            "proven": self.proven,
            "goal": self.goal,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "error": self.error,
            "expected_match": self.expected_match,
            "ag2": self.ag2,
        }


def load_catalog(path: Path) -> list[Scenario]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    return [Scenario.from_dict(item) for item in data["scenarios"]]


def merge_catalogs(*paths: Path) -> list[Scenario]:
    seen: set[str] = set()
    out: list[Scenario] = []
    for path in paths:
        if not path.exists():
            continue
        for scenario in load_catalog(path):
            if scenario.id in seen:
                raise ValueError(f"duplicate scenario id: {scenario.id}")
            seen.add(scenario.id)
            out.append(scenario)
    return out
